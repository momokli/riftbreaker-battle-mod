'use strict';

// Ranglisten-Widget (Issue #132) — Tests.
//
//  1. Unit: deriveLeaderboard() sortiert Profile nach ELO (Tie-Break wins),
//     normalisiert die Bilanz (wins/losses/draws) und degradiert defensiv zu
//     "Rangliste nicht verfügbar", wenn keine erkennbare Struktur vorliegt.
//  2. Statisch: site/index.html verdrahtet das Widget (Container, Tabelle, Script).
//  3. HTTP-Mock: createLeaderboardWidget() pollt einen Mock-Server (Fixture
//     /leaderboard) und fällt bei API-Fehlern auf die leere Liste zurück.

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');

const ROOT = path.join(__dirname, '..', '..');
const MODULE_PATH = path.join(ROOT, 'site', 'leaderboard.js');
const INDEX_HTML = path.join(ROOT, 'site', 'index.html');

const { deriveLeaderboard, createLeaderboardWidget, resolveApiBase } = require(MODULE_PATH);

// ---------------------------------------------------------------------------
// Fixtures (Aufbau nach docs/PLAYER_PROFILE_MODEL.md §1.1/#1.3)
// ---------------------------------------------------------------------------

function profile(name, elo, wins, losses, draws) {
  return {
    display_name: name,
    elo,
    wins,
    losses,
    draws,
    matches_played: wins + losses + draws,
  };
}

// ---------------------------------------------------------------------------
// 1) Unit: deriveLeaderboard
// ---------------------------------------------------------------------------

test('deriveLeaderboard: sortiert absteigend nach ELO + vergibt Ränge', () => {
  const r = deriveLeaderboard({
    players: [profile('momo', 1216, 3, 2, 0), profile('matheo', 1188, 1, 4, 0), profile('kate', 1240, 5, 1, 1)],
  });
  assert.strictEqual(r.ok, true);
  assert.strictEqual(r.count, 3);
  assert.deepStrictEqual(r.rows.map((x) => x.name), ['kate', 'momo', 'matheo']);
  assert.deepStrictEqual(r.rows.map((x) => x.rank), [1, 2, 3]);
  assert.strictEqual(r.rows[0].elo, 1240);
});

test('deriveLeaderboard: Bilanz (wins/losses/draws) bleibt erhalten', () => {
  const r = deriveLeaderboard([profile('momo', 1216, 3, 2, 1)]);
  assert.strictEqual(r.rows[0].wins, 3);
  assert.strictEqual(r.rows[0].losses, 2);
  assert.strictEqual(r.rows[0].draws, 1);
  assert.strictEqual(r.rows[0].played, 6);
});

test('deriveLeaderboard: ELO-Gleichstand → mehr Siege zuerst, dann Name', () => {
  const r = deriveLeaderboard([
    profile('zoe', 1200, 1, 0, 0),
    profile('anna', 1200, 5, 2, 0),
    profile('bea', 1200, 5, 1, 0),
  ]);
  assert.deepStrictEqual(r.rows.map((x) => x.name), ['bea', 'anna', 'zoe']);
});

test('deriveLeaderboard: akzeptiert Array und Alias-Container', () => {
  for (const payload of [
    [profile('momo', 1200, 1, 0, 0)],
    { players: [profile('momo', 1200, 1, 0, 0)] },
    { leaderboard: [profile('momo', 1200, 1, 0, 0)] },
    { entries: [profile('momo', 1200, 1, 0, 0)] },
    { ranking: [profile('momo', 1200, 1, 0, 0)] },
  ]) {
    const r = deriveLeaderboard(payload);
    assert.strictEqual(r.count, 1, JSON.stringify(payload));
    assert.strictEqual(r.rows[0].name, 'momo');
  }
});

test('deriveLeaderboard: Namens-Aliase display_name|name|player', () => {
  const r = deriveLeaderboard([{ name: 'n1', elo: 1300 }, { player: 'p1', elo: 1290 }]);
  assert.deepStrictEqual(r.rows.map((x) => x.name), ['n1', 'p1']);
});

test('deriveLeaderboard: Einträge ohne Namen werden übersprungen (defensiv)', () => {
  const r = deriveLeaderboard({ players: [profile('momo', 1200, 1, 0, 0), null, {}, 'x', { elo: 9999 }] });
  assert.strictEqual(r.count, 1);
  assert.strictEqual(r.rows[0].name, 'momo');
});

test('deriveLeaderboard: leere gültige Liste → ok, count 0', () => {
  const r = deriveLeaderboard({ players: [] });
  assert.strictEqual(r.ok, true);
  assert.strictEqual(r.count, 0);
  assert.deepStrictEqual(r.rows, []);
});

test('deriveLeaderboard: keine erkennbare Struktur → nicht verfügbar', () => {
  for (const bad of [null, undefined, {}, 'x', 42, { phase: 'lobby' }]) {
    const r = deriveLeaderboard(bad);
    assert.strictEqual(r.ok, false, `input=${JSON.stringify(bad)}`);
    assert.strictEqual(r.count, 0);
    assert.strictEqual(r.line, 'Rangliste nicht verfügbar');
  }
});

test('resolveApiBase: Meta-Tag hat Vorrang, sonst Default', () => {
  const docWithMeta = {
    querySelector: (sel) =>
      sel === 'meta[name="rb-tournament-api"]' ? { content: ' https://ref.example/x/' } : null,
  };
  assert.strictEqual(resolveApiBase(docWithMeta), 'https://ref.example/x');
  assert.strictEqual(resolveApiBase({ querySelector: () => null }), '/tournament');
  assert.strictEqual(resolveApiBase(undefined), '/tournament');
});

// ---------------------------------------------------------------------------
// 2) Statisch: Verdrahtung in site/index.html
// ---------------------------------------------------------------------------

test('site/index.html verdrahtet das Ranglisten-Widget', () => {
  const html = fs.readFileSync(INDEX_HTML, 'utf8');
  assert.ok(html.includes('id="leaderboard"'), 'Widget-Container vorhanden');
  assert.ok(html.includes('id="lbBody"'), 'Tabellen-Body vorhanden');
  assert.ok(html.includes('script src="leaderboard.js"'), 'Modul-Script eingebunden');
  assert.ok(html.includes('RBLeaderboard.createLeaderboardWidget'), 'Widget-Init ruft createLeaderboardWidget auf');
});

// ---------------------------------------------------------------------------
// 3) HTTP-Mock: createLeaderboardWidget pollt /leaderboard, defekte API → leer
// ---------------------------------------------------------------------------

function startServer(handler) {
  return new Promise((resolve) => {
    const srv = http.createServer(handler);
    srv.listen(0, '127.0.0.1', () => resolve(srv));
  });
}

test('createLeaderboardWidget: Fixture /leaderboard → sortierte Zeilen, dann Fehler → leer', async () => {
  let mode = 'ok';
  const srv = await startServer((req, res) => {
    if (req.url === '/leaderboard') {
      if (mode === 'ok') {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ players: [profile('momo', 1188, 1, 4, 0), profile('matheo', 1216, 3, 2, 0)] }));
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
  const widget = createLeaderboardWidget({
    apiBase: base,
    render: (r) => { last = r; },
  });

  try {
    await widget.refresh();
    assert.strictEqual(last.ok, true);
    assert.deepStrictEqual(last.rows.map((x) => x.name), ['matheo', 'momo']);

    mode = 'down';
    await widget.refresh();
    assert.strictEqual(last.ok, false);
    assert.strictEqual(last.count, 0);
    assert.strictEqual(last.line, 'Rangliste nicht verfügbar');
  } finally {
    widget.stop();
    await new Promise((r) => srv.close(r));
  }
});

test('createLeaderboardWidget: Netzwerkfehler (Connection refused) → leere Liste', async () => {
  const srv = await startServer(() => {});
  const port = srv.address().port;
  await new Promise((r) => srv.close(r));

  let last = null;
  const widget = createLeaderboardWidget({
    apiBase: `http://127.0.0.1:${port}`,
    render: (r) => { last = r; },
  });

  try {
    await widget.refresh();
    assert.strictEqual(last.ok, false);
    assert.strictEqual(last.count, 0);
  } finally {
    widget.stop();
  }
});
