// Host-Test fuer route_rules.h (Issue #843) — laeuft in der CI auf ubuntu-latest
// (g++ -std=c++17), ohne Wine/Windows/Spiel. Reine Logik, keine Dependencies.

#include "route_rules.h"

#include <cstdio>
#include <string>

using rbroute::Endpoint;
using rbroute::Rule;
using rbroute::Table;

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

static Endpoint ep(const std::string &spec) {
  Endpoint e;
  if (!rbroute::parseEndpoint(spec, e)) {
    std::fprintf(stderr, "FAIL: Testdaten unparsbar: %s\n", spec.c_str());
    ++g_failures;
  }
  return e;
}

static std::string matchStr(const Table &t, const std::string &key) {
  const Rule *r = t.match(key);
  return r == nullptr ? std::string("<null>") : r->target.str();
}

static void testParseEndpoint() {
  Endpoint e;
  check(rbroute::parseEndpoint("127.0.0.1:6324", e), "parse ok");
  checkEq(e.ip, "127.0.0.1", "parse ip");
  check(e.port == 6324, "parse port");
  checkEq(e.str(), "127.0.0.1:6324", "endpoint str");

  check(!rbroute::parseEndpoint("127.0.0.1", e), "ohne Port -> false");
  check(!rbroute::parseEndpoint("127.0.0.1:", e), "leerer Port -> false");
  check(!rbroute::parseEndpoint("127.0.0.1:0", e), "Port 0 -> false");
  check(!rbroute::parseEndpoint("127.0.0.1:65536", e), "Port > 65535 -> false");
  check(!rbroute::parseEndpoint("256.0.0.1:1234", e), "Oktett > 255 -> false");
  check(!rbroute::parseEndpoint("127.0.0:1234", e), "nur 3 Oktette -> false");
  check(!rbroute::parseEndpoint("127.0.0.1.5:1234", e), "5 Oktette -> false");
  check(!rbroute::parseEndpoint("127.0.0.x:1234", e), "kein Oktett -> false");
}

static void testIpToU32() {
  uint32_t v = 0;
  check(rbroute::ipToU32("0.0.0.0", v) && v == 0x00000000u, "ip 0.0.0.0");
  check(rbroute::ipToU32("1.2.3.4", v) && v == 0x01020304u, "ip 1.2.3.4");
  check(rbroute::ipToU32("255.255.255.255", v) && v == 0xFFFFFFFFu, "ip 255er");
  check(!rbroute::ipToU32("1.2.3", v), "ip mit 3 Oktetten -> false");
  check(!rbroute::ipToU32("1.2.3.256", v), "ip Oktett 256 -> false");
}

static Table sampleTable() {
  Table t;
  t.add("*-dev", ep("127.0.0.1:6324"));
  t.add("*-staging", ep("127.0.0.1:6323"));
  t.add("str:AB12", ep("127.0.0.1:6322"));
  t.add("*", ep("127.0.0.1:6322"));
  return t;
}

static void testSuffixRouting() {
  Table t = sampleTable();
  checkEq(matchStr(t, "momo-dev"), "127.0.0.1:6324", "namen-suffix -dev");
  checkEq(matchStr(t, "momo-staging"), "127.0.0.1:6323", "namen-suffix -staging");
  checkEq(matchStr(t, "momo"), "127.0.0.1:6322", "ohne Suffix -> Default");
  checkEq(matchStr(t, "staging"), "127.0.0.1:6322",
           "Suffix nur mit Bindestrich -> Default");
  checkEq(matchStr(t, "-dev"), "127.0.0.1:6324", "nur Suffix als Name trifft");
}

static void testPriorityExactBeatsSuffixAndDefault() {
  Table t = sampleTable();
  t.add("momo-dev", ep("127.0.0.1:9999"));
  checkEq(matchStr(t, "momo-dev"), "127.0.0.1:9999", "exakt schlaegt Suffix");
  checkEq(matchStr(t, "lisa-dev"), "127.0.0.1:6324", "Suffix bleibt fuer andere");
  checkEq(matchStr(t, "str:AB12"), "127.0.0.1:6322", "exakter Identitaets-Key");
}

static void testLongestSuffixWins() {
  Table t;
  t.add("*-dev", ep("127.0.0.1:6324"));
  t.add("*-beta-dev", ep("127.0.0.1:7000"));
  checkEq(matchStr(t, "momo-beta-dev"), "127.0.0.1:7000",
           "laengerer Suffix gewinnt");
  checkEq(matchStr(t, "momo-dev"), "127.0.0.1:6324", "kurzer Suffix sonst");
}

static void testSuffixBeatsDefault() {
  Table t;
  t.add("*", ep("127.0.0.1:6322"));
  t.add("*-staging", ep("127.0.0.1:6323"));
  checkEq(matchStr(t, "x-staging"), "127.0.0.1:6323", "Suffix schlaegt Default");
  checkEq(matchStr(t, "x"), "127.0.0.1:6322", "Default sonst");
}

static void testNoMatchWithoutDefault() {
  Table t;
  t.add("*-dev", ep("127.0.0.1:6324"));
  check(t.match("momo") == nullptr, "ohne Default -> nullptr");
  check(matchStr(t, "momo") == "<null>", "ohne Default -> <null>");
  check(t.defaultRule() == nullptr, "kein Default vorhanden");
  check(t.match("momo-dev")->target.port == 6324, "Suffix greift trotzdem");
}

static void testMatchSpecificIgnoresDefault() {
  Table t = sampleTable();
  check(t.matchSpecific("momo-dev") != nullptr, "spezifisch: Suffix");
  check(t.matchSpecific("str:AB12") != nullptr, "spezifisch: exakt");
  check(t.matchSpecific("irgendwer") == nullptr,
        "spezifisch: Default zaehlt nicht");
}

static void testDuplicateKeyLastWins() {
  Table t = sampleTable();
  check(t.size() == 4, "4 Regeln");
  t.add("*-dev", ep("127.0.0.1:8888"));
  check(t.size() == 4, "Doppel-Key erzeugt keine neue Regel");
  checkEq(matchStr(t, "momo-dev"), "127.0.0.1:8888", "letzte Zeile gewinnt");
}

static void testExtractTokens() {
  // BINSER-Handshake: Name als laengenpraefixierter Klartext (4d 6f 6d 6f).
  std::string payload = "BINSERv0014e002";
  payload.push_back('\0');
  payload += "\x04momo-staging";
  payload.push_back('\x01');
  payload += "EXE: 1186 DATA: 847";

  const std::vector<std::string> tokens = rbroute::extractTokens(payload);
  bool foundName = false;
  bool foundExe = false;
  for (const std::string &t : tokens) {
    if (t.find("momo-staging") != std::string::npos) {
      foundName = true;
    }
    if (t == "EXE: 1186 DATA: 847") {
      foundExe = true;
    }
  }
  check(foundName, "Token mit Spielernamen gefunden");
  check(foundExe, "Token mit EXE/DATA gefunden");

  // Zu kurze Laeufe werden verworfen (minLen = 3).
  check(rbroute::extractTokens(std::string("\x01" "ab" "\x02")).empty(),
        "zu kurzer Lauf verworfen");
  check(rbroute::extractTokens(std::string("\x01" "abc" "\x02")).size() == 1,
        "3 Zeichen wird genommen");
}

static void testTokenToRoute() {
  Table t = sampleTable();
  // Der Kernpfad des Relays: Tokens aus dem Payload -> spezifische Regel.
  const std::string payload = std::string("\x0c") + "momo-staging";
  std::string routed;
  for (const std::string &token : rbroute::extractTokens(payload)) {
    const Rule *r = t.matchSpecific(token);
    if (r != nullptr) {
      routed = r->target.str();
      break;
    }
  }
  checkEq(routed, "127.0.0.1:6323", "Payload-Token -> staging-Regel");
}

int main() {
  testParseEndpoint();
  testIpToU32();
  testSuffixRouting();
  testPriorityExactBeatsSuffixAndDefault();
  testLongestSuffixWins();
  testSuffixBeatsDefault();
  testNoMatchWithoutDefault();
  testMatchSpecificIgnoresDefault();
  testDuplicateKeyLastWins();
  testExtractTokens();
  testTokenToRoute();

  if (g_failures == 0) {
    std::printf("test_route_rules: %d Checks OK\n", g_checks);
    return 0;
  }
  std::fprintf(stderr, "test_route_rules: %d von %d Checks fehlgeschlagen\n",
               g_failures, g_checks);
  return 1;
}
