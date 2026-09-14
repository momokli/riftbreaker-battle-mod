'use strict';

// Host-Test (kein Windows/Spiel/Netz) für die #387-Verdrahtung bis zur
// Web-UI: die neuen POST-Routen in pipe_bridge.c, die DOM-Controls in der
// single-source cockpit.html und die Sync des generierten cockpit_html.inc.
// Reine Datei-/Textprüfungen — laufen immer (kein C-Compiler nötig).

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.join(__dirname, '..', '..');
const BRIDGE_DIR = path.join(ROOT, 'bausteine', '04-trainer-io', 'bridge');
const PIPE = path.join(BRIDGE_DIR, 'pipe_bridge.c');
const COCKPIT = path.join(BRIDGE_DIR, 'cockpit.html');
const INC = path.join(BRIDGE_DIR, 'cockpit_html.inc');

const pipe = fs.readFileSync(PIPE, 'utf8');
const cockpit = fs.readFileSync(COCKPIT, 'utf8');

test('#387: pipe_bridge.c verdrahtet /pause_dom + /resume_dom', () => {
  for (const route of ['"/pause_dom"', '"/resume_dom"']) {
    assert.ok(
      pipe.includes(`strcmp(path, ${route}) == 0`),
      `Route ${route} fehlt in pipe_bridge.c`,
    );
  }
  // Handler leitet cmd + optionalen ref durch und liest die dom_control-Zeile.
  assert.ok(pipe.includes('handle_dom_control('), 'handle_dom_control fehlt');
  assert.ok(
    pipe.includes('pipe_wait_line(h, "dom_control"'),
    'dom_control-Wartebedingung fehlt',
  );
  assert.ok(pipe.includes('json_get_raw_number('), 'ref-Numerik-Helfer fehlt');
});

test('#387: cockpit.html bietet DOM Pause/Resume (Write) + Status (Read)', () => {
  assert.ok(cockpit.includes('id="dom_pause"'), 'DOM-Pause-Button fehlt');
  assert.ok(cockpit.includes('id="dom_resume"'), 'DOM-Resume-Button fehlt');
  assert.ok(cockpit.includes('id="dom_status"'), 'DOM-Statusfeld fehlt');
  assert.ok(
    cockpit.includes('domControl("pause_dom")') &&
      cockpit.includes('domControl("resume_dom")'),
    'DOM-Buttons lösen nicht die neuen Routen aus',
  );
  // Blocker darf nicht versteckt werden: ok:false wird sichtbar gerendert.
  assert.ok(
    cockpit.includes('FAIL:') && cockpit.includes('r.reason'),
    'ok:false/reason wird nicht angezeigt',
  );
});

test('#387: generiertes cockpit_html.inc ist synchron zu cockpit.html', (t) => {
  if (!fs.existsSync(INC)) {
    t.skip('cockpit_html.inc fehlt (Build-Artefakt) — Drift-Guard übersprungen');
    return;
  }
  let lines = cockpit.split('\n');
  if (lines.length && lines[lines.length - 1] === '') lines = lines.slice(0, -1);
  const body = lines
    .map((ln) => '    "' + ln.replace(/\\/g, '\\\\').replace(/"/g, '\\"') + '\\n"')
    .join('\n');
  const expected =
    '/* GENERIERT aus cockpit.html (scripts/gen_cockpit_html.py). */\n' +
    'static const char COCKPIT_HTML[] =\n' +
    body +
    ';\n';
  const actual = fs.readFileSync(INC, 'utf8');
  assert.strictEqual(
    actual,
    expected,
    'cockpit_html.inc ist nicht synchron — scripts/gen_cockpit_html.py laufen lassen',
  );
});
