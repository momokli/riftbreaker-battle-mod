"use strict";

// Host-Test für die /health-Logik von pipe_bridge (Issue #902, US2):
// Kompiliert die Windows-freie Header-Logik (health_logic.h) mit dem
// HOST-Compiler (cc, dann gcc — NIE mingw, das erzeugt Windows-Binaries)
// und führt den Harness aus. Beweist Abnahme (a): "Pipe künstlich trennen"
// (pipe_ok=0) -> non-2xx/p pipe:false, ohne Windows/Spiel/Wine/Netz.
// Ist kein Host-CC vorhanden, wird der Test SICHTBAR übersprungen
// (skip mit Begründung) — niemals stillschweigend grün.

const { test } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFileSync } = require("node:child_process");

const ROOT = path.join(__dirname, "..", "..");
const BRIDGE_DIR = path.join(ROOT, "server", "pipe-bridge");
const HARNESS = path.join(
  __dirname,
  "hosttest",
  "pipe_bridge_health_hosttest.c",
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

test("pipe-bridge host-test: /health-Statuscode + Body (health_logic.h)", (t) => {
  const cc = findHostCC();
  if (!cc) {
    t.skip(
      "kein Host-C-Compiler (cc/gcc) im PATH — Host-Harness nicht " +
        "kompilierbar; Test bleibt ungeprüft (nicht grün)",
    );
    return;
  }
  assert.ok(fs.existsSync(HARNESS), `Harness fehlt: ${HARNESS}`);

  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "rb902-hosttest-"));
  try {
    const bin = path.join(dir, "pipe_bridge_health_hosttest");
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
