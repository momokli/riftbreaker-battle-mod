#!/usr/bin/env bash
# RIFT BATTLE — lokalen Dev-Server anhalten, ohne neu zu bauen (Issue #566).
# Fuer den Erstlauf/nach Code-Aenderungen: scripts/local-dev.sh
set -euo pipefail

RBBATTLE_LOCAL_ROOT="${RBBATTLE_LOCAL_ROOT:-/srv/rift-local}"
COMPOSE_FILE="$RBBATTLE_LOCAL_ROOT/compose/riftbreaker/docker-compose.yml"

if [ ! -f "$COMPOSE_FILE" ]; then
    echo "[local-dev-stop] $COMPOSE_FILE nicht gefunden — noch nie mit scripts/local-dev.sh gestartet?" >&2
    exit 1
fi

docker compose -p riftbreaker -f "$COMPOSE_FILE" stop
