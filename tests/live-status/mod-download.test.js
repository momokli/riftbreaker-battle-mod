'use strict';

// Issue #119 — Download-Link == deployed Stand.
//
// Drei Invarianten:
//   1) Die Mod-Version wird eindeutig aus den Mod-Metadaten (mod/<GUID>.manifest,
//      Feld `version`) abgeleitet — nicht aus Git-SHA/Datum. scripts/mod_version.sh
//      liefert exakt diesen Wert.
//   2) dev (site/solo.html) verlinkt den Download auf das SAME-ORIGIN-Zip
//      /mods/rbbattle.zip (der deployed main-Stand auf rift.projectmellon.de) —
//      nie auf den (ggf. älteren) GitHub-Release.
//   3) prod (site/index.html) verlinkt den Download auf das aktuelle getaggte
//      Release (releases/latest/download/rbbattle.zip).

const { test } = require('node:test');
const assert = require('node:assert');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.join(__dirname, '..', '..');
const SOLO_HTML = path.join(ROOT, 'site', 'solo.html');
const INDEX_HTML = path.join(ROOT, 'site', 'index.html');
const CONNECTIVITY_HTML = path.join(ROOT, 'site', 'connectivity.html');

function manifestVersion() {
  const modDir = path.join(ROOT, 'mod');
  const manifests = fs.readdirSync(modDir).filter((f) => f.endsWith('.manifest'));
  assert.strictEqual(manifests.length, 1, `genau ein Mod-Manifest erwartet, gefunden: ${manifests.length}`);
  const content = fs.readFileSync(path.join(modDir, manifests[0]), 'utf8');
  const m = content.match(/^\s*version\s*"([^"]+)"/m);
  assert.ok(m, `kein 'version "…"'-Eintrag in ${manifests[0]}`);
  return m[1];
}

test('scripts/mod_version.sh liefert die Version aus dem Mod-Manifest', () => {
  const expected = manifestVersion();
  const out = execFileSync('bash', ['scripts/mod_version.sh'], {
    cwd: ROOT,
    encoding: 'utf8',
  });
  assert.strictEqual(out.trim(), expected, 'mod_version.sh muss exakt die Manifest-Version liefern');
});

test('site/solo.html (dev) verlinkt den Download auf /mods/rbbattle.zip (Same-Origin)', () => {
  const html = fs.readFileSync(SOLO_HTML, 'utf8');
  assert.ok(
    html.includes('href="/mods/rbbattle.zip"'),
    'dev-Download muss auf das deployed Same-Origin-Zip zeigen'
  );
  assert.ok(
    !/releases\/latest\/download\/rbbattle\.zip/.test(html),
    'dev-Download darf NICHT auf den GitHub-Release zeigen'
  );
});

test('site/index.html (prod) verlinkt den Download auf das getaggte Release', () => {
  const html = fs.readFileSync(INDEX_HTML, 'utf8');
  assert.ok(
    html.includes('releases/latest/download/rbbattle.zip'),
    'prod-Download muss auf das aktuelle getaggte Release zeigen'
  );
});

test('site/connectivity.html (dev-Server 6321) verlinkt auf den deployed Stand statt Release', () => {
  const html = fs.readFileSync(CONNECTIVITY_HTML, 'utf8');
  assert.ok(
    html.includes('href="/mods/rbbattle.zip"'),
    'dev-Server-Hinweis muss auf das deployed Same-Origin-Zip zeigen'
  );
});
