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
#   * legt `context.log` (letzte N Container-Zeilen) + `meta.json`
#     (Image-Tag, Git-SHA, Container-Uptime, Modulbasis aus der
#     `module_range`-Zeile, Fault-Adresse aus der `page fault`-Zeile) dazu,
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
# `CRASH:` (CrashHandlerWin32-Ausgabe) statt nur `CRASH` — sonst wuerde die
# Zeile „[critical] CrashHandlerWin32.cpp:103 - " selbst als Marker zaehlen.
MARKER_RE="${RB_CRASH_MARKER_RE:-CRASH:|page fault}"

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
  mapfile -t dirs < <(find "$CRASH_DIR" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null | sort)
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

meta = {
    "collected_at": env("RB_META_COLLECTED_AT", ""),
    "uuid": env("RB_META_UUID", ""),
    "bundle": env("RB_META_BUNDLE", ""),
    "container": env("RB_META_CONTAINER", ""),
    "image": env("RB_META_IMAGE", "") or None,
    "git_sha": env("RB_META_GIT_SHA", "") or None,
    "container_started_at": started or None,
    "container_uptime_seconds": uptime,
    "module_base": env("RB_META_MODULE_BASE", "") or None,
    "module_size": module_size,
    "fault_address": env("RB_META_FAULT_ADDRESS", "") or None,
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

  ring_dump >"${bundle}/context.log"

  IMAGE="$(container_image)"
  STARTED_AT="$(container_started_at)"
  GIT_SHA="${IMAGE##*:}"
  if [ "$GIT_SHA" = "$IMAGE" ]; then GIT_SHA=""; fi

  parse_context "${bundle}/context.log"
  marker="$(marker_name "$marker_line")"

  write_meta "${bundle}/meta.json" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$uuid" "$bundle_name" "$marker" \
    "$marker_line" "${files# }" \
    || log "WARN: meta.json konnte nicht geschrieben werden (python3 fehlt?)"

  log "Bundle gesichert: ${bundle_name} (${files# })"
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
