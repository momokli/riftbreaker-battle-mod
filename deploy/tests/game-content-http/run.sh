#!/usr/bin/env bash
# Hermetischer Selbsttest der game-content-http-Idempotenz (Issue #1095).
#
# Prüft die ECHTE Rolle deploy/roles/game-content im Modus `http` gegen ein
# tmpdir-Fixture mit unerreichbarer `riftbreaker_content_http_base`:
#
#   Fall 1 (warm):  Binary + PDB vorhanden  -> KEIN Download, Lauf gruen.
#                   Unter dem Bug aus #1095 lief der get_url trotzdem und
#                   brach ab -> genau dieser Fall ist der Rot-Before-Green.
#   Fall 2 (cold):  Binary fehlt            -> Download wird versucht, Lauf rot
#                   (beweist: der Unreachable-Detektor schlägt an + der
#                    `not binary`-Zweig des Guards greift).
#   Fall 3 (force): force=true, warm        -> Download wird versucht, Lauf rot
#                   (beweist Akzeptanz #2: force lädt weiterhin neu).
#
# Kein Host, kein Netz, kein Vault, kein Docker. Läuft in deploy-check-local.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../../.." && pwd)"
play="$here/main.yml"
roles="$repo/deploy/roles"

fail() { echo "::error::$1"; exit 1; }

base="$(mktemp -d)"
trap 'rm -rf "$base"' EXIT

# Connection-refused auf 127.0.0.1:1 — jeder get_url-Lauf scheitert sofort.
UNREACHABLE="http://127.0.0.1:1"

run() {  # <fixture-root> [extra-args...]
  local root="$1"; shift
  ANSIBLE_ROLES_PATH="$roles" ansible-playbook "$play" \
    -e "test_fixture_root=$root" -e "test_http_base=$UNREACHABLE" "$@" 2>&1
}

warm_fixture() {  # <root>
  mkdir -p "$1/game/bin"
  printf 'dummy' > "$1/game/bin/DedicatedServer.exe"
  printf 'dummy' > "$1/game/bin/riftbreaker_dll_win_release.pdb"
}

echo "== Fall 1 (warm, offline): Binary + PDB vorhanden -> kein Download, gruen =="
warm="$base/warm"; warm_fixture "$warm"
if ! out="$(run "$warm")"; then
  printf '%s\n' "$out"
  fail "Warm-Lauf (Binary+PDB vorhanden, unreachable Base) ist nicht gruen — Idempotenz-Guard fehlt/wirkt nicht (#1095)."
fi
if grep -qE 'fatal:|FAILED!' <<<"$out"; then
  printf '%s\n' "$out"
  fail "Warm-Lauf enthält einen fatal/FAILED-Eintrag."
fi
echo "   OK (rc=0, kein Download, kein fatal)"

echo "== Fall 2 (cold, offline): Binary fehlt -> Download laeuft, Lauf muss rot werden =="
cold="$base/cold"; mkdir -p "$cold"
set +e
out="$(run "$cold")"
rc=$?
set -e
if [ "$rc" -eq 0 ]; then
  printf '%s\n' "$out"
  fail "Cold-Lauf (Binary fehlt, unreachable Base) ist gruen — der Download bei fehlendem Binary wird nicht ausgelöst."
fi
echo "   OK (rc!=0 — Download bei fehlendem Binary wird tatsächlich versucht)"

echo "== Fall 3 (force): warm + force=true -> Download laeuft, Lauf muss rot werden =="
frc="$base/force"; warm_fixture "$frc"
set +e
out="$(run "$frc" -e riftbreaker_content_force=true)"
rc=$?
set -e
if [ "$rc" -eq 0 ]; then
  printf '%s\n' "$out"
  fail "Force-Lauf (force=true, unreachable Base) ist gruen — force lädt nicht neu (Akzeptanz #2)."
fi
echo "   OK (rc!=0 — force lädt weiterhin neu)"

echo "OK: game-content (http) ist idempotent — nach Erst-Setup (Binary+PDB vorhanden) kontaktiert der Lauf planet nicht mehr; Cold-Start und force lösen weiterhin einen Download aus."
