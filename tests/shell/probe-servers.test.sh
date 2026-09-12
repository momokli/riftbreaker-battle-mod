#!/usr/bin/env bash
# ============================================================
# tests/shell/probe-servers.test.sh
# ------------------------------------------------------------
# Planetfreier Red/Green-Test für scripts/probe_servers.sh (Issue #246):
# `udpOk` muss "Server hat IM Container einen UDP-Socket gebunden" bedeuten —
# NICHT "ein Paket ging raus". Der frühere /dev/udp-Trick meldete "up", obwohl
# der Server tot war (kein bind, Issue #239) und hat den Ausfall maskiert.
#
# Kein Docker/Planet nötig: ein Fake-`docker` wird in den PATH gelegt und
# liefert je Modus (FAKE_UDP_BOUND=1/0/ss-missing) eine Socket-Liste mit/ohne
# Listener, sowohl über `ss` als auch über den procfs-Fallback.
#
# Läuft in CI (lint.yml, shellcheck-Job) und lokal:
#   tests/shell/probe-servers.test.sh
# ============================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROBE="${REPO_ROOT}/scripts/probe_servers.sh"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

BIN="${TMP}/bin"
mkdir -p "$BIN"

# Fake-Docker: nur die vom Probe genutzten Aufrufe (exec ss/cat, ps).
# FAKE_UDP_BOUND: 1 = Listener vorhanden, 0 = kein Listener.
# FAKE_NO_SS:     1 = `ss` im Container fehlt (leere Ausgabe) -> procfs-Pfad.
cat > "${BIN}/docker" <<'FAKE'
#!/usr/bin/env bash
case "${1:-}" in
  exec)
    shift
    shift                      # Container-Name
    case "${1:-}" in
      ss)
        if [ "${FAKE_NO_SS:-0}" = 1 ]; then
          exit 0                  # leere Ausgabe -> Probe faellt auf procfs zurueck
        fi
        [ "${FAKE_UDP_BOUND:-0}" = 1 ] && printf '%s\n' "UNCONN 0 0 0.0.0.0:6321 0.0.0.0:*"
        exit 0 ;;
      cat)
        if [ "${FAKE_UDP_BOUND:-0}" = 1 ]; then
          printf '%s\n' \
            "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode ref pointer drops" \
            "1234: 00000000:18B1 00000000:0000 07 00000000:00000000 00:00000000 00000000  0 0 1 2 0 0"
        fi
        exit 0 ;;
    esac
    exit 0 ;;
  ps)
    echo "Up 5 minutes (healthy)" ;;
esac
exit 0
FAKE
chmod +x "${BIN}/docker"

EP_FILE="${TMP}/endpoints"
printf '%s\n' "TEST-DEDI|127.0.0.1|6321|fake-dedi|" > "$EP_FILE"
EP_FILE_NO_CTR="${TMP}/endpoints-no-ctr"
printf '%s\n' "TEST-REMOTE|127.0.0.1|6321||" > "$EP_FILE_NO_CTR"

FAIL=0

check(){
  local label="$1" ep_file="$2" mode="$3" no_ss="$4" want="$5" out json got
  out="${TMP}/status.json"
  rm -f "$out"
  PATH="${BIN}:${PATH}" FAKE_UDP_BOUND="$mode" FAKE_NO_SS="$no_ss" \
    RB_OUT="$out" RB_ENDPOINTS_FILE="$ep_file" \
    RB_SITE_URL="http://127.0.0.1:1/" RB_HTTP_TIMEOUT=1 \
    bash "$PROBE" >/dev/null 2>&1
  json="$(cat "$out" 2>/dev/null || echo '')"
  # python3 liefert den udpOk-Wert (inkl. "none" fuer JSON null) und prueft
  # dabei zugleich, dass status.json valides JSON ist (ERR = kaputt/leer).
  got="$(printf '%s' "$json" | python3 -c \
    'import json,sys; d=json.load(sys.stdin); v=d["endpoints"][0]["udpOk"]; print("null" if v is None else str(v).lower())' \
    2>/dev/null || echo ERR)"
  if [ "$got" = "$want" ]; then
    printf 'PASS  %s (udpOk=%s)\n' "$label" "$got"
  else
    printf 'FAIL  %s — erwartet udpOk=%s, bekommen %s; status.json: %s\n' \
      "$label" "$want" "$got" "$json"
    FAIL=1
  fi
}

# 1) Socket gebunden (ss-Pfad) -> ehrlich "true".
check "gebundener UDP-Socket (ss) -> udpOk=true" "$EP_FILE" 1 0 true
# 2) Kein Socket (Dienst tot/kein bind) -> "false" (der alte Trick sagte hier "true").
check "kein UDP-Socket (ss) -> udpOk=false" "$EP_FILE" 0 0 false
# 3) `ss` im Container nicht verfuegbar -> procfs-Fallback, Socket gebunden.
check "gebundener UDP-Socket (procfs-Fallback) -> udpOk=true" "$EP_FILE" 1 1 true
# 4) `ss` im Container nicht verfuegbar -> procfs-Fallback, kein Socket.
check "kein UDP-Socket (procfs-Fallback) -> udpOk=false" "$EP_FILE" 0 1 false
# 5) Kein Container-Name am Endpoint (PR-#254-Review-Blocker) -> "null",
#    NICHT "false" — ein ungeprüfter Endpoint ist nicht dasselbe wie ein
#    toter. container_up behandelt denselben Fall bereits als null.
check "kein Container-Name -> udpOk=null (nicht false)" "$EP_FILE_NO_CTR" 0 0 null

# 6) site_check: httpCode bei Verbindungsfehler muss "0" (Zahl) sein, nicht
#    das dreistellige String-Literal "000" (waere ungueltiges JSON).
out="${TMP}/status-site.json"
PATH="${BIN}:${PATH}" FAKE_UDP_BOUND=0 FAKE_NO_SS=0 \
  RB_OUT="$out" RB_ENDPOINTS_FILE="$EP_FILE" \
  RB_SITE_URL="http://127.0.0.1:1/" RB_HTTP_TIMEOUT=1 \
  bash "$PROBE" >/dev/null 2>&1
site_json="$(cat "$out" 2>/dev/null || echo '')"
site_code="$(printf '%s' "$site_json" | python3 -c \
  'import json,sys; print(json.load(sys.stdin)["site"]["httpCode"])' 2>/dev/null || echo ERR)"
if [ "$site_code" = "0" ]; then
  printf 'PASS  %s (httpCode=%s)\n' "site_check meldet httpCode=0 bei Verbindungsfehler (valides JSON)" "$site_code"
else
  printf 'FAIL  %s — erwartet httpCode=0, bekommen %s; status.json: %s\n' \
    "site_check meldet httpCode=0 bei Verbindungsfehler" "$site_code" "$site_json"
  FAIL=1
fi

exit "$FAIL"
