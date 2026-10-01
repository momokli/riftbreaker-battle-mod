#!/bin/sh
# CD per SSH (ersetzt den HTTP-Hook, 2026-09-11): forced command fuer den
# deploy-User. Installation + authorized_keys: deploy/README.md → „CD: SSH-Deploy".
#
# $SSH_ORIGINAL_COMMAND = "<sha> <ref>" — vom Workflow uebergeben.
#
# PROD-ONLY (Issue #1034): es gibt nur noch EINEN Deploy-Pfad. Der <ref> MUSS
# ein Tag `refs/tags/v*` sein -> env=prod (deploy-prod.yml). Alles andere
# (Branches main/dev/staging, refs/pull/*, leer) ist ein Fehler (fail loud) —
# ein Merge auf main rollt NICHT mehr automatisch aus, staging ist entfallen.
#
# Issue #483 (Ziel A / Live-Befund #2): Der root-Wrapper arbeitet in
# /opt/rbbattle-deploy/repo-<env>/ mit eigenem Ref/SHA. Seit #1034 ist env immer
# `prod`; die Marker bleiben env-spezifisch benannt (.deploy-prod.sha/-ref plus
# Zeiger .deploy-env), damit der Bestand kompatibel bleibt.
#
# Rollout/Rueckwaerts-Kompatibilitaet: der Wrapper liest die alten, env-losen
# Marker nur noch als Fallback (Einmal-Migration, siehe deploy/README.md
# → „CD: SSH-Deploy" / Migrations-Checkliste).
#
# Der git-Checkout laeuft NICHT mehr hier (als deploy), sondern im root-Wrapper
# (als root), damit root-owned Reste von Agent-/RE-Arbeit auf planet den
# "git checkout --force" nicht blockieren ("unable to unlink … Permission
# denied"). Hier wird nur validiert, Marker geschrieben und der Wrapper dispatcht.
#
# Uebersteuerbare Pfade (Test/Hermetik; Default = Produktion):
#   RBBATTLE_DEPLOY_ROOT       -> /opt/rbbattle-deploy
#   RBBATTLE_DEPLOY_WRAPPER    -> /usr/local/bin/rbbattle-deploy
#   RBBATTLE_DEPLOY_NO_SUDO=1  -> Wrapper direkt (statt `sudo -n`) — nur Test.
set -eu

deploy_root="${RBBATTLE_DEPLOY_ROOT:-/opt/rbbattle-deploy}"
wrapper="${RBBATTLE_DEPLOY_WRAPPER:-/usr/local/bin/rbbattle-deploy}"

orig="${SSH_ORIGINAL_COMMAND:-}"
sha="${orig%% *}"
ref="${orig#* }"

case "$sha" in
  [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]*) ;;
  *) echo "invalid sha: ${sha}" >&2; exit 1 ;;
esac

case "${ref:-}" in
  refs/tags/v*) env=prod ;;
  *) echo "invalid ref: ${ref} (nur refs/tags/v* erlaubt, Issue #1034)" >&2; exit 1 ;;
esac

mkdir -p "$deploy_root"
# Env-Marker (seit #1034 immer prod; bleibt env-spezifisch benannt).
printf '%s' "$sha" > "$deploy_root/.deploy-${env}.sha"
printf '%s' "${ref:-}" > "$deploy_root/.deploy-${env}.ref"
printf '%s' "$env" > "$deploy_root/.deploy-env"

if [ -n "${RBBATTLE_DEPLOY_NO_SUDO:-}" ]; then
  exec "$wrapper"
fi
exec sudo -n "$wrapper"
