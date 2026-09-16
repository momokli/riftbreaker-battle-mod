#!/usr/bin/env bash
# Hermetischer Selbsttest der Legacy-rbtools-Symlink-Cleanup (Issue #606).
#
# Prueft die ECHTE Task-Datei deploy/tasks/legacy-rbtools-symlink-cleanup.yml
# in einem Wegwerf-Verzeichnis (kein Host, kein Vault, keine Prod-Aktion):
#   * Symlink auf das erwartete Ziel -> entfernt
#   * echtes Verzeichnis (kein Symlink) -> bleibt erhalten (Warnung)
#   * erneuter Lauf -> no-op (idempotent, changed=0)
#
# Läuft in deploy-check-local.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
play="$here/main.yml"

fail() { echo "::error::$1"; exit 1; }

assert_absent() {  # <path> <label>
  local p="$1" label="$2"
  if [ -e "$p" ] || [ -L "$p" ]; then
    fail "$label wurde nicht entfernt (Pfad existiert noch: $p)"
  fi
}

base="$(mktemp -d)"
trap 'rm -rf "$base"' EXIT

# Ziele + ein Alt-Pfad als ECHTES Verzeichnis (darf nicht angefasst werden).
mkdir -p "$base/dev" "$base/prod" "$base/rbtools-not-a-link"
printf 'keep' > "$base/rbtools-not-a-link/keep.txt"
ln -s "$base/dev" "$base/rbtools-drift"
ln -s "$base/prod" "$base/rbtools-rift"

echo "== Lauf 1: Symlinks entfernen, echtes Verzeichnis bleibt =="
ansible-playbook "$play" -e "test_base=$base"

assert_absent "$base/rbtools-drift" "rbtools-drift (Symlink)"
assert_absent "$base/rbtools-rift" "rbtools-rift (Symlink)"
[ -d "$base/rbtools-not-a-link" ] || fail "echtes Verzeichnis wurde faelschlich entfernt"
[ -f "$base/rbtools-not-a-link/keep.txt" ] || fail "Inhalt des echten Verzeichnisses fehlt"

echo "== Lauf 2: no-op (idempotent, changed=0) =="
out2="$(ansible-playbook "$play" -e "test_base=$base")"
if ! grep -qE 'changed=0' <<<"$out2"; then
  echo "$out2"
  fail "erneuter Lauf war nicht idempotent (changed != 0)"
fi

echo "OK: Legacy-rbtools-Symlink-Cleanup entfernt nur Symlinks (Issue #606)."
