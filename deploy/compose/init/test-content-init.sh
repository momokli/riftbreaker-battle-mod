#!/usr/bin/env bash
# ============================================================
# deploy/compose/init/test-content-init.sh (Issue #1102)
# ------------------------------------------------------------
# Hermetischer Selbsttest fuer den Compose-Init-Container content-init
# (deploy/compose/init/content-init.sh): Game-Content + PDB per HTTP, IDEMPOTENT
# (Issue #1095-Semantik) — nur beim Erstlauf bzw. mit RBB_CONTENT_FORCE=true.
#
# Kein Netz (curl file://-Quelle), kein Docker, kein Vault.
#   * 1) Erstlauf        -> Bundle + PDB entpackt/kopiert
#   * 2) warm + Quelle weg -> No-Op, gruen (kein Re-Download)
#   * 3) force + Quelle weg -> ROT (force laedt neu)
#
# Laeuft in deploy-check-local und lokal:  bash deploy/compose/init/test-content-init.sh
# ============================================================
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
script="$here/content-init.sh"
fail() { echo "::error::$1"; exit 1; }

[ -f "$script" ] || fail "content-init.sh nicht gefunden: $script"
command -v curl >/dev/null 2>&1 || fail "curl wird gebraucht"

base="$(mktemp -d)"
trap 'rm -rf "$base"' EXIT

src="$base/src"; game="$base/game"; dl="$base/dl"
mkdir -p "$src" "$game"

# Bundle (bin/DedicatedServer.exe) + PDB + SHA256SUMS.
mkdir -p "$base/bundle/bin"
printf 'binary' > "$base/bundle/bin/DedicatedServer.exe"
tar -czf "$src/riftbreaker-game-content.tar.gz" -C "$base/bundle" bin
printf 'pdbbytes' > "$src/riftbreaker_dll_win_release.pdb"
( cd "$src" && sha256sum riftbreaker-game-content.tar.gz riftbreaker_dll_win_release.pdb > SHA256SUMS )

run() {  # [extra VAR=val ...]
    env RBB_CONTENT_BASE="file://$src" RBB_GAME_DIR="$game" RBB_DOWNLOAD_DIR="$dl" "$@" sh "$script"
}

echo "== 1) Erstlauf: Bundle + PDB kommen an =="
run >/dev/null || fail "Erstlauf fehlgeschlagen"
[ -f "$game/bin/DedicatedServer.exe" ] || fail "Server-Binary fehlt nach Erstlauf"
[ -f "$game/bin/riftbreaker_dll_win_release.pdb" ] || fail "PDB fehlt nach Erstlauf"
echo "   OK"

echo "== 2) warm + Quelle weg -> No-Op, gruen (offline) =="
rm -rf "$src"
run >/dev/null || fail "Zweiter Lauf (warm, Quelle entfernt) ist nicht gruen — kein Idempotenz-Guard"
echo "   OK (kein Download mehr)"

echo "== 3) force=true + Quelle weg -> ROT (force laedt neu) =="
if run RBB_CONTENT_FORCE=true >/dev/null 2>&1; then
    fail "force=true ohne erreichbare Quelle war gruen — force laedt nicht neu"
fi
echo "   OK (force versucht einen Download)"

echo "OK: content-init idempotent (Erstlauf laedt, warm offline gruen, force laedt neu)."
