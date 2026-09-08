#!/usr/bin/env bash
# ============================================================================
# package_mod.sh — zippt den Mod-Ordner (mod/) fuer manuelle Verteilung/Tests.
#
# Verwendung:
#   scripts/package_mod.sh                 -> dist/rbbattle-mod-<datum>.zip
#   scripts/package_mod.sh v0.2.0          -> dist/rbbattle-mod-v0.2.0.zip
#
# Inhalt des Zips: der komplette Mod-Ordner (mod/) - also das Lua-Mod mit
# README - als Content-Root (lua/...), genau wie er nach
# <game>/mods/<ModName>/ gehoert. NUR der Mod, NIE Trainer-/Server-Teile
# (Trainer bleibt privat, siehe docs/workshop.md).
#
# Abhaengigkeit: zip ODER Python 3 (Fallback, nur Standardbibliothek) —
# laeuft damit unter Linux/macOS/Git-Bash/WSL.
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"

VERSION="${1:-$(date +%Y%m%d)}"
SRC_DIR="$ROOT/mod"
OUT_DIR="$ROOT/dist"
OUT_FILE="$OUT_DIR/rbbattle-mod-$VERSION.zip"
TMP_ZIP="$OUT_DIR/.rbbattle-mod-$VERSION.zip.tmp"

if [ ! -d "$SRC_DIR" ]; then
    echo "FEHLER: Mod-Ordner '$SRC_DIR' nicht gefunden." >&2
    exit 1
fi

mkdir -p "$OUT_DIR"
rm -f "$TMP_ZIP"
echo "[package_mod] Packe $SRC_DIR -> $OUT_FILE"

if command -v zip >/dev/null 2>&1; then
    # Von INNERHALB von mod/ zippen, damit das Zip die Content-Root ist
    # (lua/..., README.md) und nicht den Ordner mod/ selbst enthaelt.
    (cd "$SRC_DIR" && zip -r "$TMP_ZIP" . -x '*.DS_Store' >/dev/null)
elif command -v python3 >/dev/null 2>&1; then
    # Fallback: gleiche Semantik (Content-Root, .DS_Store raus) via zipfile.
    python3 - "$SRC_DIR" "$TMP_ZIP" <<'PYEOF'
import os, sys, zipfile
src, out = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for root, _dirs, files in os.walk(src):
        for f in files:
            if f == ".DS_Store":
                continue
            full = os.path.join(root, f)
            z.write(full, os.path.relpath(full, src))
PYEOF
else
    echo "FEHLER: weder 'zip' noch 'python3' verfuegbar." >&2
    exit 1
fi

mv "$TMP_ZIP" "$OUT_FILE"
echo "[package_mod] OK: $OUT_FILE"
unzip -l "$OUT_FILE" 2>/dev/null | sed -n '1,12p' \
    || python3 -c "import zipfile,sys; [print(n) for n in zipfile.ZipFile(sys.argv[1]).namelist()]" "$OUT_FILE"
echo "[package_mod] Groesse: $(du -h "$OUT_FILE" | cut -f1)"
