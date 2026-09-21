"use strict";

/*
 * Tests fuer das Cockpit-Panel "send tracker (self)" — die eigenen Sends
 * (queued/outgoing) + der send-yourself-Toggle. Ersetzt den alten
 * Carbonium-Log (Issue #527); der testbare Marker-Block wird aus cockpit.html
 * extrahiert und in einem vm-Kontext ausgewertet. Kein Netz, kein DOM.
 */

const { test } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const COCKPIT = path.join(__dirname, "..", "..", "cockpit", "cockpit.html");
const BEGIN = "// --- self-send tracker (testable) ---";
const END = "// --- end self-send tracker ---";

const IDS = ["send_log", "send_yourself"];

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
    els[id] = { id, textContent: "", checked: false };
  }
  return { els, document: { getElementById: (id) => els[id] || null } };
}

function fakeFetch(routes) {
  const calls = [];
  const fn = (route, opts) => {
    calls.push({ route, opts: opts ? JSON.parse(opts.body) : undefined });
    const r = routes[route];
    if (!r) return Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({}) });
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(r) });
  };
  fn.calls = calls;
  return fn;
}

function loadBlock() {
  const sandbox = {};
  vm.createContext(sandbox);
  const exported = vm.runInNewContext(
    extractBlock() + "\n({ createSelfSendTracker });",
    sandbox,
  );
  return exported;
}

test("Marker-Block exportiert createSelfSendTracker", () => {
  assert.equal(typeof loadBlock().createSelfSendTracker, "function");
});

test("refresh(): attack_status -> queued/outgoing + Checkbox", async () => {
  const fetch = fakeFetch({
    attack_status: { active: true, bought: [1, 2], outgoing: [], send_yourself: true },
  });
  const doc = makeDoc();
  const t = loadBlock().createSelfSendTracker({ document: doc.document, fetch });
  await t.refresh();
  assert.ok(doc.els.send_log.textContent.includes("queued: 1,2"));
  assert.ok(doc.els.send_log.textContent.includes("outgoing: -"));
  assert.equal(doc.els.send_yourself.checked, true);
});

test("refresh(): nicht aktiv -> kein Render", async () => {
  const fetch = fakeFetch({ attack_status: { active: false } });
  const doc = makeDoc();
  const t = loadBlock().createSelfSendTracker({ document: doc.document, fetch });
  await t.refresh();
  assert.equal(doc.els.send_log.textContent, "");
});

test("setSendYourself(): POST /send_yourself (on=1/0)", async () => {
  const fetch = fakeFetch({
    send_yourself: { ok: true },
    attack_status: { active: true, bought: [], outgoing: [], send_yourself: false },
  });
  const doc = makeDoc();
  const t = loadBlock().createSelfSendTracker({ document: doc.document, fetch });
  await t.setSendYourself(false);
  const call = fetch.calls.find((x) => x.route === "send_yourself");
  assert.deepEqual(call.opts, { on: 0 });
});

test("fmtWaves: leer -> '-'", () => {
  const t = loadBlock().createSelfSendTracker({ document: makeDoc().document, fetch: () => {} });
  assert.equal(t.fmtWaves([]), "-");
  assert.equal(t.fmtWaves([3, 3, 5]), "3,3,5");
});

test("Defensiv: kein location.reload im Panel-Code", () => {
  assert.ok(!extractBlock().includes("location.reload"), "kein Reload");
});
