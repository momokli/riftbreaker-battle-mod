"use strict";

/*
 * Tests fuer den Natural-Attacks-Editor (Difficulty 1-9 x attack-count/boss).
 * Der testbare Marker-Block (createNaturalAttackEditor) wird aus cockpit.html
 * extrahiert und im vm ausgewertet. Die Kern-Logik
 * (load/inc/dec/setBoss/setOffset/save/restore) ist DOM-frei.
 */

const { test } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const COCKPIT = path.join(__dirname, "..", "..", "cockpit", "cockpit.html");
const BEGIN = "// --- natural attack editor (testable) ---";
const END = "// --- end natural attack editor ---";

function extractBlock() {
  const html = fs.readFileSync(COCKPIT, "utf8");
  const b = html.indexOf(BEGIN);
  const e = html.indexOf(END);
  assert.ok(b !== -1, "Begin-Marker");
  assert.ok(e > b, "End-Marker");
  return html.slice(b, e + END.length);
}

function loadBlock() {
  const sandbox = {};
  vm.createContext(sandbox);
  return vm.runInNewContext(extractBlock() + "\n({ createNaturalAttackEditor });", sandbox);
}

const DEFAULT_RULES = {
  max_attack_count: [1, 2, 2, 2, 2, 2, 3, 3, 3],
  boss_min_level: 5,
  event_offset_fraction: 0.35,
  creature_events: [],
};

function makeFetch(rules) {
  const calls = [];
  const fn = (route, opts) => {
    calls.push({ route, opts: opts ? JSON.parse(opts.body) : undefined });
    if (route === "natural_attack_rules" && !opts) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(rules) });
    }
    if (route === "natural_attack_rules" && opts) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ ok: true }) });
    }
    return Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({}) });
  };
  fn.calls = calls;
  return fn;
}

test("Marker-Block exportiert createNaturalAttackEditor", () => {
  assert.equal(typeof loadBlock().createNaturalAttackEditor, "function");
});

test("load(): Rules geladen", async () => {
  const ed = loadBlock().createNaturalAttackEditor({ document: {}, fetch: makeFetch(DEFAULT_RULES) });
  await ed.load();
  assert.deepEqual(ed.state.rules.max_attack_count, [1, 2, 2, 2, 2, 2, 3, 3, 3]);
  assert.equal(ed.state.rules.boss_min_level, 5);
});

test("inc/dec: max_attack_count aendern, dec stoppt bei 0", () => {
  const ed = loadBlock().createNaturalAttackEditor({ document: {}, fetch: () => {} });
  ed.state.rules = JSON.parse(JSON.stringify(DEFAULT_RULES));
  ed.inc(0);
  ed.inc(0);
  assert.equal(ed.state.rules.max_attack_count[0], 3);
  ed.dec(0);
  ed.dec(0);
  ed.dec(0);
  ed.dec(0);
  assert.equal(ed.state.rules.max_attack_count[0], 0, "nicht unter 0");
});

test("setBoss(null): kein Boss", () => {
  const ed = loadBlock().createNaturalAttackEditor({ document: {}, fetch: () => {} });
  ed.state.rules = JSON.parse(JSON.stringify(DEFAULT_RULES));
  ed.setBoss(null);
  assert.equal(ed.state.rules.boss_min_level, null);
});

test("setOffset(): fraction setzen", () => {
  const ed = loadBlock().createNaturalAttackEditor({ document: {}, fetch: () => {} });
  ed.state.rules = JSON.parse(JSON.stringify(DEFAULT_RULES));
  ed.setOffset(0.5);
  assert.equal(ed.state.rules.event_offset_fraction, 0.5);
});

test("save(): POSTet Rules", async () => {
  const fetch = makeFetch(DEFAULT_RULES);
  const ed = loadBlock().createNaturalAttackEditor({ document: {}, fetch });
  await ed.load();
  ed.inc(0);
  await ed.save();
  const post = fetch.calls.find((x) => x.route === "natural_attack_rules" && x.opts);
  assert.ok(post, "POST /natural_attack_rules gesendet");
  assert.equal(post.opts.max_attack_count[0], 2);
});

test("restore(): verwirft lokale Aenderungen", () => {
  const ed = loadBlock().createNaturalAttackEditor({ document: {}, fetch: () => {} });
  ed.state.rules = JSON.parse(JSON.stringify(DEFAULT_RULES));
  ed.state.saved = JSON.parse(JSON.stringify(DEFAULT_RULES));
  ed.inc(0);
  assert.equal(ed.state.rules.max_attack_count[0], 2);
  ed.restore();
  assert.equal(ed.state.rules.max_attack_count[0], 1);
});

test("Defensiv: kein location.reload im Panel-Code", () => {
  assert.ok(!extractBlock().includes("location.reload"), "kein Reload");
});
