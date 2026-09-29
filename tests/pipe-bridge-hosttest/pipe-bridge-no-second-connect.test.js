"use strict";

// Regressions-Guard für Issue #902 (Lauf 3): handle_health() darf die
// Pipe-Readiness NICHT mehr über einen frischen Connect (pipe_connect)
// ermitteln. rbbridge.dll erzeugt die Pipe mit nMaxInstances=1; der
// persistente pipe_reader-Thread hält die einzige Instanz. Jeder zweite
// Connect träfe ERROR_PIPE_BUSY und meldete die Pipe fälschlich als tot
// (503 {"ok":false,"pipe":false}) — obwohl sie gesund ist.
//
// Der Test liest server/pipe-bridge/pipe_bridge.c, extrahiert den Rumpf von
// handle_health() und asserted, dass er KEIN `pipe_connect(` enthält und
// stattdessen `pipe_ready(` verwendet. Reiner Quelltext-Check, kein CC nötig.

const { test } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const SRC = path.join(
  __dirname,
  "..",
  "..",
  "server",
  "pipe-bridge",
  "pipe_bridge.c",
);

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

test("pipe_bridge handle_health: kein Zweit-Connect (#902)", () => {
  assert.ok(fs.existsSync(SRC), `Quelle fehlt: ${SRC}`);
  const src = fs.readFileSync(SRC, "utf8");

  const body = extractFunctionBody(src, "static void handle_health(SOCKET c, int deep)");
  assert.ok(body, "handle_health(SOCKET c, int deep) nicht gefunden");

  // Kern der Regression: handle_health darf keinen frischen Connect öffnen.
  assert.ok(
    !body.includes("pipe_connect("),
    "handle_health() enthält pipe_connect( — darf keinen Zweit-Connect öffnen (#902)",
  );

  // Readiness muss aus der persistenten Verbindung abgeleitet werden.
  assert.ok(
    body.includes("pipe_ready("),
    "handle_health() nutzt nicht pipe_ready() für die Readiness (#902)",
  );

  // Deep-Ping läuft über die bestehende Verbindung.
  assert.ok(
    body.includes("pipe_send_command("),
    "handle_health() nutzt für den Deep-Ping nicht pipe_send_command() (#902)",
  );
});

test("pipe_ready liest g_pipe unter g_pipe_cs (#902)", () => {
  const src = fs.readFileSync(SRC, "utf8");
  const body = extractFunctionBody(src, "static int pipe_ready(void)");
  assert.ok(body, "pipe_ready(void) nicht gefunden");
  assert.ok(body.includes("g_pipe"), "pipe_ready() liest g_pipe nicht");
  assert.ok(
    body.includes("EnterCriticalSection(&g_pipe_cs)"),
    "pipe_ready() liest g_pipe nicht unter g_pipe_cs",
  );
  assert.ok(
    body.includes("INVALID_HANDLE_VALUE"),
    "pipe_ready() prüft nicht auf INVALID_HANDLE_VALUE",
  );
});
