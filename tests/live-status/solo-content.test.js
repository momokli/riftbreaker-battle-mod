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
