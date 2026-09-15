#!/usr/bin/env bash
# ============================================================================
# package.sh — baut + packt die 1.0-Komponenten (server/ + client-mod/) nach dist/.
#
#   server/ (rbbridge):
#     - ist x86_64-w64-mingw32-gcc auf dem PATH: kompiliert rbbridge.dll +
#       injector.exe + rbbridge_standalone.exe (Windows x64, #ifdef
#       RBBRIDGE_STANDALONE) + pipe_bridge.exe (HTTP(9001)->Pipe-Bridge,
#       Issue #265; Win32 + ws2_32) -> rbb-rbbridge.zip
#     - sonst ist zig (PATH oder $ZIG) verfuegbar: gleicher Build via
#       "zig cc -target x86_64-windows-gnu" (Zig bringt die mingw-Libc mit)
#     - sonst: Quellen + pipe_client.py + README -> rbb-rbbridge-src.zip
#
# Ausgabe: je Zip eine Zeile "NAME=<dateiname> ZIP=<absoluter-pfad>"
# (maschinenlesbar fuer Release-Upload).
#
# Abhaengigkeit: zip ODER python3 (Fallback, nur Standardbibliothek);
# optional x86_64-w64-mingw32-gcc oder zig (fuer den server-Binary-Build).
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
    echo "[package] OK: $out"
}

# server/ (rbbridge): Compile mit mingw-gcc oder zig; sonst Quell-Zip.
# Inhalt des Binary-Zips: injector.exe + rbbridge.dll + rbbridge_standalone.exe
# + pipe_bridge.exe (Issue #265; alle aus dem aktuellen Quellstand;
# standalone via -DRBBRIDGE_STANDALONE).
SERVER_SRC="$ROOT/server"
TOOLCHAIN="none"
if command -v x86_64-w64-mingw32-gcc >/dev/null 2>&1; then
    TOOLCHAIN="mingw"
elif command -v zig >/dev/null 2>&1 || [ -n "${ZIG:-}" ]; then
    TOOLCHAIN="zig"
fi
build_server_binaries() { # <builddir> — kompiliert die 4 Windows-x64-Binaries
    local bd="$1"
    if [ "$TOOLCHAIN" = "mingw" ]; then
        echo "[package] server: x86_64-w64-mingw32-gcc gefunden -> Build (Windows x64)"
        (cd "$bd" \
            && x86_64-w64-mingw32-gcc -O2 -Wall -Wextra -shared -o rbbridge.dll "$SERVER_SRC/dll/rbbridge.c" \
            && x86_64-w64-mingw32-gcc -O2 -Wall -Wextra -o injector.exe "$SERVER_SRC/injector/injector.c" \
            && x86_64-w64-mingw32-gcc -O2 -Wall -Wextra -DRBBRIDGE_STANDALONE -o rbbridge_standalone.exe "$SERVER_SRC/dll/rbbridge.c" \
            && x86_64-w64-mingw32-gcc -O2 -Wall -Wextra -o pipe_bridge.exe "$SERVER_SRC/pipe-bridge/pipe_bridge.c" -lws2_32)
    else
        local zigc="${ZIG:-zig}"
        echo "[package] server: kein mingw-gcc, aber zig -> Build (zig cc, x86_64-windows-gnu)"
        (cd "$bd" \
            && "$zigc" cc -target x86_64-windows-gnu -O2 -Wall -Wextra -shared -o rbbridge.dll "$SERVER_SRC/dll/rbbridge.c" \
            && "$zigc" cc -target x86_64-windows-gnu -O2 -Wall -Wextra -o injector.exe "$SERVER_SRC/injector/injector.c" \
            && "$zigc" cc -target x86_64-windows-gnu -O2 -Wall -Wextra -DRBBRIDGE_STANDALONE -o rbbridge_standalone.exe "$SERVER_SRC/dll/rbbridge.c" \
            && "$zigc" cc -target x86_64-windows-gnu -O2 -Wall -Wextra -o pipe_bridge.exe "$SERVER_SRC/pipe-bridge/pipe_bridge.c" -lws2_32)
    fi
}
BUILD_DIR="$OUT_DIR/.build-server"
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

# Web-UI (single source) -> C-String-Include generieren (pipe_bridge.c
# braucht cockpit_html.inc beim Compile).
python3 "$ROOT/scripts/gen_cockpit_html.py"

if [ "$TOOLCHAIN" = "none" ]; then
    rm -rf "$BUILD_DIR"
    echo "[package] server: weder x86_64-w64-mingw32-gcc noch zig verfuegbar -> Quell-Zip"
    out_server="$OUT_DIR/rbb-rbbridge-src.zip"
    zip_content_root "$SERVER_SRC" "$out_server"
    echo "NAME=rbb-rbbridge-src.zip ZIP=$out_server"
elif build_server_binaries "$BUILD_DIR"; then
    # nur die 4 Binaries ins Zip (keine .pdb/.lib-Artefakte von zig)
    rm -f "$BUILD_DIR"/*.pdb "$BUILD_DIR"/*.lib "$BUILD_DIR"/*.o
    out_server="$OUT_DIR/rbb-rbbridge.zip"
    zip_content_root "$BUILD_DIR" "$out_server"
    rm -rf "$BUILD_DIR"
    echo "NAME=rbb-rbbridge.zip ZIP=$out_server"
else
    echo "FEHLER: server-Build (TOOLCHAIN=$TOOLCHAIN) fehlgeschlagen." >&2
    exit 1
fi

# Einzel-Mod (Issue #16): der fusionierte Mod client-mod/ als rbbattle.zip —
# Primär-Download. Content-Root = client-mod/ (lua/ + <GUID>.manifest + README.md an
# der Zip-Wurzel), genau wie er nach <game>/mods/rbbattle/ gehoert.
SRCMOD="$ROOT/client-mod"
outmod="$OUT_DIR/rbbattle.zip"
zip_content_root "$SRCMOD" "$outmod"
echo "NAME=rbbattle.zip ZIP=$outmod"

# Einzel-Mod versioniert (Issue #119): die Mod-Version kommt aus den
# Mod-Metadaten (client-mod/<GUID>.manifest, Feld `version`), nicht aus Git-SHA oder
# Datum. rbbattle-v<version>.zip ist der kanonische, versionierte Download;
# rbbattle.zip bleibt der stabile Alias für die Deploy-Kette (md5-Parität +
# Server-Extraktion), damit Download-Link und Server-Stand nie auseinanderlaufen.
MOD_VERSION="$(bash "$ROOT/scripts/mod_version.sh")"
outmod_ver="$OUT_DIR/rbbattle-v$MOD_VERSION.zip"
cp "$outmod" "$outmod_ver"
echo "NAME=rbbattle-v$MOD_VERSION.zip ZIP=$outmod_ver"
echo "MOD_VERSION=$MOD_VERSION"

n_zip=$(find "$OUT_DIR" -maxdepth 1 -name 'rbb-*.zip' | wc -l)
echo "[package] fertig: ${n_zip} Komponente-Zip(s) + rbbattle.zip in $OUT_DIR"
