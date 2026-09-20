"use strict";

/*
 * Tests fuer das Cockpit-Panel "personas" (Issue #788).
 *
 * Wie beim send-tracker: der testbare Marker-Block wird aus cockpit.html
 * extrahiert und in einem vm-Kontext ausgewertet. Fake-document (Stub-Elemente)
 * + Fake-fetch; kein Netzwerk, keine echte DOM, kein Deploy.
 *
 * Geprueft wird:
 *  - load(): GET /personas -> State + Render (Liste, aktives Dropdown, Checkbox)
 *  - save(): upsert + POST /personas (ganze Defs)
 *  - remove(): loeschen + POST /personas
 *  - setActive(): POST /persona_active
 *  - parseSends/formatSends: null-Handling, Validierung
 *  - defensives Contract: kein location.reload
 */

const { test } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const COCKPIT = path.join(__dirname, "..", "..", "cockpit", "cockpit.html");
const BEGIN = "// --- personas (testable) ---";
const END = "// --- end personas ---";
const DASH = "—";

const IDS = [
  "personas_list",
  "personas_msg",
  "persona_active",
  "persona_name",
  "persona_sends",
];

function extractBlock() {
  const html = fs.readFileSync(COCKPIT, "utf8");
  const begin = html.indexOf(BEGIN);
  const end = html.indexOf(END);
  assert.ok(begin !== -1, "Begin-Marker vorhanden");
  assert.ok(end > begin, "End-Marker nach Begin-Marker");
  return html.slice(begin, end + END.length);
}

function makeDoc() {
  const els = {};
  for (const id of IDS) {
    els[id] = {
      id,
      textContent: "",
      value: "",
      checked: false,
      innerHTML: "",
    };
  }
  return {
    els,
    document: { getElementById: (id) => els[id] || null },
  };
}

function fakeFetch(routes) {
  const calls = [];
  const fn = (route, opts) => {
    calls.push({ route, opts: opts ? JSON.parse(opts.body) : undefined });
    const r = routes[route];
    if (!r)
      return Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({}) });
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(r) });
  };
  fn.calls = calls;
  return fn;
}

function loadBlock() {
  const sandbox = {};
  vm.createContext(sandbox);
  const exported = vm.runInNewContext(
    extractBlock() + "\n({ createPersonasController });",
    sandbox,
  );
  return exported;
}

test("Marker-Block exportiert createPersonasController", () => {
  const m = loadBlock();
  assert.equal(typeof m.createPersonasController, "function");
});

test("parseSends/formatSends: nested je Attack", () => {
  const m = loadBlock();
  const mk = () =>
    m.createPersonasController({ document: makeDoc().document, fetch: () => {} });
  assert.deepEqual(mk().parseSends("1,3 / 5 / 9,9"), [[1, 3], [5], [9, 9]]);
  assert.deepEqual(mk().parseSends("1,1,1"), [[1, 1, 1]]);
  assert.deepEqual(mk().parseSends("- / 2"), [[], [2]]);
  assert.deepEqual(mk().parseSends("  "), []);
  assert.equal(mk().formatSends([[3, 5], [], [7]]), "3,5 / - / 7");
  assert.equal(mk().formatSends([[1, 1, 1]]), "1,1,1");
  assert.throws(() => mk().parseSends("3,abc"), /invalid level/);
});

test("load(): GET /personas -> State + Render (Liste + Dropdown + Checkbox)", async () => {
  const fetch = fakeFetch({
    personas: {
      personas: { aggro: [[3, 5], [7]], ruhig: [[], [2]] },
      active: "aggro",
      send_yourself: false,
    },
  });
  const doc = makeDoc();
  const c = loadBlock().createPersonasController({ document: doc.document, fetch });
  await c.load();
  assert.deepEqual(c.state.personas, { aggro: [[3, 5], [7]], ruhig: [[], [2]] });
  assert.equal(c.state.active, "aggro");
  const list = doc.els.personas_list.textContent;
  assert.ok(list.includes("* aggro: 3,5 / 7"), "aktive persona markiert");
  assert.ok(list.includes("ruhig: - / 2"), "zweite persona gelistet");
  assert.equal(doc.els.persona_active.value, "aggro");
});

test("save(): upsert + POST /personas (ganze Defs)", async () => {
  const fetch = fakeFetch({
    personas: { personas: { aggro: [[3, 5]] }, active: "", send_yourself: true },
  });
  const doc = makeDoc();
  const c = loadBlock().createPersonasController({ document: doc.document, fetch });
  await c.load();
  doc.els.persona_name.value = "neu";
  doc.els.persona_sends.value = "1,2";
  await c.save();
  const postCall = fetch.calls.find((x) => x.route === "personas" && x.opts);
  assert.ok(postCall, "POST /personas gesendet");
  assert.deepEqual(postCall.opts, { aggro: [[3, 5]], neu: [[1, 2]] });
});

test("save(): leerer Name -> Meldung, kein POST", async () => {
  const fetch = fakeFetch({
    personas: { personas: {}, active: "", send_yourself: true },
  });
  const doc = makeDoc();
  const c = loadBlock().createPersonasController({ document: doc.document, fetch });
  await c.load();
  doc.els.persona_name.value = "";
  await c.save();
  assert.match(doc.els.personas_msg.textContent, /name fehlt/);
  assert.equal(fetch.calls.filter((x) => x.route === "personas" && x.opts).length, 0);
});

test("remove(): loescht + POST /personas", async () => {
  const fetch = fakeFetch({
    personas: {
      personas: { aggro: [[3, 5]], ruhig: [[], [2]] },
      active: "aggro",
      send_yourself: true,
    },
  });
  const doc = makeDoc();
  const c = loadBlock().createPersonasController({ document: doc.document, fetch });
  await c.load();
  doc.els.persona_name.value = "aggro";
  await c.remove();
  const postCall = fetch.calls.find((x) => x.route === "personas" && x.opts);
  assert.deepEqual(postCall.opts, { ruhig: [[], [2]] });
});

test("setActive(): POST /persona_active", async () => {
  const fetch = fakeFetch({
    personas: { personas: { aggro: [[3]] }, active: "", send_yourself: true },
    persona_active: { ok: true },
  });
  const doc = makeDoc();
  const c = loadBlock().createPersonasController({ document: doc.document, fetch });
  await c.setActive("aggro");
  assert.equal(c.state.active, "aggro");
  const call = fetch.calls.find((x) => x.route === "persona_active");
  assert.deepEqual(call.opts, { name: "aggro" });
});

test("Defensiv: kein location.reload im Panel-Code", () => {
  const block = extractBlock();
  assert.ok(!block.includes("location.reload"), "kein Reload");
});
