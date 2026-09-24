// UI-Post-Check fuers Cockpit (#832-Folge): rendert cockpit.html im echten
// Chromium, shootet jeden Tab und meldet Layout-Heuristiken als
// 'clunky'-Indikatoren. Die Screenshots sind die visuelle Abnahme fuer Menschen
// (dieses Tool bewertet NICHT selbst "schoen", nur maschinelle Auffaelligkeiten).
//
// Aufruf:  node tools/cockpit-ui-review/shoot.js
// Ausgabe: docs/screenshots/ui/<tab>.png + ein Report auf stdout.
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(__dirname, "..", "..");
const COCKPIT = path.join(REPO, "cockpit", "cockpit.html");
const OUT = path.join(REPO, "docs", "screenshots", "ui");

const html = fs.readFileSync(COCKPIT, "utf8");

// Realistische Persona-Matrix (10 Attacken x 9 Wellen): der Renderer adressiert
// [attack][wave] ueber den vollen Bereich — eine kuerzere Matrix wirft und
// laesst die Persona-Tabelle leer.
function personaMatrix(seed) {
  const m = [];
  for (let a = 0; a < 10; a++) m.push(new Array(9).fill(0));
  m[seed % 10][(seed + 2) % 9] = 1;
  m[(seed + 1) % 10][(seed + 6) % 9] = 1;
  return m;
}

// Mock-Endpunkte: das Cockpit pollt all das; ohne Mock bliebe es leer.
const ROUTES = {
  game_config: { mode: "solo", warmup_s: 120, natural: true, persona: true, send_yourself: true, send_enemy: false },
  personas: {
    personas: { aggro: personaMatrix(0), ruhig: personaMatrix(3) },
    active: "aggro",
  },
  attack_status: {
    active: true, state: "running", level: 3, seconds_to_next_attack: 187,
    seconds_to_next_difficulty: 412, bought: [3, 5, 5], outgoing: [], send_yourself: true,
    wave_cost: { 1: 300, 2: 700, 3: 1400, 4: 2450, 5: 4000, 6: 5350, 7: 7600, 8: 9650, 9: 10500 },
    history: [{ attack: 1, natural: 1, self: [], enemy: [] }],
  },
  natural_attack_rules: {
    max_attack_count: [1, 2, 2, 2, 2, 2, 3, 3, 3], boss_min_level: 5,
    creature_events: [
      { name: "shegret_attack", min_level: 2, max_level: 4, attack_strength: "normal", weight: 3 },
      { name: "kermon_attack", min_level: 6, max_level: 7, attack_strength: "hard", weight: 1 },
    ],
    event_offset_fraction: 0.35,
  },
  difficulty_interval: { ok: true, difficulty_interval_first_s: 200, difficulty_interval_subsequent_s: 600 },
  attack_interval: { ok: true, interval_s: 420 },
  get_state: { ok: true, hq_hp: 100, hq_hp_max: 100, carbonium: 50000000, carbonium_max: 90000000, ironium: 10000000, ironium_max: 20000000, mission_flow: "attack_level_3_id_1.logic", mission_flow_active: true, players: 1 },
  // Kapsel-Flow (Issue #931, US8): das Cockpit pollt /capsule/status fuer den
  // Phase-Readout. Ohne Mock bliebe "capsule: —" (Fallback) und der Review
  // wuerde die neue Anzeige nicht abdecken.
  "capsule/status": { phase: "running", env: "solo", instance: "parked-1", bridge_url: "http://127.0.0.1:40001", gns_endpoint: "127.0.0.1:41001", round: 0, cycle: { state: "running" } },
  probe: { ok: true },
  "server/status": { ok: true, state: "running", uptime: "01:23:45", started_at: "2026-09-21T12:00:00Z" },
  "server/logs": { ok: true, lines: ["[12:00:01] server up", "[12:00:02] wave 1 fired", "[12:00:03] hq 100%"] },
};

const server = http.createServer((req, res) => {
  const url = (req.url || "/").split("?")[0].replace(/^\//, "");
  if (url === "" || url === "contract" || url === "contract/") {
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(html);
    return;
  }
  const body = ROUTES[url];
  if (body === undefined) { res.writeHead(404); res.end("{}"); return; }
  res.writeHead(200, { "content-type": "application/json" });
  res.end(JSON.stringify(body));
});

// --- Layout-Heuristiken (im Browser ausgefuehrt) --------------------------
async function heuristics(page) {
  return page.evaluate(() => {
    const issues = [];
    const de = document.documentElement;
    if (de.scrollWidth > de.clientWidth + 1) {
      issues.push("horizontaler Seiten-Overflow: scrollWidth " + de.scrollWidth + " > clientWidth " + de.clientWidth);
    }
    const vw = window.innerWidth;
    const clickable = "button, a, input, select, label";
    const seen = new Set();
    document.querySelectorAll("*").forEach((el) => {
      const r = el.getBoundingClientRect();
      if (r.width === 0 && r.height === 0) return;
      const sel = el.tagName.toLowerCase() + (el.id ? "#" + el.id : "");
      // horizontales Rausragen (links/rechts)
      if ((r.right > vw + 2 || r.left < -2) && el.offsetParent !== null) {
        const k = "overflow:" + sel;
        if (!seen.has(k)) { seen.add(k); issues.push("ragt horizontal raus: <" + sel + "> left=" + Math.round(r.left) + " right=" + Math.round(r.right) + " (vw=" + vw + ")"); }
      }
      // abgeschnittener Text (scrollWidth > clientWidth). Bewusst screenreader-
      // only Elemente (.visually-hidden) ausnehmen: deren 1px-Clip ist gewollt,
      // kein abgeschnittener UI-Text.
      if (el.scrollWidth > el.clientWidth + 2 && (el.textContent || "").trim().length > 0 && el.children.length === 0 && !(el.closest && el.closest(".visually-hidden"))) {
        const k = "clip:" + sel;
        if (!seen.has(k)) { seen.add(k); issues.push("Text abgeschnitten: <" + sel + "> \"" + (el.textContent || "").trim().slice(0, 30) + "\""); }
      }
    });
    // Klickziele kleiner als 24x24
    document.querySelectorAll(clickable).forEach((el) => {
      const r = el.getBoundingClientRect();
      if ((r.width > 0 || r.height > 0) && (r.width < 24 || r.height < 24)) {
        const sel = el.tagName.toLowerCase() + (el.id ? "#" + el.id : "");
        const k = "small:" + sel;
        if (!seen.has(k)) { seen.add(k); issues.push("kleines Klickziel (<24px): <" + sel + "> " + Math.round(r.width) + "x" + Math.round(r.height)); }
      }
    });
    return issues;
  });
}

server.listen(0, async () => {
  const port = server.address().port;
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 });
  const jsErrors = [];
  page.on("pageerror", (e) => jsErrors.push(e.message));
  await page.goto("http://127.0.0.1:" + port + "/", { waitUntil: "networkidle" });
  await page.waitForTimeout(800);

  const tabIds = await page.$$eval("nav.tabs button", (bs) => bs.map((b) => b.id));
  console.log("Gefundene Tabs: " + tabIds.join(", "));

  for (const id of tabIds) {
    await page.click("#" + id);
    await page.waitForTimeout(500);
    const shot = path.join(OUT, id + ".png");
    await page.screenshot({ path: shot });
    const hs = await heuristics(page);
    console.log("\n### " + id + "  -> " + path.relative(REPO, shot));
    if (hs.length === 0) console.log("  (keine Layout-Auffaelligkeiten)");
    else hs.forEach((h) => console.log("  - " + h));
  }

  if (jsErrors.length) {
    console.log("\n### JS-Fehler");
    jsErrors.forEach((e) => console.log("  - " + e));
  } else {
    console.log("\nKeine JS-Fehler.");
  }

  console.log("\nScreenshots liegen in " + path.relative(REPO, OUT) + "/ — visuelle Abnahme durch Mensch.");
  await browser.close();
  server.close();
});
