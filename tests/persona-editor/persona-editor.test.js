"use strict";

/*
 * Tests fuer den Persona-Editor (10x9-Count-Matrix). Der testbare Marker-Block
 * (createPersonaEditor) wird aus cockpit.html extrahiert und im vm ausgewertet.
 * Die Kern-Logik (load/inc/dec/save/restore/add/remove/setActive) ist DOM-frei.
 */

const { test } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const COCKPIT = path.join(__dirname, "..", "..", "cockpit", "cockpit.html");
const BEGIN = "// --- persona editor (testable) ---";
const END = "// --- end persona editor ---";

const WAVE_COST = {
  1: 300,
  2: 700,
  3: 1400,
  4: 2450,
  5: 4000,
  6: 5350,
  7: 7600,
  8: 9650,
  9: 10500,
};

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
  return vm.runInNewContext(extractBlock() + "\n({ createPersonaEditor });", sandbox);
}

function makeFetch(personas, active) {
  let saved = JSON.parse(JSON.stringify(personas));
  const calls = [];
  const fn = (route, opts) => {
    calls.push({ route, opts: opts ? JSON.parse(opts.body) : undefined });
    if (route === "personas" && !opts) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ personas, active: active || "" }),
      });
    }
    if (route === "personas" && opts) {
      saved = opts;
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ ok: true }),
      });
    }
    if (route === "persona_active") {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ ok: true }),
      });
    }
    if (route === "attack_status") {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ wave_cost: WAVE_COST }),
      });
    }
    return Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({}) });
  };
  fn.calls = calls;
  return fn;
}

test("Marker-Block exportiert createPersonaEditor", () => {
  assert.equal(typeof loadBlock().createPersonaEditor, "function");
});

test("emptyPersona(): 10 Attacken x 9 Nullen", () => {
  const ed = loadBlock().createPersonaEditor({ document: {}, fetch: () => {} });
  const p = ed.emptyPersona();
  assert.equal(p.length, 10);
  assert.equal(p[0].length, 9);
  assert.ok(p.every((a) => a.every((c) => c === 0)));
});

test("load(): Personas + active", async () => {
  const fetch = makeFetch({ aggro: [[1, 0, 0, 0, 0, 0, 0, 0, 0]] }, "aggro");
  const ed = loadBlock().createPersonaEditor({ document: {}, fetch });
  await ed.load();
  assert.deepEqual(Object.keys(ed.state.personas), ["aggro"]);
  assert.equal(ed.state.active, "aggro");
});

test("inc/dec: Counts aendern, dec stoppt bei 0", () => {
  const ed = loadBlock().createPersonaEditor({ document: {}, fetch: () => {} });
  ed.state.personas = { aggro: [[0, 0, 0, 0, 0, 0, 0, 0, 0]] };
  ed.inc("aggro", 0, 2);
  ed.inc("aggro", 0, 2);
  assert.equal(ed.state.personas["aggro"][0][2], 2);
  ed.dec("aggro", 0, 2);
  assert.equal(ed.state.personas["aggro"][0][2], 1);
  ed.dec("aggro", 0, 2);
  ed.dec("aggro", 0, 2);
  assert.equal(ed.state.personas["aggro"][0][2], 0, "nicht unter 0");
});

test("restore(): verwirft lokale Aenderungen", () => {
  const ed = loadBlock().createPersonaEditor({ document: {}, fetch: () => {} });
  ed.state.personas = { aggro: [[0, 0, 0, 0, 0, 0, 0, 0, 0]] };
  ed.state.saved = { aggro: [[0, 0, 0, 0, 0, 0, 0, 0, 0]] };
  ed.inc("aggro", 0, 2);
  assert.equal(ed.state.personas["aggro"][0][2], 1);
  ed.restore("aggro");
  assert.equal(ed.state.personas["aggro"][0][2], 0);
});

test("save(): POSTet alle Personas", async () => {
  const fetch = makeFetch({ aggro: [[1, 0, 0, 0, 0, 0, 0, 0, 0]] }, "");
  const ed = loadBlock().createPersonaEditor({ document: {}, fetch });
  await ed.load();
  ed.inc("aggro", 0, 0);
  await ed.save("aggro");
  const post = fetch.calls.find((x) => x.route === "personas" && x.opts);
  assert.ok(post, "POST /personas gesendet");
  assert.equal(post.opts.aggro[0][0], 2);
});

test("addPersona/removePersona", () => {
  const ed = loadBlock().createPersonaEditor({ document: {}, fetch: () => {} });
  ed.state.personas = {};
  ed.state.saved = {};
  ed.addPersona("neu");
  assert.ok("neu" in ed.state.personas);
  ed.removePersona("neu");
  assert.ok(!("neu" in ed.state.personas));
});

test("setActive(): POST /persona_active", async () => {
  const fetch = makeFetch({}, "");
  const ed = loadBlock().createPersonaEditor({ document: {}, fetch });
  await ed.setActive("aggro");
  assert.equal(ed.state.active, "aggro");
  const call = fetch.calls.find((x) => x.route === "persona_active");
  assert.deepEqual(call.opts, { name: "aggro" });
});

test("totalCost(): Summe der Wellen-Kosten je Attack", () => {
  const ed = loadBlock().createPersonaEditor({ document: {}, fetch: () => {} });
  ed.state.cost = WAVE_COST;
  // wave1 x1 + wave3 x2 = 300 + 2*1400 = 3100
  assert.equal(ed.totalCost([1, 0, 2, 0, 0, 0, 0, 0, 0]), 3100);
  assert.equal(ed.totalCost([0, 0, 0, 0, 0, 0, 0, 0, 0]), 0);
});

test("Defensiv: kein location.reload im Panel-Code", () => {
  assert.ok(!extractBlock().includes("location.reload"), "kein Reload");
});
