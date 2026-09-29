"use strict";

// Host-Test fuer das Ready-Gate von pipe_bridge (Issue #937, US3/US4):
//   (1) kompiliert die Windows-freie Logik (ready_gate.h) mit dem HOST-Compiler
//       (cc, dann gcc — NIE mingw) und fuehrt den Harness aus (pure Logik:
//       idempotentes add/remove, distinct-count, all_ready, should_fire-Latch,
//       Timeout-Grenzfall, Status-JSON).
//   (2) Quelltext-Guard: pipe_bridge.c muss ready_gate.h einbinden, `/ready`
//       im player_chat-Zweig erkennen und die Statusfelder in
//       handle_get_game_config ausliefern.
// Ist kein Host-CC vorhanden, wird (1) SICHTBAR uebersprungen (skip mit
// Begruendung) — niemals stillschweigend gruen.

const { test } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFileSync } = require("node:child_process");

const ROOT = path.join(__dirname, "..", "..");
const BRIDGE_DIR = path.join(ROOT, "server", "pipe-bridge");
const SRC = path.join(BRIDGE_DIR, "pipe_bridge.c");
const HARNESS = path.join(
  __dirname,
  "hosttest",
  "pipe_bridge_ready_gate_hosttest.c",
);
const FLOW_HARNESS = path.join(
  __dirname,
  "hosttest",
  "pipe_bridge_ready_gate_flow_hosttest.c",
);

function findHostCC() {
  for (const cc of ["cc", "gcc"]) {
    try {
      execFileSync("sh", ["-c", `command -v ${cc}`], { stdio: "ignore" });
      return cc;
    } catch (e) {
      /* nächster Kandidat */
    }
  }
  return null;
}

test("pipe-bridge host-test: Ready-Gate-Logik (ready_gate.h, #937)", (t) => {
  const cc = findHostCC();
  if (!cc) {
    t.skip(
      "kein Host-C-Compiler (cc/gcc) im PATH — Host-Harness nicht " +
        "kompilierbar; Test bleibt ungeprüft (nicht grün)",
    );
    return;
  }
  assert.ok(fs.existsSync(HARNESS), `Harness fehlt: ${HARNESS}`);

  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "rb937-hosttest-"));
  try {
    const bin = path.join(dir, "pipe_bridge_ready_gate_hosttest");
    execFileSync(
      cc,
      ["-O1", "-g", "-Wall", "-Wextra", "-I", BRIDGE_DIR, "-o", bin, HARNESS],
      { stdio: "pipe" },
    );

    const out = execFileSync(bin, [], { encoding: "utf8" });
    const m = out.match(/HOSTTEST_PASS=(\d+) HOSTTEST_FAIL=(\d+)/);
    assert.ok(m, `Harness-Ausgabe ohne Ergebniszeile:\n${out}`);
    assert.strictEqual(Number(m[2]), 0, `Harness-FAILs:\n${out}`);
    assert.ok(Number(m[1]) >= 15, `zu wenige Checks ausgeführt: ${m[1]}`);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

// #937 (Integration, host-seitig): Kern-Flow des Ready-Gates. Faehrt die
// Bridge-Aufrufsequenz (ready_gate_on_chat/ready_gate_tick/round-reset) ueber
// einen Mini-Treiber ueber die reine Logik nach: mehrere /ready mit
// unterschiedlichen conn_ids -> GENAU EIN Start (start_epoch++) bei
// ready_count==players; gleicher conn_id zweimal -> count bleibt 1;
// players=0 -> kein Start; Timeout exakt an der Deadline -> ready_timeout,
// kein Start; Runden-Reset -> Gate feuert erneut.
test("pipe-bridge host-test: Ready-Gate Kern-Flow (Integration, #937)", (t) => {
  const cc = findHostCC();
  if (!cc) {
    t.skip(
      "kein Host-C-Compiler (cc/gcc) im PATH — Flow-Harness nicht " +
        "kompilierbar; Test bleibt ungeprüft (nicht grün)",
    );
    return;
  }
  assert.ok(fs.existsSync(FLOW_HARNESS), `Harness fehlt: ${FLOW_HARNESS}`);

  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "rb937-flow-"));
  try {
    const bin = path.join(dir, "pipe_bridge_ready_gate_flow_hosttest");
    execFileSync(
      cc,
      [
        "-O1",
        "-g",
        "-Wall",
        "-Wextra",
        "-I",
        BRIDGE_DIR,
        "-o",
        bin,
        FLOW_HARNESS,
      ],
      { stdio: "pipe" },
    );

    const out = execFileSync(bin, [], { encoding: "utf8" });
    const m = out.match(/HOSTTEST_PASS=(\d+) HOSTTEST_FAIL=(\d+)/);
    assert.ok(m, `Harness-Ausgabe ohne Ergebniszeile:\n${out}`);
    assert.strictEqual(Number(m[2]), 0, `Harness-FAILs:\n${out}`);
    assert.ok(Number(m[1]) >= 30, `zu wenige Checks ausgeführt: ${m[1]}`);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

// Extrahiert den Funktionsrumpf (zwischen den balancierten Klammern) einer
// Funktionsdefinition, die mit `signature` beginnt.
function extractFunctionBody(src, signature) {
  const start = src.indexOf(signature);
  if (start === -1) return null;
  const brace = src.indexOf("{", start);
  if (brace === -1) return null;
  let depth = 0;
  for (let i = brace; i < src.length; i++) {
    if (src[i] === "{") depth++;
    else if (src[i] === "}") {
      depth--;
      if (depth === 0) return src.slice(brace, i + 1);
    }
  }
  return null;
}

test("pipe_bridge: /ready-Gate + Statusfelder verdrahtet (#937)", () => {
  assert.ok(fs.existsSync(SRC), `Quelle fehlt: ${SRC}`);
  const src = fs.readFileSync(SRC, "utf8");

  assert.ok(
    src.includes('#include "ready_gate.h"'),
    "pipe_bridge.c bindet ready_gate.h nicht ein",
  );

  // /ready-Erkennung im player_chat-Zweig von route_pipe_line.
  const route = extractFunctionBody(src, "static void route_pipe_line(HANDLE h, const char *line)");
  assert.ok(route, "route_pipe_line nicht gefunden");
  assert.ok(
    route.includes("player_chat") && route.includes("/ready"),
    "route_pipe_line erkennt /ready im player_chat-Event nicht",
  );

  // Statusfelder in handle_get_game_config.
  const cfg = extractFunctionBody(src, "static void handle_get_game_config(SOCKET c)");
  assert.ok(cfg, "handle_get_game_config nicht gefunden");
  assert.ok(
    cfg.includes("ready_count") && cfg.includes("ready_players"),
    "handle_get_game_config liefert ready_count/ready_players nicht",
  );
  assert.ok(
    cfg.includes("ready_gate_status_json"),
    "handle_get_game_config nutzt ready_gate_status_json nicht",
  );

  // #937/US3: "genau einmal PRO RUNDE" — der fired-Latch muss beim Runden-Reset
  // geloescht werden (Bridge-seitiges Runden-Reset-Signal: POST /attack_reset
  // bzw. /round_reset mit {"reset":1}).
  const attackReset = extractFunctionBody(
    src,
    "static void handle_post_attack_reset(SOCKET c, const char *body)",
  );
  assert.ok(attackReset, "handle_post_attack_reset nicht gefunden");
  assert.ok(
    attackReset.includes("ready_gate_reset("),
    "handle_post_attack_reset setzt den Gate-Runden-Latch nicht zurueck (US3)",
  );
  const roundReset = extractFunctionBody(
    src,
    "static void handle_post_round_reset(SOCKET c, const char *body)",
  );
  assert.ok(roundReset, "handle_post_round_reset nicht gefunden");
  assert.ok(
    roundReset.includes("ready_gate_reset("),
    "handle_post_round_reset setzt den Gate-Runden-Latch nicht zurueck (US3)",
  );
});

test("pipe_bridge: Server->Spieler-Status nutzt #934 send_chat (#937/US6)", () => {
  assert.ok(fs.existsSync(SRC), `Quelle fehlt: ${SRC}`);
  const src = fs.readFileSync(SRC, "utf8");

  // Kein NEUER Hook: der Status laeuft ueber das bestehende send_chat-Kommando.
  const body = extractFunctionBody(src, "static void bridge_send_chat_status(int players, int count, int timeout)");
  assert.ok(body, "bridge_send_chat_status nicht gefunden");
  assert.ok(
    body.includes('"cmd\\":\\"send_chat') || body.includes('send_chat'),
    "stellt nicht das send_chat-Kommando (#934) auf die Pipe",
  );
  assert.ok(
    !body.includes("pipe_send_command("),
    "darf im reader-Thread NICHT auf send_chat_result warten (Selbst-Deadlock) — fire-and-forget",
  );
  assert.ok(
    body.includes("json_escape("),
    "escaped den Text nicht ueber den bestehenden Builder (json_escape)",
  );

  // #937/US6 (Fix): NICHT g_cmd_cs nehmen! pipe_send_command haelt g_cmd_cs
  // ueber das gesamte WaitForSingleObject(g_resp_ev, timeout_ms); die Antwort
  // kann nur DIESER reader-Thread liefern -> nähme er g_cmd_cs, entstuende ein
  // Bounded-Deadlock bis RBB_BRIDGE_TIMEOUT_MS (Default 20 s). Nur g_pipe_cs
  // (Write-Serialisierung) ist erlaubt.
  assert.ok(
    !body.includes("EnterCriticalSection(&g_cmd_cs)"),
    "darf g_cmd_cs nicht nehmen (reader-Thread -> Bounded-Deadlock, US6)",
  );
  assert.ok(
    body.includes("EnterCriticalSection(&g_pipe_cs)"),
    "muss die Pipe-Writes weiterhin unter g_pipe_cs serialisieren",
  );

  // #937/US4 (Fix): RBB_READY_TIMEOUT_S oben begrenzen (env_int klemmt nur n>0).
  // Der Helfer steht in ready_gate.h; pipe_bridge.c muss ihn auch nutzen.
  const headerPath = path.join(BRIDGE_DIR, "ready_gate.h");
  assert.ok(fs.existsSync(headerPath), `Header fehlt: ${headerPath}`);
  const header = fs.readFileSync(headerPath, "utf8");
  assert.ok(
    src.includes("ready_timeout_cap("),
    "RBB_READY_TIMEOUT_S wird nicht ueber ready_timeout_cap() begrenzt (US4)",
  );
  assert.ok(
    header.includes("RBB_READY_TIMEOUT_MAX") &&
      header.includes("ready_timeout_cap"),
    "ready_timeout_cap/RBB_READY_TIMEOUT_MAX (sane Obergrenze) fehlt in ready_gate.h (US4)",
  );

  // Aufrufe an den Zeitpunkten: erstes ready UND alle ready UND timeout.
  assert.ok(
    src.includes("bridge_send_chat_status"),
    "Status wird nirgends ausgeloest",
  );
});
