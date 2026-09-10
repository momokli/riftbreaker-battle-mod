#!/usr/bin/env bash
#
# test-wait-menu.sh — Trockenlauf-Tests für wait-menu.sh (Fake import/convert).
#
# Ersetzt `import`/`convert` durch Fake-Binaries und prüft die Timeout-/Erfolgs-
# Logik von wait-menu.sh OHNE Xvfb, Spiel-Assets oder GPU. Kein Game-Content
# nötig — die Render-Heuristik wird über FAKE_STD (Standardabweichung) gesteuert.
#
# Nutzung:
#   tools/headless-client/test-wait-menu.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WAIT_MENU="$SCRIPT_DIR/wait-menu.sh"
WORK="$(mktemp -d)"
BIN="$WORK/bin"
mkdir -p "$BIN"
trap 'rm -rf "$WORK"' EXIT

# --- Fake `import`: erzeugt eine nicht-leere Datei (Inhalt egal) ---------------
cat > "$BIN/import" <<'EOF'
#!/usr/bin/env bash
# import -window root <out>  → erzeuge eine nicht-leere Datei, damit der
# "-s"-Check in wait-menu.sh greift und convert den FAKE_STD-Wert liefern kann.
out=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        -window) shift 2 ;;
        *) out="$1"; shift ;;
    esac
done
printf 'x' > "$out"
EOF

# --- Fake `convert`: liefert die Standardabweichung aus FAKE_STD ---------------
cat > "$BIN/convert" <<'EOF'
#!/usr/bin/env bash
echo "${FAKE_STD:-0}"
EOF

chmod +x "$BIN/import" "$BIN/convert"
export IMPORT_BIN="$BIN/import" CONVERT_BIN="$BIN/convert"

pass=0; fail=0
RC=0

run() { RC=0; "$@" || RC=$?; }              # Kommando ausführen, rc merken (set -e-sicher)
ok()  { echo "PASS  $1"; pass=$((pass+1)); }
bad() { echo "FAIL  $1"; fail=$((fail+1)); }

check_rc() {  # desc expected
    if [[ "$RC" -eq "$2" ]]; then ok "$1"; else bad "$1 (rc=$RC)"; fi
}

check_exists() {  # desc path
    if [[ -s "$2" ]]; then ok "$1"; else bad "$1 ($2 fehlt/leer)"; fi
}

# --- 1. Sofort gerendert (std=0.5) → exit 0 + Screenshot ----------------------
FAKE_STD=0.5 WAIT_TIMEOUT=5 RENDER_POLL=1 \
    run "$WAIT_MENU" "$WORK/menu1.png"
check_rc "sofort gerenderter Frame (rc=0)" 0
check_exists "Screenshot angelegt" "$WORK/menu1.png"

# --- 2. Schwarz-Frame (std=0) → Timeout, exit 1, letzter Frame gesichert ------
FAKE_STD=0 WAIT_TIMEOUT=2 RENDER_POLL=1 \
    run "$WAIT_MENU" "$WORK/menu2.png"
check_rc "schwarzer Frame → Timeout (rc=1)" 1
check_exists "letzter Frame gesichert" "$WORK/menu2.png"

# --- 3. std knapp unter Schwelle → exit 1 -------------------------------------
FAKE_STD=0.01 WAIT_TIMEOUT=2 RENDER_POLL=1 RENDER_MIN_STD=0.05 \
    run "$WAIT_MENU" "$WORK/menu3.png"
check_rc "std unter Schwelle (rc=1)" 1

# --- Ergebnis -----------------------------------------------------------------
echo
echo "Ergebnis: $pass bestanden, $fail fehlgeschlagen."
[[ "$fail" -eq 0 ]]
