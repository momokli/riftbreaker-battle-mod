#!/usr/bin/env bash
# One-time Wine prefix setup (VC++ runtime, D3D compiler) for DedicatedServer.exe.
#
# Issue #241 — Root Cause: `wineboot --init` schreibt system.reg ASYNCHRON — der
# wineserver flusht die Registry erst, wenn er beendet wird. Ohne `wineserver -w`
# fehlte system.reg -> `could not load kernel32.dll`. Fix: nach wineboot auf den
# wineserver warten. wineboot selbst braucht KEIN X; winetricks schon (xvfb-run).
set -euo pipefail

WINE="${WINE:-/usr/bin/wine}"
WINEPREFIX="${WINEPREFIX:-/data/.wine}"
MARKER="${WINEPREFIX}/.riftbreaker-runtime-done"

if [[ -f "${MARKER}" ]]; then
  exit 0
fi

export WINEDEBUG="${WINEDEBUG:--all}"
export WINE="${WINE}"
export WINEPREFIX="${WINEPREFIX}"

echo "[wine-init] Preparing Wine prefix at ${WINEPREFIX}..."
mkdir -p "${WINEPREFIX}"
if [[ ! -f "${WINEPREFIX}/system.reg" ]]; then
  "${WINE}" wineboot --init 2>/dev/null || true
  # Auf den async Flush des wineservers warten (system.reg).
  wineserver -w 2>/dev/null || true
fi

echo "[wine-init] Installing vcrun2022 and d3dcompiler_47 (may take a few minutes)..."
# winetricks BRAUCHT einen Display (der vc_redist-Installer) -> xvfb-run.
# Exit-Code ist unzuverlässig (vc_redist.x86.exe /q bricht mit 130 ab, obwohl
# die DLLs per cabextract bereits extrahiert wurden) -> Artefakt-Check.
xvfb-run -a winetricks -q vcrun2022 d3dcompiler_47 || true

if [[ -f "${WINEPREFIX}/drive_c/windows/system32/vcruntime140.dll" \
   && -f "${WINEPREFIX}/drive_c/windows/system32/d3dcompiler_47.dll" ]]; then
  touch "${MARKER}"
  echo "[wine-init] Done."
else
  echo "[wine-init] WARNING: vcrun2022/d3dcompiler_47-DLLs fehlen; Server startet evtl. nicht. Wine-Volume zurücksetzen und erneut versuchen." >&2
fi
