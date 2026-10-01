#!/usr/bin/env bash
# Hermetischer Regressionstest der GNS-Entry-Relay-Routen (Issue #995).
#
# Hintergrund: die A/B-Zuordnung der zweiten prod-Welt (prod-B) entsteht in
# deploy/roles/gns-relay/templates/routes.j2 aus den Rollen-Variablen
# gns_relay_target_a/_b (prod-A :6322 / prod-B :6325). Der C++-Matcher selbst
# (tools/gns-proxy/test_route_rules.cpp, 44 Checks) ist getestet — die
# gerenderte Routen-Datei aber war in keinem Repo-Test abgedeckt: eine
# vertauschte/entfernte *-b-Zeile oder ein hartkodiertes Ziel bliebe gruen.
#
# Nachweis: das ECHTE Template wird gerendert (Rollen-Defaults) und die
# effektive Routen-Tabelle geprueft. Negativ-Probe: ein Override von
# gns_relay_target_b MUSS die *-b-Zeile verschieben (kein Hardcode).
#
# PROD-ONLY (Issue #1034): dev/staging sind entfallen — die Routen tragen nur
# noch *-a / *-b / Default.
#
# Lokal, kein Host, kein Vault, keine Prod-Aktion. Laeuft in deploy-check-local.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../../.." && pwd)"
play="$here/main.yml"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

fail() { echo "::error::$1"; exit 1; }

# render <outdir> [extra ansible args...] -> rendert die Routen-Datei.
render() {
  local out="$1"; shift
  mkdir -p "$out"
  RBBATTLE_RENDER_DIR="$out" ansible-playbook "$play" "$@" >/dev/null
  [ -s "$out/routes" ] || fail "routes.j2 wurde nicht gerendert."
}

# route_of <file> <key> -> Ziel der ersten passenden Zeile (spaltenbasiert,
# Kommentare werden ignoriert).
route_of() { awk -v k="$2" '$1==k {print $3; exit}' "$1"; }

echo "== Positiv: A/B + Default (prod-only, Issue #1034) =="
out="$work/out"
render "$out"
[ "$(route_of "$out/routes" '*-a')" = "127.0.0.1:6322" ] || fail "*-a zeigt nicht auf prod-A :6322."
[ "$(route_of "$out/routes" '*-b')" = "127.0.0.1:6325" ] || fail "*-b zeigt nicht auf prod-B :6325 (Issue #995)."
[ "$(route_of "$out/routes" '*')" = "127.0.0.1:6322" ] || fail "Default '*' nicht auf prod-A :6322 (Entscheidung D1)."
! grep -qE '^\*-dev' "$out/routes" || fail "*-dev darf nach #1034 NICHT mehr gerendert werden."
! grep -qE '^\*-staging' "$out/routes" || fail "*-staging darf nach #1034 NICHT mehr gerendert werden."
echo "   *-a=6322 *-b=6325 '*'=6322 (kein dev/staging)"

echo "== Reihenfolge: spezifische Suffixe VOR dem Default (Prioritaet exakt > laengster Suffix > Default) =="
def_line="$(grep -nE '^\*[[:space:]]*=' "$out/routes" | head -1 | cut -d: -f1)"
[ -n "$def_line" ] || fail "Default-Zeile '*' fehlt."
for key in '\*-a' '\*-b'; do
  line="$(grep -nE "^${key}[[:space:]]*=" "$out/routes" | head -1 | cut -d: -f1)"
  [ -n "$line" ] || fail "Route ${key} fehlt in der gerenderten Datei."
  [ "$line" -lt "$def_line" ] || fail "Route ${key} steht NICHT vor dem Default."
done
echo "   alle Suffix-Routen stehen vor '*'."

echo "== Negativ-Probe: gns_relay_target_b-Override MUSS die *-b-Zeile verschieben (kein Hardcode) =="
neg="$work/neg"
render "$neg" -e gns_relay_target_b=127.0.0.1:6000
[ "$(route_of "$neg/routes" '*-b')" = "127.0.0.1:6000" ] \
  || fail "Negativ-Probe: *-b folgt nicht gns_relay_target_b (Template hartkodiert?)."
[ "$(route_of "$neg/routes" '*-a')" = "127.0.0.1:6322" ] \
  || fail "Negativ-Probe: *-a wurde durch den *-b-Override mitveraendert."
echo "   *-b folgt gns_relay_target_b; *-a unveraendert."

echo "OK: GNS-Routen A/B korrekt gerendert + var-getrieben (Issue #995/#1034)."
