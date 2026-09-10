#!/usr/bin/env bash
#
# E2E-Test Baustein 06 — Tournament-Server + 2 Mock-Clients
#
# Skriptiertes Match (2 Runden):
#   - player_a (Creator) startet Runden und schickt pro Runde eine Welle
#     (wave_sent level=1, level=2),
#   - player_b empfängt beide als incoming_wave und quittiert (wave_received),
#   - beide liefern periodisch score_update (Scores kumulieren über die
#     Runden: A 1000+1000, B 1050+1100), round_end/match_end kommen an.
#
# Assertions danach (Server-State + Error-Kontrakt: 400/404/409).
# Exit 0 = alles ok, 1 = Fehler. Voraussetzung: node im PATH.
#
# Aufruf: bash test_e2e.sh   (aus diesem Verzeichnis)

set -u
cd "$(dirname "$0")" || exit

echo "== Voraussetzungen =="
if ! command -v node >/dev/null 2>&1; then
  echo "FAIL: node fehlt im PATH (node --version)." >&2
  echo "      Code ist fertig; Test muss lokal ausgeführt werden, sobald node installiert ist." >&2
  exit 1
fi
echo "node $(node --version) gefunden"

DIR=$(mktemp -d /tmp/rb06-e2e.XXXXXX)
PORT=$(( (RANDOM % 20000) + 20000 ))
export PORT  # server.js liest PORT aus der Umgebung
URL="http://127.0.0.1:$PORT"
SERVER_PID=""
A_PID=""
B_PID=""

# shellcheck disable=SC2317  # False Positive: cleanup wird via "trap ... EXIT" aufgerufen
cleanup() {
  [ -n "$A_PID" ] && kill "$A_PID" 2>/dev/null
  [ -n "$B_PID" ] && kill "$B_PID" 2>/dev/null
  [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null
  wait 2>/dev/null
  rm -rf "$DIR"
}
trap cleanup EXIT

echo "== Start Server (PORT $PORT) =="
node server.js >"$DIR/server.log" 2>&1 &
SERVER_PID=$!

READY=0
for _ in $(seq 1 30); do
  if node -e "require('http').get('$URL/health',r=>process.exit(r.statusCode===200?0:1)).on('error',()=>process.exit(1))" 2>/dev/null; then
    READY=1
    break
  fi
  sleep 0.5
done
if [ "$READY" != 1 ]; then
  echo "FAIL: Server nicht erreichbar auf $URL"
  cat "$DIR/server.log"
  exit 1
fi
echo "Server läuft."

echo "== Start Mock-Clients (player_a = Creator, player_b = Gegner) =="
node mock_client.js --player player_a --creator --opponent player_b \
  --rounds 2 --duration 4 --url "$URL" --state-dir "$DIR" >"$DIR/a.log" 2>&1 &
A_PID=$!
node mock_client.js --player player_b \
  --rounds 2 --duration 4 --url "$URL" --state-dir "$DIR" >"$DIR/b.log" 2>&1 &
B_PID=$!

echo "== Warte auf beide Summaries (Timeout 90s) =="
DONE=0
for _ in $(seq 1 180); do
  if [ -f "$DIR/summary.player_a.json" ] && [ -f "$DIR/summary.player_b.json" ]; then
    DONE=1
    break
  fi
  sleep 0.5
done

if [ "$DONE" != 1 ]; then
  echo "FAIL: Timeout — Clients haben nicht abgeschlossen."
  echo "--- server.log ---"; cat "$DIR/server.log"
  echo "--- a.log ---"; cat "$DIR/a.log"
  echo "--- b.log ---"; cat "$DIR/b.log"
  exit 1
fi

echo "== Assertions =="
cat >"$DIR/assert.js" <<'ASSERT'
const http = require('http');
const fs = require('fs');
const path = require('path');

const BASE = process.env.URL;
const DIR = process.env.DIR;
let fails = 0;

function check(name, cond, extra) {
  if (cond) { console.log('ok   - ' + name); }
  else { fails += 1; console.log('FAIL - ' + name + (extra ? ' :: ' + extra : '')); }
}

function req(method, p, body) {
  return new Promise((resolve) => {
    const u = new URL(BASE);
    const data = body ? JSON.stringify(body) : null;
    const r = http.request({
      host: u.hostname, port: u.port, method, path: p,
      headers: { 'Content-Type': 'application/json',
                 'Content-Length': data ? Buffer.byteLength(data) : 0 },
    }, (res) => {
      let b = '';
      res.on('data', (c) => { b += c; });
      res.on('end', () => {
        let j = null;
        try { j = JSON.parse(b); } catch (e) { /* leer */ }
        resolve({ status: res.statusCode, json: j });
      });
    });
    r.on('error', (e) => resolve({ status: 0, error: e.message }));
    if (data) r.write(data);
    r.end();
  });
}

(async () => {
  const ma = JSON.parse(fs.readFileSync(path.join(DIR, 'summary.player_a.json'), 'utf8'));
  const mb = JSON.parse(fs.readFileSync(path.join(DIR, 'summary.player_b.json'), 'utf8'));
  const mf = JSON.parse(fs.readFileSync(path.join(DIR, 'match.json'), 'utf8'));

  check('match.json existiert mit match_id', !!mf.match_id);
  check('A: 2x round_start empfangen', ma.received.round_start === 2);
  check('A: 2x round_end empfangen', ma.received.round_end === 2);
  check('A: match_end empfangen', ma.received.match_end === 1);
  check('B: 2x incoming_wave empfangen (Runde 1 + 2)', mb.received.incoming_wave === 2, JSON.stringify(mb.received));
  check('B: match_end empfangen', mb.received.match_end === 1);
  check('A: Score kumuliert über 2 Runden (1000 + 1000)',
    ma.total_score === 2000 && JSON.stringify(ma.rounds) === JSON.stringify([{ round: 1, score: 1000 }, { round: 2, score: 1000 }]),
    JSON.stringify(ma.rounds) + ' total=' + ma.total_score);
  check('B: Score kumuliert (1050 + 1100, +50 je Wellen-Level)',
    mb.total_score === 2150, JSON.stringify(mb.rounds) + ' total=' + mb.total_score);
  check('Sieger laut Clients = player_b', ma.winner === 'player_b' && mb.winner === 'player_b');

  const st = await req('GET', '/match/' + encodeURIComponent(mf.match_id) + '/state');
  check('GET /match/:id/state -> 200', st.status === 200, String(st.status));
  check('state.status = finished', st.json && st.json.status === 'finished', st.json && st.json.status);
  check('state.winner = player_b', st.json && st.json.winner === 'player_b', st.json && st.json.winner);
  check('state.reason = score', st.json && st.json.reason === 'score', st.json && st.json.reason);
  check('Scoreboard kumuliert (player_a=2000, player_b=2150)',
    st.json && st.json.scoreboard.player_a && st.json.scoreboard.player_b &&
    st.json.scoreboard.player_a.score === 2000 && st.json.scoreboard.player_b.score === 2150,
    JSON.stringify(st.json && st.json.scoreboard));

  const bad = await req('POST', '/event', { match_id: mf.match_id, player_id: 'player_a', event: { type: 'explosion' } });
  check('POST /event mit unbekanntem Typ -> 400', bad.status === 400, String(bad.status) + ' ' + JSON.stringify(bad.json));
  const nf = await req('GET', '/match/does-not-exist/state');
  check('GET /match/does-not-exist/state -> 404', nf.status === 404, String(nf.status));
  const pf = await req('GET', '/poll/never-registered');
  check('GET /poll/never-registered -> 404', pf.status === 404, String(pf.status));
  const rs = await req('POST', '/round/start', { match_id: mf.match_id, duration_s: 5 });
  check('POST /round/start nach match_end -> 409', rs.status === 409, String(rs.status) + ' ' + JSON.stringify(rs.json));

  if (fails === 0) {
    console.log('\nALLE ASSERTIONS OK — Baustein 06 funktioniert (2 Runden, Routing, Scoreboard, Lifecycle, Error-Kontrakt).');
    process.exit(0);
  }
  console.log('\n' + fails + ' Assertion(s) fehlgeschlagen.');
  process.exit(1);
})();
ASSERT

export URL DIR
node "$DIR/assert.js"
RC=$?

echo "--- server.log (Auszug) ---"
grep '\[server\]' "$DIR/server.log" || true

if [ "$RC" = 0 ]; then
  echo "== E2E OK (Exit 0) =="
else
  echo "== E2E FEHLGESCHLAGEN (Exit $RC) =="
  echo "--- a.log ---"; tail -30 "$DIR/a.log"
  echo "--- b.log ---"; tail -30 "$DIR/b.log"
fi
exit "$RC"
