#!/usr/bin/env bash
# RIFT BATTLE — bereits provisionierten lokalen Dev-Server wieder starten,
# ohne neu zu bauen (Issue #566). Fuer den Erstlauf/nach Code-Aenderungen:
# scripts/local-dev.sh
set -euo pipefail

RBBATTLE_LOCAL_ROOT="${RBBATTLE_LOCAL_ROOT:-/srv/rift-local}"
COMPOSE_FILE="$RBBATTLE_LOCAL_ROOT/compose/riftbreaker/docker-compose.yml"

if [ ! -f "$COMPOSE_FILE" ]; then
    echo "[local-dev-start] $COMPOSE_FILE nicht gefunden — erst scripts/local-dev.sh laufen lassen." >&2
    exit 1
fi

# `up -d` statt `start` (Issue #1092): `start` startet nur bestehende
# Container — neu hinzugekommene Services (Sidecars) wuerden nie angelegt.
docker compose -p riftbreaker -f "$COMPOSE_FILE" up -d --remove-orphans
echo "[local-dev-start] Bridge:  http://127.0.0.1:9001/"
echo "[local-dev-start] Status:  curl -H 'Authorization: Bearer localdev' http://127.0.0.1:8091/status"
