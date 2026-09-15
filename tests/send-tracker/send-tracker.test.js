"use strict";

/*
 * Tests fuer das Cockpit-Panel "send tracker" (Issue #527, 1.0-Send-Loop #517).
 *
 * Wie beim server-control-panel: der testbare Marker-Block wird aus
 * cockpit.html extrahiert und in einem vm-Kontext ausgewertet. Fake-document
 * (Stub-Elemente mit textContent/value) + Fake-store/source steuern das
 * Verhalten; kein Netzwerk, keine echte DOM, kein Deploy.
 *
 * Geprueft wird:
 *  - Send-Eintraege erscheinen mit timestamp + amount (Akzeptanz 1),
 *  - persistent/querybar statt transient: Reload laedt aus dem Store
 *    (Akzeptanz 2), freie Suche filtert,
 *  - die Quelle ist gekapselt/austauschbar (kein Adapter -> DASH + Meldung),
 *  - defensives Contract: nie werfen, nie reload, Quota-Fehler degradieren.
 */

const { test } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const COCKPIT = path.join(
  __dirname,
  "..",
  "..",
  "bausteine",
  "08-control-ui",
  "cockpit.html",
);
const BEGIN = "// --- send tracker (testable) ---";
const END = "// --- end send tracker ---";
const DASH = "—";

const IDS = [
  "send_log",
  "send_total",
  "send_count",
  "send_source",
  "send_query",
  "send_tracker_msg",
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
      textContent: id === "send_log" ? DASH : "",
      value: "",
    };
  }
  return {
    els,
    document: { getElementById: (id) => els[id] || null },
  };
}

function makeStorage(seed) {
  const data = Object.assign({}, seed);
  return {
    data,
    getItem: (k) => (k in data ? data[k] : null),
    setItem: (k, v) => {
      data[k] = String(v);
    },
  };
}

function memoryStore() {
  let mem = [];
  return {
    load: () => mem.slice(),
    save: (e) => {
      mem = e.slice();
    },
  };
}

function fakeSource(result) {
  return {
    name: "fake",
    calls: [],
    poll(cursor) {
      this.calls.push(cursor);
      if (result instanceof Error) return Promise.reject(result);
      return Promise.resolve(
        typeof result === "function" ? result(cursor) : result,
      );
    },
  };
}

function loadBlock() {
  const sandbox = {};
  vm.createContext(sandbox);
  const exported = vm.runInNewContext(
    extractBlock() +
      "\n({ createSendTracker, createLocalStorageStore, createBridgeSendSource });",
    sandbox,
  );
  return exported;
}

test("Marker-Block exportiert die drei Fabriken", () => {
  const m = loadBlock();
  assert.equal(typeof m.createSendTracker, "function");
  assert.equal(typeof m.createLocalStorageStore, "function");
  assert.equal(typeof m.createBridgeSendSource, "function");
});

test("Akzeptanz 1: Eintraege erscheinen mit timestamp + amount", () => {
  const { document } = makeDoc();
  const tracker = loadBlock().createSendTracker({
    document,
    store: memoryStore(),
    source: null,
    entries: [
      { timestamp: 1757000000, resource: "carbonium", amount: 50 },
      { timestamp: 1757000100, resource: "carbonium", amount: 25 },
    ],
  });
  tracker.render();
  const log = document.getElementById("send_log").textContent;
  assert.ok(log.includes("carbonium"), "resource sichtbar");
  assert.ok(log.includes("+50"), "amount sichtbar");
  assert.ok(log.includes("+25"), "amount sichtbar");
  assert.ok(/\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}/.test(log), "timestamp sichtbar");
  assert.equal(document.getElementById("send_count").textContent, "2");
  assert.equal(document.getElementById("send_total").textContent, "75");
});

test("Akzeptanz 2: persistent — Reload laedt Eintraege aus dem Store", async () => {
  const m = loadBlock();
  const storage = makeStorage();
  const storeA = m.createLocalStorageStore("rbb.send_log", storage);
  const docA = makeDoc();
  const trackerA = m.createSendTracker({
    document: docA.document,
    store: storeA,
    source: fakeSource({
      entries: [{ timestamp: 1757000000, resource: "carbonium", amount: 40 }],
      cursor: 1,
    }),
  });
  await trackerA.poll();
  assert.ok(storage.data["rbb.send_log"].includes("40"), "in Storage geschrieben");

  // Neuer Tracker = Reload: keine Poll-Quelle, trotzdem Eintraege sichtbar.
  const docB = makeDoc();
  const trackerB = m.createSendTracker({
    document: docB.document,
    store: m.createLocalStorageStore("rbb.send_log", storage),
    source: null,
  });
  trackerB.render();
  assert.ok(
    docB.document.getElementById("send_log").textContent.includes("+40"),
    "Eintrag ueberlebt den Reload (nicht transient)",
  );
});

test("Akzeptanz 2: querybar — Filter blendet nichttreffer aus", () => {
  const { document } = makeDoc();
  const tracker = loadBlock().createSendTracker({
    document,
    store: memoryStore(),
    source: null,
    entries: [
      { timestamp: 1757000000, resource: "carbonium", amount: 50 },
      { timestamp: 1757000100, resource: "ironium", amount: 7 },
    ],
  });
  document.getElementById("send_query").value = "ironium";
  tracker.render();
  const log = document.getElementById("send_log").textContent;
  assert.ok(log.includes("ironium"), "Treffer sichtbar");
  assert.ok(!log.includes("carbonium"), "Nichttreffer ausgeblendet");
  assert.equal(document.getElementById("send_count").textContent, "1");
});

test("Quelle ist gekapselt: ohne Adapter DASH + Meldung, kein Wurf", async () => {
  const { document } = makeDoc();
  const tracker = loadBlock().createSendTracker({
    document,
    store: memoryStore(),
    source: null,
  });
  const res = await tracker.poll();
  assert.equal(res.added, 0);
  assert.equal(document.getElementById("send_log").textContent, DASH);
  assert.match(
    document.getElementById("send_tracker_msg").textContent,
    /#526/,
    "offener Anschluss wird benannt",
  );
});

test("Defensiv: Source-Fehler wirft nicht und laesst persistente Eintraege stehen", async () => {
  const { document } = makeDoc();
  const tracker = loadBlock().createSendTracker({
    document,
    store: memoryStore(),
    source: fakeSource(new Error("boom")),
    entries: [{ timestamp: 1757000000, resource: "carbonium", amount: 5 }],
  });
  const res = await tracker.poll();
  assert.equal(res.error, "boom");
  assert.ok(
    document.getElementById("send_log").textContent.includes("+5"),
    "alte Eintraege bleiben sichtbar",
  );
  assert.match(document.getElementById("send_tracker_msg").textContent, /unavailable/);
});

test("Poll merged idempotent (Dubletten) und zieht den cursor nach", async () => {
  const { document } = makeDoc();
  const source = fakeSource((cursor) => ({
    entries:
      cursor === null
        ? [{ timestamp: 10, resource: "carbonium", amount: 1 }]
        : [
            { timestamp: 10, resource: "carbonium", amount: 1 },
            { timestamp: 20, resource: "carbonium", amount: 2 },
          ],
    cursor: 20,
  }));
  const tracker = loadBlock().createSendTracker({
    document,
    store: memoryStore(),
    source,
  });
  await tracker.poll();
  assert.equal(tracker.entries().length, 1);
  await tracker.poll();
  assert.equal(tracker.entries().length, 2, "Dublette nicht doppelt gezaehlt");
  assert.equal(source.calls[1], 20, "cursor wurde weitergereicht");
});

test("Store degradiert ohne localStorage (In-Memory, kein Wurf)", () => {
  const m = loadBlock();
  const store = m.createLocalStorageStore("rbb.send_log", null);
  assert.equal(store.persistent(), false);
  store.save([{ timestamp: 1, resource: "carbonium", amount: 1 }]);
  assert.equal(store.load().length, 1, "In-Memory-Fallback");
});

test("Defensiv: kaputter/Quota-werfender Storage liefert [] und bleibt nutzbar", () => {
  const m = loadBlock();
  const throwing = {
    getItem: () => "{not json",
    setItem: () => {
      throw new Error("QuotaExceededError");
    },
  };
  const store = m.createLocalStorageStore("rbb.send_log", throwing);
  assert.deepEqual(store.load(), [], "Parse-Fehler -> []");
  // Save wirft (Quota/privacy mode): kein Wurf nach aussen, der
  // In-Memory-Fallback traegt den Eintrag weiter (Log bleibt sichtbar).
  store.save([{ timestamp: 1, resource: "carbonium", amount: 9 }]);
  assert.equal(store.load().length, 1, "In-Memory-Fallback traegt");
});

test("Bridge-Adapter (provisorisch, #526) postet seit-cursor und liest entries", async () => {
  const m = loadBlock();
  const calls = [];
  const fetchFn = (route, opts) => {
    calls.push({ route, opts });
    return Promise.resolve({
      ok: true,
      json: () =>
        Promise.resolve({
          entries: [{ timestamp: 3, resource: "carbonium", amount: 4 }],
          cursor: 3,
        }),
    });
  };
  const source = m.createBridgeSendSource(fetchFn);
  assert.equal(source.name, "bridge:get_send_log");
  const res = await source.poll(2);
  assert.equal(calls[0].route, "get_send_log");
  assert.equal(JSON.parse(calls[0].opts.body).since, 2);
  assert.equal(res.entries[0].amount, 4);
  assert.equal(res.cursor, 3);
});

test("Bridge-Adapter wirft bei HTTP-Fehler (Quelle bleibt austauschbar)", async () => {
  const m = loadBlock();
  const fetchFn = () => Promise.resolve({ ok: false, status: 503 });
  const source = m.createBridgeSendSource(fetchFn);
  await assert.rejects(() => source.poll(null), /503/);
});

test("Kein location.reload im Panel-Code", () => {
  const block = extractBlock();
  assert.ok(!block.includes("location.reload"), "kein Reload");
});
