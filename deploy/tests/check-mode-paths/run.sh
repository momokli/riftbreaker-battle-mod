#!/usr/bin/env bash
# Hermetischer Check-Mode-Selbsttest der Schreibkette (Issue #483, Befund
# deploy-check planet / PR #496).
#
# Prueft die ECHTE Rolle deploy/roles/riftbreaker-server im `--check` gegen ein
# NICHT existierendes `<env>`-Pfadschema (Host-Zustand VOR der Pfad-Migration):
#   * Lauf MUSS gruen sein (vor dem Fix: "Destination directory … does not
#     exist" beim Copy-Task),
#   * der Check-Mode-Hinweis muss geloggt sein (der Uebersprung ist sichtbar,
#     nicht still),
#   * die Live-Verzeichnisse duerfen im --check NICHT real angelegt werden.
#
# Negativ-Probe (red-before-green): dieselbe Rolle ohne den Check-Mode-Schutz
# in einem Fixture-Kopie — der Lauf MUSS dann mit genau diesem Fehler
# abbrechen. Sonst ist der Test wirkungslos.
#
# Laeuft in deploy-check-local (kein Host, kein Vault, kein Docker).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../../.." && pwd)"
play="$here/main.yml"
roles="$repo/deploy/roles"

fail() { echo "::error::$1"; exit 1; }

base="$(mktemp -d)"
trap 'rm -rf "$base"' EXIT

missing="$base/missing-root"      # bewusst NICHT angelegt (vor der Migration)
fixture="$base/fixture"
mkdir -p "$fixture"
printf 'dummy-zip' > "$fixture/rbbattle.zip"   # planet hat das Mod-Zip bereits

run_check() {  # <roles-path>
  ANSIBLE_ROLES_PATH="$1" ansible-playbook "$play" --check \
    -e "test_missing_root=$missing" -e "test_fixture_root=$fixture" 2>&1
}

echo "== Positiv: --check gegen noch nicht migrierte <env>-Pfade =="
if ! out="$(run_check "$roles")"; then
  tail -30 <<<"$out"
  fail "Rolle riftbreaker-server bricht im --check gegen nicht existierende <env>-Pfade ab (Befund PR #496)."
fi
grep -q "Schreibziele fehlen" <<<"$out" \
  || fail "Check-Mode-Hinweis der Schreibziele fehlt — Test waere wirkungslos."
if grep -q "Destination directory" <<<"$out"; then
  fail "Copy-Task lief trotz fehlendem Zielverzeichnis los."
fi
[ ! -e "$missing" ] \
  || fail "--check hat die Live-Pfade real angelegt ($missing) — Check-Mode-Verletzung."
echo "   OK (rc=0, Hinweis geloggt, KEIN Verzeichnis real angelegt)"

echo "== Negativ-Probe: ohne Check-Mode-Schutz MUSS es knallen =="
cp -r "$roles" "$base/roles-noguard"
sed -i '/when: (not ansible_check_mode) or (riftbreaker_write_dirs_ready | bool)/d' \
  "$base/roles-noguard/riftbreaker-server/tasks/main.yml"
if out="$(run_check "$base/roles-noguard")"; then
  fail "Negativ-Probe: Lauf ohne Check-Mode-Schutz ist gruen (Test erkennt den Befund nicht)."
fi
grep -q "Destination directory" <<<"$out" \
  || fail "Negativ-Probe brach ab, aber nicht mit 'Destination directory … does not exist'."
echo "   Negativ-Probe erkannt (#496-Fehler reproduziert)."

echo "OK: Schreibkette der Rolle riftbreaker-server ist check-mode-sicher (#483)."
