#!/bin/sh
# CD per SSH (ersetzt den HTTP-Hook, 2026-09-11): forced command für den
# deploy-User. Installation + authorized_keys: deploy/README.md → „CD: SSH-Deploy".
#
# $SSH_ORIGINAL_COMMAND = "<sha> <ref>" — vom Workflow übergeben. Der <ref>
# bestimmt die Env UND den Deploy-Checkout:
#   * refs/heads/* (main)  -> env=dev
#   * refs/tags/v*         -> env=prod
# Alles andere (z. B. refs/pull/*) ist ein Fehler (fail loud).
#
# Issue #483 (Ziel A / Live-Befund #2): dev und prod können PARALLEL laufen.
# Deshalb werden pro Env eigene Marker geschrieben —
#   .deploy-<env>.sha / .deploy-<env>.ref
# plus der Zeiger .deploy-env (welche Env gerade deployt wird). Der root-Wrapper
# arbeitet dann in /opt/rbbattle-deploy/repo-<env>/ mit eigenem Ref/SHA. Die
# früheren, env-losen Marker (.deploy-sha/-ref) im GETEILTEN repo überschrieben
# sich beim parallelen dev/prod-Lauf (SHA-Race).
#
# Rollout/Rückwärts-Kompatibilität: der Wrapper liest die alten, env-losen
# Marker nur noch als Fallback (Einmal-Migration, siehe deploy/README.md
# → „CD: SSH-Deploy" / Migrations-Checkliste).
#
# Der git-Checkout läuft NICHT mehr hier (als deploy), sondern im root-Wrapper
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
  refs/heads/*|"") env=dev ;;
  *) echo "invalid ref: ${ref} (erwartet refs/heads/* oder refs/tags/v*)" >&2; exit 1 ;;
esac

mkdir -p "$deploy_root"
# Env-spezifische Marker (je Env eigener Ref/SHA -> kein Cross-Env-Race).
printf '%s' "$sha" > "$deploy_root/.deploy-${env}.sha"
printf '%s' "${ref:-}" > "$deploy_root/.deploy-${env}.ref"
printf '%s' "$env" > "$deploy_root/.deploy-env"

if [ -n "${RBBATTLE_DEPLOY_NO_SUDO:-}" ]; then
  exec "$wrapper"
fi
exec sudo -n "$wrapper"
