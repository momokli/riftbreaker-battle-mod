#!/usr/bin/env bash
set -euo pipefail

STEAM_USER="${STEAM_USER:-steamuser}"

for dir in /data/.wine /data/saves /opt/riftbreaker /home/steamuser; do
  if [[ -d "${dir}" ]]; then
    chown -R "${STEAM_USER}:${STEAM_USER}" "${dir}" 2>/dev/null || true
  fi
done

exec gosu "${STEAM_USER}" /scripts/entrypoint.sh "$@"
