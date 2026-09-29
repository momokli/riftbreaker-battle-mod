"use strict";

// Host-Test fuer den Ingress-Endpoint POST /incoming_send von pipe_bridge
// (Issue #996, US3/G5):
//   (1) kompiliert die Windows-freie Logik (incoming_send.h) mit dem
//       HOST-Compiler (cc, dann gcc — NIE mingw) und fuehrt den Harness aus
//       (pure Logik: level-Validierung >= 1, from/delay_s-Extraktion, Bau des
//       Ziel-Kommandos `incoming_wave {level,from,delay_s}`, Escaping,
//       Puffer-Grenzfall).
//   (2) Quelltext-Guard: pipe_bridge.c muss incoming_send.h einbinden, die
//       Route /incoming_send registrieren und den Handler definieren.
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
const HEADER = path.join(BRIDGE_DIR, "incoming_send.h");
const HARNESS = path.join(
  __dirname,
  "hosttest",
  "pipe_bridge_incoming_send_hosttest.c",
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

test("pipe-bridge host-test: /incoming_send-Ingress-Logik (incoming_send.h, #996)", (t) => {
  const cc = findHostCC();
  if (!cc) {
    t.skip(
      "kein Host-C-Compiler (cc/gcc) im PATH — Host-Harness nicht " +
        "kompilierbar; Test bleibt ungeprüft (nicht grün)",
    );
    return;
  }
  assert.ok(fs.existsSync(HARNESS), `Harness fehlt: ${HARNESS}`);
  assert.ok(fs.existsSync(HEADER), `Header fehlt: ${HEADER}`);

  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "rb996-hosttest-"));
  try {
    const bin = path.join(dir, "pipe_bridge_incoming_send_hosttest");
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

test("pipe_bridge: /incoming_send-Endpoint verdrahtet (#996/US3)", () => {
  assert.ok(fs.existsSync(SRC), `Quelle fehlt: ${SRC}`);
  const src = fs.readFileSync(SRC, "utf8");

  assert.ok(
    src.includes('#include "incoming_send.h"'),
    "pipe_bridge.c bindet incoming_send.h nicht ein",
  );

  // Route-Registrierung im HTTP-Router.
  assert.ok(
    src.includes('strcmp(path, "/incoming_send") == 0'),
    "pipe_bridge.c registriert die Route /incoming_send nicht",
  );

  // Handler + Nutzung der host-testbaren Logik.
  const handler = extractFunctionBody(
    src,
    "static void handle_incoming_send(SOCKET c, const char *body)",
  );
  assert.ok(handler, "handle_incoming_send nicht gefunden");
  assert.ok(
    handler.includes("incoming_send_parse(") &&
      handler.includes("incoming_send_pipe_line("),
    "handle_incoming_send nutzt die host-testbare Parse/Build-Logik nicht",
  );
  assert.ok(
    handler.includes("invalid_request"),
    "handle_incoming_send meldet ungueltiges level nicht als invalid_request",
  );
  assert.ok(
    handler.includes("pipe_unavailable"),
    "handle_incoming_send meldet fehlende Pipe nicht als pipe_unavailable",
  );
  assert.ok(
    handler.includes('pipe_send_command("incoming_wave_result"'),
    "handle_incoming_send dispatcht nicht auf den Wave-Kanal",
  );
});
