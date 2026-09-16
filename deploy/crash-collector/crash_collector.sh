#!/usr/bin/env bash
# ============================================================
# rbmods-crash-collector.sh — Crash-Artefakte sichern (Issue #462)
# ------------------------------------------------------------
# Läuft als systemd-Dauerdienst (Rolle deploy/roles/crash-collector) auf planet.
#
# Was er tut:
#   * beobachtet `docker logs -f --tail 0 <container>` auf Crash-Marker
#     (`CRASH`, `page fault` — rhcrash/„Unhandled page fault" des Wine-Prozesses),
#   * kopiert beim Crash die NEUESTEN `crash_info/<uuid>.{dmp,log,trace}` aus
#     dem Wine-Volume (`docker cp`) nach `<crash_dir>/<ts>-<uuid>/`,
#   * parst den Minidump minimal (`deploy/crash-collector/minidump_meta.py`, Issue #481) und
#     uebernimmt Exception-Code/-Adresse, Modul, Modulbasis, RVA, Fault-Thread
#     und Stack-RVAs — robuster als das Log-Zeilen-Fenster,
#   * sichert beim Crash die geladenen Modul-Bytes (rbbridge.dll + Game-DLL) mit
#     sha256 (Issue #588) — Checksums/Pfade landen in `meta.json`
#     (`module_sha256`/`module_paths`); fehlende Dateien sind kein Fehler,
#   * legt `context.log` (letzte N Container-Zeilen) + `meta.json`
#     (Image-Tag, Git-SHA, Container-Uptime, Modulbasis/Fault-Adresse aus dem
#     Dump; Fallback: `module_range`-/`page fault`-Zeile DIESES Bundles) dazu,
#   * Retention: behält die neuesten N Bundles UND räumt ältere
#     `crash_info`-Dateien im Wine-Volume weg (behebt das unbegrenzte
#     Wachstum, Befund: 39 Dateien / unbegrenzt).
#
# Umgebung (setzt die systemd-Unit; Defaults s.u.):
#   RB_CRASH_DOCKER         Docker-Binary (Default: docker)
#   RB_CRASH_CONTAINER      beobachteter Container (Default: riftbreaker-dedicated)
#   RB_CRASH_DIR            Zielverzeichnis der Bundles (Default: /opt/rbmods/crashes)
#   RB_CRASH_CRASHINFO      crash_info-Pfad IM Container
#   RB_CRASH_CONTEXT_LINES  Zeilen für context.log (Default: 200)
#   RB_CRASH_RETENTION      behaltene Bundles (Default: 20)
#   RB_CRASH_WAIT_SECS      Wartezeit auf die Dump-Datei nach dem Marker (Default: 30)
#   RB_CRASH_RETRY_SLEEP    Pause, bevor `docker logs -f` erneut angehängt wird
#   RB_CRASH_PRUNE_WINE     1 = crash_info im Container auf Retention kürzen
#   RB_CRASH_LOG_CMD        Test-Seam: Stream-Quelle statt `docker logs -f`
#   RB_CRASH_MARKER_RE      Crash-Marker-Regex (Default: CRASH|page fault)
#   RB_CRASH_MINIDUMP_PY    Minidump-Parser (Default: Geschwister des Collectors,
#                           `<dir von $0>/minidump_meta.py`). Fehlt er oder ist
#                           der Dump kaputt -> neue Felder null, KEIN Abbruch.
#   RB_CRASH_PYTHON         Python fuer meta.json + Parser (Default: python3)
#   RB_CRASH_RBBRIDGE_DLL   Host-Pfad rbbridge.dll (Default: /opt/rbmods/rbtools/rbbridge.dll,
#                           nur manueller Fallback — die systemd-Unit setzt den per-env-Pfad)
#   RB_CRASH_DLL            Host-Pfad Game-DLL (Default: /srv/rbgame/bin/riftbreaker_dll_win_release.dll)
#
# Aufruf: rbmods-crash-collector.sh [--once]
#   --once  liest den Stream bis EOF, sammelt einen evtl. Crash und beendet
#           sich (für Tests / Einmal-Läufe). Default: Dauerdienst.
# ============================================================
# Kein `set -e`: ein Dauerdienst darf an einem kaputten Einzel-Aufruf nicht
# sterben — Fehler werden explizit geloggt und der Dienst läuft weiter.
set -uo pipefail

DOCKER="${RB_CRASH_DOCKER:-docker}"
CONTAINER="${RB_CRASH_CONTAINER:-riftbreaker-dedicated}"
CRASH_DIR="${RB_CRASH_DIR:-/opt/rbmods/crashes}"
CRASHINFO="${RB_CRASH_CRASHINFO:-/data/.wine/drive_c/users/steamuser/Documents/The Riftbreaker/crash_info}"
CONTEXT_LINES="${RB_CRASH_CONTEXT_LINES:-200}"
RETENTION="${RB_CRASH_RETENTION:-20}"
WAIT_SECS="${RB_CRASH_WAIT_SECS:-30}"
RETRY_SLEEP="${RB_CRASH_RETRY_SLEEP:-5}"
PRUNE_WINE="${RB_CRASH_PRUNE_WINE:-1}"
LOG_CMD="${RB_CRASH_LOG_CMD:-}"
# Symbolik (Issue #480): CLI-Seam, die das Bundle collector-seitig symbolisiert.
SYMBOLIZE_BIN="${RB_CRASH_SYMBOLIZE_BIN:-/usr/local/bin/rbmods-crash-symbolize.sh}"
# `CRASH:` (CrashHandlerWin32-Ausgabe) statt nur `CRASH` — sonst wuerde die
# Zeile „[critical] CrashHandlerWin32.cpp:103 - " selbst als Marker zaehlen.
MARKER_RE="${RB_CRASH_MARKER_RE:-CRASH:|page fault}"
# Modul-Bytes + sha256 (Issue #588): Host-Pfade der geladenen DLLs. Die
# systemd-Unit setzt RB_CRASH_RBBRIDGE_DLL/RB_CRASH_DLL bereits (Rolle
# crash-collector, per-env); die Defaults darunter greifen nur bei manuellen
# Einzel-Läufen ohne Unit und sind bewusst NICHT per-env (plain shell).
RBBRIDGE_DLL="${RB_CRASH_RBBRIDGE_DLL:-/opt/rbmods/rbtools/rbbridge.dll}"
GAME_DLL="${RB_CRASH_DLL:-/srv/rbgame/bin/riftbreaker_dll_win_release.dll}"

RUN_ONCE=0
for arg in "$@"; do
  case "$arg" in
    --once) RUN_ONCE=1 ;;
    -h | --help)
      printf 'Aufruf: %s [--once]\n' "$(basename "$0")"
      exit 0
      ;;
  esac
done

log() { printf 'rbmods-crash-collector: %s\n' "$*"; }

PYTHON="${RB_CRASH_PYTHON:-python3}"
MINIDUMP_PY="${RB_CRASH_MINIDUMP_PY:-$(dirname "$0")/minidump_meta.py}"
# Rohes Parser-JSON des aktuellen Bundles (set -u-Fest, von collect_bundle gesetzt).
DUMP_JSON=""
# Modul-Sha256/Pfade + kopierte Modul-Dateinamen (Issue #588). JSON-Objekte
# "{}" als Leerwert, damit `set -u` nie feuert (von collect_modules gesetzt).
MODULE_SHA256_JSON="{}"
MODULE_PATH_JSON="{}"
MODULE_FILES=""

# --- Minidump-Parse (Issue #481) ----------------------------------------------
# Rohes Helper-JSON (oder leer): Datei fehlt / Python fehlt / Parsefehler ->
# leer, der Collector faellt auf die Log-Fenster-Werte zurueck.
minidump_meta_json() {
  local dmp="$1"
  [ -f "$dmp" ] || return 0
  [ -f "$MINIDUMP_PY" ] || {
    log "WARN: Minidump-Helper ${MINIDUMP_PY} fehlt — Dump-Felder null"
    return 0
  }
  "$PYTHON" "$MINIDUMP_PY" "$dmp" 2>/dev/null || true
}

# --- Ringpuffer der letzten Stream-Zeilen (Kontext zum Crash-Zeitpunkt) ------
RING=()
RING_N=0

ring_push() {
  RING[RING_N]="$1"
  RING_N=$((RING_N + 1))
  if [ "$RING_N" -gt "$CONTEXT_LINES" ]; then
    RING=("${RING[@]:1}")
    RING_N=$((RING_N - 1))
  fi
}

ring_dump() {
  local i
  for ((i = 0; i < RING_N; i++)); do
    printf '%s\n' "${RING[$i]}"
  done
}

is_marker() {
  local line="$1" rc
  shopt -s nocasematch
  if [[ "$line" =~ $MARKER_RE ]]; then rc=0; else rc=1; fi
  shopt -u nocasematch
  return "$rc"
}

stream_cmd() {
  if [ -n "$LOG_CMD" ]; then
    bash -c "$LOG_CMD"
  else
    "$DOCKER" logs -f --tail 0 "$CONTAINER"
  fi
}

# --- Container-Metadaten ------------------------------------------------------
container_image() {
  "$DOCKER" inspect --format '{{.Config.Image}}' "$CONTAINER" 2>/dev/null | tr -d '\r' || true
}

container_started_at() {
  "$DOCKER" inspect --format '{{.State.StartedAt}}' "$CONTAINER" 2>/dev/null | tr -d '\r' || true
}

# Neueste crash_info-uuid im Container (mtime-sortiert). Leer bei Fehler.
newest_uuid() {
  local p
  p="$("$DOCKER" exec "$CONTAINER" sh -c "ls -1t '$CRASHINFO'/*.dmp 2>/dev/null | head -n1" 2>/dev/null | tr -d '\r' || true)"
  [ -n "$p" ] || return 1
  basename "$p" .dmp
}

# Bundle für diese uuid existiert schon? (Idempotenz: kein Doppel-Bundle)
bundle_exists() {
  local uuid="$1" d
  for d in "$CRASH_DIR"/*-"$uuid"; do
    [ -d "$d" ] && return 0
  done
  return 1
}

# --- Retention ----------------------------------------------------------------
retention_prune() {
  local dirs=() n
  # Portabel: kein `find -printf` (GNU-only) — BSD/macOS-find kennt es nicht
  # und lieferte dort eine leere Liste (Retention griff nicht, Test rot).
  mapfile -t dirs < <(find "$CRASH_DIR" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sed 's|.*/||' | sort)
  n=${#dirs[@]}
  while [ "$n" -gt "$RETENTION" ]; do
    log "Retention: entferne altes Bundle ${dirs[0]}"
    rm -rf "${CRASH_DIR:?}/${dirs[0]}"
    dirs=("${dirs[@]:1}")
    n=$((n - 1))
  done
}

# crash_info im Wine-Volume auf die neuesten RETENTION uuids kürzen (entfernt
# das unbegrenzte Wachstum). Läuft erst NACH dem Kopieren — die frischeste
# Datei bleibt immer erhalten.
wine_prune() {
  [ "$PRUNE_WINE" = "1" ] || return 0
  "$DOCKER" exec "$CONTAINER" sh -c \
    "cd '$CRASHINFO' 2>/dev/null || exit 0; ls -1t *.dmp 2>/dev/null | tail -n +$((RETENTION + 1)) | while read -r f; do rm -f \"\${f%.dmp}.dmp\" \"\${f%.dmp}.log\" \"\${f%.dmp}.trace\"; done" \
    >/dev/null 2>&1 || true
}

# --- meta.json ----------------------------------------------------------------
write_meta() {
  local meta_path="$1" collected_at="$2" uuid="$3" bundle_name="$4" marker="$5" crash_line="$6" files="$7"
  RB_META_COLLECTED_AT="$collected_at" \
    RB_META_UUID="$uuid" \
    RB_META_BUNDLE="$bundle_name" \
    RB_META_MARKER="$marker" \
    RB_META_CRASH_LINE="$crash_line" \
    RB_META_FILES="$files" \
    RB_META_CONTAINER="$CONTAINER" \
    RB_META_IMAGE="$IMAGE" \
    RB_META_GIT_SHA="$GIT_SHA" \
    RB_META_STARTED_AT="$STARTED_AT" \
    RB_META_MODULE_BASE="$MODULE_BASE" \
    RB_META_MODULE_SIZE="$MODULE_SIZE" \
    RB_META_FAULT_ADDRESS="$FAULT_ADDRESS" \
    RB_META_CONTEXT_LINES="$CONTEXT_LINES" \
    RB_META_DUMP_JSON="$DUMP_JSON" \
    RB_META_MODULE_SHA256="$MODULE_SHA256_JSON" \
    RB_META_MODULE_PATH="$MODULE_PATH_JSON" \
    "$PYTHON" - "$meta_path" <<'PY'
import datetime
import json
import os
import sys

env = os.environ.get

started = env("RB_META_STARTED_AT", "")
uptime = None
if started:
    try:
        dt = datetime.datetime.fromisoformat(started.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        uptime = int((datetime.datetime.now(datetime.timezone.utc) - dt).total_seconds())
    except (ValueError, TypeError):
        uptime = None

try:
    module_size = int(env("RB_META_MODULE_SIZE", ""))
except (TypeError, ValueError):
    module_size = None

try:
    context_lines = int(env("RB_META_CONTEXT_LINES", "0"))
except (TypeError, ValueError):
    context_lines = 0

# Issue #481: Minidump-Felder aus dem selben Bundle. Nur ein _ok:true-Dump
# zaehlt; sonst bleiben die neuen Felder null und die Log-Werte (dieses
# Bundles) gelten weiter. Kein Fremdboot-Wert.
dump = {}
raw_dump = env("RB_META_DUMP_JSON", "")
if raw_dump:
    try:
        candidate = json.loads(raw_dump)
    except (TypeError, ValueError):
        candidate = None
    if isinstance(candidate, dict) and candidate.get("_ok"):
        dump = candidate

# Issue #588: Modul-Bytes/Checksums aus dem selben Bundle. JSON-Objekte werden
# von collect_modules erzeugt; fehlt etwas, bleibt das Feld ein leeres Objekt.
module_sha256 = {}
raw_module_sha256 = env("RB_META_MODULE_SHA256", "")
if raw_module_sha256:
    try:
        candidate = json.loads(raw_module_sha256)
        if isinstance(candidate, dict):
            module_sha256 = candidate
    except (TypeError, ValueError):
        pass

module_paths = {}
raw_module_paths = env("RB_META_MODULE_PATH", "")
if raw_module_paths:
    try:
        candidate = json.loads(raw_module_paths)
        if isinstance(candidate, dict):
            module_paths = candidate
    except (TypeError, ValueError):
        pass

module_base = env("RB_META_MODULE_BASE", "") or None
fault_address = env("RB_META_FAULT_ADDRESS", "") or None
exception_address = None
exception_code = None
fault_thread = None
module = None
fault_rva = None
stack_rvas = None
if dump:
    exception_code = dump.get("exception_code")
    exception_address = dump.get("exception_address")
    fault_thread = dump.get("fault_thread")
    module = dump.get("module")
    fault_rva = dump.get("fault_rva")
    stack_rvas = dump.get("stack_rvas") or []
    # Dump bevorzugt, sonst Log-Fenster — beides aus DIESEM Bundle.
    module_base = dump.get("module_base") or module_base
    if dump.get("module_size") is not None:
        module_size = dump["module_size"]
    fault_address = exception_address or fault_address

meta = {
    "collected_at": env("RB_META_COLLECTED_AT", ""),
    "uuid": env("RB_META_UUID", ""),
    "bundle": env("RB_META_BUNDLE", ""),
    "container": env("RB_META_CONTAINER", ""),
    "image": env("RB_META_IMAGE", "") or None,
    "git_sha": env("RB_META_GIT_SHA", "") or None,
    "container_started_at": started or None,
    "container_uptime_seconds": uptime,
    "module_base": module_base,
    "module_size": module_size,
    "fault_address": fault_address,
    "exception_code": exception_code,
    "exception_address": exception_address,
    "module": module,
    "module_sha256": module_sha256,
    "module_paths": module_paths,
    "fault_rva": fault_rva,
    "fault_thread": fault_thread,
    "stack_rvas": stack_rvas,
    "crash_marker": env("RB_META_MARKER", ""),
    "crash_line": env("RB_META_CRASH_LINE", ""),
    "context_lines": context_lines,
    "files": [f for f in env("RB_META_FILES", "").split() if f],
}

with open(sys.argv[1], "w", encoding="utf-8") as fh:
    json.dump(meta, fh, indent=2, ensure_ascii=False)
    fh.write("\n")
PY
}

# --- Symbolik (Issue #480) ----------------------------------------------------
# Collector-seitig: die PDB (252 MB) bleibt ausserhalb des Images. Der Symbolizer
# liest das Minidump und schreibt `symbolized.txt` ins Bundle; hier wird nur das
# Ergebnis in `meta.json` referenziert (files-Liste + Feld `symbolized`).
# Fehler/Skips duerfen den Collector NIE toeten (kein `set -e`): das Bundle bleibt
# in jedem Fall gueltig, es gibt maximal ein WARN.
update_meta_symbolized() {
  local bundle="$1" status="$2"
  RB_META_PATH="${bundle}/meta.json" RB_META_STATUS="$status" \
    "$PYTHON" - <<'PY'
import datetime
import json
import os
import sys

path = os.environ["RB_META_PATH"]
try:
    with open(path, encoding="utf-8") as fh:
        meta = json.load(fh)
except (OSError, ValueError):
    sys.exit(1)

frames = 0
header = {}
sym_path = os.path.join(os.path.dirname(path), "symbolized.txt")
try:
    with open(sym_path, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("# ") and ":" in line:
                key, _, value = line[2:].partition(":")
                header[key.strip()] = value.strip()
            elif line.startswith("0x"):
                frames += 1
except OSError:
    pass

files = meta.get("files") or []
if frames and "symbolized.txt" not in files:
    files.append("symbolized.txt")
meta["files"] = files
meta["symbolized"] = {
    "status": os.environ["RB_META_STATUS"],
    "frames": frames,
    "tool": header.get("tool") or None,
    "dll": header.get("dll") or None,
    "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
}

with open(path, "w", encoding="utf-8") as fh:
    json.dump(meta, fh, indent=2, ensure_ascii=False)
    fh.write("\n")
PY
}

symbolize_bundle() {
  local bundle="$1" out
  # Fehlt das Symbolizer-Skript, verhaelt sich der Collector wie vor #480.
  [ -x "$SYMBOLIZE_BIN" ] || return 0
  out="$("$SYMBOLIZE_BIN" "$bundle" 2>&1)" || true
  if [ -n "$out" ]; then
    while IFS= read -r line; do log "${line}"; done <<<"$out"
  fi
  if [ -s "${bundle}/symbolized.txt" ]; then
    update_meta_symbolized "$bundle" ok || log "WARN: meta.json-Symbolikfeld nicht schreibbar"
  else
    update_meta_symbolized "$bundle" skipped || log "WARN: meta.json-Symbolikfeld nicht schreibbar"
  fi
}

# --- Parsen der Crash-Kontextzeilen aus context.log ---------------------------
parse_context() {
  local ctx="$1"
  MODULE_BASE=""
  MODULE_SIZE=""
  FAULT_ADDRESS=""
  local ml fl
  ml="$(grep -i 'module_range:' "$ctx" 2>/dev/null | tail -n1 || true)"
  MODULE_BASE="$(printf '%s' "$ml" | sed -n 's/.*base=\([0-9a-fA-F]\{1,\}\).*/\1/p')"
  MODULE_SIZE="$(printf '%s' "$ml" | sed -n 's/.*size=\([0-9]\{1,\}\).*/\1/p')"
  fl="$(grep -i 'page fault' "$ctx" 2>/dev/null | tail -n1 || true)"
  # Fault-Adresse: bevorzugt die Adresse hinter „at address" (Wine nennt dort
  # die Fault-Adresse, waehrend der erste 0x-Token die Zugriffsadresse ist),
  # sonst der erste 8..16-stellige Hex-Token. Ergebnis ohne 0x-Praefix.
  FAULT_ADDRESS="$(printf '%s' "$fl" | grep -oiE 'at address +0x?[0-9a-f]+' | head -n1 | grep -oiE '[0-9a-f]{8,16}$' || true)"
  if [ -z "$FAULT_ADDRESS" ]; then
    FAULT_ADDRESS="$(printf '%s' "$fl" | grep -oiE '0x[0-9a-f]{8,16}' | head -n1 | sed 's/^0x//I' || true)"
  fi
  if [ -z "$FAULT_ADDRESS" ]; then
    FAULT_ADDRESS="$(printf '%s' "$fl" | grep -oiE '[0-9a-f]{8,16}' | head -n1 || true)"
  fi
}

# Marker-Name aus der ausloesenden Zeile (fuer meta.json).
marker_name() {
  shopt -s nocasematch
  if [[ "$1" == *"page fault"* ]]; then
    printf 'page fault'
  elif [[ "$1" == *"crash"* ]]; then
    printf 'CRASH'
  else
    printf 'unknown'
  fi
  shopt -u nocasematch
}

# --- Modul-Bytes + sha256 (Issue #588) ----------------------------------------
# Beim Crash die geladenen DLL-Bytes ins Bundle kopieren und die Checksumme
# berechnen, damit CI-Artefakt und Crash-Zeit-DLL byte-identisch vergleichbar
# sind (der Minidump enthaelt keine Modul-Bytes). Graceful: fehlende Datei =>
# Eintrag ausgelassen + WARN, der Collector stirbt nie.
json_escape() {
  local s="$1" out="" i ch
  for ((i = 0; i < ${#s}; i++)); do
    ch="${s:$i:1}"
    case "$ch" in
      '"' | '\') out="${out}\\${ch}" ;;
      *) out="${out}${ch}" ;;
    esac
  done
  printf '%s' "$out"
}

collect_modules() {
  local bundle="$1"
  local names=("rbbridge.dll" "riftbreaker_dll_win_release.dll")
  local paths=("$RBBRIDGE_DLL" "$GAME_DLL")
  local i n name host_path dest sha
  local sha_json="{" path_json="{" sha_first=1 path_first=1

  MODULE_SHA256_JSON="{}"
  MODULE_PATH_JSON="{}"
  MODULE_FILES=""

  n=${#names[@]}
  for ((i = 0; i < n; i++)); do
    name="${names[$i]}"
    host_path="${paths[$i]}"
    [ -n "$host_path" ] || {
      log "WARN: kein Host-Pfad fuer Modul ${name} — ausgelassen"
      continue
    }
    if [ ! -f "$host_path" ]; then
      log "WARN: Modul ${name} fehlt (${host_path}) — ausgelassen"
      continue
    fi

    dest="${bundle}/${name}"
    if ! cp "$host_path" "$dest" 2>/dev/null; then
      log "WARN: ${name} konnte nicht kopiert werden (${host_path})"
      continue
    fi
    log "Modul gesichert: ${name} <- ${host_path}"
    MODULE_FILES="${MODULE_FILES} ${name}"

    [ "$path_first" = "1" ] || path_json="${path_json},"
    path_json="${path_json}\"$(json_escape "$name")\":\"$(json_escape "$host_path")\""
    path_first=0

    sha=""
    if command -v sha256sum >/dev/null 2>&1; then
      sha="$(sha256sum "$dest" 2>/dev/null | awk '{print $1}')"
    elif command -v shasum >/dev/null 2>&1; then
      sha="$(shasum -a 256 "$dest" 2>/dev/null | awk '{print $1}')"
    fi
    if [ -n "$sha" ]; then
      [ "$sha_first" = "1" ] || sha_json="${sha_json},"
      sha_json="${sha_json}\"$(json_escape "$name")\":\"${sha}\""
      sha_first=0
    else
      log "WARN: sha256sum/shasum fehlt — Checksum fuer ${name} ausgelassen"
    fi
  done

  MODULE_SHA256_JSON="${sha_json}}"
  MODULE_PATH_JSON="${path_json}}"
}

# --- Ein Crash -> ein Bundle --------------------------------------------------
collect_bundle() {
  local marker_line="$1"
  local uuid="" waited=0 marker

  while [ "$waited" -lt "$WAIT_SECS" ]; do
    uuid="$(newest_uuid || true)"
    if [ -n "$uuid" ] && ! bundle_exists "$uuid"; then break; fi
    uuid=""
    sleep 2
    waited=$((waited + 2))
  done

  if [ -z "$uuid" ]; then
    log "kein (neues) crash_info/<uuid>.dmp gefunden — kein Bundle (Dump fehlt/Container weg)"
    return 0
  fi
  if bundle_exists "$uuid"; then
    log "Bundle fuer ${uuid} existiert bereits — uebersprungen"
    return 0
  fi

  local ts bundle_name bundle
  ts="$(date -u +%Y%m%dT%H%M%SZ)"
  bundle_name="${ts}-${uuid}"
  bundle="${CRASH_DIR}/${bundle_name}"
  mkdir -p "$bundle" || { log "FEHLER: ${bundle} nicht anlegbar"; return 1; }

  local ext files=""
  for ext in dmp log trace; do
    if "$DOCKER" cp "${CONTAINER}:${CRASHINFO}/${uuid}.${ext}" "${bundle}/${uuid}.${ext}" >/dev/null 2>&1; then
      files="${files} ${uuid}.${ext}"
    else
      log "WARN: ${uuid}.${ext} konnte nicht kopiert werden"
    fi
  done

  # Modul-Bytes + sha256 (Issue #588) — vor write_meta, damit die Checksums
  # und kopierten Dateinamen (files-Liste) im selben Bundle landen.
  collect_modules "$bundle"
  files="${files}${MODULE_FILES}"

  ring_dump >"${bundle}/context.log"

  IMAGE="$(container_image)"
  STARTED_AT="$(container_started_at)"
  GIT_SHA="${IMAGE##*:}"
  if [ "$GIT_SHA" = "$IMAGE" ]; then GIT_SHA=""; fi

  parse_context "${bundle}/context.log"
  DUMP_JSON="$(minidump_meta_json "${bundle}/${uuid}.dmp")"
  marker="$(marker_name "$marker_line")"

  write_meta "${bundle}/meta.json" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$uuid" "$bundle_name" "$marker" \
    "$marker_line" "${files# }" \
    || log "WARN: meta.json konnte nicht geschrieben werden (python3 fehlt?)"

  log "Bundle gesichert: ${bundle_name} (${files# })"
  symbolize_bundle "$bundle"
  retention_prune
  wine_prune
}

# --- Beobachten ---------------------------------------------------------------
watch_stream() {
  local line
  while IFS= read -r line; do
    ring_push "$line"
    if is_marker "$line"; then
      log "Crash-Marker erkannt: ${line}"
      collect_bundle "$line"
    fi
  done < <(stream_cmd)
}

mkdir -p "$CRASH_DIR" 2>/dev/null || true
log "start: container=${CONTAINER} dir=${CRASH_DIR} retention=${RETENTION} markers=${MARKER_RE}"

while true; do
  watch_stream
  if [ "$RUN_ONCE" = "1" ]; then
    log "Stream-Ende (--once) — beende"
    break
  fi
  log "Stream getrennt — haenge in ${RETRY_SLEEP}s erneut an"
  sleep "$RETRY_SLEEP"
done
