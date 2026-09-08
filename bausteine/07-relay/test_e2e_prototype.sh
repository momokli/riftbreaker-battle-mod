#!/usr/bin/env bash
#
# E2E-Prototyp Baustein 07/08 — Strecke Spiel-Log → Relay → Server → Web-UI
#
# Startet den Tournament-Server (06, Port 8765), simuliert Spiel-Log-Zeilen
# ([RBBATTLE] key=value, wie der Lua-Mod sie schreibt), startet den Relay (07)
# dagegen und prueft die ganze Strecke:
#
#   log-Zeile -> relay tail -> POST /event -> Server
#     -> SSE /stream (Web-UI-Feed) UND Outbox -> GET /poll/:player_id
#     -> exec_command -> relay loggt "dispatch pending: <command>" (v0-TODO)
#   + Web-UI-Dateien (06/web) werden ausgeliefert (html/js, 404 sonst)
#
# Assertions werden gezaehlt; Exit 0 = alles ok, 1 = Fehler.
# Voraussetzungen: node, python3, curl im PATH.
#
# Aufruf: bash test_e2e_prototype.sh   (aus diesem Verzeichnis oder via Pfad)

set -u
cd "$(dirname "$0")"

SERVER_JS="../06-tournament-server/server.js"
RELAY_PY="relay.py"
PORT=8765
URL="http://127.0.0.1:$PORT"

OK=0
FAIL=0
DIR=$(mktemp -d /tmp/rb07-e2e.XXXXXX)
SERVER_PID=""
RELAY_PID=""
SSE_PID=""

cleanup() {
  [ -n "$RELAY_PID" ] && kill "$RELAY_PID" 2>/dev/null
  [ -n "$SSE_PID" ] && kill "$SSE_PID" 2>/dev/null
  [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null
  wait 2>/dev/null
  rm -rf "$DIR"
}
trap cleanup EXIT

# check <name> <cmd...>: fuehrt <cmd> aus, exit 0 = Assertion ok
check() {
  local name=$1
  shift
  if "$@" >/dev/null 2>&1; then
    OK=$((OK + 1))
    echo "ok   - $name"
  else
    FAIL=$((FAIL + 1))
    echo "FAIL - $name"
  fi
}

# wait_for <sekunden> <grep-muster> <datei>
wait_for() {
  local tries=$1 pattern=$2 file=$3
  local i
  for i in $(seq 1 "$tries"); do
    grep -q "$pattern" "$file" 2>/dev/null && return 0
    sleep 0.5
  done
  return 1
}

http_code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }

# Simulierte Spiel-Log-Zeilen (wie Baustein 03/05-Lua sie schreibt).
fake_write() {
  echo "[RBBATTLE] $1" >> "$DIR/fake.log"
}

echo "== Voraussetzungen =="
NODE_OK=1; PY_OK=1; CURL_OK=1
command -v node >/dev/null 2>&1 || { echo "FAIL: node fehlt." >&2; NODE_OK=0; }
command -v python3 >/dev/null 2>&1 || { echo "FAIL: python3 fehlt." >&2; PY_OK=0; }
command -v curl >/dev/null 2>&1 || { echo "FAIL: curl fehlt." >&2; CURL_OK=0; }
[ "$NODE_OK$PY_OK$CURL_OK" = "111" ] || exit 1
echo "node $(node --version) / python3 $(python3 --version | cut -d' ' -f2) / curl gefunden"

echo "== Start Tournament-Server (Port $PORT) =="
PORT=$PORT node "$SERVER_JS" >"$DIR/server.log" 2>&1 &
SERVER_PID=$!

READY=0
for _ in $(seq 1 40); do
  if curl -s "$URL/health" | grep -q '"ok":true'; then READY=1; break; fi
  sleep 0.5
done
if [ "$READY" != 1 ]; then
  echo "FAIL: Server nicht erreichbar auf $URL"
  cat "$DIR/server.log"
  exit 1
fi
echo "Server laeuft."

echo "== SSE-Stream abonnieren (curl -N) =="
curl -sN -D "$DIR/sse.headers" "$URL/stream" >"$DIR/sse.log" 2>&1 &
SSE_PID=$!
sleep 0.7

echo "== Spieler registrieren (curl) =="
CODE_A=$(http_code -X POST "$URL/register" -H 'Content-Type: application/json' \
  -d '{"player_id":"player_a"}')
CODE_B=$(http_code -X POST "$URL/register" -H 'Content-Type: application/json' \
  -d '{"player_id":"player_b"}')
check "POST /register player_a -> 200" test "$CODE_A" = 200
check "POST /register player_b -> 200" test "$CODE_B" = 200

echo "== Match anlegen (curl) =="
MID=$(curl -s -X POST "$URL/match/create" -H 'Content-Type: application/json' \
  -d '{"players":["player_a","player_b"],"rounds":3}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin).get("match_id",""))')
check "POST /match/create liefert match_id" test -n "$MID"
echo "match_id=$MID"

echo "== Relay starten (player_a, match $MID, log=$DIR/fake.log) =="
RBB_PLAYER_ID=player_a RBB_MATCH_ID="$MID" RBB_LOG_PATH="$DIR/fake.log" \
RBB_SERVER="$URL" python3 "$RELAY_PY" >"$DIR/relay.log" 2>&1 &
RELAY_PID=$!
check "relay registriert sich (register: ok)" \
  wait_for 20 '\[relay\] register: ok player_id=player_a' "$DIR/relay.log"

echo "== Spiel-Log simulieren: [RBBATTLE]-Zeilen schreiben =="
fake_write "event=score_update score=500 wave=1"
fake_write "event=wave_sent level=2 cost=50"
fake_write "event=bridge_test status=done"
check "relay tailt score_update-Zeile" \
  wait_for 20 '\[relay\] tail: .*event=score_update score=500 wave=1' "$DIR/relay.log"
check "relay liefert event ein (post: ok type=score_update)" \
  wait_for 20 '\[relay\] post: ok type=score_update' "$DIR/relay.log"
check "wave_sent wird eingeliefert (post: ok type=wave_sent)" \
  wait_for 20 '\[relay\] post: ok type=wave_sent' "$DIR/relay.log"
check "nicht-server-faehige Zeile (bridge_test) wird uebersprungen" \
  grep -q 'event=bridge_test nicht server-faehig' "$DIR/relay.log"

echo "== SSE-Stream prüfen =="
check "SSE: content-type text/event-stream" \
  grep -qi 'content-type: text/event-stream' "$DIR/sse.headers"
check "SSE: player_registered (player_a)" \
  grep -q '"kind":"player".*"player_id":"player_a"' "$DIR/sse.log"
check "SSE: player_registered (player_b)" \
  grep -q '"kind":"player".*"player_id":"player_b"' "$DIR/sse.log"
check "SSE: match_created" \
  grep -q '"kind":"match".*"match_id":"'"$MID"'"' "$DIR/sse.log"
check "SSE: /event-Eingang sichtbar (input type=score_update)" \
  grep -q '"kind":"input".*"type":"score_update"' "$DIR/sse.log"
check "SSE: Zustellung sichtbar (delivery incoming_wave an player_b)" \
  grep -q '"kind":"delivery".*"player_id":"player_b".*"event":"incoming_wave"' "$DIR/sse.log"

echo "== exec_command -> dispatch pending (relay-stdout) =="
CODE_EC=$(http_code -X POST "$URL/event" -H 'Content-Type: application/json' \
  -d "{\"match_id\":\"$MID\",\"player_id\":\"player_a\",\
       \"event\":{\"type\":\"exec_command\",\"command\":\"rb_wave 3\"}}")
check "POST /event exec_command rb_wave 3 -> 200" test "$CODE_EC" = 200
check "relay loggt 'dispatch pending: rb_wave 3'" \
  wait_for 20 'dispatch pending: rb_wave 3' "$DIR/relay.log"
check "SSE: exec_command-Zustellung sichtbar (delivery)" \
  grep -q '"kind":"delivery".*"event":"exec_command".*"command":"rb_wave 3"' "$DIR/sse.log"

echo "== /poll-Queue (curl, player_b — kein Relay, Outbox unangetastet) =="
CODE_EC2=$(http_code -X POST "$URL/event" -H 'Content-Type: application/json' \
  -d "{\"match_id\":\"$MID\",\"player_id\":\"player_b\",\
       \"event\":{\"type\":\"exec_command\",\"command\":\"rb_status\"}}")
check "POST /event exec_command rb_status (player_b) -> 200" test "$CODE_EC2" = 200
sleep 1.2   # Relay-Poll von player_a darf rb_status nicht beruehren (andere Outbox)
curl -s "$URL/poll/player_b" > "$DIR/poll_b.json"
python3 - "$DIR/poll_b.json" >"$DIR/poll_assert.log" 2>&1 <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
evs = d.get('events') or []
cmds = [e for e in evs if e.get('event') == 'exec_command']
assert len(evs) >= 1, 'keine events in outbox'
assert any(e.get('event') == 'incoming_wave' and e.get('level') == 2
           for e in evs), 'incoming_wave (Routing) fehlt in outbox'
assert cmds and cmds[0].get('command') == 'rb_status', evs
PY
check "GET /poll/player_b liefert exec_command rb_status" test $? = 0
grep -q 'dispatch pending: rb_status' "$DIR/relay.log"
NEG=$?
check "relay hat rb_status NICHT dispatchen koennen (kein pending rb_status)" test "$NEG" != 0

echo "== Web-UI-Dateien (statisch aus 06/web) =="
check "GET / -> 200 (index.html)" test "$(http_code "$URL/")" = 200
check "GET /index.html -> 200" test "$(http_code "$URL/index.html")" = 200
CODE_APPJS=$(http_code "$URL/app.js")
check "GET /app.js -> 200" test "$CODE_APPJS" = 200
curl -s -D - -o /dev/null "$URL/app.js" | tr -d '\r' | grep -qi '^content-type: text/javascript'
check "GET /app.js content-type text/javascript" test $? = 0
check "GET /unbekannt.css -> 404 (nur html/js/css)" \
  test "$(http_code "$URL/unbekannt.css")" = 404
curl -s "$URL/health" > "$DIR/health.json"
check "GET /health weiterhin ok (API unangetastet)" grep -q '"ok":true' "$DIR/health.json"

echo ""
echo "== Ergebnis: $OK ok, $FAIL fail =="
if [ "$FAIL" = 0 ]; then
  echo "== E2E-PROTOTYP OK (Exit 0) — Strecke log→relay→server→webui funktioniert =="
  exit 0
fi
echo "== E2E-PROTOTYP FEHLGESCHLAGEN =="
echo "--- relay.log ---"; cat "$DIR/relay.log"
echo "--- poll_b.json ---"; cat "$DIR/poll_b.json" 2>/dev/null || echo "(fehlt)"
echo "--- poll_assert.log ---"; cat "$DIR/poll_assert.log" 2>/dev/null || echo "(fehlt)"
echo "--- server.log (Auszug) ---"; grep '\[server\]' "$DIR/server.log" || true
echo "--- sse.log (Auszug) ---"; tail -20 "$DIR/sse.log"
exit 1
