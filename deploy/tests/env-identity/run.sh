#!/usr/bin/env bash
# Hermetischer Selbsttest der Deploy-Identitaet (Issue #483, US1).
#
# Prüft die ECHTE Task-Datei deploy/tasks/deploy-identity.yml für alle drei
# Envs (dev/prod/test): die Identitaet ist "<env> · <ref>", dev/test tragen den
# Checkout-SHA, prod traegt zusaetzlich den Git-Tag.
#
# Kein Host, kein SSH, kein Vault, keine Prod-Aktion. Läuft in
# deploy-check-local.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
play="$here/main.yml"

# Wegwerf-Checkout mit Tag: `git describe --tags --always` liefert so den Tag
# (prod-Fall) deterministisch, unabhaengig von der Umgebung.
checkout="$(mktemp -d)"
trap 'rm -rf "$checkout"' EXIT
git -c init.defaultBranch=main -C "$checkout" init -q
git -C "$checkout" -c user.email=test@example.invalid -c user.name=Test \
  commit -q --allow-empty -m "init"
git -C "$checkout" tag v9.9.9

for env in dev test prod; do
  echo "== Env: $env =="
  ansible-playbook "$play" -e "test_env=$env" -e "test_checkout=$checkout"
done

echo "OK: Deploy-Identitaet fuer dev/prod/test korrekt (<env> · <ref>)."
