#!/usr/bin/env bash
# Entry point for the containerized crash-collector (Issue #1093).
#
# Mirrors the RB_CRASH_* environment of the former systemd unit
# `rbmods-crash-collector` but
# with container paths as defaults. Everything is overridable via the
# environment (compose `environment:` / `.env`), so the operator keeps the exact
# knobs of the role.
#
# Scripts are baked into /opt/crash from `deploy/crash-collector/`; the
# game DLL/PDB and rbbridge.dll are mounted read-only (paths below). The observed
# game container is reached via the mounted Docker socket — no direct log/volume
# access needed (docker logs/cp/exec).
set -euo pipefail

# --- paths of the baked scripts (deploy/crash-collector -> /opt/crash) -------
export RB_CRASH_MINIDUMP_PY="${RB_CRASH_MINIDUMP_PY:-/opt/crash/minidump_meta.py}"
export RB_CRASH_SYMBOLIZE_BIN="${RB_CRASH_SYMBOLIZE_BIN:-/opt/crash/crash_symbolize.sh}"
export RB_CRASH_SYMBOLIZE_TOOL="${RB_CRASH_SYMBOLIZE_TOOL:-/opt/crash/symbolize.py}"
export RB_CRASH_LLVM_SYMBOLIZER="${RB_CRASH_LLVM_SYMBOLIZER:-/usr/lib/llvm-18/bin/llvm-symbolizer}"
export RB_CRASH_PYTHON="${RB_CRASH_PYTHON:-python3}"
export RB_CRASH_DOCKER="${RB_CRASH_DOCKER:-docker}"

# --- observed container + bundle destination ----------------------------------
export RB_CRASH_CONTAINER="${RB_CRASH_CONTAINER:-rbb-dedicated}"
export RB_CRASH_ENV="${RB_CRASH_ENV:-dev}"
export RB_CRASH_REF="${RB_CRASH_REF:-${RBB_REF:-}}"
export RB_CRASH_DIR="${RB_CRASH_DIR:-/crashes}"
# crash_info path INSIDE the observed container (Wine prefix volume rb-wine).
export RB_CRASH_CRASHINFO="${RB_CRASH_CRASHINFO:-/data/.wine/drive_c/users/steamuser/Documents/The Riftbreaker/crash_info}"

# --- behavior knobs (role defaults) -------------------------------------------
export RB_CRASH_CONTEXT_LINES="${RB_CRASH_CONTEXT_LINES:-200}"
export RB_CRASH_RETENTION="${RB_CRASH_RETENTION:-20}"
export RB_CRASH_WAIT_SECS="${RB_CRASH_WAIT_SECS:-30}"
export RB_CRASH_RETRY_SLEEP="${RB_CRASH_RETRY_SLEEP:-5}"
export RB_CRASH_PRUNE_WINE="${RB_CRASH_PRUNE_WINE:-1}"

# --- symbolic modules (bind-mounted read-only) --------------------------------
# Game DLL/PDB -> rb-game volume (/game); rbbridge.dll -> rb-rbtools (/rbtools).
export RB_CRASH_DLL="${RB_CRASH_DLL:-/game/bin/riftbreaker_dll_win_release.dll}"
export RB_CRASH_PDB="${RB_CRASH_PDB:-/game/bin/riftbreaker_dll_win_release.pdb}"
export RB_CRASH_RBBRIDGE_DLL="${RB_CRASH_RBBRIDGE_DLL:-/rbtools/rbbridge.dll}"

# --- symbolization master switch (Issue #480) ---------------------------------
export RB_CRASH_SYMBOLIZE="${RB_CRASH_SYMBOLIZE:-1}"
export RB_CRASH_SYMBOLIZE_TIMEOUT="${RB_CRASH_SYMBOLIZE_TIMEOUT:-60}"
export RB_CRASH_STACK_SCAN="${RB_CRASH_STACK_SCAN:-1}"

if [ ! -f /opt/crash/crash_collector.sh ]; then
  echo "crash-collector: /opt/crash/crash_collector.sh fehlt — Image unvollstaendig (Bake fehlt)" >&2
  exit 2
fi

mkdir -p "$RB_CRASH_DIR" 2>/dev/null || true

# "$@" is forwarded: `--once` = read the stream to EOF and exit.
exec bash /opt/crash/crash_collector.sh "$@"
