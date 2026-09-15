#!/usr/bin/env bash
# Hermetischer Selbsttest der Env-Pfad-Migration (Issue #483, Ziel B).
#
# Prueft die ECHTE Task-Datei deploy/tasks/env-path-migration.yml in einem
# Wegwerf-Verzeichnis (kein Host, kein Vault, keine Prod-Aktion):
#   * alter Pfad vorhanden, neuer fehlt -> `mv` (kein copy) + Symlink Alt->Neu
#   * erneuter Lauf               -> no-op (idempotent, changed=0)
#   * Alt UND Neu vorhanden       -> kein mv (mehrdeutig, nur Warnung)
#
# Läuft in deploy-check-local.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
play="$here/main.yml"

fail() { echo "::error::$1"; exit 1; }

base="$(mktemp -d)"
trap 'rm -rf "$base"' EXIT

# Alt-Pfade mit Inhalt (mv-Nachweis: Inhalt muss am NEUEN Pfad liegen).
mkdir -p "$base/old-game" "$base/old-rbtools" "$base/both-old" "$base/both-new"
printf 'game-content' > "$base/old-game/marker.txt"
printf 'bin' > "$base/old-rbtools/rbbridge.dll"

echo "== Lauf 1: mv + Symlink =="
ansible-playbook "$play" -e "test_base=$base"

[ -d "$base/new/game" ] || fail "neuer Pfad new/game fehlt"
[ -e "$base/new/game/marker.txt" ] || fail "mv hat den Inhalt nicht mitgenommen (kein copy-Nachweis)"
[ -L "$base/old-game" ] || fail "alter Pfad ist kein Symlink"
[ "$(readlink "$base/old-game")" = "$base/new/game" ] || fail "Symlink zeigt nicht auf den neuen Pfad"
[ ! -e "$base/old-game/marker.txt" ] || true   # Altpfad liest ueber den Symlink den neuen Inhalt
[ -e "$base/old-game/marker.txt" ] || fail "Symlink-Uebergang kaputt (Altpfad liest neuen Inhalt nicht)"
[ -L "$base/old-rbtools" ] || fail "alter rbtools-Pfad ist kein Symlink"

echo "== Mehrdeutig: Alt UND Neu vorhanden -> kein mv =="
[ -d "$base/both-old" ] || fail "mehrdeutiger Alt-Pfad wurde faelschlich verschoben"
[ ! -L "$base/both-old" ] || fail "mehrdeutiger Alt-Pfad wurde faelschlich verlinkt"

echo "== Lauf 2: no-op (idempotent, changed=0) =="
out2="$(ansible-playbook "$play" -e "test_base=$base")"
if ! grep -qE 'changed=0' <<<"$out2"; then
  echo "$out2"
  fail "erneuter Lauf war nicht idempotent (changed != 0)"
fi

echo "OK: Env-Pfad-Migration idempotent (mv + Symlink, kein copy; Issue #483)."