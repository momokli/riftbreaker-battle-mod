#!/usr/bin/env bash
# ============================================================================
# package_bausteine.sh — zippt JEDEN Baustein einzeln nach dist/.
#
#   Bausteine 00-03 + 05: der Mod-Ordner (bausteine/<nr>-*/<modordner>) als
#   Content-Root -> rbb-00-mod-skeleton.zip, rbb-01-wave-spawn.zip,
#   rbb-02-custom-ui.zip, rbb-03-log-bridge.zip, rbb-05-economy-loop.zip
#   (lua/... an der Zip-Wurzel, genau wie nach <game>/mods/<ModName>/ gehoert).
#
#   Baustein 04 (trainer-io):
#     - ist x86_64-w64-mingw32-gcc auf dem PATH: kompiliert rbbridge.dll +
#       injector.exe + rbbridge_standalone.exe (Windows x64, #ifdef
#       RBBRIDGE_STANDALONE) -> rbb-04-trainer-io.zip
#     - sonst ist zig (PATH oder $ZIG) verfuegbar: gleicher Build via
#       "zig cc -target x86_64-windows-gnu" (Zig bringt die mingw-Libc mit)
#     - sonst: Quellen + pipe_client.py + README -> rbb-04-trainer-io-src.zip
#
#   Baustein 06 (tournament-server): der Baustein-Ordner als Content-Root ->
#   rbb-06-tournament-server.zip (server.js, mock_client.js, test_e2e.sh,
#   README.md an der Zip-Wurzel).
#
# Ausgabe: je Zip eine Zeile "NAME=<dateiname> ZIP=<absoluter-pfad>"
# (maschinenlesbar fuer Release-Upload).
#
# Abhaengigkeit: zip ODER python3 (Fallback, nur Standardbibliothek);
# optional x86_64-w64-mingw32-gcc oder zig (fuer den 04-Binary-Build).
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
OUT_DIR="$ROOT/dist"
mkdir -p "$OUT_DIR"

# zip_content_root <srcdir> <outzip>: packt den INHALT von <srcdir> mit
# <srcdir> als Content-Root (der Ordner selbst kommt NICHT ins Zip),
# .DS_Store wird rausgefiltert.
zip_content_root() {
    local src="$1" out="$2" tmp="$2.tmp"
    rm -f "$tmp"
    if command -v zip >/dev/null 2>&1; then
        (cd "$src" && zip -r "$tmp" . -x '*.DS_Store' >/dev/null)
    elif command -v python3 >/dev/null 2>&1; then
        python3 - "$src" "$tmp" <<'PYEOF'
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
    mv "$tmp" "$out"
    echo "[package_bausteine] OK: $out"
}

# Mod-Bausteine 00-03 + 05: "<zipname>|<modordner-relativ>"
BAUSTEINE=(
    "rbb-00-mod-skeleton|bausteine/00-mod-skeleton/rbbattle_00_skeleton"
    "rbb-01-wave-spawn|bausteine/01-wave-spawn/rbbattle_01_wavespawn"
    "rbb-02-custom-ui|bausteine/02-custom-ui/rbbattle_02_customui"
    "rbb-03-log-bridge|bausteine/03-log-bridge/rbbattle_03_logbridge"
    "rbb-05-economy-loop|bausteine/05-economy-loop/rbbattle_05_economy"
)

for entry in "${BAUSTEINE[@]}"; do
    name="${entry%%|*}"
    srcdir="${entry#*|}"
    if [ ! -d "$ROOT/$srcdir" ]; then
        echo "FEHLER: Mod-Ordner '$ROOT/$srcdir' nicht gefunden." >&2
        exit 1
    fi
    out="$OUT_DIR/$name.zip"
    zip_content_root "$ROOT/$srcdir" "$out"
    echo "NAME=$name.zip ZIP=$out"
done

# Baustein 04: trainer-io (Compile mit mingw-gcc oder zig; sonst Quell-Zip).
# Inhalt des Binary-Zips: injector.exe + rbbridge.dll + rbbridge_standalone.exe
# (alle drei aus dem aktuellen Quellstand; standalone via -DRBBRIDGE_STANDALONE).
SRC04="$ROOT/bausteine/04-trainer-io"
TOOLCHAIN="none"
if command -v x86_64-w64-mingw32-gcc >/dev/null 2>&1; then
    TOOLCHAIN="mingw"
elif command -v zig >/dev/null 2>&1 || [ -n "${ZIG:-}" ]; then
    TOOLCHAIN="zig"
fi
build_04_binaries() { # <builddir> — kompiliert die 3 Windows-x64-Binaries
    local bd="$1"
    if [ "$TOOLCHAIN" = "mingw" ]; then
        echo "[package_bausteine] 04: x86_64-w64-mingw32-gcc gefunden -> Build (Windows x64)"
        (cd "$bd" \
            && x86_64-w64-mingw32-gcc -O2 -Wall -Wextra -shared -o rbbridge.dll "$SRC04/rbbridge/rbbridge.c" \
            && x86_64-w64-mingw32-gcc -O2 -Wall -Wextra -o injector.exe "$SRC04/injector/injector.c" \
            && x86_64-w64-mingw32-gcc -O2 -Wall -Wextra -DRBBRIDGE_STANDALONE -o rbbridge_standalone.exe "$SRC04/rbbridge/rbbridge.c")
    else
        local zigc="${ZIG:-zig}"
        echo "[package_bausteine] 04: kein mingw-gcc, aber zig -> Build (zig cc, x86_64-windows-gnu)"
        (cd "$bd" \
            && "$zigc" cc -target x86_64-windows-gnu -O2 -Wall -Wextra -shared -o rbbridge.dll "$SRC04/rbbridge/rbbridge.c" \
            && "$zigc" cc -target x86_64-windows-gnu -O2 -Wall -Wextra -o injector.exe "$SRC04/injector/injector.c" \
            && "$zigc" cc -target x86_64-windows-gnu -O2 -Wall -Wextra -DRBBRIDGE_STANDALONE -o rbbridge_standalone.exe "$SRC04/rbbridge/rbbridge.c")
    fi
}
BUILD_DIR="$OUT_DIR/.build-04"
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"
if [ "$TOOLCHAIN" = "none" ]; then
    rm -rf "$BUILD_DIR"
    echo "[package_bausteine] 04: weder x86_64-w64-mingw32-gcc noch zig verfuegbar -> Quell-Zip"
    out04="$OUT_DIR/rbb-04-trainer-io-src.zip"
    zip_content_root "$SRC04" "$out04"
    echo "NAME=rbb-04-trainer-io-src.zip ZIP=$out04"
elif build_04_binaries "$BUILD_DIR"; then
    # nur die 3 Binaries ins Zip (keine .pdb/.lib-Artefakte von zig)
    rm -f "$BUILD_DIR"/*.pdb "$BUILD_DIR"/*.lib "$BUILD_DIR"/*.o
    out04="$OUT_DIR/rbb-04-trainer-io.zip"
    zip_content_root "$BUILD_DIR" "$out04"
    rm -rf "$BUILD_DIR"
    echo "NAME=rbb-04-trainer-io.zip ZIP=$out04"
else
    echo "FEHLER: 04-Build (TOOLCHAIN=$TOOLCHAIN) fehlgeschlagen." >&2
    exit 1
fi

# Baustein 06: tournament-server (Server + Mock-Client + E2E + README)
SRC06="$ROOT/bausteine/06-tournament-server"
for f in server.js mock_client.js test_e2e.sh README.md; do
    [ -f "$SRC06/$f" ] || { echo "FEHLER: $SRC06/$f nicht gefunden." >&2; exit 1; }
done
out06="$OUT_DIR/rbb-06-tournament-server.zip"
zip_content_root "$SRC06" "$out06"
echo "NAME=rbb-06-tournament-server.zip ZIP=$out06"

# Einzel-Mod (Issue #16): der fusionierte Mod mod/ als rbbattle.zip —
# Primär-Download. Content-Root = mod/ (lua/ + <GUID>.manifest + README.md an
# der Zip-Wurzel), genau wie er nach <game>/mods/rbbattle/ gehoert.
SRCMOD="$ROOT/mod"
outmod="$OUT_DIR/rbbattle.zip"
zip_content_root "$SRCMOD" "$outmod"
echo "NAME=rbbattle.zip ZIP=$outmod"

# Einzel-Mod versioniert (Issue #119): die Mod-Version kommt aus den
# Mod-Metadaten (mod/<GUID>.manifest, Feld `version`), nicht aus Git-SHA oder
# Datum. rbbattle-v<version>.zip ist der kanonische, versionierte Download;
# rbbattle.zip bleibt der stabile Alias für die Deploy-Kette (md5-Parität +
# Server-Extraktion), damit Download-Link und Server-Stand nie auseinanderlaufen.
MOD_VERSION="$(bash "$ROOT/scripts/mod_version.sh")"
outmod_ver="$OUT_DIR/rbbattle-v$MOD_VERSION.zip"
cp "$outmod" "$outmod_ver"
echo "NAME=rbbattle-v$MOD_VERSION.zip ZIP=$outmod_ver"
echo "MOD_VERSION=$MOD_VERSION"

n_zip=$(find "$OUT_DIR" -maxdepth 1 -name 'rbb-*.zip' | wc -l)
echo "[package_bausteine] fertig: ${n_zip} Baustein-Zip(s) + rbbattle.zip in $OUT_DIR"
