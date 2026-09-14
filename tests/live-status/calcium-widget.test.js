'use strict';

// Kalzium-Dashboard-Widget (Issue #383) — Tests.
//
//  1. Unit: deriveCalcium() leitet aus POST /get_state die Anzeige-Zeile ab
//     ("Kalzium: N" bzw. "Kalzium: —" bei ok:false/nicht erreichbar).
//  2. Statisch: site/calcium.html verdrahtet das Widget (Meta, Container, Script).
//  3. HTTP-Mock: createWidget() pollt einen Mock-Server (Fixture /get_state)
//     und degradiert defensiv zu "—", sobald die Bridge Fehler liefert.

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');

const ROOT = path.join(__dirname, '..', '..');
const MODULE_PATH = path.join(ROOT, 'site', 'calcium-widget.js');
const CALCIUM_HTML = path.join(ROOT, 'site', 'calcium.html');

const calciumMod = require(MODULE_PATH);
const { deriveCalcium, createWidget, resolveApiBase, FIXED_POINT_SCALE } = calciumMod;

// ---------------------------------------------------------------------------
// Fixtures (Aufbau nach bausteine/04-trainer-io/rbbridge/rbbridge.c
// dispatch_get_state(), feature/363-dedicated-io-interface)
// ---------------------------------------------------------------------------

function getStateOk(carbonium, resources) {
  return { event: 'get_state_result', ok: true, carbonium: carbonium, resources: resources || [] };
}

// ---------------------------------------------------------------------------
// 1) Unit: deriveCalcium
// ---------------------------------------------------------------------------

test('deriveCalcium: ok:true mit Fixed-Point-Wert (300.0 carbonium)', () => {
  const r = deriveCalcium(getStateOk(300 * FIXED_POINT_SCALE));
  assert.strictEqual(r.ok, true);
  assert.strictEqual(r.value, 300);
  assert.strictEqual(r.line, 'Kalzium: 300');
});

test('deriveCalcium: rundet auf ganze Zahl', () => {
  const r = deriveCalcium(getStateOk(300.4 * FIXED_POINT_SCALE));
  assert.strictEqual(r.value, 300);
});

test('deriveCalcium: ok:false (z. B. pipe_unavailable) → "Kalzium: —"', () => {
  const r = deriveCalcium({ ok: false, reason: 'pipe_unavailable' });
  assert.strictEqual(r.ok, false);
  assert.strictEqual(r.line, 'Kalzium: —');
  assert.strictEqual(r.sub, 'pipe_unavailable');
});

test('deriveCalcium: no_account (kein Spieler online) → "Kalzium: —"', () => {
  const r = deriveCalcium({ ok: false, reason: 'no_account' });
  assert.strictEqual(r.ok, false);
  assert.strictEqual(r.sub, 'no_account');
});

test('deriveCalcium: fehlendes/ungueltiges carbonium-Feld → "Kalzium: —"', () => {
  for (const bad of [null, undefined, {}, { ok: true }, { ok: true, carbonium: 'x' }, 'y']) {
    const r = deriveCalcium(bad);
    assert.strictEqual(r.ok, false, `input=${JSON.stringify(bad)}`);
    assert.strictEqual(r.line, 'Kalzium: —');
  }
});

test('resolveApiBase: Meta-Tag hat Vorrang, sonst Default "/bridge"', () => {
  const docWithMeta = {
    querySelector: (sel) =>
      sel === 'meta[name="rb-bridge-api"]' ? { content: ' https://ref.example/x/' } : null,
  };
  assert.strictEqual(resolveApiBase(docWithMeta), 'https://ref.example/x');
  const docWithoutMeta = { querySelector: () => null };
  assert.strictEqual(resolveApiBase(docWithoutMeta), '/bridge');
  assert.strictEqual(resolveApiBase(undefined), '/bridge');
});

// ---------------------------------------------------------------------------
// 2) Statisch: Verdrahtung in site/calcium.html
// ---------------------------------------------------------------------------

test('site/calcium.html verdrahtet das Kalzium-Widget', () => {
  const html = fs.readFileSync(CALCIUM_HTML, 'utf8');
  assert.ok(html.includes('meta name="rb-bridge-api"'), 'Meta-Tag für Bridge-API-Basis vorhanden');
  assert.ok(html.includes('id="value"'), 'Wert-Container vorhanden');
  assert.ok(html.includes('script src="calcium-widget.js"'), 'Modul-Script eingebunden');
  assert.ok(html.includes('RBCalciumWidget.createWidget'), 'Widget-Init ruft createWidget auf');
});

// ---------------------------------------------------------------------------
// 3) HTTP-Mock: createWidget pollt POST /get_state und degradiert defensiv
// ---------------------------------------------------------------------------

function startServer(handler) {
  return new Promise((resolve) => {
    const srv = http.createServer(handler);
    srv.listen(0, '127.0.0.1', () => resolve(srv));
  });
}

test('createWidget: Fixture /get_state → korrekte Zahl, danach ok:false → "—"', async () => {
  let mode = 'ok';
  const srv = await startServer((req, res) => {
    if (req.url === '/get_state' && req.method === 'POST') {
      if (mode === 'ok') {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify(getStateOk(300 * FIXED_POINT_SCALE)));
      } else {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: false, reason: 'pipe_unavailable' }));
      }
    } else {
      res.writeHead(404);
      res.end();
    }
  });

  const base = `http://127.0.0.1:${srv.address().port}`;
  let last = null;
  const widget = createWidget({
    apiBase: base,
    render: (st) => { last = st; },
  });

  try {
    await widget.refresh();
    assert.strictEqual(last.ok, true);
    assert.strictEqual(last.line, 'Kalzium: 300');

    mode = 'down';
    await widget.refresh();
    assert.strictEqual(last.ok, false);
    assert.strictEqual(last.line, 'Kalzium: —');
  } finally {
    widget.stop();
    await new Promise((r) => srv.close(r));
  }
});

test('createWidget: Netzwerkfehler (Connection refused) → "Kalzium: —"', async () => {
  const srv = await startServer(() => {});
  const port = srv.address().port;
  await new Promise((r) => srv.close(r));

  let last = null;
  const widget = createWidget({
    apiBase: `http://127.0.0.1:${port}`,
    render: (st) => { last = st; },
  });

  try {
    await widget.refresh();
    assert.strictEqual(last.ok, false);
    assert.strictEqual(last.line, 'Kalzium: —');
  } finally {
    widget.stop();
  }
});
