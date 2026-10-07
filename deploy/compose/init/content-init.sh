#!/bin/sh
# content-init (Issue #1093): Game-Content + private PDB per HTTP von planet.
#
# Idempotent (Issue #1095): geholt wird nur, wenn das Server-Binary bzw. die PDB
# fehlt ODER RBB_CONTENT_FORCE=true. Danach kontaktiert ein Lauf planet nicht
# mehr (offline nach Erst-Setup).
set -eu

game="${RBB_GAME_DIR:-/game}"
dl="${RBB_DOWNLOAD_DIR:-/downloads}"
base="${RBB_CONTENT_BASE:?RBB_CONTENT_BASE ist nicht gesetzt}"
archive="${RBB_CONTENT_ARCHIVE:-riftbreaker-game-content.tar.gz}"
pdb="${RBB_CONTENT_PDB:-riftbreaker_dll_win_release.pdb}"
sums="${RBB_CONTENT_CHECKSUMS:-SHA256SUMS}"
bin="${RBB_SERVER_BIN:-bin/DedicatedServer.exe}"
force="${RBB_CONTENT_FORCE:-false}"

mkdir -p "$game" "$game/bin" "$dl"

# verify <file> <name>: prüft den Datei-Hash gegen die passende Zeile in
# $dl/$sums (SHA256SUMS-Format "<hash>  <name>").
verify() {
  file="$1"
  name="$2"
  want="$(awk -v n="$name" '$2 == n || $2 == "*" n { print $1; exit }' "$dl/$sums")"
  if [ -z "$want" ]; then
    echo "content-init: '$name' steht nicht in $sums" >&2
    exit 1
  fi
  echo "$want  $file" | sha256sum -c - >/dev/null
}

fetch_sums() {
  curl -fsSL "$base/$sums" -o "$dl/$sums"
}

if [ "$force" = "true" ] || [ ! -e "$game/$bin" ]; then
  echo "content-init: hole Game-Content-Bundle"
  curl -fsSL "$base/$archive" -o "$dl/$archive"
  fetch_sums
  verify "$dl/$archive" "$archive"
  tar -xzf "$dl/$archive" -C "$game"
fi

if [ "$force" = "true" ] || [ ! -e "$game/bin/$pdb" ]; then
  echo "content-init: hole private PDB"
  fetch_sums
  curl -fsSL "$base/$pdb" -o "$game/bin/$pdb"
  verify "$game/bin/$pdb" "$pdb"
fi

if [ ! -e "$game/$bin" ]; then
  echo "content-init: FEHLER — $game/$bin fehlt nach dem Lauf" >&2
  exit 1
fi

echo "content-init: OK"
