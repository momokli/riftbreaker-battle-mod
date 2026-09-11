#!/usr/bin/env bash
# One-time Wine prefix setup (VC++ runtime, D3D compiler) for DedicatedServer.exe.
set -euo pipefail

WINE="${WINE:-/usr/local/bin/wine64}"
WINEPREFIX="${WINEPREFIX:-/data/.wine}"
MARKER="${WINEPREFIX}/.riftbreaker-runtime-done"

if [[ -f "${MARKER}" ]]; then
  exit 0
fi

echo "[wine-init] Preparing Wine prefix at ${WINEPREFIX}..."
mkdir -p "${WINEPREFIX}"
if [[ ! -f "${WINEPREFIX}/system.reg" ]]; then
  xvfb-run -a "${WINE}" wineboot --init 2>/dev/null || true
fi

echo "[wine-init] Installing vcrun2022 and d3dcompiler_47 (may take a few minutes)..."
export WINEDEBUG="${WINEDEBUG:--all}"
export WINE="${WINE}"
export WINEPREFIX="${WINEPREFIX}"
if xvfb-run -a winetricks -q vcrun2022 d3dcompiler_47; then
  touch "${MARKER}"
  echo "[wine-init] Done."
else
  echo "[wine-init] WARNING: winetricks failed; server may not start. Reset wine-data volume and retry." >&2
fi
