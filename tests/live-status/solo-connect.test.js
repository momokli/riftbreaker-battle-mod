'use strict';

// Solo-Connect-Widget (Issue #97) — Tests.
//
//  1. Unit: normalizeAddress() normalisiert die Server-Adresse (Fallback,
//     Trim, Trailing-Slash).
//  2. Statisch: site/solo.html verdrahtet das Widget (Input, Buttons, Script).
//  3. HTTP-Mock: createSoloConnect() verbindet (GET /state) und startet
//     (POST /sp); degradiert defensiv bei nicht erreichbarem Server und
//     blockiert den Start ohne Verbindung/Name.

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');

const ROOT = path.join(__dirname, '..', '..');
const MODULE_PATH = path.join(ROOT, 'site', 'solo-connect.js');
const SOLO_HTML = path.join(ROOT, 'site', 'solo.html');

const { normalizeAddress, createSoloConnect, DEFAULT_API_BASE } = require(MODULE_PATH);

// ---------------------------------------------------------------------------
// 1) Unit: normalizeAddress
// ---------------------------------------------------------------------------

test('normalizeAddress: leer/whitespace/null → Fallback', () => {
  assert.strictEqual(normalizeAddress('', '/tournament'), '/tournament');
  assert.strictEqual(normalizeAddress('   ', '/tournament'), '/tournament');
  assert.strictEqual(normalizeAddress(null, '/tournament'), '/tournament');
  assert.strictEqual(normalizeAddress(undefined, undefined), DEFAULT_API_BASE);
});

test('normalizeAddress: trimmt und entfernt Trailing-Slash', () => {
  assert.strictEqual(normalizeAddress('  https://ref.example.com/  ', '/x'), 'https://ref.example.com');
  assert.strictEqual(normalizeAddress('/tournament//', '/x'), '/tournament');
  assert.strictEqual(normalizeAddress('https://ref.example.com', '/x'), 'https://ref.example.com');
});

test('normalizeAddress: Fallback wird ebenfalls normalisiert', () => {
  assert.strictEqual(normalizeAddress('', '/tournament/'), '/tournament');
});

// ---------------------------------------------------------------------------
// 2) Statisch: Verdrahtung in site/solo.html
// ---------------------------------------------------------------------------

test('site/solo.html verdrahtet das Solo-Connect-Widget', () => {
  const html = fs.readFileSync(SOLO_HTML, 'utf8');
  assert.ok(html.includes('id="serverAddr"'), 'Eingabefeld Server-Adresse vorhanden');
  assert.ok(html.includes('id="connectBtn"'), 'Verbinden-Button vorhanden');
  assert.ok(html.includes('id="startBtn"'), 'Spielstart-Button vorhanden');
  assert.ok(html.includes('id="connectStatus"'), 'Status-Feedback vorhanden');
  assert.ok(html.includes('script src="solo-connect.js"'), 'Modul-Script eingebunden');
  assert.ok(html.includes('RBSoloConnect.createSoloConnect'), 'Widget-Init ruft createSoloConnect auf');
});

// ---------------------------------------------------------------------------
// 3) HTTP-Mock: createSoloConnect
// ---------------------------------------------------------------------------

function startServer(handler) {
  return new Promise((resolve) => {
    const srv = http.createServer(handler);
    srv.listen(0, '127.0.0.1', () => resolve(srv));
  });
}

test('createSoloConnect: connect (GET /state ok) → verbunden, start (POST /sp) → gestartet', async () => {
  let spReceived = null;
  const srv = await startServer((req, res) => {
    if (req.method === 'GET' && req.url === '/state') {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ match_id: 'rift-1', mode: 'duel', phase: 'lobby', round: 0 }));
    } else if (req.method === 'POST' && req.url === '/sp') {
      let body = '';
      req.on('data', (c) => { body += c; });
      req.on('end', () => {
        spReceived = JSON.parse(body);
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ started: true, phase: 'running', round: 1, mode: 'sp' }));
      });
    } else {
      res.writeHead(404);
      res.end();
    }
  });

  const base = `http://127.0.0.1:${srv.address().port}`;
  const widget = createSoloConnect({
    apiBase: base,
    getAddress: () => '',
    onState: () => {},
  });

  try {
    const c = await widget.connect();
    assert.strictEqual(c.ok, true);
    assert.strictEqual(c.phase, 'lobby');
    assert.strictEqual(widget.isConnected(), true);

    const s = await widget.start('momo');
    assert.strictEqual(s.ok, true);
    assert.strictEqual(s.started, true);
    assert.strictEqual(s.mode, 'sp');
    assert.deepStrictEqual(spReceived, { player: 'momo' });
  } finally {
    await new Promise((r) => srv.close(r));
  }
});

test('createSoloConnect: connect bei nicht erreichbarem Server → Fehler, start blockiert', async () => {
  const srv = await startServer(() => {});
  const port = srv.address().port;
  await new Promise((r) => srv.close(r));

  const widget = createSoloConnect({
    apiBase: `http://127.0.0.1:${port}`,
    getAddress: () => '',
    onState: () => {},
  });

  const c = await widget.connect();
  assert.strictEqual(c.ok, false);
  assert.strictEqual(c.connected, false);
  assert.strictEqual(widget.isConnected(), false);

  const s = await widget.start('momo');
  assert.strictEqual(s.ok, false);
  assert.ok(/verbinden/.test(s.error));
});

test('createSoloConnect: start ohne Name → Fehler "Name eingeben"', async () => {
  const srv = await startServer((req, res) => {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ phase: 'lobby', mode: 'duel', round: 0 }));
  });
  const base = `http://127.0.0.1:${srv.address().port}`;
  const widget = createSoloConnect({ apiBase: base, getAddress: () => '', onState: () => {} });

  try {
    await widget.connect();
    const s = await widget.start('   ');
    assert.strictEqual(s.ok, false);
    assert.ok(/Name/.test(s.error));
  } finally {
    await new Promise((r) => srv.close(r));
  }
});
