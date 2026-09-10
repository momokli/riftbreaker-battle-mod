#!/usr/bin/env bash
#
# test-nav-lobby.sh — Trockenlauf-Tests für nav-lobby.sh (Fake xdo-nav/import/convert).
#
# Ersetzt `xdo-nav.sh` durch ein Fake-Binary (protokolliert Aktionen), sowie
# `import`/`convert` durch Fakes (liefern FAKE_STD als Frame-Standardabweichung)
# und prüft Plan-Parsing, Server-Auflösung, Connect-Ablauf und die Render-Check-
# Abbruchlogik — OHNE Xvfb, xdotool oder Spiel-Assets. Kein Game-Content nötig.
#
# Nutzung:
#   tools/headless-client/test-nav-lobby.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAV_LOBBY="$SCRIPT_DIR/nav-lobby.sh"
WORK="$(mktemp -d)"
BIN="$WORK/bin"
mkdir -p "$BIN"
trap 'rm -rf "$WORK"' EXIT

# --- Fake `xdo-nav.sh`: protokolliert Sub-Kommando + Argumente -----------------
cat > "$BIN/xdo-nav.sh" <<'EOF'
#!/usr/bin/env bash
{
    printf '%s' "$1"
    shift
    for a in "$@"; do printf ' %s' "$a"; done
    printf '\n'
} >> "${FAKE_XDO_LOG:?FAKE_XDO_LOG unset}"
[[ "${FAKE_XDO_FAIL:-0}" == "1" ]] && exit 1
exit 0
EOF

# --- Fake `import`: erzeugt nicht-leere Datei (wie test-wait-menu.sh) ----------
cat > "$BIN/import" <<'EOF'
#!/usr/bin/env bash
out=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        -window) shift 2 ;;
        *) out="$1"; shift ;;
    esac
done
printf 'x' > "$out"
EOF

# --- Fake `convert`: liefert Standardabweichung aus FAKE_STD -------------------
cat > "$BIN/convert" <<'EOF'
#!/usr/bin/env bash
echo "${FAKE_STD:-0}"
EOF

chmod +x "$BIN/xdo-nav.sh" "$BIN/import" "$BIN/convert"
export XDO_NAV="$BIN/xdo-nav.sh" IMPORT_BIN="$BIN/import" CONVERT_BIN="$BIN/convert"
export FAKE_XDO_LOG="$WORK/xdo.log"

pass=0; fail=0
RC=0

run() { RC=0; "$@" || RC=$?; }
ok()  { echo "PASS  $1"; pass=$((pass+1)); }
bad() { echo "FAIL  $1"; fail=$((fail+1)); }
check_rc() { if [[ "$RC" -eq "$2" ]]; then ok "$1"; else bad "$1 (rc=$RC)"; fi; }
check_exists() { if [[ -s "$2" ]]; then ok "$1"; else bad "$1 ($2 fehlt/leer)"; fi; }

# --- 1. --dry-run: Plan parsen, keine Aktionen ausführen ----------------------
: > "$FAKE_XDO_LOG"
run "$NAV_LOBBY" --dry-run
check_rc "dry-run (rc=0)" 0
if [[ ! -s "$FAKE_XDO_LOG" ]]; then ok "dry-run: keine xdo-Aufrufe"; else bad "dry-run: xdo aufgerufen"; fi

# --- 2. connect löst Server auf + tippt Adresse + Return ----------------------
: > "$FAKE_XDO_LOG"
printf 'connect rb-winetest\n' > "$WORK/plan2"
NAV_RENDER_CHECK=0 run "$NAV_LOBBY" --plan "$WORK/plan2"
check_rc "connect rb-winetest (rc=0)" 0
if grep -q '^type 65.21.27.234:6322$' "$FAKE_XDO_LOG"; then ok "connect tippt aufgelöste Adresse"; else bad "connect tippt Adresse ($(cat "$FAKE_XDO_LOG"))"; fi
if grep -q '^key Return$' "$FAKE_XDO_LOG"; then ok "connect drückt Return"; else bad "connect drückt Return"; fi

# --- 3. unbekannte Aktion → Abbruch -------------------------------------------
: > "$FAKE_XDO_LOG"
printf 'bogus arg\n' > "$WORK/plan3"
run "$NAV_LOBBY" --plan "$WORK/plan3"
check_rc "unbekannte Aktion (rc!=0)" 1

# --- 4. nicht auflösbarer Server → Abbruch ------------------------------------
: > "$FAKE_XDO_LOG"
printf 'connect unknownserver\n' > "$WORK/plan4"
run "$NAV_LOBBY" --plan "$WORK/plan4"
check_rc "unbekannter Server (rc!=0)" 1

# --- 5. Render-Check: schwarzer Frame nach Eingabe → Abbruch ------------------
: > "$FAKE_XDO_LOG"
printf 'click 960 540\n' > "$WORK/plan5"
FAKE_STD=0 run "$NAV_LOBBY" --plan "$WORK/plan5"
check_rc "schwarzer Frame nach click (rc!=0)" 1

# --- 6. Happy Path: wait-menu → connect → shot --------------------------------
: > "$FAKE_XDO_LOG"
cat > "$WORK/plan6" <<EOF
# Kommentar + Leerzeile

wait-menu $WORK/menu.png
click 960 540
wait 1
connect rb-winetest
shot $WORK/connected.png
EOF
FAKE_STD=0.5 run "$NAV_LOBBY" --plan "$WORK/plan6"
check_rc "happy path (rc=0)" 0
check_exists "wait-menu legt Beweis-Screenshot ab" "$WORK/menu.png"
if grep -q '^click 960 540$' "$FAKE_XDO_LOG"; then ok "click ausgeführt"; else bad "click ausgeführt"; fi

# --- 7. verify: erfolgreiches Kommando läuft durch ----------------------------
: > "$FAKE_XDO_LOG"
printf 'verify test 1 -eq 1\n' > "$WORK/plan7"
run "$NAV_LOBBY" --plan "$WORK/plan7"
check_rc "verify ok (rc=0)" 0

# --- 8. connect ohne Argument nutzt --server/NAV_SERVER ----------------------
: > "$FAKE_XDO_LOG"
printf 'connect\n' > "$WORK/plan8"
NAV_RENDER_CHECK=0 NAV_SERVERS='myserver=10.0.0.1:9999' \
    run "$NAV_LOBBY" --plan "$WORK/plan8" --server myserver
check_rc "connect ohne Argument (rc=0)" 0
if grep -q '^type 10.0.0.1:9999$' "$FAKE_XDO_LOG"; then ok "connect ohne Argument nutzt NAV_SERVER"; else bad "connect ohne Argument nutzt NAV_SERVER ($(cat "$FAKE_XDO_LOG"))"; fi

# --- Ergebnis -----------------------------------------------------------------
echo
echo "Ergebnis: $pass bestanden, $fail fehlgeschlagen."
[[ "$fail" -eq 0 ]]
