"use strict";

/*
 * Tests fuer den Game-Config-Editor (mode/warmup_s + 4 Toggles + start/ready/state).
 * Der testbare Marker-Block (createGameConfigEditor) wird aus cockpit.html
 * extrahiert und im vm ausgewertet. Die Kern-Logik
 * (load/loadStatus/setField/save/restore/start/setReady) ist DOM-frei.
 */

const { test } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const COCKPIT = path.join(__dirname, "..", "..", "cockpit", "cockpit.html");
const BEGIN = "// --- game config editor (testable) ---";
const END = "// --- end game config editor ---";

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
  return vm.runInNewContext(extractBlock() + "\n({ createGameConfigEditor });", sandbox);
}

const DEFAULT_CONFIG = {
  mode: "solo",
  warmup_s: 120,
  natural: true,
  persona: false,
  send_yourself: true,
  send_enemy: false,
};

const STATUS = { state: "warmup" };

function makeFetch(config) {
  const calls = [];
  const fn = (route, opts) => {
    calls.push({
      route,
      method: opts ? opts.method : "GET",
      opts: opts ? JSON.parse(opts.body) : undefined,
    });
    if (route === "game_config" && !opts) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(config) });
    }
    if (route === "attack_status" && !opts) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(STATUS) });
    }
    if (opts) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ ok: true }) });
    }
    return Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({}) });
  };
  fn.calls = calls;
  return fn;
}

test("Marker-Block exportiert createGameConfigEditor", () => {
  assert.equal(typeof loadBlock().createGameConfigEditor, "function");
});

test("load(): Config geladen und als saved gesichert", async () => {
  const ed = loadBlock().createGameConfigEditor({ document: {}, fetch: makeFetch(DEFAULT_CONFIG) });
  await ed.load();
  assert.equal(ed.state.config.mode, "solo");
  assert.equal(ed.state.config.warmup_s, 120);
  assert.equal(ed.state.config.natural, true);
  assert.equal(ed.state.config.send_enemy, false);
  assert.deepEqual(ed.state.saved, ed.state.config);
});

test("loadStatus(): attack_status.state uebernommen", async () => {
  const ed = loadBlock().createGameConfigEditor({ document: {}, fetch: makeFetch(DEFAULT_CONFIG) });
  await ed.loadStatus();
  assert.equal(ed.state.status.state, "warmup");
});

test("setField(): Einzelfeld aendern", () => {
  const ed = loadBlock().createGameConfigEditor({ document: {}, fetch: () => {} });
  ed.state.config = JSON.parse(JSON.stringify(DEFAULT_CONFIG));
  ed.setField("mode", "vs");
  ed.setField("send_enemy", true);
  ed.setField("warmup_s", 60.5);
  assert.equal(ed.state.config.mode, "vs");
  assert.equal(ed.state.config.send_enemy, true);
  assert.equal(ed.state.config.warmup_s, 60.5);
});

test("setField(): initialisiert fehlende Config", () => {
  const ed = loadBlock().createGameConfigEditor({ document: {}, fetch: () => {} });
  ed.setField("mode", "vs");
  assert.equal(ed.state.config.mode, "vs");
});

test("save(): POSTet die komplette Config an game_config", async () => {
  const fetch = makeFetch(DEFAULT_CONFIG);
  const ed = loadBlock().createGameConfigEditor({ document: {}, fetch });
  await ed.load();
  ed.setField("mode", "vs");
  await ed.save();
  const post = fetch.calls.find((x) => x.route === "game_config" && x.opts);
  assert.ok(post, "POST /game_config gesendet");
  assert.equal(post.method, "POST");
  assert.equal(post.opts.mode, "vs");
  assert.equal(post.opts.warmup_s, 120);
  assert.equal(ed.state.saved.mode, "vs");
});

test("restore(): verwirft lokale Aenderungen", () => {
  const ed = loadBlock().createGameConfigEditor({ document: {}, fetch: () => {} });
  ed.state.config = JSON.parse(JSON.stringify(DEFAULT_CONFIG));
  ed.state.saved = JSON.parse(JSON.stringify(DEFAULT_CONFIG));
  ed.setField("mode", "vs");
  assert.equal(ed.state.config.mode, "vs");
  ed.restore();
  assert.equal(ed.state.config.mode, "solo");
  assert.equal(ed.state.config.natural, true);
});

test("start(): POSTet /start", async () => {
  const fetch = makeFetch(DEFAULT_CONFIG);
  const ed = loadBlock().createGameConfigEditor({ document: {}, fetch });
  await ed.start();
  const post = fetch.calls.find((x) => x.route === "start");
  assert.ok(post, "POST /start gesendet");
  assert.equal(post.method, "POST");
});

test("setReady(true): POSTet /ready mit on=1", async () => {
  const fetch = makeFetch(DEFAULT_CONFIG);
  const ed = loadBlock().createGameConfigEditor({ document: {}, fetch });
  await ed.setReady(true);
  const post = fetch.calls.find((x) => x.route === "ready");
  assert.ok(post, "POST /ready gesendet");
  assert.equal(post.opts.on, 1);
});

test("setReady(false): POSTet /ready mit on=0", async () => {
  const fetch = makeFetch(DEFAULT_CONFIG);
  const ed = loadBlock().createGameConfigEditor({ document: {}, fetch });
  await ed.setReady(false);
  const post = fetch.calls.find((x) => x.route === "ready");
  assert.ok(post, "POST /ready gesendet");
  assert.equal(post.opts.on, 0);
});

test("Defensiv: kein location.reload im Panel-Code", () => {
  assert.ok(!extractBlock().includes("location.reload"), "kein Reload");
});
