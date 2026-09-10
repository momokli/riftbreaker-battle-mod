#!/usr/bin/env bash
#
# run-client.sh — startet den Riftbreaker-Client headless (Xvfb + Wine).
#
# Startet bei Bedarf einen virtuellen X-Server (Xvfb) auf DISPLAY, initialisiert
# das Wine-Prefix und startet die Client-Exe. Rendering läuft über Mesa-llvmpipe
# (Software). Eingaben/Auswertung über tools/headless-client/xdo-nav.sh
# (xdotool + Screenshots).
#
# Nutzung:
#   RB_CLIENT_EXE=riftbreaker_win_release.exe run-client.sh
#   run-client.sh --screenshot /tmp/shot.png    # nach Start Screenshot ziehen
#
# Umgebung:
#   RB_CLIENT_EXE    Name der Client-Exe im Mount (Default: riftbreaker_win_release.exe)
#   RB_CLIENT_ARGS   Zusätzliche Start-Argumente (z. B. --server rb-winetest)
#   GAME_DIR         Verzeichnis der Client-Dateien (Default: /srv/rbclient/game)
#   DISPLAY          X-Display (Default: :99)
#   WINEPREFIX       Wine-Prefix (Default: /root/.wine)

set -euo pipefail

DISPLAY="${DISPLAY:-:99}"
export DISPLAY
WINEPREFIX="${WINEPREFIX:-/root/.wine}"
export WINEPREFIX

RB_CLIENT_EXE="${RB_CLIENT_EXE:-riftbreaker_win_release.exe}"
RB_CLIENT_ARGS="${RB_CLIENT_ARGS:-}"
GAME_DIR="${GAME_DIR:-/srv/rbclient/game}"

# 1) Xvfb starten, falls auf DISPLAY noch kein X-Server lauscht.
XVFB_PID=""
if ! xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
    echo "[run-client] starte Xvfb auf ${DISPLAY}"
    Xvfb "$DISPLAY" -screen 0 1920x1080x24 -nolisten tcp &
    XVFB_PID=$!
    # Warten, bis der X-Server erreichbar ist (max. ~10 s).
    for _ in $(seq 1 50); do
        xdpyinfo -display "$DISPLAY" >/dev/null 2>&1 && break
        sleep 0.2
    done
    xdpyinfo -display "$DISPLAY" >/dev/null 2>&1 || {
        echo "[run-client] FEHLER: Xvfb nicht erreichbar" >&2
        exit 1
    }
fi

cleanup() {
    if [[ -n "$XVFB_PID" ]]; then
        kill "$XVFB_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

cd "$GAME_DIR"

if [[ ! -f "$RB_CLIENT_EXE" ]]; then
    echo "[run-client] FEHLER: '${RB_CLIENT_EXE}' nicht in ${GAME_DIR} gefunden" >&2
    echo "[run-client] Dateien zuerst syncen: tools/headless-client/sync-client.sh" >&2
    exit 1
fi

echo "[run-client] starte Client: wine ${RB_CLIENT_EXE} ${RB_CLIENT_ARGS}"
# RB_CLIENT_ARGS ist bewusst word-splitting (einzelne Argumente).
# shellcheck disable=SC2086
wine "$RB_CLIENT_EXE" $RB_CLIENT_ARGS &
CLIENT_PID=$!

# Optionaler Sofort-Screenshot nach dem Start (Hilfe für erste Verbindungstests).
if [[ "${1:-}" == "--screenshot" ]]; then
    sleep "${SCREENSHOT_DELAY:-15}"
    exec /usr/local/bin/xdo-nav.sh shot "${2:-/tmp/rbclient.png}"
fi

# Client im Vordergrund halten; Exit-Code des Clients weiterreichen.
wait "$CLIENT_PID"
