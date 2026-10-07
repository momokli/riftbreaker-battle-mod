#!/usr/bin/env bash
# ============================================================
# tests/shell/package-determinism.test.sh
# ------------------------------------------------------------
# Planetfreier Red/Green-Test fuer die Reproduzierbarkeit von
# scripts/package.sh (Issue #1101): bei gleichem Quellstand muss der
# Battle-Mod-Zip (dist/rbbattle.zip) byte-identisch sein -> stabiler md5.
#
# Das traegt die md5-Paritaet (Zip == planet) und den Marker-No-Op im
# Mod-Rollout (deploy/compose/mod-build/rollout.sh).
#
# Kein Docker/Netz/Vault. Laeuft in CI (lint.yml) und lokal:
#   tests/shell/package-determinism.test.sh
# ============================================================
set -euo pipefail
cd "$(dirname "$0")/../.."

md5_of() {
    if command -v md5sum >/dev/null 2>&1; then
        md5sum "$1" | awk '{print $1}'
    elif command -v md5 >/dev/null 2>&1; then
        md5 -q "$1"
    else
        echo "::error::weder md5sum noch md5 verfuegbar" >&2
        exit 1
    fi
}

[ -f scripts/package.sh ] || { echo "::error::scripts/package.sh fehlt" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "::error::python3 wird gebraucht" >&2; exit 1; }

bash scripts/package.sh >/dev/null
[ -f dist/rbbattle.zip ] || { echo "::error::dist/rbbattle.zip fehlt nach package.sh" >&2; exit 1; }
m1="$(md5_of dist/rbbattle.zip)"

# Zweiter Lauf auf UNVERAENDERTEM Quellstand.
bash scripts/package.sh >/dev/null
m2="$(md5_of dist/rbbattle.zip)"

if [ "$m1" != "$m2" ]; then
    echo "::error::package.sh ist NICHT deterministisch: rbbattle.zip md5 $m1 != $m2 (Issue #1101)" >&2
    exit 1
fi

echo "OK: rbbattle.zip reproduzierbar (md5=$m1)"
