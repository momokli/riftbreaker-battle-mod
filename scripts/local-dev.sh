#!/usr/bin/env bash
# RIFT BATTLE — lokales Solo-Dev-Setup hochfahren (Issue #566).
#
# Baut/aktualisiert Image + Game-Content (SteamCMD) + Server-I/O-Tools und
# startet Dedicated-Server + Bridge + Server-Control + Crash-Collector auf
# DEINEM Rechner — kein planet-Zugriff noetig. Idempotent: nach dem ersten
# vollen Lauf macht ein erneuter Aufruf nur noch das, was sich geaendert hat
# (deutlich schneller). Details/Voraussetzungen: deploy/LOCAL_DEV.md.
#
# Aufruf:
#   scripts/local-dev.sh              # alles (Content+Image+Server+...)
#   scripts/local-dev.sh --tags content   # nur Mod-Zip + Game-Content
#   scripts/local-dev.sh --tags server    # nur Image+Tools+Server+Server-Control
#   scripts/local-dev.sh --tags crash     # nur Crash-Collector
#   scripts/local-dev.sh --tags tournament  # zusaetzlich Tournament-Server
# Beliebige weitere ansible-playbook-Argumente werden durchgereicht (z. B.
# -e riftbreaker_local_root=/anderer/pfad).
#
# Zum Stoppen/Starten des bereits provisionierten Servers (kein Ansible noetig,
# schneller): scripts/local-dev-stop.sh / scripts/local-dev-start.sh
set -euo pipefail

cd "$(dirname "$0")/.."

VENV="${RBBATTLE_ANSIBLE_VENV:-$HOME/.venvs/rift-deploy}"

if [ ! -x "$VENV/bin/ansible-playbook" ]; then
    echo "[local-dev] ansible-core nicht gefunden, installiere einmalig in $VENV ..."
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install --disable-pip-version-check -q "ansible-core>=2.19"
fi

echo "[local-dev] Playbook laeuft mit sudo (Docker/\`/srv\`/\`/opt\`-Zugriff) — Passwort ggf. gleich abgefragt."

exec "$VENV/bin/ansible-playbook" \
    -i deploy/local-inventory.yml deploy/local-deploy.yml \
    -e @deploy/local-vars.yml \
    -K \
    "$@"
