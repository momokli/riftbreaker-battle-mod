'use strict';

// Kern-IO-Pfad (Issue #288) — was OHNE Spieler/Player beweisbar ist.
//
// Der Live-Befund aus #288: `POST /wave` liefert `exec_result.ok=true`, aber im
// Spiel entstehen 0 Kreaturen. `ok:true` belegt nur, dass
// `ConsoleService::ExecuteCommand` lief — NICHT, dass gespawnt wurde.
//
// Dieses File deckt zwei Bausteine ab, die zusammen mit
// `tests/lua-static/wave-anchor.test.js` (Log-Seite, kein Player) den Kernpfad
// ohne Spieler absichern:
//
//   1. Pipe-Roundtrip: Relay-`dispatch_exec` schreibt `{"cmd":"exec",...}` auf
//      die Pipe und liest auf derselben Verbindung ein `exec_result ok:true`
//      (Issue #73) — mit einem FIFO-"rbbridge-Responder" statt Windows.
//   2. Graceful Fail: `exec_result ok:false reason=console_service_not_found`
//      (Anker/Kommando nicht aufloesbar) -> Relay meldet status=error, KEIN
//      Crash, kein Retry-Haenger.
//   3. Falsch-Gruen-Kontrakt (statisch): `exec_result` traegt nur
//      command/ok/reason — kein Spawn-Feld. Der Spawn-Beweis ist die
//      `[RBBATTLE] event=wave …`-Log-Zeile, nie `exec_result.ok`.
//   4. EGRESS-Verifikation (statisch): die dedizierte `pipe_bridge` ist ein
//      reiner exec-Kanal und verwirft Nicht-`exec_result`-Zeilen — es gibt
//      KEIN Forwarding/HTTP-Client. Egress ist damit eine offene Flanke, keine
//      Regression (#13). Als OFFEN markiert, nicht als erledigt.
//
// OFFEN (Player-Test Momo/Matheo, NICHT erledigt): dass die Welle im headless
// Dedicated-Server **sichtbar** spawnt.

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execSync } = require('node:child_process');

const ROOT = path.join(__dirname, '..', '..');
const RELAY_DIR = path.join(ROOT, 'bausteine', '07-relay');
const RBBRIDGE_C = path.join(ROOT, 'bausteine', '04-trainer-io', 'rbbridge', 'rbbridge.c');
const PIPE_BRIDGE_C = path.join(ROOT, 'bausteine', '04-trainer-io', 'bridge', 'pipe_bridge.c');
const MOD_PATH = path.join(ROOT, 'mod', 'lua', 'rbbattle_autoexec.lua');

// ---------------------------------------------------------------------------
// Python-Harness: echter relay.py + echtes PipeClient gegen eine FIFO-"Pipe"
// mit einem Responder, der die exec_result-Antwort schreibt (no Player).
// Der Server wird per monkeypatch von relay.http_json ersetzt (kein Netz).
// ---------------------------------------------------------------------------

function runRelayDispatch(reply) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rb288-pipe-'));
  try {
    const fifo = path.join(dir, 'fake_pipe');
    execSync(`mkfifo "${fifo}"`);
    const pyFile = path.join(dir, 'harness.py');
    fs.writeFileSync(pyFile, `import io, json, os, sys, time, threading, contextlib
sys.path.insert(0, ${JSON.stringify(RELAY_DIR)})
import relay

fifo = sys.argv[1]
reply = json.loads(sys.argv[2])

def responder():
    # Kurz warten, damit der Relay schon geschrieben hat und liest; dann die
    # exec_result-Antwort in dieselbe FIFO-Queue legen (Named-Pipe-Ersatz).
    time.sleep(0.25)
    fd = os.open(fifo, os.O_RDWR)
    try:
        os.write(fd, (json.dumps(reply) + '\\n').encode('utf-8'))
    finally:
        os.close(fd)

threading.Thread(target=responder, daemon=True).start()

posts = []
relay.http_json = lambda method, url, body=None: (posts.append(body) or (200, {'ok': True}))

cfg = {
    'log_path': '/tmp/fake.log',
    'player_id': 'player_a',
    'match_id': 'm1-abcdef',
    'server': 'http://127.0.0.1:8080',
    'poll_s': 1.0,
    'pipe_path': fifo,
    'pipe_timeout_s': 3.0,
}
r = relay.Relay(cfg)  # echter PipeClient (kein Stub)
r.handle_outgoing({'event': 'exec_command', 'command': 'rb_wave 3', 'cmd_id': 7})
_due, _seq, item = r.dispatch_queue.get_nowait()
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    ok = r._handle_dispatch_item(item)
out = {'ok': ok, 'acked': 7 in r.acked, 'reports': posts, 'log': buf.getvalue()}
print('RESULT ' + json.dumps(out))
`);
    const out = execSync(`python3 ${pyFile} ${JSON.stringify(fifo)} ${JSON.stringify(JSON.stringify(reply))}`,
      { encoding: 'utf8' });
    const line = out.split('\n').map((s) => s.trim()).filter((s) => s.startsWith('RESULT ')).pop();
    assert.ok(line, 'Harness muss RESULT liefern; Ausgabe: ' + out);
    return JSON.parse(line.slice('RESULT '.length));
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

// ---------------------------------------------------------------------------
// 1) Pipe-Roundtrip: dispatch_exec -> exec_result ok:true (ohne Spieler)
// ---------------------------------------------------------------------------

test('Pipe-Roundtrip (ohne Spieler): exec rb_wave 3 -> exec_result ok:true', () => {
  const res = runRelayDispatch({ event: 'exec_result', command: 'rb_wave 3', ok: true });
  assert.strictEqual(res.ok, true, 'Dispatch gilt als erfolgreich (Kommando geschrieben + quittiert)');
  assert.strictEqual(res.acked, true, 'cmd_id=7 ist acked (nach erfolgreichem Roundtrip)');
  assert.ok(res.log.includes('dispatch sent cmd_id=7'), 'Log: dispatch sent cmd_id=7');
  assert.ok(res.log.includes('dispatch result cmd_id=7 status=ok'),
    'Log: dispatch result cmd_id=7 status=ok');
  assert.ok(res.log.includes('dispatch result reported cmd_id=7 status=ok'),
    'Log: Ergebnis an den Server gemeldet');
  // Das gemeldete Event traegt ok:true — und mehr nicht (kein Spawn-Feld).
  const rep = res.reports[0] && res.reports[0].event;
  assert.deepStrictEqual(rep, {
    type: 'exec_result', command: 'rb_wave 3', cmd_id: 7, ok: true, status: 'ok',
  });
});

// ---------------------------------------------------------------------------
// 2) Graceful Fail: ok:false (Kommando/Anker nicht aufloesbar) -> kein Crash
// ---------------------------------------------------------------------------

test('Graceful Fail (ohne Spieler): exec_result ok:false -> status=error, kein Crash', () => {
  const res = runRelayDispatch({
    event: 'exec_result', command: 'rb_wave 3', ok: false, reason: 'console_service_not_found',
  });
  // Dispatch selbst bleibt "erfolgreich" (Transport ok) — der Fehler steckt im
  // exec_result und wird klassifiziert/gemeldet, nicht als Absturz behandelt.
  assert.strictEqual(res.ok, true, 'Transport ok -> kein Dispatch-Fehler');
  assert.ok(res.log.includes('dispatch result cmd_id=7 status=error'),
    'Log: status=error aus dem exec_result');
  assert.ok(res.log.includes('console_service_not_found'),
    'Log: reason=console_service_not_found durchgereicht');
  const rep = res.reports[0] && res.reports[0].event;
  assert.strictEqual(rep.ok, false);
  assert.strictEqual(rep.status, 'error');
  assert.strictEqual(rep.reason, 'console_service_not_found');
});

// ---------------------------------------------------------------------------
// 3) Falsch-Gruen-Kontrakt (statisch): ok:true traegt keine Spawn-Info
// ---------------------------------------------------------------------------

test('Falsch-Gruen-Kontrakt: exec_result(ok:true) beweist keinen Spawn', () => {
  const c = fs.readFileSync(RBBRIDGE_C, 'utf8');
  // Erfolgs-Zeile nach fn(instance, command): nur event/ok/command.
  const idxCall = c.indexOf('fn(instance, command);');
  const idxOk = c.indexOf('\\"ok\\":true', idxCall);
  assert.ok(idxCall > 0 && idxOk > idxCall, 'ok:true wird erst NACH ExecuteCommand emittiert');
  const okLiteral = c.slice(idxOk - 40, idxOk + 80);
  assert.ok(!okLiteral.includes('spawn'),
    'exec_result(ok:true) enthaelt kein spawn-Feld -> Spawn-Beweis ist das Log');
  // Der Mod liefert den Spawn-Beweis als eigener Status (tests/lua-static/wave-anchor).
  const mod = fs.readFileSync(MOD_PATH, 'utf8');
  assert.ok(mod.includes('status=done spawned=%d') && mod.includes('status=no_spawns spawned=0'),
    'Mod trennt status=done (spawned>0) von status=no_spawns (0 Spawns)');
});

// ---------------------------------------------------------------------------
// 4) EGRESS-Verifikation (statisch): offene Flanke, KEINE Regression (#13)
// ---------------------------------------------------------------------------

test('EGRESS: pipe_bridge ist reiner exec-Kanal, kein State-Forwarding (OFFEN #13)', () => {
  const b = fs.readFileSync(PIPE_BRIDGE_C, 'utf8');
  // Nur zwei Lese-Ereignisse werden gesucht; alles andere wird verworfen.
  assert.ok(b.includes('pipe_wait_line(h, "exec_result"'), 'liest exec_result');
  assert.ok(b.includes('pipe_wait_line(h, "pong"'), 'liest pong');
  // Kein HTTP-Client / kein Report-Endpoint -> score_update kann nicht raus.
  const lower = b.toLowerCase();
  assert.ok(!lower.includes('winhttp') && !b.includes('/report'),
    'kein Forwarding/HTTP-Client in pipe_bridge (Egress unverdrahtet)');
  assert.ok(!b.includes('score_update'),
    'pipe_bridge kennt score_update gar nicht (reiner exec-Kanal)');
  // Gegenprobe rbbridge: send_state existiert, laeuft aber nur in serve_client
  // (Heartbeat waehrend einer Dauer-Verbindung) — die pipe_bridge verbindet
  // pro Request, haelt also keine Dauerleitung.
  const rb = fs.readFileSync(RBBRIDGE_C, 'utf8');
  assert.ok(rb.includes('send_state(hPipe)'), 'rbbridge send_state vorhanden');
  assert.ok(rb.includes('nur bei Client'),
    'send_state ist auf eine verbundene Client-Session begrenzt (Heartbeat)');
});

// ---------------------------------------------------------------------------
// OFFEN: nur mit Player pruefbar
// ---------------------------------------------------------------------------

test('OFFEN (Player-Test Momo/Matheo): Welle spawnt sichtbar und korrekt',
  { skip: 'OFFEN (#288): "spawnt sichtbar und korrekt" braucht einen beigetretenen Spieler/Client + Screenshot — Player-Test Momo/Matheo, NICHT erledigt. Headless belegt: statt eines Spawns kommt status=no_player/no_spawns.' },
  () => {});
