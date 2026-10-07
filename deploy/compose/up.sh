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

# Host-Root (Pfad-Modell, #1112): geteilte Dirs unter EINEM absoluten Pfad, den
# der Provisioner 1:1 sieht (Sibling-Container via `docker run`).
RBB_HOST_ROOT="${RBB_HOST_ROOT:-/srv/rbbattle}"
case "$RBB_HOST_ROOT" in
    /*) : ;;
    *) echo "up.sh: RBB_HOST_ROOT muss absolut sein (ist: $RBB_HOST_ROOT)." >&2; exit 1 ;;
 esac
export RBB_HOST_ROOT

# Repo-Root (für die Sidecar-/Persona-Pfade des Provisioners), absolut.
RBB_REPO_ROOT="${RBB_REPO_ROOT:-$root}"
export RBB_REPO_ROOT

mkdir -p "$RBB_HOST_ROOT/game" "$RBB_HOST_ROOT/config" "$RBB_HOST_ROOT/rbtools" \
    "$RBB_HOST_ROOT/gns" "$RBB_HOST_ROOT/sessions" "$RBB_HOST_ROOT/backups" \
    "$RBB_HOST_ROOT/crashes" "$RBB_HOST_ROOT/tournament" \
    "$RBB_HOST_ROOT/downloads" "$RBB_HOST_ROOT/queue"

echo "up.sh: RBB_REF=$RBB_REF RBB_HOST_ROOT=$RBB_HOST_ROOT -> docker compose --env-file .env up --build -d $*"
exec docker compose --env-file .env up --build -d "$@"
