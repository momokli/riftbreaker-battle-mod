// Host-Test fuer mode_gate.h (Issue #1093) — laeuft wie die uebrigen
// gns-proxy-Host-Tests mit `g++ -std=c++17`, ohne Wine/Windows/Spiel. Reine
// Logik, keine Dependencies.
//
//   g++ -std=c++17 -Wall -Wextra -O1 -o /tmp/test_mode_gate \
//       tools/gns-proxy/test_mode_gate.cpp && /tmp/test_mode_gate

#include "mode_gate.h"

#include <cstdio>
#include <string>

using rbmode::Mode;

static int g_failures = 0;
static int g_checks = 0;

static void check(bool cond, const char *what) {
  ++g_checks;
  if (!cond) {
    ++g_failures;
    std::fprintf(stderr, "FAIL: %s\n", what);
  }
}

static void checkEq(const std::string &got, const std::string &want,
                    const char *what) {
  ++g_checks;
  if (got != want) {
    ++g_failures;
    std::fprintf(stderr, "FAIL: %s (got '%s', want '%s')\n", what, got.c_str(),
                 want.c_str());
  }
}

static std::string modeOf(const std::string &raw) {
  Mode m = Mode::Both;
  if (!rbmode::parseMode(raw, m)) {
    return "<invalid>";
  }
  return rbmode::modeName(m);
}

static void testParseMode() {
  // Externe Produkt-Schreibweise + Sidecar-Schreibweise.
  checkEq(modeOf("solo"), "solo", "solo");
  checkEq(modeOf("versus"), "versus", "versus");
  checkEq(modeOf("vs"), "versus", "vs -> versus (Sidecar-Mapping)");
  checkEq(modeOf("VERSUS"), "versus", "case-insensitiv");
  checkEq(modeOf("  Solo\t"), "solo", "Whitespace getrimmt");
  // Default-Schreibweisen.
  checkEq(modeOf(""), "both", "leer -> both");
  checkEq(modeOf("both"), "both", "both");
  checkEq(modeOf("all"), "both", "all -> both");
  // Unbekannt -> fail-loud.
  checkEq(modeOf("solo vs"), "<invalid>", "zwei Woerter -> invalid");
  checkEq(modeOf("1v1"), "<invalid>", "unbekannt -> invalid");
  checkEq(modeOf("verus"), "<invalid>", "Tippfehler -> invalid");
}

static void testModeName() {
  checkEq(rbmode::modeName(Mode::Both), "both", "name both");
  checkEq(rbmode::modeName(Mode::Solo), "solo", "name solo");
  checkEq(rbmode::modeName(Mode::Versus), "versus", "name versus");
}

static void testRouteClassification() {
  check(rbmode::isSoloRoute("POST", "/solo"), "POST /solo ist Solo-Route");
  check(!rbmode::isSoloRoute("GET", "/solo"), "GET /solo ist keine Solo-Route");
  check(rbmode::isVersusRoute("POST", "/queue"),
        "POST /queue ist Versus-Route");
  check(rbmode::isVersusRoute("POST", "/queue/leave"),
        "/queue/leave ist Versus");
  check(rbmode::isVersusRoute("POST", "/queue/finish"),
        "/queue/finish ist Versus");
  check(rbmode::isVersusRoute("POST", "/queue/rematch"),
        "/queue/rematch ist Versus");
  check(rbmode::isVersusRoute("GET", "/queue/status"),
        "/queue/status ist Versus");
  check(!rbmode::isVersusRoute("POST", "/queuefoo"), "kein Prefix-Fehltreffer");
  check(!rbmode::isVersusRoute("POST", "/route"),
        "/route ist keine Versus-Route");
}

static void testRouteAllowedBoth() {
  // Default: alles erlaubt (heutiges Verhalten, keine Regression).
  check(rbmode::routeAllowedInMode(Mode::Both, "POST", "/solo"), "both: /solo");
  check(rbmode::routeAllowedInMode(Mode::Both, "POST", "/queue"),
        "both: /queue");
  check(rbmode::routeAllowedInMode(Mode::Both, "GET", "/queue/status"),
        "both: /queue/status");
  check(rbmode::routeAllowedInMode(Mode::Both, "POST", "/route"),
        "both: /route");
  check(rbmode::routeAllowedInMode(Mode::Both, "POST", "/ready"),
        "both: /ready");
}

static void testRouteAllowedSolo() {
  // Solo: Solo-Pfad frei, Versus-Pfad 4xx.
  check(rbmode::routeAllowedInMode(Mode::Solo, "POST", "/solo"),
        "solo: /solo frei");
  check(!rbmode::routeAllowedInMode(Mode::Solo, "POST", "/queue"),
        "solo: POST /queue gesperrt");
  check(!rbmode::routeAllowedInMode(Mode::Solo, "POST", "/queue/leave"),
        "solo: /queue/leave gesperrt");
  check(!rbmode::routeAllowedInMode(Mode::Solo, "GET", "/queue/status"),
        "solo: /queue/status gesperrt");
  // Geteilte Routen bleiben frei.
  check(rbmode::routeAllowedInMode(Mode::Solo, "POST", "/route"),
        "solo: /route frei");
  check(rbmode::routeAllowedInMode(Mode::Solo, "POST", "/ready"),
        "solo: /ready frei");
  check(rbmode::routeAllowedInMode(Mode::Solo, "GET", "/sessions"),
        "solo: /sessions frei");
}

static void testRouteAllowedVersus() {
  // Versus: Queue-Pfad frei, Solo-Pfad 4xx.
  check(rbmode::routeAllowedInMode(Mode::Versus, "POST", "/queue"),
        "versus: /queue frei");
  check(rbmode::routeAllowedInMode(Mode::Versus, "POST", "/queue/rematch"),
        "versus: /queue/rematch frei");
  check(!rbmode::routeAllowedInMode(Mode::Versus, "POST", "/solo"),
        "versus: POST /solo gesperrt");
  check(rbmode::routeAllowedInMode(Mode::Versus, "POST", "/route"),
        "versus: /route frei");
}

int main() {
  testParseMode();
  testModeName();
  testRouteClassification();
  testRouteAllowedBoth();
  testRouteAllowedSolo();
  testRouteAllowedVersus();

  if (g_failures == 0) {
    std::printf("test_mode_gate: %d Checks OK\n", g_checks);
    return 0;
  }
  std::fprintf(stderr, "test_mode_gate: %d von %d Checks fehlgeschlagen\n",
               g_failures, g_checks);
  return 1;
}
