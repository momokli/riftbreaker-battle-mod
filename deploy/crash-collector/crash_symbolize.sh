#!/usr/bin/env bash
# ============================================================
# rbmods-crash-symbolize.sh — Crash-Bundle symbolisieren (Issue #480)
# ------------------------------------------------------------
# Collector-seitige Symbolik: die PDB (252 MB) bleibt ausserhalb des Images,
# llvm-symbolizer loest die Adressen des Minidumps gegen die bestehende DLL/PDB
# auf. Laeuft direkt nach dem Sammeln (crash_collector.sh) oder manuell:
#
#   rbmods-crash-symbolize.sh /opt/rbmods/crashes/<ts>-<uuid>
#
# Was es tut:
#   * sucht `<uuid>.dmp` im Bundle (oder nimmt `--dmp`),
#   * prueft die Skip-Regeln (Master-Switch/Pflichtdateien/Tool),
#   * ruft den Python-Kern (`deploy/crash-collector/symbolize.py`) und schreibt
#     `<bundle>/symbolized.txt` ATOMAR (Temp-Datei + `mv`).
#
# Umgebung (setzt die systemd-Unit; Defaults s.u.):
#   RB_CRASH_SYMBOLIZE       1 = an, 0 = ueberspringen
#   RB_CRASH_DLL             Game-Modul fuer `--obj`
#   RB_CRASH_PDB             Existenz-Check (Skip-Regel)
#   RB_CRASH_LLVM_SYMBOLIZER Tool-Binary
#   RB_CRASH_SYMBOLIZE_TOOL  Python-Kern
#   RB_CRASH_SYMBOLIZE_TIMEOUT  Zeitbudget in Sekunden (Default 60)
#   RB_CRASH_STACK_SCAN      1 = vollen Stack mit-symbolisieren (Default 1),
#                            0 = nur Fault-/Kontext-Frames
#   RB_CRASH_PYTHON          Python-Interpreter (Default python3)
#
# Vertrag: der Aufruf ist GRACEFUL — jeder Skip/Fehler endet mit rc=0 und
# loggt eine Zeile „symbolize: skip <grund>"; es gibt nie ein halb
# geschriebenes `symbolized.txt`. Nur ein falscher Aufruf (kein Bundle-Pfad)
# endet mit rc=2. Idempotent: existiert `symbolized.txt`, kein Re-Run.
# ============================================================
set -uo pipefail

log() { printf 'rbmods-crash-symbolize: %s\n' "$*"; }

usage() {
  printf 'Aufruf: %s [--dmp <pfad>] [--out <pfad>] <bundle-dir>\n' "$(basename "$0")"
}

BUNDLE=""
DMP=""
OUT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --dmp)
      DMP="${2:-}"
      shift 2 || { usage; exit 2; }
      ;;
    --out)
      OUT="${2:-}"
      shift 2 || { usage; exit 2; }
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    -*)
      log "unbekannte Option: $1"
      usage
      exit 2
      ;;
    *)
      BUNDLE="$1"
      shift
      ;;
  esac
done

if [ -z "$BUNDLE" ]; then
  usage
  exit 2
fi

SYMBOLIZE="${RB_CRASH_SYMBOLIZE:-1}"
PDB="${RB_CRASH_PDB:-/srv/rbgame/bin/riftbreaker_dll_win_release.pdb}"
DLL="${RB_CRASH_DLL:-/srv/rbgame/bin/riftbreaker_dll_win_release.dll}"
LLVM_SYMBOLIZER="${RB_CRASH_LLVM_SYMBOLIZER:-/usr/lib/llvm-18/bin/llvm-symbolizer}"
TOOL="${RB_CRASH_SYMBOLIZE_TOOL:-/usr/local/lib/rbmods/crash/symbolize.py}"
TIMEOUT="${RB_CRASH_SYMBOLIZE_TIMEOUT:-60}"
PYTHON="${RB_CRASH_PYTHON:-python3}"
# Zweites Modul (Issue #559): rbbridge.dll (eigene injizierte DLL, mingw -g).
# Optional: nur wenn gesetzt UND die Datei existiert, sonst nur Game-DLL.
RBBRIDGE_DLL="${RB_CRASH_RBBRIDGE_DLL:-}"
RBBRIDGE_MODULE="${RB_CRASH_RBBRIDGE_MODULE:-rbbridge.dll}"
# Stack-Scan (Issue #639): den vollen Stack des faultenden Threads mit-
# symbolisieren (Fault + RIP + gescannte Return-Adressen). Der Python-Kern
# filtert die Kandidaten .text-basiert, sobald die DLL eine echte PE-Section-
# Tabelle hat; Datenwerte (vftable/Konstanten) bleiben dann draussen.
STACK_SCAN="${RB_CRASH_STACK_SCAN:-1}"

[ -n "$OUT" ] || OUT="${BUNDLE}/symbolized.txt"
TMP_OUT="${OUT}.tmp.$$"
trap 'rm -f "${TMP_OUT:-}"' EXIT

# skip: Grund loggen, rc=0 (der Collector darf davon nie sterben).
skip() {
  log "symbolize: skip $*"
  exit 0
}

[ "$SYMBOLIZE" = "0" ] && skip "RB_CRASH_SYMBOLIZE=0"
[ -d "$BUNDLE" ] || skip "kein Bundle-Verzeichnis ($BUNDLE)"
[ -s "$OUT" ] && skip "bereits symbolisiert ($OUT)"

if [ -z "$DMP" ]; then
  for candidate in "$BUNDLE"/*.dmp; do
    [ -f "$candidate" ] || continue
    DMP="$candidate"
    break
  done
fi
if [ -z "$DMP" ] || [ ! -f "$DMP" ]; then
  skip "kein Minidump (.dmp) im Bundle"
fi

[ -f "$PDB" ] || skip "PDB fehlt ($PDB)"
[ -f "$DLL" ] || skip "DLL fehlt ($DLL)"
[ -x "$LLVM_SYMBOLIZER" ] || skip "Tool fehlt ($LLVM_SYMBOLIZER)"
[ -f "$TOOL" ] || skip "Symbolizer-Kern fehlt ($TOOL)"
command -v "$PYTHON" >/dev/null 2>&1 || skip "Python fehlt ($PYTHON)"

# Optionales zweites Modul (rbbridge.dll) vorbereiten — graceful, nie Hard-Fail.
EXTRA=()
if [ -n "$RBBRIDGE_DLL" ] && [ -f "$RBBRIDGE_DLL" ]; then
  EXTRA=(--dll2 "$RBBRIDGE_DLL" --module2 "$RBBRIDGE_MODULE")
fi

# Stack-Scan-Flag (Issue #639): 0 schaltet auf das alte Verhalten zurück.
STACK_FLAGS=()
[ "$STACK_SCAN" = "1" ] && STACK_FLAGS=(--stack-scan)

rm -f "$TMP_OUT"
RC=0
if command -v timeout >/dev/null 2>&1; then
  timeout "$TIMEOUT" "$PYTHON" "$TOOL" \
    --dmp "$DMP" --dll "$DLL" --symbolizer "$LLVM_SYMBOLIZER" \
    --uuid "$(basename "$DMP" .dmp)" --out "$TMP_OUT" \
    ${STACK_FLAGS[@]+"${STACK_FLAGS[@]}"} ${EXTRA[@]+"${EXTRA[@]}"} || RC=$?
else
  "$PYTHON" "$TOOL" \
    --dmp "$DMP" --dll "$DLL" --symbolizer "$LLVM_SYMBOLIZER" \
    --uuid "$(basename "$DMP" .dmp)" --out "$TMP_OUT" \
    ${STACK_FLAGS[@]+"${STACK_FLAGS[@]}"} ${EXTRA[@]+"${EXTRA[@]}"} || RC=$?
fi

case "$RC" in
  0)
    if [ -s "$TMP_OUT" ]; then
      mv -f "$TMP_OUT" "$OUT"
      log "symbolize: ok ${OUT} ($(grep -c $'\t' "$OUT" || true) Frames)"
    else
      rm -f "$TMP_OUT"
      log "symbolize: error (leere Ausgabe) — Bundle bleibt unsymbolisiert"
    fi
    ;;
  3)
    rm -f "$TMP_OUT"
    skip "kein Modul-Frame"
    ;;
  *)
    rm -f "$TMP_OUT"
    log "symbolize: error (Symbolizer rc=${RC}) — Bundle bleibt unsymbolisiert"
    ;;
esac
exit 0
