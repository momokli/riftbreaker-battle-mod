#!/usr/bin/env bash
set -euo pipefail

STEAMCMD="/opt/steamcmd/steamcmd.sh"
INSTALL_DIR="${INSTALL_DIR:-/opt/riftbreaker}"
STEAMAPPID="${STEAMAPPID:-4114030}"
MAX_TRIES=8

echo "[install-update] Updating app ${STEAMAPPID} in ${INSTALL_DIR}..."

try=0
while (( try < MAX_TRIES )); do
  if "${STEAMCMD}" \
    +@sSteamCmdForcePlatformType windows \
    +force_install_dir "${INSTALL_DIR}" \
    +login anonymous \
    +app_update "${STEAMAPPID}" \
    +quit; then
    echo "[install-update] Done."
    exit 0
  fi
  try=$((try + 1))
  echo "[install-update] SteamCMD requested restart or failed (attempt ${try}/${MAX_TRIES}), retrying..."
  sleep 2
done

echo "[install-update] ERROR: SteamCMD did not complete after ${MAX_TRIES} attempts" >&2
exit 1
