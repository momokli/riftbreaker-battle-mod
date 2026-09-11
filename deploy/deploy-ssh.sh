#!/bin/sh
# CD per SSH (ersetzt den HTTP-Hook, 2026-09-11): forced command für den
# deploy-User. Installation + authorized_keys: deploy/README.md → „CD: SSH-Deploy".
# $SSH_ORIGINAL_COMMAND = "<sha> <ref>" — vom Workflow übergeben.
set -eu
orig="${SSH_ORIGINAL_COMMAND:-}"
sha="${orig%% *}"
case "$sha" in
  [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]*) ;;
  *) echo "invalid sha: ${sha}" >&2; exit 1 ;;
esac
cd /opt/rbbattle-deploy/repo
git fetch --prune --quiet origin
git checkout --force "$sha" >/dev/null
exec sudo -n /usr/local/bin/rbbattle-deploy
