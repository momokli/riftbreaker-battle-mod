'use strict';

// Solo Dev-Log Widget (Issue #98) — Tests.
//
//  1. Unit: describeEvent() leitet aus einem rohen Feed-Event ({seq,t,kind,
//     msg}, GET /events) eine Render-Spec ab — bekannte kinds bekommen eine
//     Farbklasse, unbekannte/künftige kinds fallen NICHT weg (generischer
//     Fallback "[<kind>] <msg>").
//  2. Statisch: site/solo.html verdrahtet das Widget (Meta, Container, Script).
//  3. HTTP-Mock: createLogWidget() pollt den Cursor-Feed (GET /events?since=),
//     verliert keine Events, holt bei wiederholtem Poll keine Duplikate und
//     degradiert defensiv bei Netz-/HTTP-Fehlern (kein Reload, kein Crash).

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');

const ROOT = path.join(__dirname, '..', '..');
const MODULE_PATH = path.join(ROOT, 'site', 'dev-log.js');
const SOLO_HTML = path.join(ROOT, 'site', 'solo.html');

const devLog = require(MODULE_PATH);
const { describeEvent, createLogWidget, resolveApiBase } = devLog;

// ---------------------------------------------------------------------------
// 1) Unit: describeEvent
// ---------------------------------------------------------------------------

test('describeEvent: bekannte kinds bekommen ihre Farbklasse + "[kind] msg"-Text', () => {
  const cases = [
    ['register', 'dl-join'],
    ['ready', 'dl-sys'],
    ['go', 'dl-sys'],
    ['send', 'dl-send'],
    ['wave', 'dl-wave'],
    ['reveal', 'dl-wave'],
    ['hq', 'dl-hq'],
    ['finish', 'dl-hl'],
    ['match_end', 'dl-hl'],
    ['score', 'dl-sys'],
    ['rematch', 'dl-sys'],
  ];
  for (const [kind, cls] of cases) {
    const r = describeEvent({ seq: 5, t: 123, kind, msg: 'hallo welt' });
    assert.strictEqual(r.cls, cls, `kind=${kind}`);
    assert.strictEqual(r.kind, kind);
    assert.strictEqual(r.seq, 5);
    assert.strictEqual(r.text, `[${kind}] hallo welt`);
  }
});

test('describeEvent: unbekannter/künftiger kind fällt NICHT weg (generischer Fallback)', () => {
  const r = describeEvent({ seq: 9, kind: 'brandneu_v2', msg: 'irgendwas Neues' });
  assert.strictEqual(r.cls, 'dl-sys');
  assert.strictEqual(r.kind, 'brandneu_v2');
  assert.strictEqual(r.text, '[brandneu_v2] irgendwas Neues');
});

test('describeEvent: fehlendes kind -> "unknown", fehlende/non-string msg defensiv behandelt', () => {
  const noKind = describeEvent({ seq: 1, msg: 'x' });
  assert.strictEqual(noKind.kind, 'unknown');
  assert.strictEqual(noKind.text, '[unknown] x');

  const noMsg = describeEvent({ seq: 2, kind: 'send' });
  assert.strictEqual(noMsg.text, '[send] ');

  const objMsg = describeEvent({ seq: 3, kind: 'score', msg: { a: 1 } });
  assert.strictEqual(objMsg.text, '[score] {"a":1}');
});

test('describeEvent: ungültiges Event (null/kein Objekt) crasht nicht', () => {
  for (const bad of [null, undefined, 'x', 42]) {
    const r = describeEvent(bad);
    assert.strictEqual(r.cls, 'dl-sys');
    assert.strictEqual(typeof r.text, 'string');
  }
});

test('resolveApiBase: Meta-Tag hat Vorrang, sonst Default (wie live-status.js)', () => {
  const docWithMeta = {
    querySelector: (sel) =>
      sel === 'meta[name="rb-tournament-api"]' ? { content: ' https://ref.example/x/' } : null,
  };
  assert.strictEqual(resolveApiBase(docWithMeta), 'https://ref.example/x');
  const docWithoutMeta = { querySelector: () => null };
  assert.strictEqual(resolveApiBase(docWithoutMeta), '/tournament');
});

// ---------------------------------------------------------------------------
// 2) Statisch: Verdrahtung in site/solo.html
// ---------------------------------------------------------------------------

test('site/solo.html verdrahtet das Dev-Log-Widget', () => {
  const html = fs.readFileSync(SOLO_HTML, 'utf8');
  assert.ok(html.includes('id="devlog"'), 'Dev-Log-Sektion vorhanden');
  assert.ok(html.includes('id="devlogBody"'), 'Log-Container vorhanden');
  assert.ok(html.includes('id="devlogWho"'), '"wer spielt"-Zeile vorhanden');
  assert.ok(html.includes('script src="dev-log.js"'), 'Modul-Script eingebunden');
  assert.ok(html.includes('RBDevLog.createLogWidget'), 'Widget-Init ruft createLogWidget auf');
});

// ---------------------------------------------------------------------------
// 3) HTTP-Mock: createLogWidget pollt den Cursor-Feed
// ---------------------------------------------------------------------------

function startServer(handler) {
  return new Promise((resolve) => {
    const srv = http.createServer(handler);
    srv.listen(0, '127.0.0.1', () => resolve(srv));
  });
}

test('createLogWidget: Cursor-Poll liefert neue Events, keine Duplikate bei wiederholtem Poll', async () => {
  const seenSince = [];
  const srv = await startServer((req, res) => {
    const u = new URL(req.url, 'http://x');
    if (u.pathname === '/events') {
      const since = Number(u.searchParams.get('since') || '0');
      seenSince.push(since);
      let events = [];
      if (since < 2) {
        events = [
          { seq: 1, t: 100, kind: 'register', msg: "Spieler 'momo' registriert für Welt A" },
          { seq: 2, t: 110, kind: 'ready', msg: 'Welt A ist bereit' },
        ];
      } else if (since < 3) {
        events = [{ seq: 3, t: 200, kind: 'send', msg: 'Send A -> B: 5 Einheiten' }];
      }
      const last_seq = events.length > 0 ? events[events.length - 1].seq : since;
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ events, last_seq }));
    } else {
      res.writeHead(404);
      res.end();
    }
  });

  const base = `http://127.0.0.1:${srv.address().port}`;
  const received = [];
  const widget = createLogWidget({
    apiBase: base,
    onEvents: (events) => { received.push(...events); },
  });

  try {
    await widget.refresh(); // seq 1,2
    assert.strictEqual(received.length, 2);
    assert.strictEqual(received[0].kind, 'register');
    assert.strictEqual(received[1].kind, 'ready');
    assert.strictEqual(widget.getSince(), 2);

    await widget.refresh(); // seq 3
    assert.strictEqual(received.length, 3);
    assert.strictEqual(received[2].kind, 'send');
    assert.strictEqual(widget.getSince(), 3);

    await widget.refresh(); // nichts Neues -> keine Duplikate
    assert.strictEqual(received.length, 3, 'kein erneutes Event bei leerem Delta');

    assert.deepStrictEqual(seenSince, [0, 2, 3], 'Cursor wandert korrekt weiter (since=last_seq)');
  } finally {
    widget.stop();
    await new Promise((r) => srv.close(r));
  }
});

test('createLogWidget: HTTP-Fehler ruft onError statt zu werfen (kein Reload/Crash)', async () => {
  const srv = await startServer((req, res) => {
    res.writeHead(500, { 'Content-Type': 'application/json' });
    res.end('{"error":"kaputt"}');
  });
  const base = `http://127.0.0.1:${srv.address().port}`;
  let errCount = 0;
  const widget = createLogWidget({
    apiBase: base,
    onEvents: () => { throw new Error('sollte nie aufgerufen werden'); },
    onError: () => { errCount++; },
  });
  try {
    await widget.refresh();
    assert.strictEqual(errCount, 1);
  } finally {
    widget.stop();
    await new Promise((r) => srv.close(r));
  }
});

test('createLogWidget: Netzwerkfehler (Connection refused) ruft onError', async () => {
  const srv = await startServer(() => {});
  const port = srv.address().port;
  await new Promise((r) => srv.close(r));

  let errCount = 0;
  const widget = createLogWidget({
    apiBase: `http://127.0.0.1:${port}`,
    onError: () => { errCount++; },
  });
  try {
    await widget.refresh();
    assert.strictEqual(errCount, 1);
  } finally {
    widget.stop();
  }
});
