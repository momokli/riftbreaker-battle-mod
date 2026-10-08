#!/usr/bin/env bash
# Entry point for the containerized server-control agent (Issue #1093).
#
# Mirrors the SERVER_CONTROL_* environment of the former systemd unit
# `server-control` with container
# paths as defaults. The bearer token is NOT defaulted: without a non-empty
# SERVER_CONTROL_TOKEN the agent refuses to start (fail-closed, exit 2) — this is
# the same hardening as the role (#424/#298).
set -euo pipefail

# --- runtime ----------------------------------------------------------------
export SERVER_CONTROL_DOCKER="${SERVER_CONTROL_DOCKER:-/usr/local/bin/docker}"
export SERVER_CONTROL_CONTAINER="${SERVER_CONTROL_CONTAINER:-rbb-dedicated}"

# --- network: bind inside the container; publish on host loopback only ------
export SERVER_CONTROL_BIND="${SERVER_CONTROL_BIND:-0.0.0.0}"
export SERVER_CONTROL_PORT="${SERVER_CONTROL_PORT:-8092}"

# --- behavior (role defaults) -----------------------------------------------
export SERVER_CONTROL_TIMEOUT="${SERVER_CONTROL_TIMEOUT:-60}"
export SERVER_CONTROL_LOG_LEVEL="${SERVER_CONTROL_LOG_LEVEL:-INFO}"
# Deploy identity (Issue #483/US4) surfaced in GET /server/status.
export SERVER_CONTROL_ENV="${SERVER_CONTROL_ENV:-${RBB_ENV:-dev}}"
export SERVER_CONTROL_REF="${SERVER_CONTROL_REF:-${RBB_REF:-unknown}}"

# --- config.cfg (POST /server/config) ---------------------------------------
# Target file lives in the config volume (rb-config -> /config).
export SERVER_CONTROL_CONFIG_PATH="${SERVER_CONTROL_CONFIG_PATH:-/config/config.cfg}"
export SERVER_CONTROL_CONFIG_TEMPLATE="${SERVER_CONTROL_CONFIG_TEMPLATE:-/etc/rbmods/server-control/config.cfg.j2}"
if [ -z "${SERVER_CONTROL_CONFIG_VARS:-}" ] && [ -f /etc/rbmods/server-control/config-vars.json ]; then
  export SERVER_CONTROL_CONFIG_VARS=/etc/rbmods/server-control/config-vars.json
fi

# Optional: provision config-vars.json from the RBB_SERVER_* variables (the same
# values config-init uses), so POST /server/config works without an Ansible
# render. Only when no file was mounted. Keys match config-vars.json.j2.
if [ -z "${SERVER_CONTROL_CONFIG_VARS:-}" ] && [ -n "${RBB_SERVER_NAME:-}" ]; then
  mkdir -p /run/rbb-server-control
  python3 - <<'PY'
import json
import os

env = os.environ
mapping = {
    "riftbreaker_server_name": "RBB_SERVER_NAME",
    "riftbreaker_server_password": "RBB_SERVER_PASSWORD",
    "riftbreaker_server_rcon_password": "RBB_SERVER_RCON_PASSWORD",
    "riftbreaker_server_max_players": "RBB_SERVER_MAX_PLAYERS",
    "riftbreaker_server_broadcast_enabled": "RBB_SERVER_BROADCAST_ENABLED",
    "riftbreaker_server_pause_game_when_empty": "RBB_SERVER_PAUSE_GAME_WHEN_EMPTY",
    "riftbreaker_server_campaign": "RBB_SERVER_CAMPAIGN",
    "riftbreaker_server_mission": "RBB_SERVER_MISSION",
    "riftbreaker_server_difficulty": "RBB_SERVER_DIFFICULTY",
}
data = {key: env.get(var, "") for key, var in mapping.items()}
with open("/run/rbb-server-control/config-vars.json", "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2)
    fh.write("\n")
PY
  export SERVER_CONTROL_CONFIG_VARS=/run/rbb-server-control/config-vars.json
fi

# `--check` only validates the configuration and exits (rc 2 without a token).
exec python3 -u /app/server_control.py "$@"
