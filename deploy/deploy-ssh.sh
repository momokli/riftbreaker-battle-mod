#!/bin/sh
# CD per SSH (ersetzt den HTTP-Hook, 2026-09-11): forced command für den
# deploy-User. Installation + authorized_keys: deploy/README.md → „CD: SSH-Deploy".
# $SSH_ORIGINAL_COMMAND = "<sha> <ref> [<instance>]" — vom Workflow übergeben.
#
# Multi-Instanz (Issue #290): das optionale dritte Feld wählt die Ziel-Instanz
# (`release` | `dev`). Es wird NICHT als Argument an den root-Wrapper übergeben
# (dessen sudoers-Snippet erlaubt nur den argumentlosen Aufruf), sondern in die
# deploy-lesbare Datei /opt/rbbattle-deploy/instance geschrieben; der Wrapper
# liest sie (Default `release`, siehe deploy/README.md → „Root-Weg"). Fehlt das
# Feld, bleibt es `release` — ein alter Aufruf verhält sich unverändert.
set -eu
orig="${SSH_ORIGINAL_COMMAND:-}"
sha="${orig%% *}"
rest="${orig#* }"
[ "$rest" = "$orig" ] && rest=""
instance="${rest#* }"
[ "$instance" = "$rest" ] && instance=""
instance="${instance:-release}"
case "$sha" in
  [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]*) ;;
  *) echo "invalid sha: ${sha}" >&2; exit 1 ;;
esac
case "$instance" in
  release|dev) ;;
  *) echo "invalid instance: ${instance}" >&2; exit 1 ;;
esac
cd /opt/rbbattle-deploy/repo
git fetch --prune --quiet origin
git checkout --force "$sha" >/dev/null
printf '%s\n' "$instance" > /opt/rbbattle-deploy/instance
exec sudo -n /usr/local/bin/rbbattle-deploy
