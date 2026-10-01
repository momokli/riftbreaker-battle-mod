#!/usr/bin/env bash
# Hermetischer Selbsttest der Legacy-Pfad-Cleanup (Issue #536, US1).
#
# Prueft die ECHTE Task-Datei deploy/tasks/legacy-path-cleanup.yml in einem
# Wegwerf-Verzeichnis (kein Host, kein Vault, keine Prod-Aktion):
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

# Ziel + ein Alt-Pfad als ECHTES Verzeichnis (darf nicht angefasst werden).
mkdir -p "$base/http" "$base/not-a-link"
printf 'keep' > "$base/not-a-link/keep.txt"
ln -s "$base/http" "$base/rbmods-site"

echo "== Lauf 1: Symlink entfernen, echtes Verzeichnis bleibt =="
ansible-playbook "$play" -e "test_base=$base"

assert_absent "$base/rbmods-site" "rbmods-site (Symlink)"
[ -d "$base/not-a-link" ] || fail "echtes Verzeichnis wurde faelschlich entfernt"
[ -f "$base/not-a-link/keep.txt" ] || fail "Inhalt des echten Verzeichnisses fehlt"

echo "== Lauf 2: no-op (idempotent, changed=0) =="
out2="$(ansible-playbook "$play" -e "test_base=$base")"
if ! grep -qE 'changed=0' <<<"$out2"; then
  echo "$out2"
  fail "erneuter Lauf war nicht idempotent (changed != 0)"
fi

echo "== Lauf 3: fehlender Pfad -> no-op (changed=0) =="
out3="$(ansible-playbook "$play" -e "test_base=$base")"
if ! grep -qE 'changed=0' <<<"$out3"; then
  echo "$out3"
  fail "fehlender Pfad war nicht no-op (changed != 0)"
fi

echo "OK: Legacy-Pfad-Cleanup entfernt nur Symlinks (Issue #536)."
