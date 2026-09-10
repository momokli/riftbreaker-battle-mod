'use strict';

// Issue #95 — solo.html darf keine LLM-Prompt-Reste/Anweisungen enthalten.
//
// Regression-Test: stellt sicher, dass im Inhalt von site/solo.html keine
// Prompt-/Anweisungs-Fragmente ("You are …", "Lorem ipsum", Platzhalter-
// Instruktionen, Meta-Notizen wie "Kurz & wichtig …") mehr vorkommen.
// Der Test liest die Quelldatei direkt; die Fundstellen liegen als Klartext
// im Markup (sichtbarer Content).

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.join(__dirname, '..', '..');
const SOLO_HTML = path.join(ROOT, 'site', 'solo.html');

const FORBIDDEN_PATTERNS = [
  // LLM-Prompt-Reste (englisch/deutsch)
  /you are (an? |a )?(ai|assistant|language model|helpful assistant)/i,
  /as an (ai|language model|assistant)/i,
  /du bist (ein|eine)? (ki|assistent|ki-assistent)/i,
  /als (ki|assistent|ki-assistent) (sollst|soll|musst|musst du)/i,
  // Platzhalter-Instruktionen / Fülltext
  /lorem ipsum/i,
  /\bTODO\b/,
  /\bFIXME\b/,
  /\[(insert|placeholder|hier|text|inhalt|content|beschreibung)/i,
  /\{+(inhalt|text|content|platzhalter)\}+/i,
  // Konkret entfernte Meta-/Anweisungs-Fragmente (Issue #95)
  /kurz & wichtig/i,
];

test('site/solo.html enthält keine LLM-Prompt-Reste/Anweisungen', () => {
  const html = fs.readFileSync(SOLO_HTML, 'utf8');
  for (const re of FORBIDDEN_PATTERNS) {
    const m = html.match(re);
    assert.strictEqual(m, null, `Prompt-/Anweisungs-Rest gefunden (${re}): ${m && m[0]}`);
  }
});

// Issue #96 — solo.html: Copy gekürzt; jede Sektion hat eine klare Funktion
// (Verbinden / Spielen / Status) und liefert konkrete Anweisungen statt Fülltext.

test('site/solo.html: Sektionen haben klare Funktion (Verbinden / Spielen / Status)', () => {
  const html = fs.readFileSync(SOLO_HTML, 'utf8');

  // Verbinden: Join-Anweisung mit konkreter Server-Adresse.
  assert.ok(/65\.21\.27\.234:6321/.test(html), 'Server-Adresse vorhanden');
  assert.ok(/join/i.test(html), 'Verbinden-Anweisung vorhanden');

  // Spielen: konkrete Commands im How-to und in der Command-Tabelle.
  assert.ok(html.includes('rb_convert'), 'rb_convert vorhanden');
  assert.ok(html.includes('rb_status'), 'rb_status vorhanden');
  // Send-Boost (#39): die Seite bewirbt den "Boost für die nächste Welle" —
  // der zugehörige Command muss in der Command-Tabelle stehen.
  assert.ok(html.includes('rb_boost'), 'rb_boost vorhanden (Send-Boost #39)');

  // Status: Live-Status-Widget und Verweis auf die Status-Seite.
  assert.ok(html.includes('id="liveStatus"'), 'Live-Status-Widget vorhanden');
  assert.ok(html.includes('/connectivity.html'), 'Server-Status-Verweis vorhanden');
});
