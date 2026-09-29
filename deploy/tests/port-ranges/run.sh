#!/usr/bin/env bash
# Hermetischer Regressionstest: Test-Port-Bloecke sind disjunkt (Issue #988).
#
# Hintergrund: boot-test.yml leitet Tournament- und Bridge-Host-Port aus
# github.run_id ab, test-vars.yml den Attack-Cycle-Host-Port. Lagen zwei
# Familien im selben Wertebereich, kollidierten im Test-Compose zwei
# `127.0.0.1:<port>`-Host-Publishes -> sporadisch "port is already allocated"
# (~10 % der Runs, RUN_ID % 20000 < 2000). Fix: drei disjunkte 2000er-Bloecke
# unterhalb der ephemeren Linux-Range (32768+):
#   tournament 10000-11999, bridge 12000-13999, attack-cycle 14000-15999.
#
# Nachweis: die ECHTEN Formeln werden aus boot-test.yml + test-vars.yml gelesen
# und ueber einen Lauf-Id-Bereich ausgewertet. Jede Familie bleibt in ihrem
# Block, die Bloecke ueberlappen nicht, alle Ports < 32768, und die
# tournament-Formel ist in beiden Dateien identisch.
#
# Lokal, kein Host, kein Netz, keine Secrets. Laeuft in deploy-check-local.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../../.." && pwd)"

boot="$repo/.github/workflows/boot-test.yml"
vars="$repo/deploy/test-vars.yml"

for f in "$boot" "$vars"; do
  [ -f "$f" ] || { echo "::error::Datei fehlt: $f"; exit 1; }
done

fail() { echo "::error::$1"; exit 1; }

# nums <zeile> -> "base mod" (die ersten beiden Zahlen der Formel).
nums() { grep -oE '[0-9]+' <<<"$1" | head -2 | tr '\n' ' '; }

t_line="$(grep -E 'T_PORT=\$\(' "$boot" || true)"
b_line="$(grep -E 'B_PORT=\$\(' "$boot" || true)"
tv_line="$(grep -E '^tournament_port:' "$vars" || true)"
ac_line="$(grep -E '^riftbreaker_attack_cycle_port:' "$vars" || true)"

[ -n "$t_line" ]  || fail "T_PORT-Formel nicht in boot-test.yml gefunden."
[ -n "$b_line" ]  || fail "B_PORT-Formel nicht in boot-test.yml gefunden."
[ -n "$tv_line" ] || fail "tournament_port nicht in test-vars.yml gefunden."
[ -n "$ac_line" ] || fail "riftbreaker_attack_cycle_port nicht in test-vars.yml gefunden."

read -r T_BASE T_MOD <<<"$(nums "$t_line")"
read -r B_BASE B_MOD <<<"$(nums "$b_line")"
read -r TV_BASE TV_MOD <<<"$(nums "$tv_line")"
read -r A_BASE A_MOD <<<"$(nums "$ac_line")"

for v in T_BASE T_MOD B_BASE B_MOD TV_BASE TV_MOD A_BASE A_MOD; do
  eval "val=\$$v"
  [ -n "$val" ] || fail "Konnte $v nicht aus den Formeln lesen."
done

# 1) tournament-Formel in beiden Dateien identisch (curl- == deploy-Port).
[ "$T_BASE" = "$TV_BASE" ] && [ "$T_MOD" = "$TV_MOD" ] \
  || fail "tournament_port (${TV_BASE}+%${TV_MOD}) != T_PORT (${T_BASE}+%${T_MOD}) - boot-test.yml und test-vars.yml muessen identisch sein."

EPHEMERAL=32768

# 2) Jeder Block bleibt unterhalb der ephemeren Linux-Range.
check_hi() { # <name> <base> <mod>
  local name="$1" base="$2" mod="$3" hi=$(( $2 + $3 - 1 ))
  [ "$hi" -lt "$EPHEMERAL" ] || fail "$name-Block endet bei $hi (>= $EPHEMERAL) - in der ephemeren Linux-Range."
}
check_hi tournament "$T_BASE" "$T_MOD"
check_hi bridge "$B_BASE" "$B_MOD"
check_hi attack-cycle "$A_BASE" "$A_MOD"

# 3) Die drei Bloecke ueberlappen nicht (paarweise).
overlap() { # base_a mod_a base_b mod_b -> true wenn ueberlappend
  local a1="$1" am="$2" b1="$3" bm="$4"
  local a2=$(( $1 + $2 - 1 )) b2=$(( $3 + $4 - 1 ))
  [ "$a1" -le "$b2" ] && [ "$b1" -le "$a2" ]
}
if overlap "$T_BASE" "$T_MOD" "$B_BASE" "$B_MOD"; then fail "tournament- und bridge-Block ueberlappen (#988)."; fi
if overlap "$T_BASE" "$T_MOD" "$A_BASE" "$A_MOD"; then fail "tournament- und attack-cycle-Block ueberlappen (#988)."; fi
if overlap "$B_BASE" "$B_MOD" "$A_BASE" "$A_MOD"; then fail "bridge- und attack-cycle-Block ueberlappen (#988)."; fi

# 4) Konkreter Lauf-Id-Bereich: die drei Ports sind je Lauf paarweise verschieden.
#    (Deckt den urspruenglichen Kollisionsfall RUN_ID % 20000 < 2000 ab.)
for rid in 0 1 7 1888 1999 2000 19999 123456789; do
  tp=$(( T_BASE + (rid % T_MOD) ))
  bp=$(( B_BASE + (rid % B_MOD) ))
  ap=$(( A_BASE + (rid % A_MOD) ))
  { [ "$tp" != "$bp" ] && [ "$tp" != "$ap" ] && [ "$bp" != "$ap" ]; } \
    || fail "Lauf-Id $rid: Ports kollidieren (tournament=$tp bridge=$bp attack-cycle=$ap)."
done

echo "ok: Port-Bloecke disjunkt - tournament ${T_BASE}-$((T_BASE+T_MOD-1)), bridge ${B_BASE}-$((B_BASE+B_MOD-1)), attack-cycle ${A_BASE}-$((A_BASE+A_MOD-1)), alle < ${EPHEMERAL}."
