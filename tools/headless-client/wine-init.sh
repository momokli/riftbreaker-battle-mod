#!/usr/bin/env bash
#
# wine-init.sh — initialisiert das Riftbreaker-Wine-Prefix (idempotent).
#
# Issue #239: Der DedicatedServer.exe braucht drei Dinge im Prefix:
#   1) Windows-Version >= win10 (HKCU\Software\Wine Version=win10) — sonst
#      Dialog „Windows 8.1 or greater is required".
#   2) VC++ Runtime (vcrun2022) — sonst „failed to load game library".
#   3) d3dcompiler_47 — D3D-Compiler für die Engine.
#   (Der Builtin-Stub für api-ms-win-core-psm-appnotify-l1-1-0.dll kommt aus
#    winehq-stable, siehe Dockerfile.)
#
# Marker-guarded: existiert $WINEPREFIX/.riftbreaker-runtime-done, ist der Lauf
# ein No-op. Wird beim Image-Build UND beim Containerstart aufgerufen, damit
# ein frisches oder altes Named Volume sich selbst heilt.
#
# WICHTIG (1): Ohne `WINEDLLOVERRIDES=mscoree,mshtml=` versucht Wine beim
#    `wineboot` Mono/Gecko nachzuinstallieren und blockiert im headless Build
#    (Dialog) — der Build hängt dann in `wineboot --init` (Issue #239). Der
#    Wert steht daher auch im Image-ENV (Dockerfile) und wird hier defensiv
#    gesetzt.
# WICHTIG (2): ALLE Wine-Schritte laufen in EINER `xvfb-run -a`-Session. Ein
#    eigener Xvfb pro Schritt würde Xvfb zwischen den Schritten sterben lassen,
#    während `wineserver` weiterläuft — ein einziger, durchgehender X-Server +
#    `wineserver` ist das robuste Muster. `xvfb-run -a` bekommt einen freien
#    Display und räumt ihn selbst auf (kein /tmp/.X99-lock, keine Boot-Schleife).
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

export WINEDEBUG="${WINEDEBUG:--all}"
# Ohne dieses Override versucht Wine beim wineboot Mono/Gecko zu installieren
# und blockiert im headless Build (Dialog) — der Build hängt dann endlos in
# `wineboot --init` (Issue #239). Das Rig setzt es identisch im Image-ENV.
export WINEDLLOVERRIDES="${WINEDLLOVERRIDES:-mscoree,mshtml=}"
export WINE
export WINEPREFIX

# shellcheck disable=SC2016  # $WINEPREFIX/$WINE expandieren erst im Kind-Shell.
if xvfb-run -a bash -c '
  if [ ! -f "${WINEPREFIX}/system.reg" ]; then
    wineboot --init 2>/dev/null || true
  fi
  wine reg add "HKCU\\Software\\Wine" /v Version /d win10 /f >/dev/null 2>&1 || true
  winetricks -q vcrun2022 d3dcompiler_47
'; then
  touch "${MARKER}"
  echo "[wine-init] Fertig."
else
  echo "[wine-init] WARNUNG: winetricks fehlgeschlagen — DedicatedServer startet evtl. nicht." >&2
  echo "[wine-init] Wine-Prefix-Volume zurücksetzen und Container neu starten." >&2
fi
