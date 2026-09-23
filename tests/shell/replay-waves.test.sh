#!/usr/bin/env bash
# ============================================================
# tests/shell/replay-waves.test.sh
# ------------------------------------------------------------
# Planetfreier Red/Green-Test fuer tools/session-replay/replay_waves.sh
# (Issue #784): prueft Level-Reihenfolge, Logic-Pfade (inkl. Level-9-Teilt-
# sich-mit-Level-8, #658), Validierung der Optionen und den echten HTTP-Pfad
# gegen einen Fake-`curl` im PATH -- kein Netz, kein echtes Cockpit.
#
# Laeuft in CI (lint.yml, shellcheck-Job deckt das Skript selbst ab) und
# lokal: tests/shell/replay-waves.test.sh
# ============================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REPLAY="${REPO_ROOT}/tools/session-replay/replay_waves.sh"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

FAIL=0
pass() { echo "PASS  $1"; }
fail() {
	echo "FAIL  $1" >&2
	FAIL=1
}

# -- (a) Dry-run: Default-Range (1..5), Reihenfolge + Wartezeilen ------------
out="$("$REPLAY" --dry-run)"
if echo "$out" | grep -q "wave1 ->" \
	&& echo "$out" | grep -q "wave5 ->" \
	&& ! echo "$out" | grep -q "wave6 ->"; then
	pass "Default dry-run deckt genau Level 1..5 ab"
else
	fail "Default dry-run deckt nicht genau Level 1..5 ab"
fi
if echo "$out" | grep -q "warte 60s"; then
	pass "Default-Intervall (60s) wird als Wartezeile ausgegeben"
else
	fail "Default-Intervall (60s) fehlt in der Ausgabe"
fi
first_line_no=$(echo "$out" | grep -n "wave1 ->" | head -1 | cut -d: -f1)
last_line_no=$(echo "$out" | grep -n "wave5 ->" | head -1 | cut -d: -f1)
if [ "$first_line_no" -lt "$last_line_no" ]; then
	pass "Wave1 kommt vor Wave5 (aufsteigende Reihenfolge)"
else
	fail "Reihenfolge falsch: Wave1 nicht vor Wave5"
fi

# -- (b) Custom Range (--start-level 3 --max-level 4) ------------------------
out="$("$REPLAY" --dry-run --start-level 3 --max-level 4)"
if echo "$out" | grep -q "wave3 ->" && echo "$out" | grep -q "wave4 ->" \
	&& ! echo "$out" | grep -q "wave2 ->" && ! echo "$out" | grep -q "wave5 ->"; then
	pass "Custom Range (--start-level 3 --max-level 4) exakt eingehalten"
else
	fail "Custom Range nicht exakt eingehalten"
fi

# -- (c) Level 9 teilt sich die Logic-Datei mit Level 8 (#658) ---------------
out="$("$REPLAY" --dry-run --start-level 9 --max-level 9)"
if echo "$out" | grep -q "attack_level_8_id_1.logic"; then
	pass "Level 9 nutzt attack_level_8_id_1.logic (#658)"
else
	fail "Level 9 nutzt nicht die erwartete Level-8-Logic-Datei"
fi

# -- (d) Validierung ungueltiger Optionen ------------------------------------
if "$REPLAY" --dry-run --start-level 0 >/dev/null 2>&1; then
	fail "--start-level 0 haette fehlschlagen muessen"
else
	pass "--start-level 0 wird abgelehnt"
fi
if "$REPLAY" --dry-run --max-level 10 >/dev/null 2>&1; then
	fail "--max-level 10 haette fehlschlagen muessen"
else
	pass "--max-level 10 (> 9) wird abgelehnt"
fi
if "$REPLAY" --dry-run --start-level 5 --max-level 3 >/dev/null 2>&1; then
	fail "start-level > max-level haette fehlschlagen muessen"
else
	pass "start-level > max-level wird abgelehnt"
fi
if "$REPLAY" --dry-run --interval abc >/dev/null 2>&1; then
	fail "--interval abc (nicht numerisch) haette fehlschlagen muessen"
else
	pass "--interval abc wird abgelehnt"
fi

# -- (e) Ohne --dry-run: COCKPIT_URL/COCKPIT_PASS sind Pflicht ---------------
if env -u COCKPIT_URL -u COCKPIT_PASS "$REPLAY" --max-level 1 >/dev/null 2>&1; then
	fail "Ohne COCKPIT_URL/COCKPIT_PASS haette der echte Lauf fehlschlagen muessen"
else
	pass "Fehlende COCKPIT_URL/COCKPIT_PASS werden ohne --dry-run abgelehnt (fail-closed)"
fi

# -- (f) Echter HTTP-Pfad gegen Fake-curl ------------------------------------
BIN="${TMP}/bin"
mkdir -p "$BIN"
FAKE_CURL_LOG="${TMP}/curl.log"
export FAKE_CURL_LOG
cat >"${BIN}/curl" <<'FAKE'
#!/usr/bin/env bash
echo "$*" >> "$FAKE_CURL_LOG"
outfile=""
prev=""
for arg in "$@"; do
	if [ "$prev" = "-o" ]; then
		outfile="$arg"
	fi
	prev="$arg"
done
if [ -n "$outfile" ]; then
	echo '{"ok":true}' >"$outfile"
fi
printf '200'
FAKE
chmod +x "${BIN}/curl"

PATH="${BIN}:${PATH}" COCKPIT_URL="https://cockpit.example.test" \
	COCKPIT_USER="operator" COCKPIT_PASS="s3cret" \
	"$REPLAY" --start-level 1 --max-level 2 --interval 0 >/dev/null

call_count=$(wc -l <"$FAKE_CURL_LOG" | tr -d ' ')
if [ "$call_count" = "2" ]; then
	pass "Echter Lauf (Level 1..2) feuert genau 2 curl-Aufrufe"
else
	fail "Erwartet 2 curl-Aufrufe, gezaehlt: ${call_count}"
fi
if grep -q "operator:s3cret" "$FAKE_CURL_LOG" \
	&& grep -q "https://cockpit.example.test/activate_mission_flow" "$FAKE_CURL_LOG" \
	&& grep -q "attack_level_1_id_1.logic" "$FAKE_CURL_LOG" \
	&& grep -q "attack_level_2_id_1.logic" "$FAKE_CURL_LOG"; then
	pass "curl-Aufrufe tragen Basic-Auth, Ziel-URL und korrekte Logic-Pfade"
else
	fail "curl-Aufrufe fehlen erwartete Auth/URL/Logic-Angaben"
fi

if [ "$FAIL" -eq 0 ]; then
	echo
	echo "replay-waves.test.sh: OK"
	exit 0
fi
exit 1
