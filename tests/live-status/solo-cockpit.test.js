'use strict';

// Solo „cockpit console" Match-Page (Issue #173, ref #159) — Tests.
//
//  1. Unit: deriveCockpit() mappt den rohen /state auf die Cockpit-View
//     (Phase SETUP/WAVES/GAME OVER, Wave, HQ-Integrität, P1/P2, Announce
//     COMMENCE/GAME OVER) — defensiv bei fehlenden Feldern.
//  2. Statisch: site/solo.html verdrahtet das Cockpit (Sektionen + Widget).
//  3. HTTP-Mock: createCockpit() pollt /state und ruft render(view).
//  4. God-Commands (#159/#266): createGodPanel() sendet nur erreichbare
//     Aktionen an existierende Referee-Endpoints (/report, /rematch, /wave);
//     nicht erreichbare Kommandos sind geparkt (kein POST). Fehlender
//     Endpoint degradiert defensiv. waveResult() wertet das exec_result aus.
//     #266: Client-Timeout (kein „SENDE …"-Haenger), waveOutcome() mappt
//     Erfolg/Fehler/unklare Antwort, bindCommandButton() sperrt den Button
//     waehrend eines In-Flight-Kommandos (kein Doppel-POST).
//  5. Gate: createAccessGate() — optional, ohne Secret im Repo.

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');

const ROOT = path.join(__dirname, '..', '..');
const MODULE_PATH = path.join(ROOT, 'site', 'solo-cockpit.js');
const SOLO_HTML = path.join(ROOT, 'site', 'solo.html');
const CSS_PATH = path.join(ROOT, 'site', 'solo-cockpit.css');

const cockpit = require(MODULE_PATH);
const { deriveCockpit, createCockpit, createGodPanel, createAccessGate } = cockpit;

function state(phase, extra) {
  return Object.assign({
    match_id: 'rift-1',
    mode: 'sp',
    phase: phase,
    round: 4,
    hq_hp_start: 100,
    winner: null,
    teams: {
      A: { player: 'momo', ready: true, hq_hp: 78, score: 1240, wave: 4 },
      B: { player: 'MIRROR', ready: true, hq_hp: 78, score: 900, wave: 4 },
    },
  }, extra || {});
}

// ---------------------------------------------------------------------------
// 1) Unit: deriveCockpit
// ---------------------------------------------------------------------------

test('deriveCockpit: running -> WAVES + COMMENCE-Announce + Wave/HQ/P1/P2', () => {
  const v = deriveCockpit(state('running'));
  assert.strictEqual(v.phase, 'running');
  assert.strictEqual(v.phaseLabel, 'WAVES');
  assert.strictEqual(v.waveText, 'WELLE 04');
  assert.strictEqual(v.hq.pct, 78);
  assert.strictEqual(v.hq.critical, false);
  assert.strictEqual(v.players.p1.player, 'momo');
  assert.strictEqual(v.players.p2.player, 'MIRROR');
  assert.strictEqual(v.players.p1.hpPct, 78);
  assert.deepStrictEqual(v.announce.kind, 'commence');
  assert.match(v.announce.text, /COMMENCE/);
  assert.match(v.announce.text, /momo/);
});

test('deriveCockpit: finished -> GAME OVER-Announce mit Sieger (lobby -> SETUP/idle)', () => {
  const done = deriveCockpit(state('finished', { winner: 'A' }));
  assert.strictEqual(done.phaseLabel, 'GAME OVER');
  assert.strictEqual(done.announce.kind, 'gameover');
  assert.match(done.announce.text, /GAME OVER/);
  assert.match(done.announce.text, /momo/);

  const lobby = deriveCockpit(state('lobby'));
  assert.strictEqual(lobby.phaseLabel, 'SETUP');
  assert.strictEqual(lobby.announce.kind, 'idle');
});

test('deriveCockpit: HQ-Wert wird geklemmt, <20% ist kritisch; leere Teams crashen nicht', () => {
  const high = deriveCockpit({
    phase: 'running', hq_hp_start: 100,
    teams: { A: { hq_hp: 150 }, B: { hq_hp: -5 } },
  });
  assert.strictEqual(high.hq.pct, 100);

  const low = deriveCockpit({
    phase: 'running', hq_hp_start: 100,
    teams: { A: { hq_hp: 12 }, B: {} },
  });
  assert.strictEqual(low.hq.pct, 12);
  assert.strictEqual(low.hq.critical, true);

  const empty = deriveCockpit(null);
  assert.strictEqual(empty.phaseLabel, 'SETUP');
  assert.strictEqual(empty.hq.pct, 100);
  assert.strictEqual(empty.announce.kind, 'idle');
  assert.strictEqual(empty.waveText, 'WELLE --');
});

// ---------------------------------------------------------------------------
// 2) Statisch: Verdrahtung + Checklisten-Sektionen in site/solo.html
// ---------------------------------------------------------------------------

test('site/solo.html verdrahtet das Cockpit-Widget + Stylesheet', () => {
  const html = fs.readFileSync(SOLO_HTML, 'utf8');
  assert.ok(html.includes('id="cockpit"'), 'Cockpit-Sektion vorhanden');
  assert.ok(html.includes('id="godcommands"'), 'God-Commands-Panel vorhanden');
  assert.ok(html.includes('id="ckGate"'), 'Operator-Gate vorhanden');
  assert.ok(html.includes('script src="solo-cockpit.js"'), 'Cockpit-Modul eingebunden');
  assert.ok(html.includes('RBSoloCockpit'), 'Cockpit-Modul global referenziert');
  assert.ok(html.includes('createCockpit('), 'State-Strip-Widget initialisiert');
  assert.ok(html.includes('createGodPanel('), 'God-Panel initialisiert');
  assert.ok(html.includes('createAccessGate('), 'Gate initialisiert');
  assert.ok(html.includes('href="solo-cockpit.css"'), 'Cockpit-Stylesheet verlinkt');
});

test('site/solo.html enthält die Design-Sektionen (Top-Bar, State-Strip, Announce, God, Footer)', () => {
  const html = fs.readFileSync(SOLO_HTML, 'utf8');
  // Top-Bar: LIVE-Pill + Padlock (Zugang gesichert).
  assert.ok(html.includes('id="ckLiveText"'), 'LIVE-Pill vorhanden');
  assert.ok(/ACCESS: SECURED/.test(html), 'Padlock-Zugangsanzeige vorhanden');
  // State-Strip: Phase SETUP/WAVES/GAME OVER + HQ-Meter + P1/P2.
  assert.ok(/data-phase="setup"/.test(html), 'Phase SETUP');
  assert.ok(/data-phase="waves"/.test(html), 'Phase WAVES');
  assert.ok(/data-phase="gameover"/.test(html), 'Phase GAME OVER');
  assert.ok(html.includes('id="ckHqBar"'), 'HQ-Integritätsmeter vorhanden');
  assert.ok(html.includes('id="ckP1Name"') && html.includes('id="ckP2Name"'), 'Player-Chips P1/P2 vorhanden');
  // Announce-Banner.
  assert.ok(html.includes('id="ckAnnounce"'), 'Announce-Banner vorhanden');
  // God-Commands: 4 Aktionen, destruktive markiert.
  assert.ok(/WELLE SPAWNEN/.test(html) && html.includes('data-ck-cmd="wave_spawn"'), 'Wave-Spawn-Button vorhanden');
  const destructive = (html.match(/data-ck-destructive="1"/g) || []).length;
  assert.strictEqual(destructive, 2, 'genau 2 destruktive Buttons (HQ zerstören, Restart)');
  // Footer: Build-Version + Server.
  assert.ok(html.includes('id="ckBuild"') && html.includes('id="ckServer"'), 'Footer Build/Server vorhanden');
});

test('site/solo-cockpit.css: Design-Tokens (Canvas/Panel/Border/Cyan) + CRT-Scanlines', () => {
  const css = fs.readFileSync(CSS_PATH, 'utf8');
  assert.ok(/#0a1018/i.test(css) || /var\(--panel\)/.test(css), 'Panel-Fläche');
  assert.ok(/var\(--acc\)/.test(css), 'Cyan-Akzent');
  assert.ok(/clip-path/.test(css), 'harte 90°-Kanten (Chamfer)');
});

test('site/solo.html: Welle-Spawn verdrahtet, nur noch unerreichbare Commands geparkt', () => {
  const html = fs.readFileSync(SOLO_HTML, 'utf8');
  const parked = (html.match(/data-ck-parked="1"/g) || []).length;
  assert.strictEqual(parked, 1, 'genau 1 geparkter Button (Ressourcen)');
  assert.match(html, /data-ck-cmd="give_resources"[^>]*disabled/, 'Ressourcen-Button ist geparkt/disabled');
  // #266: Wave-Spawn ist verdrahtet (kein parked/disabled).
  assert.ok(html.includes('data-ck-cmd="wave_spawn"'), 'Wave-Spawn-Button verdrahtet');
  assert.doesNotMatch(html, /data-ck-cmd="wave_spawn"[^>]*disabled/, 'Wave-Spawn ist nicht disabled');
  assert.ok(html.includes('id="ckGodNote"'), 'Notiz zu verdrahteten/geparkten Kommandos vorhanden');
  assert.ok(/GEPARKT — FOLLOW-UP #159/.test(html), 'geparkte Buttons sind als Follow-up markiert');
  assert.ok(/POST \/wave/.test(html), 'God-Note dokumentiert POST /wave');
  // #266: Button-Controller + zentrales Status-Mapping statt inline-Fall-Through.
  assert.ok(html.includes('bindCommandButton('), 'Button-Controller verdrahtet');
  assert.ok(html.includes('commandOutcome('), 'commandOutcome im Status-Mapping');
});

test('deploy/: /solo-Zugangsschutz per Vault/ENV-Namen dokumentiert, kein Secret im Repo', () => {
  const tpl = fs.readFileSync(
    path.join(ROOT, 'deploy', 'roles', 'website', 'templates', 'rbmods.caddy.j2'),
    'utf8'
  );
  assert.ok(tpl.includes('basic_auth'), 'basic_auth im Caddy-Snippet');
  assert.ok(tpl.includes('solo_basic_auth_hash'), 'Hash-Variable referenziert');
  assert.ok(/rewrite \/solo \/solo\.html/.test(tpl), '/solo ohne Datei-Endung erreichbar');
  assert.ok(!/\$2[aby]\$/.test(tpl), 'kein bcrypt-Hash-Wert im Snippet');

  const vars = fs.readFileSync(
    path.join(ROOT, 'deploy', 'inventory', 'host_vars', 'planet', 'vars.yml'),
    'utf8'
  );
  assert.ok(vars.includes('vault_solo_basic_auth_hash'), 'Hash kommt aus dem Vault (Name dokumentiert)');
  assert.ok(!/\$2[aby]\$/.test(vars), 'kein bcrypt-Hash-Wert in vars.yml');

  const readme = fs.readFileSync(path.join(ROOT, 'deploy', 'README.md'), 'utf8');
  assert.ok(readme.includes('SOLO_BASIC_AUTH_HASH'), 'ENV-Variablenname dokumentiert');
  assert.ok(readme.includes('vault_solo_basic_auth_hash'), 'Vault-Variablenname dokumentiert');
});

test('site/solo-cockpit.css: geparkte God-Buttons + Notiz gestylt', () => {
  const css = fs.readFileSync(CSS_PATH, 'utf8');
  assert.ok(/\.ck-god-note/.test(css), 'Notiz-Stil vorhanden');
  assert.ok(/\.ck-cmd\[disabled\]/.test(css), 'disabled-Buttons gestylt');
});

// ---------------------------------------------------------------------------
// 3) HTTP-Mock: createCockpit pollt /state
// ---------------------------------------------------------------------------

function startServer(handler) {
  return new Promise((resolve) => {
    const srv = http.createServer(handler);
    srv.listen(0, '127.0.0.1', () => resolve(srv));
  });
}

test('createCockpit: /state -> render(view); Fehler -> onError (kein Crash)', async () => {
  let mode = 'running';
  const srv = await startServer((req, res) => {
    if (req.url === '/state' && mode === 'running') {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(state('running')));
    } else {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end('{"error":"kaputt"}');
    }
  });
  const base = `http://127.0.0.1:${srv.address().port}`;
  const views = [];
  let errors = 0;
  const widget = createCockpit({
    apiBase: base,
    render: (v) => views.push(v),
    onError: () => { errors++; },
  });
  try {
    await widget.refresh();
    assert.strictEqual(views.length, 1);
    assert.strictEqual(views[0].phaseLabel, 'WAVES');
    assert.strictEqual(views[0].announce.kind, 'commence');

    mode = 'down';
    await widget.refresh();
    assert.strictEqual(errors, 1);
    assert.strictEqual(views.length, 1, 'kein render bei Fehler');
  } finally {
    widget.stop();
    await new Promise((r) => srv.close(r));
  }
});

// ---------------------------------------------------------------------------
// 4) God-Commands
// ---------------------------------------------------------------------------

test('createGodPanel: destruktiv verlangt Bestätigung; Abbruch sendet NICHT', async () => {
  let posted = 0;
  const srv = await startServer((req, res) => {
    posted++;
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end('{"ok":true}');
  });
  const base = `http://127.0.0.1:${srv.address().port}`;
  const calls = [];
  const panel = createGodPanel({
    apiBase: base,
    confirm: () => false,
    onResult: (r) => calls.push(r),
  });
  try {
    const r = await panel.send('destroy_hq');
    assert.strictEqual(r.aborted, true);
    assert.strictEqual(posted, 0, 'kein POST bei Abbruch');
    assert.strictEqual(calls[0].aborted, true);
  } finally {
    await new Promise((r2) => srv.close(r2));
  }
});

test('createGodPanel: erreichbare Kommandos POSTen auf den echten Referee-Endpoint', async () => {
  const seen = [];
  const srv = await startServer((req, res) => {
    let body = '';
    req.on('data', (c) => { body += c; });
    req.on('end', () => {
      seen.push({ method: req.method, url: req.url, body: JSON.parse(body || '{}') });
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end('{"ok":true}');
    });
  });
  const base = `http://127.0.0.1:${srv.address().port}`;
  let asked = 0;
  const panel = createGodPanel({
    apiBase: base,
    confirm: () => { asked++; return true; },
    onResult: () => {},
  });
  try {
    // HQ zerstören (Test) -> POST /report mit hq_hp=0 (dokumentierter Testweg).
    const hq = await panel.send('destroy_hq');
    assert.strictEqual(hq.ok, true);
    assert.strictEqual(hq.action, 'destroy_hq');
    // Spiel neu starten -> POST /rematch (Reset in die Lobby).
    const re = await panel.send('restart');
    assert.strictEqual(re.ok, true);

    assert.strictEqual(asked, 2, 'destruktive Aktionen fragen je genau einmal nach');
    assert.deepStrictEqual(seen[0], {
      method: 'POST',
      url: '/report',
      body: { world: 'A', event: 'hq_hp', hp: 0 },
    });
    assert.deepStrictEqual(seen[1], { method: 'POST', url: '/rematch', body: {} });
  } finally {
    await new Promise((r2) => srv.close(r2));
  }
});

test('createGodPanel: wave_spawn POSTet /wave und liefert exec_result durch', async () => {
  const seen = [];
  const srv = await startServer((req, res) => {
    let body = '';
    req.on('data', (c) => { body += c; });
    req.on('end', () => {
      seen.push({ method: req.method, url: req.url, body: JSON.parse(body || '{}') });
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({
        ok: true, world: 'A', command: 'rb_wave 3',
        exec_result: { ok: true, results: [{ command: 'rb_wave 3', ok: true }] },
      }));
    });
  });
  const base = `http://127.0.0.1:${srv.address().port}`;
  const results = [];
  const panel = createGodPanel({ apiBase: base, confirm: () => true, onResult: (r) => results.push(r) });
  try {
    const r = await panel.send('wave_spawn');
    assert.strictEqual(r.ok, true);
    assert.strictEqual(r.action, 'wave_spawn');
    assert.deepStrictEqual(seen, [{ method: 'POST', url: '/wave', body: { world: 'A', n: 3 } }]);
    const w = cockpit.waveResult(r.data);
    assert.strictEqual(w.ok, true);
    assert.strictEqual(w.sent, true);
    assert.strictEqual(w.command, 'rb_wave 3');
    assert.strictEqual(results.length, 1);
  } finally {
    await new Promise((r2) => srv.close(r2));
  }
});

test('waveResult: ok nur bei exec_result ok:true (bzw. alle results[].ok)', () => {
  assert.deepStrictEqual(
    cockpit.waveResult({ ok: true, command: 'rb_wave 3', exec_result: { ok: true } }),
    {
      sent: true, ok: true, command: 'rb_wave 3', wave: 3, world: null,
      recognized: true, status: null, error: null, reason: null,
    },
  );
  assert.strictEqual(
    cockpit.waveResult({ ok: true, exec_result: { results: [{ ok: true }, { ok: true }] } }).ok,
    true,
  );
  const bad = cockpit.waveResult({
    ok: false, status: 503, error: 'Endpoint antwortete 503',
    exec_result: { ok: false, reason: 'pipe_unavailable' },
  });
  assert.strictEqual(bad.sent, false);
  assert.strictEqual(bad.ok, false);
  assert.strictEqual(bad.status, 503);
  assert.strictEqual(bad.reason, 'pipe_unavailable');
  assert.strictEqual(
    cockpit.waveResult({ ok: true, exec_result: { results: [{ ok: true }, { ok: false }] } }).ok,
    false,
  );
  // Review #271 Finding 2/3: Referee liefert 200 (Zustellung ok), aber
  // exec_result.ok=false (z. B. Mod-Timeout) -> ankommen, aber nicht ausgefuehrt.
  const delivered = cockpit.waveResult({
    ok: true, exec_ok: false, status: 200,
    exec_result: { ok: false, reason: 'no_response' },
  });
  assert.strictEqual(delivered.sent, true);
  assert.strictEqual(delivered.ok, false);
  assert.strictEqual(delivered.status, 200);
  assert.strictEqual(delivered.reason, 'no_response');
  assert.strictEqual(cockpit.waveResult(null).ok, false);
  assert.strictEqual(cockpit.waveResult({}).sent, false);
});

test('waveResult (#266): Welt + Wellennummer + recognized (kein Falsch-Erfolg)', () => {
  const ok = cockpit.waveResult({
    ok: true, world: 'A', command: 'rb_wave 7', exec_result: { ok: true },
  });
  assert.strictEqual(ok.world, 'A');
  assert.strictEqual(ok.wave, 7);
  assert.strictEqual(ok.recognized, true);

  // Fallback: Wellennummer aus dem Feld `n`, wenn `command` fehlt.
  assert.strictEqual(cockpit.waveResult({ ok: true, n: 5 }).wave, 5);
  assert.strictEqual(cockpit.waveResult({ ok: true, command: 'rb_wave x' }).wave, null);

  // 200 mit unparsebarem Body -> data=null -> NICHT erkannt.
  assert.strictEqual(cockpit.waveResult(null).recognized, false);
  assert.strictEqual(cockpit.waveResult({}).recognized, false);
  // Nur `ok` (boolean) traegt bereits als erkannte Antwort.
  assert.strictEqual(cockpit.waveResult({ ok: false }).recognized, true);
});

test('waveOutcome (#266): ok/exec-failed/unrecognized/error + Welle/Welt im Text', () => {
  const ok = cockpit.waveOutcome(cockpit.waveResult({
    ok: true, world: 'A', command: 'rb_wave 3', exec_result: { ok: true },
  }));
  assert.strictEqual(ok.kind, 'ok');
  assert.strictEqual(ok.ok, true);
  assert.match(ok.text, /WELLE 03/);
  assert.match(ok.text, /WELT A/);

  const failed = cockpit.waveOutcome(cockpit.waveResult({
    ok: true, world: 'A', command: 'rb_wave 3',
    exec_result: { ok: false, reason: 'no_response' },
  }));
  assert.strictEqual(failed.kind, 'exec-failed');
  assert.strictEqual(failed.ok, false);
  assert.match(failed.text, /no_response/);
  assert.match(failed.text, /WELLE 03/);
  assert.match(failed.text, /WELT A/);

  const junk = cockpit.waveOutcome(cockpit.waveResult(null));
  assert.strictEqual(junk.kind, 'unrecognized');
  assert.strictEqual(junk.ok, false);
  assert.doesNotMatch(junk.text, /^OK/);

  const err = cockpit.waveOutcome({ recognized: true, sent: false, ok: false, error: 'HTTP 503' });
  assert.strictEqual(err.kind, 'error');
  assert.match(err.text, /503/);
});

test('commandOutcome (#266): abgebrochen/geparkt/Fehler/wave/OK mit Klasse', () => {
  const aborted = cockpit.commandOutcome({ aborted: true, action: 'restart' });
  assert.strictEqual(aborted.cls, 'is-err');
  const parked = cockpit.commandOutcome({ parked: true, action: 'give_resources' });
  assert.match(parked.text, /GEPARKT/);
  assert.strictEqual(parked.cls, 'is-err');
  const err = cockpit.commandOutcome({ ok: false, action: 'restart', error: 'HTTP 404' });
  assert.strictEqual(err.cls, 'is-err');
  const wave = cockpit.commandOutcome({ ok: true, action: 'wave_spawn', data: null });
  assert.strictEqual(wave.kind, 'unrecognized');
  assert.strictEqual(wave.cls, 'is-err');
  const ok = cockpit.commandOutcome({ ok: true, action: 'wave_spawn', data: { ok: true, world: 'A', command: 'rb_wave 3', exec_result: { ok: true } } });
  assert.strictEqual(ok.cls, 'is-ok');
  assert.match(ok.text, /WELLE 03/);
  const other = cockpit.commandOutcome({ ok: true, action: 'restart' });
  assert.strictEqual(other.cls, 'is-ok');
});

test('createGodPanel: geparktes Kommando sendet nichts und meldet parked (Follow-up #159)', async () => {
  let posted = 0;
  const srv = await startServer((req, res) => { posted++; res.writeHead(200); res.end('{}'); });
  const base = `http://127.0.0.1:${srv.address().port}`;
  const results = [];
  const panel = createGodPanel({
    apiBase: base,
    confirm: () => true,
    onResult: (r) => results.push(r),
  });
  try {
    for (const action of ['give_resources']) {
      const r = await panel.send(action);
      assert.strictEqual(r.ok, false, action);
      assert.strictEqual(r.parked, true, action);
      assert.match(r.error, /Follow-up #159/, action);
    }
    assert.strictEqual(posted, 0, 'geparkt => kein POST');
    assert.strictEqual(results.length, 1);
  } finally {
    await new Promise((r2) => srv.close(r2));
  }
});

test('createGodPanel: unbekanntes Kommando wird abgelehnt', async () => {
  const panel = createGodPanel({ apiBase: 'http://127.0.0.1:1', confirm: () => true, onResult: () => {} });
  const r = await panel.send('nope');
  assert.strictEqual(r.ok, false);
  assert.match(r.error, /unbekannt/);
});

test('createGodPanel: fehlender Endpoint (404) degradiert defensiv mit Fehler', async () => {
  const srv = await startServer((req, res) => { res.writeHead(404); res.end(); });
  const base = `http://127.0.0.1:${srv.address().port}`;
  const results = [];
  const panel = createGodPanel({ apiBase: base, confirm: () => true, onResult: (r) => results.push(r) });
  try {
    const r = await panel.send('restart');
    assert.strictEqual(r.ok, false);
    assert.strictEqual(r.status, 404);
    assert.strictEqual(results.length, 1);
  } finally {
    await new Promise((r2) => srv.close(r2));
  }
});

test('isReachable: nur verdrahtete Kommandos sind erreichbar', () => {
  assert.strictEqual(cockpit.isReachable('destroy_hq'), true);
  assert.strictEqual(cockpit.isReachable('restart'), true);
  assert.strictEqual(cockpit.isReachable('wave_spawn'), true);
  assert.strictEqual(cockpit.isReachable('wave_toggle'), false); // entfernt (#266)
  assert.strictEqual(cockpit.isReachable('give_resources'), false);
  assert.strictEqual(cockpit.isReachable('nope'), false);
});

// ---------------------------------------------------------------------------
// 4b) #266: Button-Verdrahtung ohne Player (Timeout, Muell-Antwort, exec-fail)
// ---------------------------------------------------------------------------

/** Minimaler Fake-Button (ohne jsdom): Attribute, Events, disabled. */
function fakeButton(action) {
  const handlers = {};
  return {
    disabled: false,
    attrs: { 'data-ck-cmd': action },
    getAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : null; },
    setAttribute(name, value) { this.attrs[name] = String(value); },
    removeAttribute(name) { delete this.attrs[name]; },
    addEventListener(type, fn) { (handlers[type] = handlers[type] || []).push(fn); },
    click() {
      return Promise.all((handlers.click || []).map((fn) => fn({ preventDefault() {} })));
    },
  };
}

function closeServer(srv) {
  if (typeof srv.closeAllConnections === 'function') srv.closeAllConnections();
  return new Promise((resolve) => srv.close(resolve));
}

test('#266: Button -> POST /wave -> Status is-ok mit WELLE/WELT, Button danach frei', async () => {
  const seen = [];
  const srv = await startServer((req, res) => {
    let body = '';
    req.on('data', (c) => { body += c; });
    req.on('end', () => {
      seen.push({ method: req.method, url: req.url, body: JSON.parse(body || '{}') });
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({
        ok: true, world: 'A', command: 'rb_wave 3',
        exec_result: { ok: true, results: [{ command: 'rb_wave 3', ok: true }] },
      }));
    });
  });
  const base = `http://127.0.0.1:${srv.address().port}`;
  const statuses = [];
  const panel = createGodPanel({
    apiBase: base, confirm: () => true,
    onResult: (res) => statuses.push(cockpit.commandOutcome(res)),
  });
  const btn = fakeButton('wave_spawn');
  const bound = cockpit.bindCommandButton(btn, {
    panel, setStatus: (t) => statuses.push({ text: t, cls: null }),
  });
  try {
    await btn.click();
    assert.strictEqual(seen.length, 1, 'genau ein POST');
    assert.deepStrictEqual(seen[0].body, { world: 'A', n: 3 });
    assert.strictEqual(seen[0].url, '/wave');
    const last = statuses[statuses.length - 1];
    assert.strictEqual(last.cls, 'is-ok');
    assert.match(last.text, /WELLE 03/);
    assert.match(last.text, /WELT A/);
    assert.strictEqual(btn.disabled, false, 'Button nach Erfolg wieder frei');
    assert.strictEqual(btn.getAttribute('aria-busy'), null);
    assert.strictEqual(bound.isBusy(), false);
  } finally {
    await closeServer(srv);
  }
});

test('#266: Doppelklick loest genau einen POST aus (In-Flight-Sperre)', async () => {
  let posted = 0;
  const srv = await startServer((req, res) => {
    posted++;
    setTimeout(() => {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, world: 'A', command: 'rb_wave 3', exec_result: { ok: true } }));
    }, 30);
  });
  const base = `http://127.0.0.1:${srv.address().port}`;
  const panel = createGodPanel({ apiBase: base, confirm: () => true, onResult: () => {} });
  const btn = fakeButton('wave_spawn');
  cockpit.bindCommandButton(btn, { panel, setStatus: () => {} });
  try {
    const first = btn.click();
    assert.strictEqual(btn.disabled, true, 'waehrend In-Flight gesperrt');
    const second = btn.click(); // No-Op
    await Promise.all([first, second]);
    assert.strictEqual(posted, 1, 'kein zweiter POST');
    assert.strictEqual(btn.disabled, false);
  } finally {
    await closeServer(srv);
  }
});

test('#266: Haengender Relay -> Timeout -> Status is-err, Button frei (kein SENDE-Haenger)', async () => {
  const srv = await startServer(() => { /* antwortet nie */ });
  const base = `http://127.0.0.1:${srv.address().port}`;
  const statuses = [];
  const results = [];
  const panel = createGodPanel({
    apiBase: base, confirm: () => true, timeoutMs: 50,
    onResult: (res) => { results.push(res); statuses.push(cockpit.commandOutcome(res)); },
  });
  const btn = fakeButton('wave_spawn');
  cockpit.bindCommandButton(btn, { panel, setStatus: (t) => statuses.push({ text: t, cls: null }) });
  try {
    await btn.click();
    assert.strictEqual(results.length, 1, 'onResult genau einmal');
    assert.strictEqual(results[0].ok, false);
    assert.strictEqual(results[0].timeout, true);
    assert.match(results[0].error, /Zeit/);
    assert.strictEqual(btn.disabled, false, 'Button nach Timeout frei');
    assert.strictEqual(btn.getAttribute('aria-busy'), null);
    const last = statuses[statuses.length - 1];
    assert.strictEqual(last.cls, 'is-err');
    assert.match(last.text, /FEHLER/);
    assert.match(last.text, /Zeit/);
  } finally {
    await closeServer(srv);
  }
});

test('#266: 200 mit Muell-Body -> unrecognized, nicht OK', async () => {
  const srv = await startServer((req, res) => {
    res.writeHead(200, { 'Content-Type': 'text/html' });
    res.end('<html>kein json</html>');
  });
  const base = `http://127.0.0.1:${srv.address().port}`;
  const results = [];
  const panel = createGodPanel({ apiBase: base, confirm: () => true, onResult: (r) => results.push(r) });
  try {
    const r = await panel.send('wave_spawn');
    assert.strictEqual(r.ok, true, 'Transport OK');
    const out = cockpit.waveOutcome(cockpit.waveResult(r.data));
    assert.strictEqual(out.kind, 'unrecognized');
    assert.strictEqual(out.ok, false);
  } finally {
    await closeServer(srv);
  }
});

test('#266: exec_result.ok=false -> exec-failed mit Grund + Welle/Welt', async () => {
  const srv = await startServer((req, res) => {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({
      ok: true, world: 'A', command: 'rb_wave 3',
      exec_result: { ok: false, reason: 'no_response' },
    }));
  });
  const base = `http://127.0.0.1:${srv.address().port}`;
  const panel = createGodPanel({ apiBase: base, confirm: () => true, onResult: () => {} });
  try {
    const r = await panel.send('wave_spawn');
    const out = cockpit.waveOutcome(cockpit.waveResult(r.data));
    assert.strictEqual(out.kind, 'exec-failed');
    assert.match(out.text, /no_response/);
    assert.match(out.text, /WELLE 03/);
    assert.match(out.text, /WELT A/);
  } finally {
    await closeServer(srv);
  }
});

// ---------------------------------------------------------------------------
// 5) Operator-Gate (optional, kein Secret im Repo)
// ---------------------------------------------------------------------------

test('createAccessGate: disabled -> offen; enabled -> gesperrt bis unlock', () => {
  const store = {
    _m: {},
    getItem(k) { return Object.prototype.hasOwnProperty.call(this._m, k) ? this._m[k] : null; },
    setItem(k, v) { this._m[k] = String(v); },
    removeItem(k) { delete this._m[k]; },
  };

  const off = createAccessGate({ enabled: false, storage: store });
  assert.strictEqual(off.isUnlocked(), true);

  const on = createAccessGate({ enabled: true, storage: store });
  assert.strictEqual(on.isUnlocked(), false);
  assert.strictEqual(on.unlock('   '), false, 'leerer Code wird abgelehnt');
  assert.strictEqual(on.unlock('operator-code'), true);
  assert.strictEqual(on.isUnlocked(), true);
  on.lock();
  assert.strictEqual(on.isUnlocked(), false);
});
