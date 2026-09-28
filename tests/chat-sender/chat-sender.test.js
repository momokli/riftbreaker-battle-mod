"use strict";

/*
 * Tests fuer den freien Chat-Absender (#935).
 *
 * Der testbare Block wird per Marker aus cockpit.html extrahiert und in einem
 * vm-Kontext ausgewertet — kein DOM, kein Netzwerk. Geprueft wird die reine
 * Normalisierung: leer/"system" -> ohne Prefix (""), sonst der bereinigte Name.
 */

const { test } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const COCKPIT = path.join(__dirname, "..", "..", "cockpit", "cockpit.html");
const BEGIN = "// --- chat-sender (testable) ---";
const END = "// --- end chat-sender ---";

function loadSender() {
  const html = fs.readFileSync(COCKPIT, "utf8");
  const begin = html.indexOf(BEGIN);
  const end = html.indexOf(END);
  assert.ok(begin !== -1, "Begin-Marker vorhanden");
  assert.ok(end > begin, "End-Marker nach Begin-Marker");
  const sandbox = {};
  vm.createContext(sandbox);
  const fn = vm.runInNewContext(
    html.slice(begin, end + END.length) + "\nchatSenderName;",
    sandbox
  );
  assert.equal(typeof fn, "function", "chatSenderName gefunden");
  return fn;
}

const sender = loadSender();

test("freie Namen bleiben erhalten", () => {
  assert.equal(sender("Operator"), "Operator");
  assert.equal(sender("Referee"), "Referee");
  assert.equal(sender("Mission Control"), "Mission Control");
});

test("leer/whitespace -> kein Absender (clean)", () => {
  assert.equal(sender(""), "");
  assert.equal(sender("   "), "");
  assert.equal(sender(null), "");
  assert.equal(sender(undefined), "");
});

test("system (case-insensitiv) -> kein Absender (clean)", () => {
  assert.equal(sender("system"), "");
  assert.equal(sender("System"), "");
  assert.equal(sender("  SYSTEM  "), "");
  assert.equal(sender("[system]"), "");
});

test("Klammern/Whitespace werden normalisiert", () => {
  assert.equal(sender("[Admin]"), "Admin");
  assert.equal(sender("A[b]c"), "A b c");
  assert.equal(sender("  Multi   Space  "), "Multi Space");
});

test("Name wird auf 24 Zeichen begrenzt", () => {
  const long = "X".repeat(40);
  assert.equal(sender(long), "X".repeat(24));
  assert.equal(sender(long).length, 24);
});
