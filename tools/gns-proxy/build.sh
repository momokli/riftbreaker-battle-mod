#!/usr/bin/env bash
# build.sh — baut gns_probe.exe mit mingw (Spike #831, E1/E2).
#
# Kein Import-Library-Schritt: der Probe laedt die mitgelieferte
# GameNetworkingSockets.dll zur Laufzeit (LoadLibraryA + GetProcAddress) und
# braucht daher nur die Datentypen aus include/steam/steamnetworkingtypes.h.
set -euo pipefail
cd "$(dirname "$0")"

CXX="${CXX:-x86_64-w64-mingw32-g++}"
# -static*: mingw-g++-EXEs brauchen sonst libstdc++-6.dll / libgcc_s_seh-1.dll aus dem
# Build-Environment — im Wine-Prefix liegen die nicht (Symptom: Wine beendet sich
# mit Exit 53 und ohne Ausgabe).
"$CXX" -O2 -Wall -Wextra -std=c++17 -Wno-cast-function-type -Iinclude \
  -static -static-libgcc -static-libstdc++ -o gns_probe.exe gns_probe.cpp

ls -la gns_probe.exe
