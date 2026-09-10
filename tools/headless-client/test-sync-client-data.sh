#!/usr/bin/env bash
#
# test-sync-client-data.sh — Unit-/Trockenlauf-Tests für sync-client-data.sh
#
# Testet Argument-Parsing, Dry-Run-Logik, rsync-Options-Zusammenbau und den
# Verifikationsschritt — OHNE echten 13-GB-Transfer. `ssh`/`rsync` werden durch
# Fake-Binaries ersetzt (PATH-Injection), die konsistente Messwerte liefern.
#
# Nutzung:
#   tools/headless-client/test-sync-client-data.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYNC="$SCRIPT_DIR/sync-client-data.sh"
WORK="$(mktemp -d)"
BIN="$WORK/bin"
mkdir -p "$BIN"
trap 'rm -rf "$WORK"' EXIT

# --- Fake `ssh`: liefert konsistente Werte statt echter Remote-Aufrufe --------
cat > "$BIN/ssh" <<'EOF'
#!/usr/bin/env bash
host="${1:-}"; shift
cmdline="$*"

size="${FAKE_SIZE:-13600000000}"
files="${FAKE_FILES:-5}"
avail="${FAKE_AVAIL:-20000000000}"
md5line="${FAKE_MD5:-0123456789abcdef0123456789abcdef  ./payload.bin}"

case "$host" in
    lan)    size="${FAKE_SRC_SIZE:-$size}"; files="${FAKE_SRC_FILES:-$files}" ;;
    planet) size="${FAKE_DST_SIZE:-$size}"; files="${FAKE_DST_FILES:-$files}" ;;
esac

case "$cmdline" in
    *"du -sb"*)  echo "$size" ;;
    *"wc -l"*)   echo "$files" ;;
    *"bash -s"*) echo "$avail" ;;
    *"md5sum"*)  echo "$md5line" ;;
    *)           echo "fake-ssh: unmatched: $cmdline" >&2; exit 1 ;;
esac
EOF

# --- Fake `rsync`: protokolliert Argumente, überträgt nichts -------------------
cat > "$BIN/rsync" <<'EOF'
#!/usr/bin/env bash
{
    printf 'fake-rsync\n'
    printf '  %s\n' "$@"
} >> "${FAKE_RSYNC_LOG:?FAKE_RSYNC_LOG unset}"
exit 0
EOF

chmod +x "$BIN/ssh" "$BIN/rsync"
export PATH="$BIN:$PATH"
export SSH_BIN=ssh RSYNC_BIN=rsync

pass=0; fail=0
RC=0

# --- Helfer -------------------------------------------------------------------
run() { RC=0; "$@" || RC=$?; }              # Kommando ausführen, rc merken (set -e-sicher)
ok()  { echo "PASS  $1"; pass=$((pass+1)); }
bad() { echo "FAIL  $1"; fail=$((fail+1)); }

check_rc() {  # desc expected
    if [[ "$RC" -eq "$2" ]]; then ok "$1"; else bad "$1 (rc=$RC)"; fi
}

# --- 1. --help ----------------------------------------------------------------
run "$SYNC" --help > "$WORK/help.out" 2>&1
check_rc "help (rc=0)" 0
if grep -q -- "--dry-run" "$WORK/help.out"; then ok "help nennt --dry-run"; else bad "help nennt --dry-run"; fi

# --- 2. Unbekannte Option → rc=2 ---------------------------------------------
run "$SYNC" --bogus >/dev/null 2>&1
check_rc "unbekannte Option (rc=2)" 2

# --- 3. Dry-Run: rsync bekommt --dry-run, Verifikation übersprungen -----------
FAKE_RSYNC_LOG="$WORK/dry.log" run "$SYNC" --dry-run > "$WORK/dry.out" 2>&1
check_rc "dry-run (rc=0)" 0
if grep -q -- "--dry-run" "$WORK/dry.log"; then ok "dry-run → rsync --dry-run"; else bad "dry-run → rsync --dry-run"; fi
if grep -q "Verifikation übersprungen" "$WORK/dry.out"; then ok "dry-run überspringt Verifikation"; else bad "dry-run überspringt Verifikation"; fi
if grep -q "Vorab-Größencheck" "$WORK/dry.out"; then ok "dry-run macht Größencheck"; else bad "dry-run macht Größencheck"; fi

# --- 4. Voll-Sync + Verifikation OK ------------------------------------------
FAKE_RSYNC_LOG="$WORK/full.log" run "$SYNC" > "$WORK/full.out" 2>&1
check_rc "full sync + verify (rc=0)" 0
if grep -q "Verifikation OK." "$WORK/full.out"; then ok "Verifikation OK"; else bad "Verifikation OK"; fi
if grep -q "MD5-Stichprobe OK" "$WORK/full.out"; then ok "MD5-Stichprobe läuft"; else bad "MD5-Stichprobe läuft"; fi

# --- 5. --delete → rsync bekommt --delete ------------------------------------
FAKE_RSYNC_LOG="$WORK/del.log" run "$SYNC" --delete >/dev/null 2>&1
check_rc "--delete (rc=0)" 0
if grep -q -- "--delete" "$WORK/del.log"; then ok "--delete → rsync --delete"; else bad "--delete → rsync --delete"; fi

# --- 6. Größen-Mismatch → rc≠0 ----------------------------------------------
FAKE_RSYNC_LOG="$WORK/mismatch.log" FAKE_DST_SIZE=1 run "$SYNC" > "$WORK/mismatch.out" 2>&1
if [[ "$RC" -ne 0 ]]; then ok "Größen-Mismatch (rc≠0)"; else bad "Größen-Mismatch (rc≠0, rc=$RC)"; fi
if grep -q "Gesamtgröße weicht ab" "$WORK/mismatch.out"; then ok "Mismatch-Meldung"; else bad "Mismatch-Meldung"; fi

# --- 7. --sample 0 deaktiviert MD5-Stichprobe --------------------------------
FAKE_RSYNC_LOG="$WORK/nomd5.log" run "$SYNC" --sample 0 > "$WORK/nomd5.out" 2>&1
check_rc "--sample 0 (rc=0)" 0
if grep -q "MD5-Stichprobe" "$WORK/nomd5.out"; then bad "--sample 0 deaktiviert MD5"; else ok "--sample 0 deaktiviert MD5"; fi

# --- 8. --no-verify überspringt Verifikation ---------------------------------
FAKE_RSYNC_LOG="$WORK/noverify.log" run "$SYNC" --no-verify > "$WORK/noverify.out" 2>&1
check_rc "--no-verify (rc=0)" 0
if grep -q "Verifikation" "$WORK/noverify.out"; then bad "--no-verify überspringt Verifikation"; else ok "--no-verify überspringt Verifikation"; fi

# --- Ergebnis ----------------------------------------------------------------
echo
echo "Ergebnis: $pass bestanden, $fail fehlgeschlagen."
[[ "$fail" -eq 0 ]]
