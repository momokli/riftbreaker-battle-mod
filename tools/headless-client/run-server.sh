#!/usr/bin/env bash
#
# run-server.sh — startet den Riftbreaker-Dedicated-Server headless (Xvfb + Wine).
#
# Die Compose-Services :6321/:6322 nutzen dieses Entrypoint statt `wine` direkt:
# Wine braucht einen X-Server — auch im `headless_mode=1` beendet sich die
# Server-Exe sonst sofort mit RC 255. Das Skript startet bei Bedarf Xvfb auf
# DISPLAY, initialisiert das Wine-Prefix (idempotent) und reicht ALLE Argumente
# unverändert an `wine` weiter (keine Shell-Interpretation im Compose).
#
# Aufruf (Compose): entrypoint: ["run-server.sh"] + command: [<exe>, <args…>]
#
# Umgebung:
#   DISPLAY      X-Display (Default: :99)
#   WINEPREFIX   Wine-Prefix (Default: /root/.wine)
set -euo pipefail

DISPLAY="${DISPLAY:-:99}"
export DISPLAY

# Xvfb starten, falls auf DISPLAY noch kein X-Server lauscht.
if ! xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
    echo "[run-server] starte Xvfb auf ${DISPLAY}"
    Xvfb "$DISPLAY" -screen 0 1920x1080x24 -nolisten tcp &
    for _ in $(seq 1 50); do
        xdpyinfo -display "$DISPLAY" >/dev/null 2>&1 && break
        sleep 0.2
    done
    if ! xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
        echo "[run-server] FEHLER: Xvfb auf ${DISPLAY} nicht erreichbar" >&2
        exit 1
    fi
fi

# Wine-Prefix initialisieren (idempotent; beim ersten Start etwas langsamer).
wineboot --init >/dev/null 2>&1 || true

echo "[run-server] starte: wine $*"
exec wine "$@"
