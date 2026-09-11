#!/usr/bin/env bash
#
# wine-init.sh — initialisiert das Riftbreaker-Wine-Prefix (idempotent).
#
# Issue #239: Der DedicatedServer.exe braucht drei Dinge im Prefix:
#   1) Windows-Version >= win10 (Registry HKCU\Software\Wine Version=win10) —
#      sonst Dialog „Windows 8.1 or greater is required".
#   2) VC++ Runtime (vcrun2022) — sonst „failed to load game library".
#   3) d3dcompiler_47 — D3D-Compiler für die Engine.
#   (Der Builtin-Stub für api-ms-win-core-psm-appnotify-l1-1-0.dll kommt aus
#    winehq-stable, siehe Dockerfile.)
#
# Marker-guarded: existiert $WINEPREFIX/.riftbreaker-runtime-done, ist der Lauf
# ein No-op. Wird beim Image-Build UND beim Containerstart aufgerufen, damit
# ein frisches oder altes Named Volume sich selbst heilt.
#
# `xvfb-run -a` (nicht eigenes Xvfb/:99): bekommt einen freien X-Display und
# räumt ihn selbst auf — kein /tmp/.X99-lock, das einen Container-Neustart in
# eine Boot-Schleife schickt (Issue #239).
set -euo pipefail

WINE="${WINE:-/usr/local/bin/wine64}"
WINEPREFIX="${WINEPREFIX:-/root/.wine}"
MARKER="${WINEPREFIX}/.riftbreaker-runtime-done"

if [[ -f "${MARKER}" ]]; then
  echo "[wine-init] Prefix bereits initialisiert (${MARKER}) — nichts zu tun."
  exit 0
fi

echo "[wine-init] Initialisiere Wine-Prefix ${WINEPREFIX} …"
mkdir -p "${WINEPREFIX}"

# wineboot nur, wenn das Prefix noch nicht existiert (idempotent).
if [[ ! -f "${WINEPREFIX}/system.reg" ]]; then
  xvfb-run -a "${WINE}" wineboot --init 2>/dev/null || true
fi

# Windows-Version auf win10 setzen (Layer 1; sonst verweigert der
# DedicatedServer.exe den Start mit „Windows 8.1 or greater is required").
xvfb-run -a "${WINE}" reg add 'HKCU\Software\Wine' /v Version /d win10 /f >/dev/null 2>&1 || true

export WINEDEBUG="${WINEDEBUG:--all}"
export WINE
export WINEPREFIX

echo "[wine-init] Installiere vcrun2022 + d3dcompiler_47 (kann einige Minuten dauern) …"
if xvfb-run -a winetricks -q vcrun2022 d3dcompiler_47; then
  touch "${MARKER}"
  echo "[wine-init] Fertig."
else
  echo "[wine-init] WARNUNG: winetricks fehlgeschlagen — DedicatedServer startet evtl. nicht." >&2
  echo "[wine-init] Wine-Prefix-Volume zurücksetzen und Container neu starten." >&2
fi
