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
# Abhaengigkeit: python3 (stdlib — Pflicht, wird ohnehin fuer
# gen_cockpit_html.py/bake_mod_ref.py gebraucht) fuer den deterministischen Zip.
# Optional x86_64-w64-mingw32-gcc oder zig (fuer den server-Binary-Build).
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
OUT_DIR="$ROOT/dist"
mkdir -p "$OUT_DIR"

# Build-Identitaet (Issue #499): ref (Commit/Tag) fuer alle Binaries + Mod.
REF="$(bash "$ROOT/scripts/build_ref.sh")"
REF_DEF="-DRBBRIDGE_REF=\"${REF}\""

# zip_content_root <srcdir> <outzip>: packt den INHALT von <srcdir> mit
# <srcdir> als Content-Root (der Ordner selbst kommt NICHT ins Zip),
# .DS_Store wird rausgefiltert.
#
# Deterministisch (Issue #1101): sortierte Eintraege, fester Zeitstempel
# (1980-01-01) und feste Rechte 0644 -> bei gleichem Quellstand byte-identisches
# Zip (stabiler md5). Das traegt die md5-Paritaet (Zip == planet) und den
# Marker-No-Op im Mod-Rollout (deploy/compose/mod-build/rollout.sh).
# Python ist ohnehin Pflicht-Abhaengigkeit (gen_cockpit_html.py/bake_mod_ref.py).
zip_content_root() {
    local src="$1" out="$2"
    rm -f "$out"
    python3 - "$src" "$out" <<'PYEOF'
import os, sys, zipfile

src, out = sys.argv[1], sys.argv[2]
entries = []
for root, _dirs, files in os.walk(src):
    for name in files:
        if name == ".DS_Store":
            continue
        entries.append(os.path.relpath(os.path.join(root, name), src))
entries.sort()
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for rel in entries:
        with open(os.path.join(src, rel), "rb") as fh:
            data = fh.read()
        info = zipfile.ZipInfo(rel, date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        z.writestr(info, data)
PYEOF
    [ -s "$out" ] || { echo "FEHLER: Zip-Bau fehlgeschlagen ($out)." >&2; exit 1; }
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
        # `-g` (DWARF) nur auf rbbridge.dll: collector-seitige Crash-Symbolik (#559).
        (cd "$bd" \
            && x86_64-w64-mingw32-gcc -O2 -g -Wall -Wextra "$REF_DEF" -shared -o rbbridge.dll "$SERVER_SRC/dll/rbbridge.c" \
            && x86_64-w64-mingw32-gcc -O2 -Wall -Wextra "$REF_DEF" -o injector.exe "$SERVER_SRC/injector/injector.c" \
            && x86_64-w64-mingw32-gcc -O2 -Wall -Wextra "$REF_DEF" -DRBBRIDGE_STANDALONE -o rbbridge_standalone.exe "$SERVER_SRC/dll/rbbridge.c" \
            && x86_64-w64-mingw32-gcc -O2 -Wall -Wextra "$REF_DEF" -o pipe_bridge.exe "$SERVER_SRC/pipe-bridge/pipe_bridge.c" -lws2_32)
    else
        local zigc="${ZIG:-zig}"
        echo "[package] server: kein mingw-gcc, aber zig -> Build (zig cc, x86_64-windows-gnu)"
        (cd "$bd" \
            && "$zigc" cc -target x86_64-windows-gnu -O2 -g -Wall -Wextra "$REF_DEF" -shared -o rbbridge.dll "$SERVER_SRC/dll/rbbridge.c" \
            && "$zigc" cc -target x86_64-windows-gnu -O2 -Wall -Wextra "$REF_DEF" -o injector.exe "$SERVER_SRC/injector/injector.c" \
            && "$zigc" cc -target x86_64-windows-gnu -O2 -Wall -Wextra "$REF_DEF" -DRBBRIDGE_STANDALONE -o rbbridge_standalone.exe "$SERVER_SRC/dll/rbbridge.c" \
            && "$zigc" cc -target x86_64-windows-gnu -O2 -Wall -Wextra "$REF_DEF" -o pipe_bridge.exe "$SERVER_SRC/pipe-bridge/pipe_bridge.c" -lws2_32)
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

# Build-Identitaet (Issue #494): ref in Lua UND Manifest backen. Temp-Kopie,
# damit die git-getrackte Quelle unveraendert bleibt (RBB_BUILD_REF -> echter ref).
MOD_TMP="$OUT_DIR/.mod-tmp"
rm -rf "$MOD_TMP"
mkdir -p "$MOD_TMP"
cp -R "$SRCMOD"/. "$MOD_TMP"/
python3 "$ROOT/scripts/bake_mod_ref.py" "$MOD_TMP" "$REF"

zip_content_root "$MOD_TMP" "$outmod"
rm -rf "$MOD_TMP"
echo "NAME=rbbattle.zip ZIP=$outmod"

# Einzel-Mod versioniert (Issue #119): die Mod-Version ist jetzt die
# Build-Identitaet (SHA bzw. Tag, Issue #494) — identisch zu dem in Lua + Manifest
# gebackenen Ref. rbbattle-v<ref>.zip ist der kanonische, versionierte Download;
# rbbattle.zip bleibt der stabile Alias für die Deploy-Kette (md5-Parität +
# Server-Extraktion), damit Download-Link und Server-Stand nie auseinanderlaufen.
MOD_VERSION="$(bash "$ROOT/scripts/mod_version.sh")"
outmod_ver="$OUT_DIR/rbbattle-v$MOD_VERSION.zip"
cp "$outmod" "$outmod_ver"
echo "NAME=rbbattle-v$MOD_VERSION.zip ZIP=$outmod_ver"
echo "MOD_VERSION=$MOD_VERSION"

n_zip=$(find "$OUT_DIR" -maxdepth 1 -name 'rbb-*.zip' | wc -l)
echo "[package] fertig: ${n_zip} Komponente-Zip(s) + rbbattle.zip in $OUT_DIR"
