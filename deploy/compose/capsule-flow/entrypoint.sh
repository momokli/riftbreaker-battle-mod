#!/usr/bin/env bash
# Entry point for the containerized capsule-flow service (Issue #1093).
#
# Mirrors the CAPSULE_* environment of the systemd unit
# (deploy/roles/capsule-flow/templates/rbmods-capsule.env.j2) with container
# paths as defaults. The bearer token is NOT defaulted: without a non-empty
# CAPSULE_TOKEN the service refuses to start (fail-closed, exit 2) — the same
# precondition the Ansible role asserts (#931, hardening like #424).
set -euo pipefail

export CAPSULE_ENV="${CAPSULE_ENV:-${RBB_ENV:-dev}}"

# Bind inside the container; publish on host loopback only (127.0.0.1:9211).
export CAPSULE_BIND="${CAPSULE_BIND:-0.0.0.0}"
export CAPSULE_PORT="${CAPSULE_PORT:-9211}"

# Neighbours (compose service DNS). Overridable via env.
export CAPSULE_PARKED_URL="${CAPSULE_PARKED_URL:-http://parked-pool:9201}"
export CAPSULE_CYCLE_URL="${CAPSULE_CYCLE_URL:-http://attack-cycle:9102}"
export CAPSULE_TIMEOUT="${CAPSULE_TIMEOUT:-5}"
export CAPSULE_LOG_LEVEL="${CAPSULE_LOG_LEVEL:-INFO}"

if [ -z "${CAPSULE_TOKEN:-}" ]; then
  echo "capsule-flow: CAPSULE_TOKEN ist leer — der Dienst startet ohne Bearer-Token NICHT (fail-closed, #931/#424)." >&2
  exit 2
fi

if [ ! -f /app/capsule_service.py ]; then
  echo "capsule-flow: /app/capsule_service.py fehlt — deploy/capsule/ nach /app mounten (ro)" >&2
  exit 2
fi

# `--check` only validates the configuration and exits (rc 2 on bad numbers).
exec python3 -u /app/capsule_service.py "$@"
