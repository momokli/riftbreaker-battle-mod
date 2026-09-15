"use strict";

/*
 * Tests fuer das Cockpit-Panel "server control (plane B)" (Issue #422).
 *
 * Es wird KEIN Netzwerk und KEINE echte DOM gebraucht: der testbare Block wird
 * per Marker aus cockpit.html extrahiert und in einem vm-Kontext ausgewertet.
 * Fake-fetch + Fake-document (Stub-Elemente mit textContent) steuern das
 * Verhalten; so ist das defensive Contract (immer "—", nie werfen, nie reload)
 * ohne Agent/Deploy pruefbar.
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
  "04-trainer-io",
  "bridge",
  "cockpit.html",
);
const BEGIN = "// --- server-control panel (testable) ---";
const END = "// --- end server-control panel ---";

const STATUS_IDS = [
  "server_state",
  "server_health",
  "server_uptime",
  "server_started_at",
];
const DASH = "—";

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
  const ids = STATUS_IDS.concat([
    "server_logs",
    "server_logs_tail",
    "server_control_msg",
  ]);
  for (const id of ids) {
    els[id] = {
      id,
      textContent: id === "server_logs" ? DASH : "",
      value: id === "server_logs_tail" ? "50" : "",
    };
  }
  return {
    els,
    document: { getElementById: (id) => els[id] || null },
  };
}

function loadPanel() {
  const sandbox = {};
  vm.createContext(sandbox);
  const fn = vm.runInNewContext(
    extractBlock() + "\ncreateServerControlPanel;",
    sandbox,
  );
  assert.equal(typeof fn, "function", "createServerControlPanel gefunden");
  return fn;
}

function resp(data, ok = true) {
  return {
    ok,
    status: ok ? 200 : 500,
    json: async () => data,
  };
}

test("Fall A: fetch wirft / HTTP !ok / Parse-Fehler -> nur '—', kein Wurf", async () => {
  const cases = [
    async () => {
      throw new Error("agent unreachable");
    },
    async () => resp({}, false),
    async () => ({
      ok: true,
      status: 200,
      json: async () => {
        throw new Error("bad json");
      },
    }),
  ];

  for (const fakeFetch of cases) {
    const { els, document } = makeDoc();
    const panel = loadPanel()({ fetch: fakeFetch, document });

    await assert.doesNotReject(() => panel.refreshStatus());
    await assert.doesNotReject(() => panel.refreshLogs());

    for (const id of STATUS_IDS) {
      assert.equal(els[id].textContent, DASH, id + " zeigt '—'");
    }
    assert.equal(els.server_logs.textContent, DASH, "logs zeigen '—'");
  }
});

test("Fall B: erfolgreiche Antworten rendern Werte", async () => {
  const { els, document } = makeDoc();
  const calls = [];
  const fakeFetch = async (url, opts) => {
    calls.push({ url, opts });
    if (url === "/server/status") {
      return resp({ state: "running", health: "healthy", uptime: "2h" });
    }
    if (url.startsWith("/server/logs")) {
      return resp({ lines: ["a", "b"], tail: 2 });
    }
    return resp({});
  };

  const panel = loadPanel()({ fetch: fakeFetch, document });

  await panel.refreshStatus();
  await panel.refreshLogs();

  assert.equal(els.server_state.textContent, "running");
  assert.equal(els.server_health.textContent, "healthy");
  assert.equal(els.server_uptime.textContent, "2h");
  assert.equal(els.server_started_at.textContent, DASH, "fehlendes Feld -> '—'");
  assert.equal(els.server_logs.textContent, "a\nb");

  const statusCall = calls.find((c) => c.url === "/server/status");
  assert.ok(statusCall, "GET /server/status aufgerufen");
  assert.equal(statusCall.opts.method, "GET");

  const logsCall = calls.find((c) => c.url.startsWith("/server/logs"));
  assert.ok(logsCall, "GET /server/logs aufgerufen");
  assert.equal(logsCall.url, "/server/logs?tail=50");
});

test("Fall C: control() postet /server/{restart,start,stop}", async () => {
  const { document } = makeDoc();
  const posts = [];
  const fakeFetch = async (url, opts) => {
    if (opts && opts.method === "POST") {
      posts.push({ url, opts });
      return resp({ ok: true });
    }
    if (url === "/server/status") return resp({ state: "running" });
    return resp({ lines: [] });
  };

  const panel = loadPanel()({ fetch: fakeFetch, document });

  await panel.control("restart");
  await panel.control("start");
  await panel.control("stop");

  assert.deepEqual(
    posts.map((p) => p.url),
    ["/server/restart", "/server/start", "/server/stop"],
  );
  for (const p of posts) {
    assert.equal(p.opts.method, "POST");
    assert.equal(p.opts.body, "{}");
  }
});

test("Fall D: Panel-Block referenziert location/reload nicht (No-Reload-Garantie)", () => {
  const block = extractBlock();
  assert.ok(
    !/\blocation\b/.test(block),
    "Panel-Block darf location nicht referenzieren",
  );
  assert.ok(!/\breload\b/.test(block), "Panel-Block darf reload nicht aufrufen");
});
