#!/usr/bin/env bash
# ============================================================================
# build_ref.sh — einheitliche Build-Identitaet (ref) fuer ALLE Komponenten.
#
# Einzige Quelle der Wahrheit: Umgebungsvariable RBB_BUILD_REF (CI setzt sie
# auf Tag oder Commit-SHA, Issue #499). Fallback: aktueller Commit-SHA via
# `git rev-parse HEAD`.
#
# Verwendung:
#   REF="$(bash scripts/build_ref.sh)"
#
# Ausgabe: der ref auf stdout (ohne Zusatz), z. B. "v0.38.0" oder "<40-char-sha>".
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -n "${RBB_BUILD_REF:-}" ]; then
    printf '%s\n' "$RBB_BUILD_REF"
else
    git -c safe.directory='*' rev-parse HEAD
fi
