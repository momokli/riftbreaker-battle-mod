#!/usr/bin/env bash
# ============================================================================
# mod_version.sh — liefert die Mod-Version.
#
# Seit der Vereinheitlichung (Issue #494) ist die Mod-Version identisch mit der
# Build-Identitaet: SHA (dev) bzw. Tag (prod). Einzige Quelle ist damit
# scripts/build_ref.sh (RBB_BUILD_REF bzw. Git-Tag/Commit). Das Mod-Manifest
# traegt denselben Wert als Platzhalter `version "RBB_BUILD_REF"` und wird beim
# Packen (scripts/package.sh) substituiert — nicht mehr von Hand gepflegt.
#
# Verwendung:
#   scripts/mod_version.sh          -> <sha> bzw. <tag>
#   VERSION="$(scripts/mod_version.sh)"
# ============================================================================
set -euo pipefail
exec bash "$(dirname "$0")/build_ref.sh"
