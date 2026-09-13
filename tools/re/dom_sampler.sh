#!/usr/bin/env bash
# dom_sampler.sh — timestamped get_state sampler (Issue #376 live-test).
#
# Pollt POST /get_state der pipe_bridge alle 2 s und schreibt je Zeile
# "<epoch> <get_state_result-json>" nach OUT (Default /tmp/dom_wave_sample.log).
# Damit laesst sich der DOM-Wellen-Counter (wave) und time_to_next als
# Zeitreihe gegen Player-Beobachtungen (HUD-Wave-Timer, Spawn-Zeitpunkt)
# korrelieren.
#
# Aufruf (auf planet, als root):
#   bash tools/re/dom_sampler.sh [outfile] [interval_s]
set -euo pipefail

OUT="${1:-/tmp/dom_wave_sample.log}"
INTERVAL="${2:-2}"
URL="${RBB_BRIDGE_URL:-http://127.0.0.1:9001}"

: > "$OUT"
echo "sampler: OUT=$OUT interval=${INTERVAL}s url=$URL"

while true; do
  TS="$(date +%s)"
  R="$(curl -s -m 10 -X POST "$URL/get_state" -d '{}' 2>/dev/null || true)"
  echo "$TS $R" >> "$OUT"
  sleep "$INTERVAL"
done
