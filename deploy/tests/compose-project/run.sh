#!/usr/bin/env bash
# Hermetischer Regressionstest Compose-Projekt/Volumes (Issue #483, Befund 2;
# prod-only #1034).
#
# Hintergrund: das Compose-Template deklariert die Volumes OHNE `name:`; Compose
# praefixt sie mit dem PROJEKTnamen (<projekt>_<volume>). Live auf planet:
#   prod-A -> riftbreaker-dedicated-prod_rb-wine-prod     (+ _rb-saves-prod)
#   prod-B -> riftbreaker-dedicated-prod-b_rb-wine-prod-b (+ _rb-saves-prod-b)
# Eine Umbenennung des Projekts auf `rift-<env>` erzeugte NEUE leere Volumes und
# verwaiste Wine-Prefix/Saves (Host-Pfad-Migration deckt Docker-Ressourcen nicht
# ab). Gewaehlte Variante: (a) historische Projektnamen beibehalten.
#
# Nachweis: aus dem ECHTEN Ansible-Render (prod/prod-b) wird `docker compose -p
# <projekt> config` gefahren; die Volumes MUESSEN die historischen Namen tragen.
# Negativ-Probe: mit dem verworfenen Projektnamen `rift-<env>` zeigt der
# gerenderte Bestand NICHT mehr auf die historischen Volume-Namen.
#
# Lokal, kein Host, kein Vault, keine Prod-Aktion. Laeuft in deploy-check-local.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../../.." && pwd)"
play="$here/main.yml"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

fail() { echo "::error::$1"; exit 1; }

# render <env> <outdir> [extra ansible args...] -> schreibt compose + meta, setzt
# METAP (3 Zeilen: projekt, wine-volume, saves-volume).
render() {
  local env="$1" out="$2"; shift 2
  mkdir -p "$out"
  RBBATTLE_RENDER_DIR="$out" ansible-playbook "$play" -e "rift_env=$env" "$@" >/dev/null
  [ -s "$out/riftbreaker-server.yml" ] || fail "$env: Compose wurde nicht gerendert."
}

# compose_names <project> <compose-file> -> die `name:`-Zeilen der Top-Level-Volumes.
compose_names() {
  docker compose -p "$1" -f "$2" config | sed -n '/^volumes:/,$p' | grep -E '^\s+name:'
}

check_env() {  # <env> <outdir> <erwartetes projekt> <wine> <saves> [extra]
  local env="$1" out="$2" exp_project="$3" exp_wine="$4" exp_saves="$5"; shift 5
  render "$env" "$out" "$@"
  local project wine saves names
  mapfile -t meta < "$out/compose-project.txt"
  project="${meta[0]}"; wine="${meta[1]}"; saves="${meta[2]}"
  names="$(compose_names "$project" "$out/riftbreaker-server.yml")"

  [ "$project" = "$exp_project" ] \
    || fail "$env: riftbreaker_compose_project ist '$project', erwartet '$exp_project' (Befund 2, Variante a)."
  # Compose praefixt unbenannte Volumes mit dem Projektnamen -> der volle Name
  # muss das HISTORISCHE docker-Volume treffen.
  grep -qF "name: ${project}_${wine}" <<<"$names" \
    || fail "$env: Volume '$wine' traegt nicht den historischen Namen '${project}_${wine}' (Regression: neues leeres Volume)."
  grep -qF "name: ${project}_${saves}" <<<"$names" \
    || fail "$env: Volume '$saves' traegt nicht den historischen Namen '${project}_${saves}' (Regression: neues leeres Volume)."
  echo "   $env: projekt=$project -> ${project}_${wine}, ${project}_${saves} (trifft bestehende Volumes)."
}

echo "== Positiv prod-A: historischer Projektname trifft riftbreaker-dedicated-prod_* =="
check_env prod "$work/prod" riftbreaker-dedicated-prod rb-wine-prod rb-saves-prod -e "@$repo/deploy/prod-vars.yml"

echo "== Positiv prod-B: eigener Projektname trifft riftbreaker-dedicated-prod-b_* (Issue #995) =="
check_env prod-b "$work/prod-b" riftbreaker-dedicated-prod-b rb-wine-prod-b rb-saves-prod-b -e "@$repo/deploy/prod-b-vars.yml"

echo "== Negativ-Probe: verworfenes Projekt 'rift-prod' verfehlt die bestehenden Volumes =="
names_rejected="$(compose_names rift-prod "$work/prod/riftbreaker-server.yml")"
if grep -qF "name: riftbreaker-dedicated-prod_rb-wine-prod" <<<"$names_rejected"; then
  fail "Negativ-Probe: verworfenes Projekt 'rift-prod' traefe trotzdem das Bestands-Volume 'riftbreaker-dedicated-prod_rb-wine-prod'."
fi
grep -qF "name: rift-prod_rb-wine-prod" <<<"$names_rejected" \
  || fail "Negativ-Probe: erwartete neue Volume-Namen ('rift-prod_rb-wine-prod') nicht gefunden."
echo "   Negativ-Probe: 'rift-prod' erzeugt neue Volume-Namen (nicht die Bestandsnamen)."

echo "== Negativ-Probe: Projekt-Rename auf 'rift-<env>' in prod-vars wird erkannt =="
fixture="$(mktemp -d)"
cp -r "$repo/deploy" "$fixture/deploy"
sed -i 's|^riftbreaker_compose_project: "riftbreaker-dedicated-prod"$|riftbreaker_compose_project: "rift-{{ rift_env }}"|' \
  "$fixture/deploy/prod-vars.yml"
grep -qF 'rift-{{ rift_env }}' "$fixture/deploy/prod-vars.yml" \
  || fail "Negativ-Probe-Fixture: prod-vars-Rename nicht angewandt."
neg_out="$work/neg"
mkdir -p "$neg_out"
RBBATTLE_RENDER_DIR="$neg_out" ansible-playbook "$fixture/deploy/tests/compose-project/main.yml" \
  -e rift_env=prod -e "@$fixture/deploy/prod-vars.yml" >/dev/null
mapfile -t neg_meta < "$neg_out/compose-project.txt"
rm -rf "$fixture"
[ "${neg_meta[0]}" != "riftbreaker-dedicated-prod" ] \
  || fail "Negativ-Probe: Projekt-Rename auf 'rift-<env>' wurde NICHT erkannt (Test wirkungslos)."
echo "   Negativ-Probe: Rename -> projekt='${neg_meta[0]}' != 'riftbreaker-dedicated-prod' (erkannt)."

echo "OK: Compose-Projekt bleibt historisch (prod/prod-b), bestehende Docker-Volumes werden getroffen (Befund 2)."