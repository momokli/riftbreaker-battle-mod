'use strict';

// Vollketten-E2E für Issue #11:
//   "rb_wave 3 via Web-UI → Spawn am headless Client"
//
// Kette (realer Codefluss):
//   Web-UI (button data-cmd="rb_wave 3" → app.js POST /event exec_command)
//     → Tournament-Server (server.js handleEvent → Outbox)
//     → Relay (relay.py GET /poll → dispatch_exec auf die rbbridge-Pipe)
//     → rbbridge ({"cmd":"exec","command":"rb_wave 3"} → ExecuteCommand)
//     → Mod (rb_wave → SpawnWave(3) → 8 Kreaturen)
//     → Spawn am headless Client (LIVE, OFFEN)
//
// Was hier deterministisch geprüft wird (ohne Windows-Spielprozess):
//   - Web-UI-Trigger            (statisch: Button + app.js POST-Body)
//   - Server-Handler            (dynamisch: /event → Outbox → cmd_id)
//   - Relay-Dispatch            (dynamisch: "dispatch sent cmd_id=...", FIFO-Pipe-Fake)
//   - rbbridge-Kommando         (statisch: dispatch_exec → ExecuteCommand)
//   - Mod-Spawn                 (fengari + Stub-Services: rb_wave 3 → 8 Spawns)
//
// Was OFFEN bleibt (kein Live-Client in CI, Linux):
//   - ExecuteCommand im echten Spielprozess (DLL-Injection, Windows)
//   - "Spawn am headless Client sichtbar" (Screenshot-Beweis)
// Der Relay → rbbridge-Pipe-Dispatch (relay.py dispatch_exec) ist seit
// Issue #60 implementiert und wird hier gegen einen FIFO-Fake (Named-Pipe-
// Ersatz unter Linux) geprüft; nur der echte rbbridge-Dispatch (Windows,
// injizierte DLL) bleibt off-CI. Siehe letzte Tests.

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const { spawn, execSync } = require('node:child_process');
const luaparse = require('luaparse');
const { lua, lauxlib, lualib, to_luastring, to_jsstring } = require('fengari');

const ROOT = path.join(__dirname, '..', '..');
const WEB_DIR = path.join(ROOT, 'bausteine', '06-tournament-server', 'web');
const SERVER_JS = path.join(ROOT, 'bausteine', '06-tournament-server', 'server.js');
const RELAY_PY = path.join(ROOT, 'bausteine', '07-relay', 'relay.py');
const RBBRIDGE_C = path.join(ROOT, 'trainer', 'rbbridge', 'rbbridge.c');
const PROTOCOL_MD = path.join(ROOT, 'trainer', 'protocol.md');
const MOD_PATH = path.join(ROOT, 'mod', 'lua', 'rbbattle_autoexec.lua');

// ---------------------------------------------------------------------------
// Statisch: Web-UI-Trigger
// ---------------------------------------------------------------------------

test('Web-UI: rb_wave 3 Button (data-cmd) vorhanden', () => {
  const html = fs.readFileSync(path.join(WEB_DIR, 'index.html'), 'utf8');
  assert.ok(
    html.includes('data-cmd="rb_wave 3"'),
    'index.html muss einen Button mit data-cmd="rb_wave 3" haben',
  );
});

test('Web-UI: app.js sendet exec_command an POST /event', () => {
  const js = fs.readFileSync(path.join(WEB_DIR, 'app.js'), 'utf8');
  // sendCommand baut {match_id, player_id, event:{type:"exec_command", command}}
  assert.ok(js.includes("type: 'exec_command'"), 'app.js setzt event.type=exec_command');
  assert.ok(js.includes("fetch('/event'"), 'app.js postet an /event');
  // Button-Klick → sendCommand(btn.dataset.cmd)
  assert.ok(js.includes('btn.dataset.cmd'), 'Button-Klick nutzt data-cmd als Kommando');
  assert.ok(js.includes('button.cmd[data-cmd]'), 'Buttons werden per [data-cmd] verdrahtet');
});

// ---------------------------------------------------------------------------
// Statisch: rbbridge-Kommando (exec → ExecuteCommand)
// ---------------------------------------------------------------------------

test('rbbridge: {"cmd":"exec","command":"rb_wave 3"} → ExecuteCommand (statisch)', () => {
  const c = fs.readFileSync(RBBRIDGE_C, 'utf8');
  // exec-Zweig in handle_line → dispatch_exec
  assert.ok(c.includes('strcmp(cmd, "exec") == 0'), 'rbbridge behandelt cmd=exec');
  assert.ok(c.includes('dispatch_exec(hPipe, command)'), 'exec ruft dispatch_exec auf');
  // dispatch_exec ist seit RE-Stand verdrahtet (kein reiner TODO/no-op mehr):
  assert.ok(c.includes('RBBRIDGE_RVA_EXEC_COMMAND'), 'ExecuteCommand-RVA definiert');
  assert.ok(c.includes('console_exec_fn'), 'console_exec_fn Typ vorhanden');
  assert.ok(c.includes('fn(instance, command)'), 'ExecuteCommand-Aufruf (this=RCX, cmd=RDX)');
});

test('Protokoll: exec-Kanal "rb_wave 3" dokumentiert (protocol.md)', () => {
  const md = fs.readFileSync(PROTOCOL_MD, 'utf8');
  assert.ok(md.includes('{"cmd":"exec","command":"rb_wave 3"}'),
    'protocol.md dokumentiert den exec-Kanal mit rb_wave 3');
});

// ---------------------------------------------------------------------------
// Mod: rb_wave 3 → Level-3-Welle (fengari + Stub-Services, deterministisch)
// ---------------------------------------------------------------------------

const modSource = fs.readFileSync(MOD_PATH, 'utf8');

const STUBS = `
-- ==== Stub-Services (Muster tests/lua-static/win-condition.test.js) ====
_G.__logs = {}
_G.__commands = {}
_G.__db = {}
INVALID_ID = -1

LogService = {
    Log = function(self, msg) _G.__logs[#_G.__logs + 1] = msg end,
}
ConsoleService = {
    Write = function(self, msg) end,
    RegisterCommand = function(self, name, fn) _G.__commands[name] = fn end,
}
FindService = {
    FindEntitiesByGroup = function(self, g) return {} end,
    FindPlayerSpawnPoints = function(self) return {} end,
}
MapGenerator = { GetInitialSpawnPoint = function(self) return nil end }
ResourceManager = { GetBlueprint = function(self, bp) return true end }
EnvironmentService = { GetTerrainHeight = function(self, pos) return 0 end }
EntityService = {
    GetName = function(self, e) return "" end,
    GetPosition = function(self, e) return { x = 0, y = 0, z = 0 } end,
    SpawnEntity = function(self, ...) return 1 end,
}
PlayerService = {
    GetPlayerControlledEnt = function(self, i) return 1 end,
    GetOrCreateGlobalDatabase = function(self, name)
        local db = {}
        db.HasInt = function(s, k) return _G.__db[k] ~= nil end
        db.GetIntOrDefault = function(s, k, d)
            local v = _G.__db[k]
            if v ~= nil then return v end
            return d
        end
        db.SetInt = function(s, k, v) _G.__db[k] = v end
        db.RemoveKey = function(s, k) _G.__db[k] = nil end
        db.Clear = function(s) _G.__db = {} end
        return db
    end,
}
DifficultyService = { GetCurrentDifficultyName = function(self) return "hard" end }
CampaignService = { GetCreaturesBaseDifficulty = function(self) return 5 end }

function RegisterGlobalEventHandler(name, fn)
    _G.__handlers[name] = fn
end
`;

const ASSERTIONS = `
-- ==== Assertions: rb_wave 3 spawns Level-3-Welle (deterministisch) ====
local failures = 0
local function check(cond, msg)
    if cond then
        print("PASS: " .. msg)
    else
        print("FAIL: " .. msg)
        failures = failures + 1
    end
end

local function log_has(sub)
    for _, m in ipairs(_G.__logs) do
        if string.find(m, sub, 1, true) then return true end
    end
    return false
end

local function count_logs(sub)
    local n = 0
    for _, m in ipairs(_G.__logs) do
        if string.find(m, sub, 1, true) then n = n + 1 end
    end
    return n
end

check(_G.__commands["rb_wave"] ~= nil, "rb_wave registriert")

-- rb_wave 3 auslösen (wie die Bridge/ConsoleService es täte).
_G.__commands["rb_wave"]({ "3" })

check(log_has("event=wave level=3 status=start"), "rb_wave 3 -> event=wave level=3 status=start")
-- Level-3-Welle = baxmoth x5 + artigian x2 + canceroth x1 = 8 Kreaturen
check(log_has("event=wave level=3 status=done spawned=8 skipped=0"),
    "rb_wave 3 -> status=done spawned=8 skipped=0")
check(count_logs("event=spawn ok blueprint=units/ground/baxmoth") == 5, "5x baxmoth gespawnt")
check(count_logs("event=spawn ok blueprint=units/ground/artigian") == 2, "2x artigian gespawnt")
check(count_logs("event=spawn ok blueprint=units/ground/canceroth") == 1, "1x canceroth gespawnt")

print("FAILURES=" .. failures)
_G.__failures = failures
`;

function runLua(code) {
  const L = lauxlib.luaL_newstate();
  lualib.luaL_openlibs(L);
  const loadStatus = lauxlib.luaL_loadstring(L, to_luastring(code));
  if (loadStatus !== lua.LUA_OK) {
    const err = to_jsstring(lua.lua_tostring(L, -1));
    lua.lua_close(L);
    throw new Error('Lua-Loadfehler: ' + err);
  }
  const status = lua.lua_pcall(L, 0, 0, 0);
  if (status !== lua.LUA_OK) {
    const err = to_jsstring(lua.lua_tostring(L, -1));
    lua.lua_close(L);
    throw new Error('Lua-Laufzeitfehler: ' + err);
  }
  lua.lua_getglobal(L, to_luastring('__failures'));
  const failures = lua.lua_tonumber(L, -1) || 0;
  lua.lua_close(L);
  return failures;
}

test('Mod: rb_wave 3 spawnt Level-3-Welle (fengari + Stub-Services)', () => {
  const failures = runLua(STUBS + modSource + '\n' + ASSERTIONS);
  assert.strictEqual(failures, 0, `Assertion-Fehler im Lua-Harness: ${failures}`);
});

// ---------------------------------------------------------------------------
// Dynamisch: Web-UI-Trigger → Server-Handler → Relay-Dispatch
// ---------------------------------------------------------------------------

function freePort() {
  return new Promise((resolve, reject) => {
    const srv = http.createServer();
    srv.on('error', reject);
    srv.listen(0, '127.0.0.1', () => {
      const p = srv.address().port;
      srv.close(() => resolve(p));
    });
  });
}

function httpReq(method, url, body) {
  return new Promise((resolve, reject) => {
    const u = new URL(url);
    const data = body === undefined ? null : JSON.stringify(body);
    const req = http.request({
      host: u.hostname,
      port: u.port,
      method,
      path: u.pathname + u.search,
      headers: data
        ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(data) }
        : {},
    }, (res) => {
      let b = '';
      res.on('data', (c) => { b += c; });
      res.on('end', () => {
        let j = null;
        try { j = JSON.parse(b); } catch (e) { /* leer */ }
        resolve({ status: res.statusCode, json: j, text: b });
      });
    });
    req.on('error', reject);
    if (data) req.write(data);
    req.end();
  });
}

async function waitFor(fn, timeoutMs, intervalMs) {
  const interval = intervalMs || 100;
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    if (await fn()) return true;
    if (Date.now() >= deadline) return false;
    await new Promise((r) => setTimeout(r, interval));
  }
}

test('Vollkette: rb_wave 3 → Server-Outbox → Relay-Dispatch auf die Pipe (dynamisch)', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rb11-e2e-'));
  let server = null;
  let relay = null;

  const serverLog = path.join(dir, 'server.log');
  const relayLog = path.join(dir, 'relay.log');
  const fakeLog = path.join(dir, 'fake.log');

  try {
    const port = await freePort();
    const base = `http://127.0.0.1:${port}`;

    // 1) Tournament-Server starten.
    server = spawn(process.execPath, [SERVER_JS], {
      env: { ...process.env, PORT: String(port) },
      stdio: ['ignore', fs.openSync(serverLog, 'a'), fs.openSync(serverLog, 'a')],
    });

    const healthy = await waitFor(async () => {
      try {
        const r = await httpReq('GET', `${base}/health`);
        return r.status === 200 && r.json && r.json.ok === true;
      } catch (e) { return false; }
    }, 8000);
    assert.ok(healthy, 'Tournament-Server muss /health liefern');

    // 2) Spieler registrieren + Match anlegen.
    const regA = await httpReq('POST', `${base}/register`, { player_id: 'player_a' });
    const regB = await httpReq('POST', `${base}/register`, { player_id: 'player_b' });
    assert.strictEqual(regA.status, 200);
    assert.strictEqual(regB.status, 200);

    const mc = await httpReq('POST', `${base}/match/create`,
      { players: ['player_a', 'player_b'], rounds: 3 });
    assert.strictEqual(mc.status, 200);
    const matchId = mc.json && mc.json.match_id;
    assert.ok(matchId, 'match/create liefert match_id');

    // 3) Relay für player_a starten (pollt die Outbox).
    fs.writeFileSync(fakeLog, '[RBBATTLE] event=score_update score=0\n');

    // Fake rbbridge-Pipe: FIFO (Named-Pipe-Ersatz unter Linux). Kein
    // externer "cat"-Leser mehr (Issue #73): dispatch_exec liest nach dem
    // Schreiben auf DEMSELBEN Handle die exec_result-Antwort (PIPE_ACCESS_
    // DUPLEX-Aequivalent) - eine FIFO ist aber nur eine einzelne Queue, also
    // bekommt der Relay deterministisch seine eigene gerade geschriebene
    // Zeile zurueck, bevor ein externer Reader je etwas sieht (verifiziert;
    // bei einer echten Windows-Named-Pipe mit getrennten Puffern je Richtung
    // passiert das nicht). Byte-Inhalt der exec-Zeile ist stattdessen per
    // FIFO in bausteine/07-relay/test_dispatch.py (PipeClientTest) und die
    // Response-Verarbeitung per os.pipe() in ReadResultTest abgedeckt; hier
    // pruefen wir nur, dass der Dispatch gegen eine echte Pipe nicht bricht.
    const pipePath = path.join(dir, 'fake_pipe');
    execSync(`mkfifo "${pipePath}"`);

    relay = spawn('python3', [RELAY_PY], {
      env: {
        ...process.env,
        RBB_PLAYER_ID: 'player_a',
        RBB_MATCH_ID: matchId,
        RBB_LOG_PATH: fakeLog,
        RBB_SERVER: base,
        RBB_POLL_S: '0.2',
        RBB_PIPE_PATH: pipePath,
        // Ohne echten rbbridge-Responder wartet dispatch_exec sonst den
        // vollen Default (5s) auf eine exec_result-Antwort, die nie kommt.
        RBB_PIPE_TIMEOUT_S: '1',
      },
      stdio: ['ignore', fs.openSync(relayLog, 'a'), fs.openSync(relayLog, 'a')],
    });

    const registered = await waitFor(() =>
      fs.readFileSync(relayLog, 'utf8').includes('register: ok player_id=player_a'), 8000);
    assert.ok(registered, 'Relay registriert sich am Server');

    // 4) Web-UI-Trigger simulieren: exakt der POST, den app.js sendet.
    const ec = await httpReq('POST', `${base}/event`, {
      match_id: matchId,
      player_id: 'player_a',
      event: { type: 'exec_command', command: 'rb_wave 3' },
    });
    assert.strictEqual(ec.status, 200, `POST /event exec_command -> 200 (got ${ec.status})`);
    assert.strictEqual(ec.json && ec.json.event, 'exec_command');

    // 5) Relay muss das Kommando pollen und auf die Fake-Pipe dispatchen.
    const dispatched = await waitFor(() =>
      fs.readFileSync(relayLog, 'utf8').includes('dispatch sent cmd_id='), 8000);
    assert.ok(dispatched, 'Relay loggt "dispatch sent cmd_id="');

    // Kein exec_result von der Fake-Pipe (kein Responder) -> Timeout, aber
    // kein Haenger im Dispatch-Thread (Issue #73).
    const timedOut = await waitFor(() =>
      fs.readFileSync(relayLog, 'utf8').includes('dispatch result cmd_id=1 status=timeout'), 8000);
    assert.ok(timedOut, 'Relay meldet status=timeout statt zu haengen');

    // Server-Seite: exec_command control -> player_a protokolliert.
    assert.ok(
      fs.readFileSync(serverLog, 'utf8').includes('exec_command control -> player_a'),
      'Server loggt exec_command control -> player_a',
    );

    // Das Dispatch-Ergebnis wird an den Server gemeldet (Issue #89, AC aus
    // #73) und dort protokolliert -> die Web-UI sieht es per SSE.
    const reported = await waitFor(() =>
      fs.readFileSync(relayLog, 'utf8').includes('dispatch result reported cmd_id=1 status=timeout'), 8000);
    assert.ok(reported, 'Relay meldet das Dispatch-Ergebnis an den Server');
    const serverSawResult = await waitFor(() =>
      fs.readFileSync(serverLog, 'utf8')
        .includes('exec_result player_a command="rb_wave 3" ok=false status=timeout'), 8000);
    assert.ok(serverSawResult, 'Server protokolliert exec_result (Web-UI-Feedback)');
  } finally {
    if (relay) relay.kill('SIGTERM');
    if (server) server.kill('SIGTERM');
    await new Promise((r) => setTimeout(r, 200));
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

// ---------------------------------------------------------------------------
// OFFEN: Live-Client-Schritte (kein Windows-Spielprozess in CI/Linux)
// ---------------------------------------------------------------------------

test('Relay → rbbridge-Pipe: Pipe fehlt → dispatch failed reason=pipe_unavailable (kein ack)', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rb60-pipe-'));
  try {
    const relayDir = path.join(ROOT, 'bausteine', '07-relay');
    const pyFile = path.join(dir, 'harness.py');
    fs.writeFileSync(pyFile, `import io, json, sys, contextlib
sys.path.insert(0, ${JSON.stringify(relayDir)})
import relay
class StubPipe:
    def send_exec_and_wait(self, command, cmd_id):
        raise OSError('pipe gone (fake)')
cfg = {'log_path':'/tmp/x','player_id':'p','match_id':'m','server':'http://x','poll_s':1.0}
r = relay.Relay(cfg, pipe=StubPipe())
r.handle_outgoing({'event':'exec_command','command':'rb_wave 3','cmd_id':7})
due, seq, item = r.dispatch_queue.get_nowait()
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    ok = r._handle_dispatch_item(item)
print('RESULT ' + json.dumps({'ok': ok, 'acked': 7 in r.acked, 'pending': 7 in r.pending_keys, 'log': buf.getvalue()}))
`);
    const out = execSync(`python3 ${pyFile}`, { encoding: 'utf8' });
    const line = out.split('\n').map((s) => s.trim()).filter((s) => s.startsWith('RESULT ')).pop();
    assert.ok(line, 'Harness muss RESULT liefern');
    const res = JSON.parse(line.slice('RESULT '.length));
    assert.strictEqual(res.ok, false, 'Pipe-Fehler -> kein Erfolg');
    assert.strictEqual(res.acked, false, 'Kommando darf nicht als ack verbrennen');
    assert.strictEqual(res.pending, true, 'Kommando bleibt fuer Retry gemerkt');
    assert.ok(res.log.includes('dispatch failed reason=pipe_unavailable'),
      'Log enthält dispatch failed reason=pipe_unavailable');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('OFFEN: ExecuteCommand im echten Spielprozess (DLL-Injection, Windows)',
  { skip: 'OFFEN: braucht riftbreaker_dll_win_release.dll + injizierte rbbridge.dll — Windows-only, nicht CI-fähig' },
  () => {});

test('OFFEN: Spawn am headless Client sichtbar (Screenshot-Beweis)',
  { skip: 'OFFEN: braucht den laufenden headless Game-Client (Dedi) + Screenshot — nur Operator live' },
  () => {});
