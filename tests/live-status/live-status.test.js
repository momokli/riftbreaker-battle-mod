'use strict';

// Landing Live-Status-Widget (Issue #30) — Tests.
//
//  1. Unit: deriveStatus() leitet aus GET /state die geforderte Status-Zeile ab
//     ("Lobby leer" / "N Spieler in Lobby" / "Match läuft: A vs B (Runde X)" /
//      "Solo-Match läuft" / "Status unbekannt" bei nicht erreichbarer API).
//  2. Statisch: site/index.html verdrahtet das Widget (Meta, Container, Script).
//  3. HTTP-Mock: createWidget() pollt einen Mock-Server (Fixture /state) und
//     degradiert defensiv zu "Status unbekannt", sobald die API Fehler liefert.

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');

const ROOT = path.join(__dirname, '..', '..');
const MODULE_PATH = path.join(ROOT, 'site', 'live-status.js');
const INDEX_HTML = path.join(ROOT, 'site', 'index.html');

const statusMod = require(MODULE_PATH);
const { deriveStatus, createWidget, resolveApiBase } = statusMod;

// ---------------------------------------------------------------------------
// Fixtures (Aufbau nach docs/TOURNAMENT_API.md → GET /state)
// ---------------------------------------------------------------------------

function team(player, ready, hq_hp, score, wave) {
  return { player, ready, hq_hp: hq_hp || 100, score: score || 0, resources: {}, wave: wave || 0 };
}

function state(phase, mode, teams, extra) {
  return Object.assign(
    {
      match_id: 'rift-1',
      mode: mode || 'duel',
      phase,
      round: 0,
      rounds_done: 0,
      rematches: 0,
      winner: null,
      started_at: null,
      hq_hp_start: 100,
      teams: teams || {},
      reveal: null,
      feed: [],
    },
    extra || {}
  );
}

// ---------------------------------------------------------------------------
// 1) Unit: deriveStatus
// ---------------------------------------------------------------------------

test('deriveStatus: Lobby leer (0 Spieler)', () => {
  const r = deriveStatus(state('lobby', 'duel', { A: team(null), B: team(null) }));
  assert.strictEqual(r.kind, 'empty');
  assert.strictEqual(r.line, 'Lobby leer');
  assert.strictEqual(r.count, 0);
});

test('deriveStatus: 1 Spieler in Lobby', () => {
  const r = deriveStatus(state('lobby', 'duel', { A: team('momo'), B: team(null) }));
  assert.strictEqual(r.kind, 'lobby');
  assert.strictEqual(r.line, '1 Spieler in Lobby');
  assert.strictEqual(r.count, 1);
});

test('deriveStatus: N Spieler in Lobby (2 Spieler)', () => {
  const r = deriveStatus(state('lobby', 'duel', { A: team('momo'), B: team('matheo') }));
  assert.strictEqual(r.kind, 'lobby');
  assert.strictEqual(r.line, '2 Spieler in Lobby');
  assert.strictEqual(r.count, 2);
});

test('deriveStatus: Ready-Phase bleibt "N Spieler in Lobby"', () => {
  const r = deriveStatus(state('ready', 'duel', {
    A: team('momo', true),
    B: team('matheo', true),
  }));
  assert.strictEqual(r.kind, 'lobby');
  assert.strictEqual(r.line, '2 Spieler in Lobby');
});

test('deriveStatus: Match läuft — A vs B (Runde X)', () => {
  const r = deriveStatus(state('running', 'duel', {
    A: team('momo'),
    B: team('matheo'),
  }, { round: 3 }));
  assert.strictEqual(r.kind, 'running');
  assert.strictEqual(r.line, 'Match läuft: momo vs matheo (Runde 3)');
});

test('deriveStatus: Match läuft ohne Namen → "Welt A vs Welt B"', () => {
  const r = deriveStatus(state('running', 'duel', { A: team(null), B: team(null) }, { round: 1 }));
  assert.strictEqual(r.line, 'Match läuft: Welt A vs Welt B (Runde 1)');
});

test('deriveStatus: Solo-Match läuft (mode=sp)', () => {
  const r = deriveStatus(state('running', 'sp', {
    A: team('momo'),
    B: team('MIRROR'),
  }, { round: 2 }));
  assert.strictEqual(r.kind, 'sp');
  assert.strictEqual(r.line, 'Solo-Match läuft');
});

test('deriveStatus: Match beendet — Sieger (duel)', () => {
  const r = deriveStatus(state('finished', 'duel', {
    A: team('momo'),
    B: team('matheo'),
  }, { winner: 'B' }));
  assert.strictEqual(r.kind, 'finished');
  assert.strictEqual(r.line, 'Match beendet — Sieger: matheo');
});

test('deriveStatus: Solo-Match beendet (mode=sp)', () => {
  const r = deriveStatus(state('finished', 'sp', {
    A: team('momo'),
    B: team('MIRROR'),
  }, { winner: 'A' }));
  assert.strictEqual(r.kind, 'finished');
  assert.strictEqual(r.line, 'Solo-Match beendet');
});

test('deriveStatus: API nicht erreichbar / null → "Status unbekannt"', () => {
  for (const bad of [null, undefined, {}, { phase: undefined }, { phase: 'bogus' }, 'x']) {
    const r = deriveStatus(bad);
    assert.strictEqual(r.kind, 'unknown', `input=${JSON.stringify(bad)}`);
    assert.strictEqual(r.line, 'Status unbekannt');
    assert.strictEqual(r.ok, false);
  }
});

test('resolveApiBase: Meta-Tag hat Vorrang, sonst Default', () => {
  const docWithMeta = {
    querySelector: (sel) =>
      sel === 'meta[name="rb-tournament-api"]' ? { content: ' https://ref.example/x/' } : null,
  };
  assert.strictEqual(resolveApiBase(docWithMeta), 'https://ref.example/x');
  const docWithoutMeta = { querySelector: () => null };
  assert.strictEqual(resolveApiBase(docWithoutMeta), '/tournament');
  assert.strictEqual(resolveApiBase(undefined), '/tournament');
});

// ---------------------------------------------------------------------------
// 2) Statisch: Verdrahtung in site/index.html
// ---------------------------------------------------------------------------

test('site/index.html verdrahtet das Live-Status-Widget', () => {
  const html = fs.readFileSync(INDEX_HTML, 'utf8');
  assert.ok(html.includes('meta name="rb-tournament-api"'), 'Meta-Tag für API-Basis vorhanden');
  assert.ok(html.includes('id="liveStatus"'), 'Widget-Container vorhanden');
  assert.ok(html.includes('id="liveLine"'), 'Status-Zeile vorhanden');
  assert.ok(html.includes('script src="live-status.js"'), 'Modul-Script eingebunden');
  assert.ok(html.includes('RBTournamentStatus.createWidget'), 'Widget-Init ruft createWidget auf');
});

// ---------------------------------------------------------------------------
// 3) HTTP-Mock: createWidget pollt /state und degradiert defensiv
// ---------------------------------------------------------------------------

function startServer(handler) {
  return new Promise((resolve) => {
    const srv = http.createServer(handler);
    srv.listen(0, '127.0.0.1', () => resolve(srv));
  });
}

test('createWidget: Fixture /state → korrekte Zeile, danach Fehler → "Status unbekannt"', async () => {
  let mode = 'running';
  const srv = await startServer((req, res) => {
    if (req.url === '/state') {
      if (mode === 'running') {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify(state('running', 'duel', {
          A: team('momo', true, 82, 1240, 4),
          B: team('matheo', true, 100, 980, 4),
        }, { round: 4 })));
      } else {
        res.writeHead(500, { 'Content-Type': 'application/json' });
        res.end('{"error":"kaputt"}');
      }
    } else {
      res.writeHead(404);
      res.end();
    }
  });

  const base = `http://127.0.0.1:${srv.address().port}`;
  let last = null;
  const rendered = [];
  const widget = createWidget({
    apiBase: base,
    render: (st) => { last = st; rendered.push(st); },
  });

  try {
    await widget.refresh();
    assert.strictEqual(last.kind, 'running');
    assert.strictEqual(last.line, 'Match läuft: momo vs matheo (Runde 4)');

    // API liefert 500 → defensiv "Status unbekannt"
    mode = 'down';
    await widget.refresh();
    assert.strictEqual(last.kind, 'unknown');
    assert.strictEqual(last.line, 'Status unbekannt');
    assert.strictEqual(last.ok, false);
  } finally {
    widget.stop();
    await new Promise((r) => srv.close(r));
  }
});

test('createWidget: Netzwerkfehler (Connection refused) → "Status unbekannt"', async () => {
  // Port ohne Listener: freien Port holen und direkt wieder schließen.
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
    assert.strictEqual(last.kind, 'unknown');
    assert.strictEqual(last.line, 'Status unbekannt');
  } finally {
    widget.stop();
  }
});
