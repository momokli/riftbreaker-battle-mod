// Host-Test fuer api_util.h (Issue #857) — laeuft in der CI auf ubuntu-latest
// (g++ -std=c++17), ohne Wine/Windows/Spiel. Reine Logik, keine Dependencies.

#include "api_util.h"

#include <cstdio>
#include <string>

using rbapi::jsonEscape;
using rbapi::jsonStringField;
using rbapi::parseTargetSpec;
using rbroute::Endpoint;

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

static void testParseTargetSpec() {
  std::string name;
  Endpoint ep;
  check(parseTargetSpec("PROD=127.0.0.1:6322", name, ep), "PROD parsed");
  checkEq(name, "PROD", "Name");
  checkEq(ep.str(), "127.0.0.1:6322", "Endpoint");

  check(parseTargetSpec("DEV=127.0.0.1:6324", name, ep), "DEV parsed");
  checkEq(name, "DEV", "Name DEV");
  checkEq(ep.str(), "127.0.0.1:6324", "Endpoint DEV");

  // Der Endpoint darf selbst ein '=' nicht enthalten — rfind ist nicht noetig,
  // da der erste '=' Name und Ziel trennt.
  check(!parseTargetSpec("127.0.0.1:6322", name, ep), "ohne '=' -> false");
  check(!parseTargetSpec("=127.0.0.1:6322", name, ep), "leerer Name -> false");
  check(!parseTargetSpec("PROD=", name, ep), "leeres Ziel -> false");
  check(!parseTargetSpec("PROD=127.0.0.1", name, ep), "Ziel ohne Port -> false");
  check(!parseTargetSpec("PROD=127.0.0.1:0", name, ep), "Port 0 -> false");
}

static void testJsonStringField() {
  const std::string body =
      "{\"identitaet\":\"str:AB12\",\"target\":\"DEV\"}";
  std::string v;
  check(jsonStringField(body, "identitaet", v), "identitaet gefunden");
  checkEq(v, "str:AB12", "identitaet Wert");
  check(jsonStringField(body, "target", v), "target gefunden");
  checkEq(v, "DEV", "target Wert");

  // Whitespace / andere Reihenfolge.
  check(jsonStringField("{ \"target\" : \"PROD\" }", "target", v),
        "Whitespace tolerant");
  checkEq(v, "PROD", "Whitespace Wert");

  // Fehlende Felder / Nicht-String-Werte.
  check(!jsonStringField(body, "fehlt", v), "fehlendes Feld -> false");
  check(!jsonStringField("{\"target\":42}", "target", v),
        "Nicht-String -> false");
  check(!jsonStringField("", "target", v), "leerer Body -> false");

  // Escapes.
  check(jsonStringField("{\"k\":\"a\\\"b\"}", "k", v), "Quote-Escape");
  checkEq(v, "a\"b", "Quote-Escape Wert");
  check(jsonStringField("{\"k\":\"a\\\\b\"}", "k", v), "Backslash-Escape");
  checkEq(v, "a\\b", "Backslash-Escape Wert");
  check(jsonStringField("{\"k\":\"x\\u0041y\"}", "k", v), "\\u-Escape");
  checkEq(v, "xAy", "\\u-Escape Wert");
}

static void testJsonEscape() {
  checkEq(jsonEscape("plain"), "plain", "unveraendert");
  checkEq(jsonEscape("a\"b"), "a\\\"b", "Quote");
  checkEq(jsonEscape("a\\b"), "a\\\\b", "Backslash");
  checkEq(jsonEscape("line\nbreak"), "line\\nbreak", "Newline");
  checkEq(jsonEscape(std::string("tab\there")), "tab\\there", "Tab");
  // Identitaeten sind reines ASCII — bleiben unberuehrt.
  checkEq(jsonEscape("str:AB12"), "str:AB12", "Identitaet unberuehrt");
}

int main() {
  testParseTargetSpec();
  testJsonStringField();
  testJsonEscape();

  if (g_failures == 0) {
    std::printf("test_api_util: %d Checks OK\n", g_checks);
    return 0;
  }
  std::fprintf(stderr, "test_api_util: %d von %d Checks fehlgeschlagen\n",
               g_failures, g_checks);
  return 1;
}
