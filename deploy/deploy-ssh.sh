#!/bin/sh
# CD per SSH (ersetzt den HTTP-Hook, 2026-09-11): forced command für den
# deploy-User. Installation + authorized_keys: deploy/README.md → „CD: SSH-Deploy".
# $SSH_ORIGINAL_COMMAND = "<sha> <ref>" — vom Workflow übergeben. Der <ref>
# bestimmt dev (refs/heads/main → site.yml) vs prod (refs/tags/v* →
# deploy-prod.yml): er wird über eine Marker-Datei an den root-Wrapper
# /usr/local/bin/rbbattle-deploy gereicht (sudo setzt die Umgebung zurück).
set -eu
orig="${SSH_ORIGINAL_COMMAND:-}"
sha="${orig%% *}"
ref="${orig#* }"
case "$sha" in
  [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]*) ;;
  *) echo "invalid sha: ${sha}" >&2; exit 1 ;;
esac
cd /opt/rbbattle-deploy/repo
git fetch --prune --quiet origin
git checkout --force "$sha" >/dev/null
printf '%s' "${ref:-}" > /opt/rbbattle-deploy/.deploy-ref
exec sudo -n /usr/local/bin/rbbattle-deploy
