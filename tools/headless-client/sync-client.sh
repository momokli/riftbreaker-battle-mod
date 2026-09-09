#!/usr/bin/env bash
#
# sync-client.sh — Riftbreaker-Client-Dateien: lan → planet
#
# Synchronisiert die lokale Riftbreaker-Installation von lan auf planet für
# die Headless-Client-Umgebung (/srv/rbclient/game).
#
# Hosts sind Tailscale-Aliasse aus ~/.ssh/config (lan, planet) — Mesh-first,
# kein Zugriff über Public-IPs.
#
# Nutzung:
#   ./sync-client.sh                  Voll-Sync (idempotent, rsync -a)
#   DRY_RUN=1 ./sync-client.sh        Probe-Lauf ohne Transfer
#
# Hinweis: Ersttransfer ~13 GB — bewusst separater Schritt, wird nicht von
# diesem Skript-Setup ausgelöst.

set -euo pipefail

SRC_HOST="lan"                                    # momo@lan (Homelab)
SRC_DIR="/home/momo/share/games/The Riftbreaker/" # Achtung: Leerzeichen → escaped

DST_HOST="planet"                                 # root@planet (Hetzner)
DST_DIR="/srv/rbclient/game/"

# Remote-Pfad mit Leerzeichen für die Remote-Shell escapen
RSYNC_OPTS=(-a --info=progress2)
if [[ "${DRY_RUN:-0}" == "1" ]]; then
    RSYNC_OPTS+=(--dry-run)
fi

SRC="${SRC_HOST}:${SRC_DIR// /\\ }"
DST="${DST_HOST}:${DST_DIR}"

echo "Sync: ${SRC} → ${DST}"
rsync "${RSYNC_OPTS[@]}" "${SRC}" "${DST}"
echo "Fertig: ${SRC} → ${DST}"
