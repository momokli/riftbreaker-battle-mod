#!/usr/bin/env bash
# ============================================================================
# mod_version.sh — liest die Mod-Version aus den Mod-Metadaten (Manifest).
#
# Einzige Quelle der Wahrheit für die Mod-Version ist das Mod-Manifest
# mod/<GUID>.manifest (Feld `version "x.y.z"`). Dieses Skript extrahiert den
# Wert und gibt ihn auf stdout aus (ohne Zusatz).
#
# Verwendung:
#   scripts/mod_version.sh          -> 0.21.0
#   VERSION="$(scripts/mod_version.sh)"
#
# Fehlerfall (Exit != 0): kein Manifest, mehrere Manifeste oder kein bzw.
# mehrere `version`-Einträge — das Skript bricht ab (set -euo pipefail).
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"

shopt -s nullglob
MANIFESTS=( "$ROOT"/mod/*.manifest )
shopt -u nullglob

if [ "${#MANIFESTS[@]}" -eq 0 ]; then
    echo "FEHLER: kein Mod-Manifest (*.manifest) unter mod/ gefunden." >&2
    exit 1
fi
if [ "${#MANIFESTS[@]}" -gt 1 ]; then
    echo "FEHLER: mehrere Mod-Manifeste unter mod/ gefunden (${#MANIFESTS[@]})." >&2
    exit 1
fi

MANIFEST="${MANIFESTS[0]}"

# `version` ist der exakte Schlüssel (game_version/visibility/title matchen NICHT).
COUNT="$(grep -cE '^[[:space:]]*version[[:space:]]*"' "$MANIFEST" || true)"
if [ "$COUNT" -ne 1 ]; then
    echo "FEHLER: erwarte genau einen 'version \"…\"'-Eintrag, gefunden $COUNT in $MANIFEST." >&2
    exit 1
fi

VERSION="$(sed -nE 's/^[[:space:]]*version[[:space:]]*"([^"]+)".*/\1/p' "$MANIFEST")"
printf '%s\n' "$VERSION"
