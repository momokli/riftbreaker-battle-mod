#!/bin/sh
# ============================================================================
# up.sh — dünner Wrapper für den lokalen Compose-Stack (Issue #1093/#1105).
#
#   deploy/compose/up.sh                    # = docker compose up --build -d
#   deploy/compose/up.sh --force-recreate   # zusätzliche Args werden durchgereicht
#
# Setzt RBB_REF aus dem Checkout (Git-SHA) und ruft
#   docker compose --env-file .env up --build -d [args]
# aus dem Repo-Root auf. Der Git-SHA wandert in den Image-Tag
# (rb-dedicated:<sha>) und in RBB_REF (Container-Labels/Logs) — so zeigt jeder
# Container den tatsächlich laufenden Stand.
#
# `docker compose logs -f` (ohne -p/-f) funktioniert direkt aus dem Repo-Root:
# `name: rbbattle` in compose.yaml legt den Projektnamen fest, `.env` liefert die
# Variablen. Der Wrapper ist nur wegen RBB_REF nötig.
# ============================================================================
set -eu

here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../.." && pwd)"
cd "$root"

if [ ! -f .env ]; then
    echo "up.sh: .env fehlt — zuerst 'cp .env.example .env' und anpassen." >&2
    exit 1
fi

# RBB_REF: ein explizit gesetzter Wert gewinnt; sonst der Git-SHA des Checkouts;
# sonst "dev". (Exportierte Vars überschreiben den .env-Wert für die Compose-
# Substitution.)
RBB_REF="${RBB_REF:-$(git -C "$root" rev-parse --short HEAD 2>/dev/null || true)}"
[ -n "$RBB_REF" ] || RBB_REF=dev
export RBB_REF

echo "up.sh: RBB_REF=$RBB_REF -> docker compose --env-file .env up --build -d $*"
exec docker compose --env-file .env up --build -d "$@"
