#!/usr/bin/env bash
# ============================================================================
# build_rbbridge_tools.sh — baut die Windows-x64-Tools des Trainer-I/O-Kanals
# (Baustein 04, Issue #265) in ein Staging-Verzeichnis.
#
# Baut aus bausteine/04-trainer-io/ (kanonische Quelle):
#   rbbridge.dll            Injection-DLL; Named-Pipe-Server \\.\pipe\rbbattle
#   injector.exe            Remote-LoadLibrary-Injector (x64)
#   rbbridge_standalone.exe Pipe-Server ohne Injection (-DRBBRIDGE_STANDALONE)
#   pipe_bridge.exe         HTTP(9001)->Pipe-Bridge (Win32 + ws2_32)
#
# Toolchain: x86_64-w64-mingw32-gcc (bevorzugt) oder zig cc (Fallback; Zig
# bringt die mingw-Libc mit). Fehlt beides -> harter Fehler (Exit 1), damit ein
# Deploy nicht still ohne Tools durchlaeuft.
#
# Aufruf:  scripts/build_rbbridge_tools.sh [<outdir>]
#            <outdir> Default: dist/rbtools (relativ zum Repo-Root)
# Ausgabe:  je Binary eine Zeile "NAME=<datei> PATH=<absoluter-pfad>"
#           (maschinenlesbar, z. B. fuer Ansible/Rollen-Staging)
# ============================================================================
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$PWD"
SRC04="$ROOT/bausteine/04-trainer-io"

OUT_DIR="${1:-$ROOT/dist/rbtools}"
mkdir -p "$OUT_DIR"
OUT_DIR="$(cd "$OUT_DIR" && pwd)"

# --- Toolchain bestimmen (mingw bevorzugt, zig als Fallback) ---------------
TOOLCHAIN="none"
if command -v x86_64-w64-mingw32-gcc >/dev/null 2>&1; then
    TOOLCHAIN="mingw"
elif command -v zig >/dev/null 2>&1 || [ -n "${ZIG:-}" ]; then
    TOOLCHAIN="zig"
fi

if [ "$TOOLCHAIN" = "none" ]; then
    echo "FEHLER: weder x86_64-w64-mingw32-gcc noch zig verfuegbar —" >&2
    echo "        der Windows-x64-Build der rbtools ist so nicht moeglich." >&2
    echo "        Installation (mingw-w64) oder ZIG=/pfad/zu/zig setzen." >&2
    exit 1
fi

echo "[build_rbbridge_tools] Toolchain: $TOOLCHAIN -> $OUT_DIR"

# cc <args...>: ruft den gewaehlten Cross-Compiler (mingw direkt, zig via cc).
cc() {
    if [ "$TOOLCHAIN" = "mingw" ]; then
        x86_64-w64-mingw32-gcc "$@"
    else
        "${ZIG:-zig}" cc -target x86_64-windows-gnu "$@"
    fi
}

# --- Quellen pruefen --------------------------------------------------------
RBBRIDGE_SRC="$SRC04/rbbridge/rbbridge.c"
INJECTOR_SRC="$SRC04/injector/injector.c"
BRIDGE_SRC="$SRC04/bridge/pipe_bridge.c"
for f in "$RBBRIDGE_SRC" "$INJECTOR_SRC" "$BRIDGE_SRC"; do
    if [ ! -f "$f" ]; then
        echo "FEHLER: Quelle nicht gefunden: $f" >&2
        exit 1
    fi
done

# --- Bauen ------------------------------------------------------------------
(cd "$OUT_DIR" \
    && cc -O2 -Wall -Wextra -shared -o rbbridge.dll "$RBBRIDGE_SRC" \
    && cc -O2 -Wall -Wextra -o injector.exe "$INJECTOR_SRC" \
    && cc -O2 -Wall -Wextra -DRBBRIDGE_STANDALONE -o rbbridge_standalone.exe "$RBBRIDGE_SRC" \
    && cc -O2 -Wall -Wextra -o pipe_bridge.exe "$BRIDGE_SRC" -lws2_32)

# Zig-Artefakte (.pdb/.lib/.o) entfernen, falls vorhanden.
rm -f "$OUT_DIR"/*.pdb "$OUT_DIR"/*.lib "$OUT_DIR"/*.o

# --- Alle vier Dateien als vorhanden verifizieren (harter Fehler sonst) -----
rc=0
for name in rbbridge.dll injector.exe rbbridge_standalone.exe pipe_bridge.exe; do
    path="$OUT_DIR/$name"
    if [ ! -s "$path" ]; then
        echo "FEHLER: Build-Artefakt fehlt/leer: $path" >&2
        rc=1
        continue
    fi
    echo "NAME=$name PATH=$path"
done

if [ "$rc" -ne 0 ]; then
    echo "FEHLER: nicht alle rbtools wurden gebaut." >&2
    exit 1
fi

echo "[build_rbbridge_tools] fertig: 4 Binaries in $OUT_DIR"
