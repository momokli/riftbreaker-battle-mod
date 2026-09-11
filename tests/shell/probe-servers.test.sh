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
# liefert je Modus (FAKE_UDP_BOUND=1/0) eine Socket-Liste mit/ohne Listener.
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

# Fake-Docker: nur die vom Probe genutzten Aufrufe (exec/ps).
cat > "${BIN}/docker" <<'FAKE'
#!/usr/bin/env bash
case "${1:-}" in
  exec)
    shift
    shift                      # Container-Name
    case "${1:-}" in
      ss)
        # Gebundener Listener nur im "bound"-Modus.
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

FAIL=0
check(){
  local label="$1" mode="$2" want="$3" out json got
  out="${TMP}/status.json"
  rm -f "$out"
  PATH="${BIN}:${PATH}" FAKE_UDP_BOUND="$mode" \
    RB_OUT="$out" RB_ENDPOINTS_FILE="$EP_FILE" \
    RB_SITE_URL="http://127.0.0.1:1/" RB_HTTP_TIMEOUT=1 \
    bash "$PROBE" >/dev/null 2>&1
  json="$(cat "$out")"
  # python3 liefert den udpOk-Wert als "true"/"false" — und prüft dabei, dass
  # status.json valides JSON ist (ERR = kaputt/leer).
  got="$(printf '%s' "$json" | python3 -c \
    'import json,sys; d=json.load(sys.stdin); print(str(d["endpoints"][0]["udpOk"]).lower())' \
    2>/dev/null || echo ERR)"
  if [ "$got" = "$want" ]; then
    printf 'PASS  %s (udpOk=%s)\n' "$label" "$got"
  else
    printf 'FAIL  %s — erwartet udpOk=%s, bekommen %s; status.json: %s\n' \
      "$label" "$want" "$got" "$json"
    FAIL=1
  fi
}

# 1) Socket gebunden → ehrlich "true".
check "gebundener UDP-Socket -> udpOk=true" 1 true
# 2) Kein Socket (Dienst tot/kein bind) → "false" (der alte Trick sagte hier "true").
check "kein UDP-Socket -> udpOk=false" 0 false

exit "$FAIL"
