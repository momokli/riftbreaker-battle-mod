"use strict";

/*
 * Render-/Funktions-Test fuers Cockpit (#832).
 *
 * Rendert cockpit/cockpit.html im ECHTEN Chromium (Playwright) und prueft die
 * Funktion, die jsdom/Unit-Tests nicht abdecken:
 *   - Tab-Struktur Operator/GameConfig/Persona/Natural/Docker/Advanced
 *     inkl. roving tabindex (aria-selected + tabindex),
 *   - Panels/Formulare rendern (Persona-Tabelle, Game-Config-Formular,
 *     Natural-Tabelle),
 *   - Lazy-Loading: der Docker-Poll startet erst beim Oeffnen des Tabs,
 *   - Docker-Log: full-width + auto-tail (laeuft nach, kein Refresh-Klicken),
 *   - keine JS-Fehler.
 *
 * Playwright/Chromium sind hier (noch) keine CI-Dependency: fehlen Modul oder
 * Browser, wird der Test uebersprungen (Skill cockpit-ui-review nennt den
 * echten Render-Post-Check als lokales Tool `tools/cockpit-ui-review/shoot.js`).
 */

const { test } = require("node:test");
const assert = require("node:assert");
const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");

let chromium = null;
try {
  ({ chromium } = require("playwright"));
} catch (e) {
  chromium = null;
}

const COCKPIT = path.join(__dirname, "..", "..", "cockpit", "cockpit.html");

// Realistische Persona-Matrix: 10 Attacken x 9 Wellen (der Renderer adressiert
// [attack][wave] ueber den vollen Bereich; eine zu kurze Matrix wirft).
function personaMatrix() {
  const m = [];
  for (let a = 0; a < 10; a++) m.push(new Array(9).fill(0));
  m[0][2] = 1;
  m[1][6] = 1;
  return m;
}

// Mock-Endpunkte: das Cockpit pollt all das; ohne Mock blieben Panels leer.
const ROUTES = {
  game_config: {
    mode: "solo",
    warmup_s: 120,
    natural: true,
    persona: true,
    send_yourself: true,
    send_enemy: false,
  },
  personas: {
    personas: { aggro: personaMatrix(), ruhig: personaMatrix() },
    active: "aggro",
    send_yourself: true,
  },
  attack_status: {
    active: true,
    state: "running",
    level: 3,
    seconds_to_next_attack: 187,
    seconds_to_next_difficulty: 412,
    bought: [3, 5, 5],
    outgoing: [],
    send_yourself: true,
    wave_cost: {
      1: 300,
      2: 700,
      3: 1400,
      4: 2450,
      5: 4000,
      6: 5350,
      7: 7600,
      8: 9650,
      9: 10500,
    },
    history: [{ attack: 1, natural: 1, self: [], enemy: [] }],
  },
  natural_attack_rules: {
    max_attack_count: [1, 2, 2, 2, 2, 2, 3, 3, 3],
    boss_min_level: 5,
    creature_events: [
      {
        name: "shegret_attack",
        min_level: 2,
        max_level: 4,
        attack_strength: "normal",
        weight: 3,
      },
      {
        name: "kermon_attack",
        min_level: 6,
        max_level: 7,
        attack_strength: "hard",
        weight: 1,
      },
    ],
    event_offset_fraction: 0.35,
  },
  difficulty_interval: {
    ok: true,
    difficulty_interval_first_s: 200,
    difficulty_interval_subsequent_s: 600,
  },
  attack_interval: { ok: true, interval_s: 420 },
  get_state: {
    ok: true,
    hq_hp: 100,
    hq_hp_max: 100,
    carbonium: 50000000,
    carbonium_max: 90000000,
    ironium: 10000000,
    ironium_max: 20000000,
    mission_flow: "attack_level_3_id_1.logic",
    mission_flow_active: true,
    players: 1,
  },
  probe: { ok: true },
  "server/status": {
    ok: true,
    state: "running",
    health: "healthy",
    uptime: "01:23:45",
    started_at: "2026-09-21T12:00:00Z",
  },
  "server/logs": {
    ok: true,
    lines: ["[12:00:01] server up", "[12:00:02] wave 1 fired", "[12:00:03] hq 100%"],
  },
};

function startServer() {
  const html = fs.readFileSync(COCKPIT, "utf8");
  const hits = [];
  const server = http.createServer((req, res) => {
    const url = (req.url || "/").split("?")[0].replace(/^\//, "");
    hits.push(url || "/");
    if (url === "" || url === "contract" || url === "contract/") {
      res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
      res.end(html);
      return;
    }
    const body = ROUTES[url];
    if (body === undefined) {
      res.writeHead(404);
      res.end("{}");
      return;
    }
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify(body));
  });
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () =>
      resolve({ server, hits, port: server.address().port }),
    );
  });
}

function countHits(hits, name) {
  return hits.filter((h) => h === name).length;
}

test("Cockpit rendert: Tabs, Formulare, Docker-Log tailt, keine JS-Fehler", async (t) => {
  if (!chromium) {
    t.skip("playwright nicht installiert");
    return;
  }

  const { server, hits, port } = await startServer();
  let browser;
  try {
    try {
      browser = await chromium.launch();
    } catch (e) {
      t.skip("chromium nicht verfuegbar: " + e.message);
      return;
    }

    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));

    await page.goto("http://127.0.0.1:" + port + "/", { waitUntil: "load" });
    await page.waitForTimeout(700);

    // --- Tab-Struktur + roving tabindex ---
    const tabIds = await page.$$eval("nav.tabs button", (bs) => bs.map((b) => b.id));
    assert.deepEqual(
      tabIds,
      [
        "tab_operator",
        "tab_game_config",
        "tab_personas",
        "tab_natural",
        "tab_docker",
        "tab_advanced",
      ],
      "6 Tabs in fester Reihenfolge",
    );
    assert.equal(await page.getAttribute("#tab_operator", "aria-selected"), "true");
    assert.equal(await page.getAttribute("#tab_operator", "tabindex"), "0");
    assert.equal(await page.getAttribute("#tab_docker", "tabindex"), "-1");

    // Operator ist Default; die anderen Panels sind versteckt.
    assert.equal(await page.locator("#workspace").isVisible(), true);
    assert.equal(await page.locator("#advanced_editor").isVisible(), false);
    assert.equal(await page.locator("#docker_editor").isVisible(), false);

    // --- Lazy-Loading: vor dem Oeffnen des Docker-Tabs kein Server-Poll ---
    assert.equal(
      countHits(hits, "server/status"),
      0,
      "kein Docker-Status-Poll vor Tab-Open",
    );
    assert.equal(countHits(hits, "server/logs"), 0, "kein Docker-Log-Poll vor Tab-Open");

    // --- Advanced-Tab: Panel sichtbar + Readouts gefuellt ---
    await page.click("#tab_advanced");
    await page.waitForTimeout(500);
    assert.equal(await page.locator("#advanced_editor").isVisible(), true);
    assert.equal(await page.locator("#workspace").isVisible(), false);
    assert.equal(await page.getAttribute("#tab_advanced", "aria-selected"), "true");
    assert.equal(await page.getAttribute("#tab_operator", "aria-selected"), "false");
    assert.equal(
      await page.locator("#mission_flow").textContent(),
      "attack_level_3_id_1.logic",
    );

    // --- Formulare rendern ---
    await page.click("#tab_personas");
    await page.waitForTimeout(500);
    const peRows = await page.locator("#pe_table tbody tr").count();
    assert.ok(peRows >= 3, "Persona-Tabelle rendert Zeilen (none + Personas): " + peRows);

    await page.click("#tab_game_config");
    await page.waitForTimeout(500);
    assert.equal(
      await page.locator("#gc_form #gc_mode").count(),
      1,
      "Game-Config-Formular rendert",
    );

    await page.click("#tab_natural");
    await page.waitForTimeout(500);
    const naRows = await page.locator("#na_table tbody tr").count();
    assert.ok(naRows >= 9, "Natural-Tabelle rendert 9 Level: " + naRows);

    // --- Docker-Tab: Lazy-Poll startet jetzt, Log ist full-width + auto-tailed ---
    await page.click("#tab_docker");
    await page.waitForTimeout(400);
    assert.equal(await page.locator("#docker_editor").isVisible(), true);
    assert.ok(countHits(hits, "server/status") >= 1, "Status-Poll startet beim Tab-Open");
    assert.ok(countHits(hits, "server/logs") >= 1, "Log-Poll startet beim Tab-Open");

    const initialLog = await page.locator("#server_logs").textContent();
    assert.ok(initialLog.includes("server up"), "Log-Inhalt initial: " + initialLog);

    // Der Log-Viewer nimmt die volle Breite des Docker-Tabs ein.
    const widths = await page.evaluate(() => {
      const area = document.querySelector("#server_logs").closest(".log-scroll-area");
      const editor = document.getElementById("docker_editor");
      return {
        area: area.getBoundingClientRect().width,
        editor: editor.getBoundingClientRect().width,
      };
    });
    assert.ok(
      widths.area > widths.editor * 0.85,
      "Log-Viewer ist full-width (" +
        Math.round(widths.area) +
        "/" +
        Math.round(widths.editor) +
        ")",
    );

    // Auto-Tail: ohne Klicken wird laufend nachgeladen.
    const logsBefore = countHits(hits, "server/logs");
    await page.waitForTimeout(2500);
    const logsAfter = countHits(hits, "server/logs");
    assert.ok(
      logsAfter > logsBefore,
      "Log tailt laufend nach (" + logsBefore + " -> " + logsAfter + ")",
    );

    // Kein manueller Refresh-Knopf mehr im Docker-Tab.
    assert.equal(
      await page.locator("#server_logs_refresh").count(),
      0,
      "kein Refresh-Knopf",
    );

    assert.deepEqual(errors, [], "keine JS-Fehler");
  } finally {
    if (browser) await browser.close();
    server.close();
  }
});
