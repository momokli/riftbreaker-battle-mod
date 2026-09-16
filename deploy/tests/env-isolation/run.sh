#!/usr/bin/env bash
# Hermetischer Selbsttest des Env-Isolations-Gates (Issue #483, US2 + US3).
#
# Prüft die ECHTE Task-Datei deploy/tasks/env-assert.yml gegen ein Fixture-Repo
# und mit den echten Var-Dateien:
#   Positiv: vollstaendig klassifizierte Vars + distinct per-env-Pfade
#            (dev/prod/test) -> Lauf läuft durch (rc=0).
#   Negativ: ein per_env-Key aus der prod-Kopie entfernt -> Lauf bricht ab
#            (auch unter --check) und nennt den Marker ENV-ISOLATION-GATE.
#
# Kein Host, kein SSH, kein Vault, keine Prod-Aktion. Läuft in
# deploy-check-local.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../../.." && pwd)"
play="$here/main.yml"

fixture="$(mktemp -d)"
trap 'rm -rf "$fixture"' EXIT
mkdir -p "$fixture/deploy/inventory/host_vars/planet" "$fixture/tools/deploy-gate"
cp "$repo/tools/deploy-gate/check_env_isolation.py" "$fixture/tools/deploy-gate/"
cp "$repo/deploy/env-schema.yml" "$fixture/deploy/"
cp "$repo/deploy/prod-vars.yml" "$repo/deploy/test-vars.yml" "$repo/deploy/staging-vars.yml" "$fixture/deploy/"
cp "$repo/deploy/inventory/host_vars/planet/vars.yml" "$fixture/deploy/inventory/host_vars/planet/"

echo "== Positiv: Fixture vollstaendig + Pfade distinct -> muss durchlaufen =="
ansible-playbook "$play" -e test_root="$fixture"

echo "== Negativ: per_env-Key aus prod-Kopie entfernt -> muss abbrechen =="
grep -v '^riftbreaker_game_dir:' "$fixture/deploy/prod-vars.yml" > "$fixture/deploy/prod-vars.tmp"
mv "$fixture/deploy/prod-vars.tmp" "$fixture/deploy/prod-vars.yml"
set +e
out="$(ansible-playbook "$play" --check -e test_root="$fixture" 2>&1)"
rc=$?
set -e
printf '%s\n' "$out"
if [ "$rc" -eq 0 ]; then
  echo "::error::Env-Isolations-Gate hat bei fehlendem per_env-Key NICHT abgebrochen (rc=0)."
  exit 1
fi
if ! grep -q "ENV-ISOLATION-GATE" <<<"$out"; then
  echo "::error::Marker ENV-ISOLATION-GATE fehlt im Abbruch."
  exit 1
fi

echo "OK: Gate bricht bei nicht isolierter per-env-Variable ab (Marker gesetzt)."
