#!/usr/bin/env bash
# build.sh — baut gns_probe.exe mit mingw (Spike #831 / Issue #843).
#
#   bash build.sh [OUT_DIR]      # Default: Verzeichnis dieses Skripts
#
# Wird sowohl manuell (planet/mac) als auch von der Deploy-Rolle `gns-relay`
# benutzt — deshalb EINE Quelle fuer die Build-Kommandos. Die Rolle ruft es per
# `bash` auf (nicht ueber das Ansible-`shell:`-Modul, das unter /bin/sh = dash
# laeuft und `set -o pipefail` nicht kennt).
set -euo pipefail
cd "$(dirname "$0")"

OUT_DIR="${1:-.}"
mkdir -p "$OUT_DIR"
OUT="${OUT_DIR}/gns_probe.exe"

# Toolchain: MinGW-w64 bevorzugt (planet), zig als Fallback.
# `-Wl,--no-insert-timestamp` haelt Builds bei gleichem Quellstand byte-identisch
# — sonst springt die Change-Detection der Deploy-Rolle bei jedem Lauf an und der
# Container wird sinnlos neu erstellt.
if command -v x86_64-w64-mingw32-g++ >/dev/null 2>&1; then
  CXX=x86_64-w64-mingw32-g++
  LD_REPRO="-Wl,--no-insert-timestamp"
elif command -v zig >/dev/null 2>&1; then
  CXX="zig c++ -target x86_64-windows-gnu"
  LD_REPRO=""
else
  echo "FEHLER: weder x86_64-w64-mingw32-g++ noch zig gefunden — gns_probe.exe kann nicht gebaut werden." >&2
  exit 1
fi

# Kein Import-Library-Schritt: der Probe laedt die mitgelieferte
# GameNetworkingSockets.dll zur Laufzeit (LoadLibraryA + GetProcAddress) und
# braucht daher nur die Datentypen aus include/steam/.
# -static*: mingw-g++-EXEs brauchen sonst libstdc++-6.dll / libgcc_s_seh-1.dll
# aus dem Build-Environment — die liegen im Wine-Prefix nicht (Symptom: Wine
# beendet sich mit Exit 53 und ohne Ausgabe).
# shellcheck disable=SC2086
# -lws2_32: Winsock fuer den HTTP-Listener der Steuer-API/Web-UI (Issue #857).
$CXX -O2 -Wall -Wextra -std=c++17 -Wno-cast-function-type $LD_REPRO \
  -Iinclude -static -static-libgcc -static-libstdc++ -o "$OUT" gns_probe.cpp \
  -lws2_32

ls -la "$OUT"
