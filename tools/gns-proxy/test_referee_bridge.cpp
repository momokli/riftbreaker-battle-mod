// Host-Test fuer referee_bridge.h (Issue #1024) — laeuft in der CI auf
// ubuntu-latest (g++ -std=c++17), ohne Wine/Windows/Spiel. Reine Logik.
//
// Deckt die socket-freie Bruecken-Logik des GNS-Relays zum Referee ab:
// Config-Parse (URL -> IPv4:port), Ready-Body-Bau, robustes Lesen der
// /state-Antwort (phase/winner/teams.*.player/ready) und das Fehler-Mapping
// (503 referee_unconfigured | 502 referee_unreachable | ok | backend_status).

#include "referee_bridge.h"

#include <cstdio>
#include <string>
#include <vector>

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

// ---------------------------------------------------------------------------
// Story 1: Config-Parse (URL -> host/port, nur IPv4-Literal)
// ---------------------------------------------------------------------------

static void testParseConfigValid() {
  std::string host;
  int port = 0;
  check(rbref::parseRefereeConfig("http://127.0.0.1:8082", host, port),
        "gueltige URL parst");
  checkEq(host, "127.0.0.1", "host");
  check(port == 8082, "port 8082");

  check(rbref::parseRefereeConfig("http://127.0.0.1:8082/state", host, port),
        "Pfad hinter host wird ignoriert");
  checkEq(host, "127.0.0.1", "host (mit Pfad)");
  check(port == 8082, "port (mit Pfad)");

  // https Default-Port zaehlt weiterhin als gueltiges Literal.
  check(rbref::parseRefereeConfig("https://10.0.0.5:9000", host, port),
        "https mit Port parst");
  checkEq(host, "10.0.0.5", "host https");
  check(port == 9000, "port https");
}

static void testParseConfigInvalid() {
  std::string host;
  int port = 0;
  check(!rbref::parseRefereeConfig("http://localhost:8082", host, port),
        "Hostname ist kein IPv4-Literal");
  // Tolerant wie der bestehende Parked-/Queue-Parser: fehlender Port = Default-
  // Port des Schemas (80/443), fehlendes Schema wird als http angenommen.
  check(rbref::parseRefereeConfig("http://127.0.0.1", host, port),
        "fehlender Port -> Default 80");
  checkEq(host, "127.0.0.1", "host ohne Port");
  check(port == 80, "Default-Port 80");
  check(rbref::parseRefereeConfig("127.0.0.1:8082", host, port),
        "ohne Schema -> wie http (Parser-Konvention)");
  check(port == 8082, "ohne Schema -> Port 8082");
  check(!rbref::parseRefereeConfig("", host, port), "leer ist ungueltig");
  check(!rbref::parseRefereeConfig("http://999.1.1.1:8082", host, port),
        "Oktett >255 ist ungueltig");
  check(!rbref::parseRefereeConfig("http://127.0.0.1:0", host, port),
        "Port 0 ist ungueltig");
}

// ---------------------------------------------------------------------------
// Story 2: Ready-Body
// ---------------------------------------------------------------------------

static void testBuildReadyBody() {
  checkEq(rbref::buildReadyBody("A"), "{\"world\":\"A\"}", "ready A");
  checkEq(rbref::buildReadyBody("B"), "{\"world\":\"B\"}", "ready B");
  // Identitaet additiv, wenn uebergeben.
  checkEq(rbref::buildReadyBody("A", "str:AB12"),
          "{\"world\":\"A\",\"identitaet\":\"str:AB12\"}", "ready mit ID");
  // Escaping: Anfuehrungszeichen/Backslash im Freitext.
  checkEq(rbref::buildReadyBody("A", "x\"y\\z"),
          "{\"world\":\"A\",\"identitaet\":\"x\\\"y\\\\z\"}", "ready escaping");
}

// ---------------------------------------------------------------------------
// Story 3: /state parsen (robust, fehlende Felder = leer, kein Crash)
// ---------------------------------------------------------------------------

static const char *kFullState =
    "{\"match_id\":\"rift-1\",\"mode\":\"duel\",\"phase\":\"Ready\","
    "\"round\":0,\"winner\":null,\"paused\":false,"
    "\"teams\":{\"A\":{\"player\":\"momo-a\",\"ready\":true,\"hq_hp\":100.0,"
    "\"resources\":{\"carbonium\":10}},\"B\":{\"player\":\"momo-b\","
    "\"ready\":false,\"hq_hp\":100.0,\"resources\":{}}},"
    "\"feed\":[{\"kind\":\"go\",\"msg\":\"go\"}]}";

static void testParseStateFull() {
  std::string phase;
  std::string winner;
  std::vector<std::string> players;
  bool bothReady = true;
  check(rbref::parseRefereeState(kFullState, phase, winner, players, bothReady),
        "vollstaendiger State parst");
  checkEq(phase, "Ready", "phase");
  checkEq(winner, "", "winner null -> leer");
  check(players.size() == 2, "zwei Spieler");
  checkEq(players.size() > 0 ? players[0] : std::string(), "momo-a",
          "Spieler A");
  checkEq(players.size() > 1 ? players[1] : std::string(), "momo-b",
          "Spieler B");
  check(!bothReady, "A ready, B nicht -> both_ready=false");
}

static void testParseStateBothReady() {
  const char *body =
      "{\"phase\":\"Ready\",\"winner\":\"A\",\"teams\":{\"A\":{\"player\":\"p1\","
      "\"ready\":true},\"B\":{\"player\":\"p2\",\"ready\":true}}}";
  std::string phase;
  std::string winner;
  std::vector<std::string> players;
  bool bothReady = false;
  check(rbref::parseRefereeState(body, phase, winner, players, bothReady),
        "Ready-State parst");
  checkEq(winner, "A", "winner A");
  check(bothReady, "beide ready -> both_ready=true");
}

static void testParseStateMissing() {
  std::string phase = "alt";
  std::string winner = "alt";
  std::vector<std::string> players;
  bool bothReady = true;
  // Kaputter/leerer Body darf nicht abstuerzen und leert die Ausgaben.
  check(!rbref::parseRefereeState("", phase, winner, players, bothReady),
        "leerer Body -> false");
  checkEq(phase, "", "leerer Body -> phase leer");
  checkEq(winner, "", "leerer Body -> winner leer");
  check(players.empty(), "leerer Body -> keine Spieler");
  check(!bothReady, "leerer Body -> both_ready=false");

  // Nur phase, keine teams.
  std::string p2;
  std::vector<std::string> pl2;
  bool br2 = true;
  check(rbref::parseRefereeState("{\"phase\":\"Lobby\"}", p2, winner, pl2, br2),
        "nur phase parst");
  checkEq(p2, "Lobby", "nur phase");
  check(pl2.empty(), "ohne teams -> keine Spieler");
  check(!br2, "ohne teams -> both_ready=false");

  // Nicht-JSON-Muell: kein Crash.
  std::string p3;
  std::vector<std::string> pl3;
  bool br3 = true;
  check(!rbref::parseRefereeState("<<<not json>>>", p3, winner, pl3, br3),
        "Muell -> false, kein Crash");
}

// ---------------------------------------------------------------------------
// Story 4: Fehler-Mapping
// ---------------------------------------------------------------------------

static void testMapError() {
  check(rbref::mapRefereeError(false, 0) == rbref::RefStatus::Unconfigured,
        "nicht konfiguriert -> unconfigured (503)");
  check(rbref::refStatusHttp(rbref::mapRefereeError(false, 0)) == 503,
        "unconfigured -> 503");
  checkEq(rbref::refStatusName(rbref::mapRefereeError(false, 0)),
          "referee_unconfigured", "unconfigured name");

  check(rbref::mapRefereeError(true, -1) == rbref::RefStatus::Unreachable,
        "status<=0 -> unreachable (502)");
  check(rbref::mapRefereeError(true, 0) == rbref::RefStatus::Unreachable,
        "status 0 -> unreachable");
  check(rbref::refStatusHttp(rbref::mapRefereeError(true, -1)) == 502,
        "unreachable -> 502");
  checkEq(rbref::refStatusName(rbref::mapRefereeError(true, -1)),
          "referee_unreachable", "unreachable name");

  check(rbref::mapRefereeError(true, 200) == rbref::RefStatus::Ok,
        "200 -> ok");
  check(rbref::refStatusHttp(rbref::mapRefereeError(true, 200)) == 200,
        "ok -> 200");

  check(rbref::mapRefereeError(true, 401) == rbref::RefStatus::BackendStatus,
        "401 -> backend_status");
  check(rbref::mapRefereeError(true, 409) == rbref::RefStatus::BackendStatus,
        "409 -> backend_status");
  check(rbref::mapRefereeError(true, 500) == rbref::RefStatus::BackendStatus,
        "500 -> backend_status");
}

// ---------------------------------------------------------------------------
// Story 5 (#1025): kontextabhaengige Ready-Route (pure Entscheidung)
// ---------------------------------------------------------------------------

static void testResolveReadyRoute() {
  // Solo: keine Welt / leere Welt -> Capsule (bit-identisch zu vorher).
  check(rbref::resolveReadyRoute(false, "") == rbref::ReadyRoute::Capsule,
        "keine Welt -> Capsule");
  check(rbref::resolveReadyRoute(false, "A") == rbref::ReadyRoute::Capsule,
        "hasWorld=false -> Capsule (auch mit Wert)");
  check(rbref::resolveReadyRoute(true, "") == rbref::ReadyRoute::Capsule,
        "leere Welt -> Capsule");

  // VS: aufgeloeste Welt A/B -> Referee.
  check(rbref::resolveReadyRoute(true, "A") == rbref::ReadyRoute::Referee,
        "Welt A -> Referee");
  check(rbref::resolveReadyRoute(true, "B") == rbref::ReadyRoute::Referee,
        "Welt B -> Referee");

  // Ungueltige, nicht-leere Welt -> BadWorld (400).
  check(rbref::resolveReadyRoute(true, "Z") == rbref::ReadyRoute::BadWorld,
        "unbekannte Welt -> BadWorld");
  check(rbref::resolveReadyRoute(true, "a") == rbref::ReadyRoute::BadWorld,
        "klein geschrieben -> BadWorld (case-sensitiv)");
  checkEq(rbref::readyRouteName(rbref::ReadyRoute::Capsule), "capsule",
          "route name capsule");
  checkEq(rbref::readyRouteName(rbref::ReadyRoute::Referee), "referee",
          "route name referee");
  checkEq(rbref::readyRouteName(rbref::ReadyRoute::BadWorld), "bad_world",
          "route name bad_world");
}

int main() {
  testParseConfigValid();
  testParseConfigInvalid();
  testBuildReadyBody();
  testParseStateFull();
  testParseStateBothReady();
  testParseStateMissing();
  testMapError();
  testResolveReadyRoute();

  std::printf("%d/%d ok\n", g_checks - g_failures, g_checks);
  if (g_failures != 0) {
    std::fprintf(stderr, "%d FAILURES\n", g_failures);
    return 1;
  }
  return 0;
}
