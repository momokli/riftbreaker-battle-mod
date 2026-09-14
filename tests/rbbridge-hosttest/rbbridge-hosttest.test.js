'use strict';

// Host-Test für rbbridge.c (Issue #243 / PR #251):
// Kompiliert die reinen Scan-/RTTI-Funktionen mit dem HOST-Compiler
// (cc, dann gcc — NIE mingw, das erzeugt Windows-Binaries) gegen einen
// SYNTHETISCHEN PE-artigen Puffer und führt den Harness aus. Kein Windows,
// kein Spielprozess, kein Netz. Ist kein Host-CC vorhanden, wird der Test
// SICHTBAR übersprungen (skip mit Begründung) — niemals stillschweigend grün.

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');

const ROOT = path.join(__dirname, '..', '..');
const RBBRIDGE_DIR = path.join(ROOT, 'bausteine', '04-trainer-io', 'rbbridge');
const HARNESS = path.join(__dirname, 'hosttest', 'rbbridge_hosttest.c');

function findHostCC() {
  for (const cc of ['cc', 'gcc']) {
    try {
      execFileSync('sh', ['-c', `command -v ${cc}`], { stdio: 'ignore' });
      return cc;
    } catch (e) {
      /* nächster Kandidat */
    }
  }
  return null;
}

test('rbbridge host-test: scan_bytes + RTTI-Resolver (synthetischer PE-Puffer)', (t) => {
  const cc = findHostCC();
  if (!cc) {
    t.skip('kein Host-C-Compiler (cc/gcc) im PATH — Host-Harness nicht '
      + 'kompilierbar; Test bleibt ungeprüft (nicht grün)');
    return;
  }
  assert.ok(fs.existsSync(HARNESS), `Harness fehlt: ${HARNESS}`);

  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rb251-hosttest-'));
  try {
    const bin = path.join(dir, 'rbbridge_hosttest');
    execFileSync(cc, ['-O1', '-g', '-Wall', '-Wextra',
      '-I', RBBRIDGE_DIR, '-o', bin, HARNESS], { stdio: 'pipe' });

    const out = execFileSync(bin, [], { encoding: 'utf8' });
    const m = out.match(/HOSTTEST_PASS=(\d+) HOSTTEST_FAIL=(\d+)/);
    assert.ok(m, `Harness-Ausgabe ohne Ergebniszeile:\n${out}`);
    assert.strictEqual(Number(m[2]), 0, `Harness-FAILs:\n${out}`);
    assert.ok(Number(m[1]) >= 20, `zu wenige Checks ausgeführt: ${m[1]}`);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
