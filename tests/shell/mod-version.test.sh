#!/usr/bin/env bash
# Hermetischer Selbsttest der Mod-Versions-Identitaet (Issue #494).
#
# Stellt sicher, dass im Mod KEINE hartcodierte Versionsnummer lebt, sondern
# ueberall der Platzhalter RBB_BUILD_REF steht, und dass
# scripts/bake_mod_ref.py ihn beim Packen in Lua UND Manifest durch den echten
# Ref (SHA bzw. Tag) ersetzt — ohne Reste.
#
# Kein Host, kein Vault, kein Docker. Laeuft in lint.yml (ubuntu-latest) und lokal.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/../.." && pwd)"

LUA="$root/client-mod/lua/rbbattle_autoexec.lua"
shopt -s nullglob
manifests=("$root"/client-mod/*.manifest)
shopt -u nullglob

fail() { echo "FEHLER: $*" >&2; exit 1; }

# --- 1) Quelle traegt NUR den Platzhalter, keine hartcodierte Version --------
[ "${#manifests[@]}" -eq 1 ] || fail "genau ein *.manifest erwartet, gefunden ${#manifests[@]}"
MANIFEST="${manifests[0]}"

grep -q 'RBB.version = "RBB_BUILD_REF"' "$LUA" \
    || fail "RBB.version traegt nicht den Platzhalter RBB_BUILD_REF"
grep -q 'RBB.ref = "RBB_BUILD_REF"' "$LUA" \
    || fail "RBB.ref traegt nicht den Platzhalter RBB_BUILD_REF"
if grep -Eq 'RBB\.version[[:space:]]*=[[:space:]]*"[0-9]' "$LUA"; then
    fail "RBB.version ist hartcodiert (Platzhalter RBB_BUILD_REF erwartet)"
fi
grep -Eq '^[[:space:]]*version[[:space:]]*"RBB_BUILD_REF"' "$MANIFEST" \
    || fail "Manifest-version traegt nicht den Platzhalter RBB_BUILD_REF"

# --- 2) Backen ersetzt den Platzhalter in Lua UND Manifest -------------------
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
cp -R "$root/client-mod"/. "$tmp/mod/"
FAKE="deadbeef1234"

python3 "$root/scripts/bake_mod_ref.py" "$tmp/mod" "$FAKE" >/dev/null \
    || fail "bake_mod_ref.py ist fehlgeschlagen"

grep -q "RBB.version = \"$FAKE\"" "$tmp/mod/lua/rbbattle_autoexec.lua" \
    || fail "RBB.version wurde nicht gebacken"
grep -q "RBB.ref = \"$FAKE\"" "$tmp/mod/lua/rbbattle_autoexec.lua" \
    || fail "RBB.ref wurde nicht gebacken"
grep -Eq "^[[:space:]]*version[[:space:]]*\"$FAKE\"" "$tmp/mod"/*.manifest \
    || fail "Manifest-version wurde nicht gebacken"

if grep -Rq 'RBB_BUILD_REF' "$tmp/mod"; then
    fail "RBB_BUILD_REF blieb nach dem Backen zurueck"
fi

echo "OK: Mod-Version = Build-Ref (kein hartcodierter Wert, Lua+Manifest konsistent)."
