#!/usr/bin/env bash
#
# run-client.sh — startet den Riftbreaker-Client headless (Xvfb + Wine).
#
# Startet bei Bedarf einen virtuellen X-Server (Xvfb) auf DISPLAY, initialisiert
# das Wine-Prefix und startet die Client-Exe. Rendering läuft über Mesa-llvmpipe
# (Software). Eingaben/Auswertung über tools/headless-client/xdo-nav.sh
# (xdotool + Screenshots).
#
# Modi:
#   run-client.sh                     Client booten, auf Hauptmenü warten
#                                     (Beweis-Screenshot via wait-menu.sh),
#                                     dann weiterlaufen.
#   run-client.sh --no-verify         Client booten und einfach weiterlaufen
#                                     (kein Menü-Check).
#   run-client.sh --screenshot <file> Boot + Menü-Check, Screenshot nach <file>,
#                                     danach beenden (One-Shot-Beweis).
#
# Umgebung:
#   RB_CLIENT_EXE    Name der Client-Exe im Mount (Default: riftbreaker_win_release.exe)
#   RB_CLIENT_ARGS   Zusätzliche Start-Argumente (z. B. --server rb-winetest)
#   GAME_DIR         Verzeichnis der Client-Dateien (Default: /srv/rbclient/game)
#   SCREENSHOT_OUT   Zielpfad des Beweis-Screenshots (Default: /srv/rbclient/screenshots/menu.png)
#   DISPLAY          X-Display (Default: :99)
#   WINEPREFIX       Wine-Prefix (Default: /root/.wine)
#   WAIT_TIMEOUT     Menü-Timeout in s (Default: 120)

set -euo pipefail

DISPLAY="${DISPLAY:-:99}"
export DISPLAY
WINEPREFIX="${WINEPREFIX:-/root/.wine}"
export WINEPREFIX

RB_CLIENT_EXE="${RB_CLIENT_EXE:-riftbreaker_win_release.exe}"
RB_CLIENT_ARGS="${RB_CLIENT_ARGS:-}"
GAME_DIR="${GAME_DIR:-/srv/rbclient/game}"
SCREENSHOT_OUT="${SCREENSHOT_OUT:-/srv/rbclient/screenshots/menu.png}"

MODE="verify"
SHOT_OUT="$SCREENSHOT_OUT"
case "${1:-}" in
    --no-verify) MODE="noverify" ;;
    --screenshot) MODE="screenshot"; SHOT_OUT="${2:-$SCREENSHOT_OUT}" ;;
    "") MODE="verify" ;;
    *) echo "[run-client] unbekanntes Argument: $1" >&2; exit 64 ;;
esac

# 1) Xvfb starten, falls auf DISPLAY noch kein X-Server lauscht.
XVFB_PID=""
if ! xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
    echo "[run-client] starte Xvfb auf ${DISPLAY}"
    Xvfb "$DISPLAY" -screen 0.20.0x1080x24 -nolisten tcp &
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
    echo "[run-client] Dateien zuerst syncen: tools/headless-client/sync-client-data.sh" >&2
    exit 1
fi

# 2) Wine-Prefix initialisieren (idempotent; beim ersten Start etwas langsamer).
echo "[run-client] initialisiere Wine-Prefix ${WINEPREFIX} …"
wineboot --init >/dev/null 2>&1 || true

# 3) Client starten.
echo "[run-client] starte Client: wine ${RB_CLIENT_EXE} ${RB_CLIENT_ARGS}"
# RB_CLIENT_ARGS ist bewusst word-splitting (einzelne Argumente).
# shellcheck disable=SC2086
wine "$RB_CLIENT_EXE" $RB_CLIENT_ARGS &
CLIENT_PID=$!

# 4) Menü-Verifikation (Heuristik: gerenderter Frame + Beweis-Screenshot).
if [[ "$MODE" == "verify" || "$MODE" == "screenshot" ]]; then
    if ! command -v wait-menu.sh >/dev/null 2>&1; then
        echo "[run-client] WARNUNG: wait-menu.sh fehlt — überspringe Menü-Check" >&2
    else
        if [[ "$MODE" == "screenshot" ]]; then
            wait-menu.sh "$SHOT_OUT"
            echo "[run-client] Beweis-Screenshot erstellt; beende."
            exit 0
        fi
        # verify: Menü abwarten, dann Client weiterlaufen lassen.
        if ! wait-menu.sh "$SCREENSHOT_OUT"; then
            echo "[run-client] WARNUNG: Hauptmenü-Check fehlgeschlagen (Details oben) — Client läuft weiter" >&2
        fi
    fi
fi

# Client im Vordergrund halten; Exit-Code des Clients weiterreichen.
wait "$CLIENT_PID"
