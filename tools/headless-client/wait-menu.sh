#!/usr/bin/env bash
#
# wait-menu.sh — wartet, bis der headless Client das Hauptmenü gerendert hat,
# und legt einen Beweis-Screenshot ab.
#
# Heuristik (bewusst kein pixel-perfekter Menü-Check):
#   1. Frame wird per ImageMagick `import` vom virtuellen Display gezogen.
#   2. Als "gerendert" gilt ein Frame, dessen Grau-Standardabweichung über
#      RENDER_MIN_STD liegt — ein leerer/schwarzer Frame hat std = 0, sobald
#      Logo/Menü erscheint, steigt die Standardabweichung.
#   3. Bei Erfolg wird der Frame nach <out> kopiert; bei Timeout wird der
#      letzte Frame trotzdem gesichert und exit 1 zurückgegeben.
#
# Nutzung:
#   wait-menu.sh [<screenshot-pfad>]
#   docker compose exec rb-headless wait-menu.sh
#
# Umgebung:
#   DISPLAY          X-Display (Default: :99)
#   SCREENSHOT_OUT   Zielpfad des Beweis-Screenshots
#   WAIT_TIMEOUT     Gesamt-Timeout in Sekunden (Default: 120)
#   RENDER_MIN_STD   Mindest-Standardabweichung 0..1 (Default: 0.02)
#   RENDER_POLL      Abstand der Frame-Checks in Sekunden (Default: 3)
#   IMPORT_BIN       import-Binary (überschreibbar für Tests)
#   CONVERT_BIN      convert-Binary (überschreibbar für Tests)

set -euo pipefail

DISPLAY="${DISPLAY:-:99}"
export DISPLAY

OUT="${1:-${SCREENSHOT_OUT:-/srv/rbclient/screenshots/menu.png}}"
WAIT_TIMEOUT="${WAIT_TIMEOUT:-120}"
RENDER_MIN_STD="${RENDER_MIN_STD:-0.02}"
RENDER_POLL="${RENDER_POLL:-3}"

IMPORT_BIN="${IMPORT_BIN:-import}"
CONVERT_BIN="${CONVERT_BIN:-convert}"

start_ts="$(date +%s)"
elapsed() { echo $(( $(date +%s) - start_ts )); }

tmp="$(mktemp --suffix=.png)"
trap 'rm -f "$tmp"' EXIT

echo "[wait-menu] warte auf gerendertes Hauptmenü (std > ${RENDER_MIN_STD}, Timeout ${WAIT_TIMEOUT}s) …"

while :; do
    "$IMPORT_BIN" -window root "$tmp" 2>/dev/null || true

    std="0"
    if [[ -s "$tmp" ]]; then
        std="$("$CONVERT_BIN" "$tmp" -colorspace Gray -format '%[fx:standard_deviation]' info: 2>/dev/null || echo 0)"
    fi

    rendered="$(awk -v s="$std" -v t="$RENDER_MIN_STD" 'BEGIN { print (s >= t) ? "1" : "0" }')"
    if [[ "$rendered" == "1" ]]; then
        mkdir -p "$(dirname "$OUT")"
        cp "$tmp" "$OUT"
        echo "[wait-menu] Hauptmenü gerendert (std=${std}); Beweis-Screenshot: ${OUT}"
        exit 0
    fi

    if [[ "$(elapsed)" -ge "$WAIT_TIMEOUT" ]]; then
        echo "[wait-menu] FEHLER: kein gerenderter Frame nach ${WAIT_TIMEOUT}s (std=${std})" >&2
        if [[ -s "$tmp" ]]; then
            mkdir -p "$(dirname "$OUT")"
            cp "$tmp" "$OUT"
            echo "[wait-menu] letzten Frame gesichert: ${OUT}" >&2
        fi
        exit 1
    fi

    sleep "$RENDER_POLL"
done
