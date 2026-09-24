// Host-Test fuer api_util.h (Issue #857) — laeuft in der CI auf ubuntu-latest
// (g++ -std=c++17), ohne Wine/Windows/Spiel. Reine Logik, keine Dependencies.

#include "api_util.h"

#include <cstdio>
#include <string>

using rbapi::isTransientClaimStatus;
using rbapi::jsonEscape;
using rbapi::jsonStringField;
using rbapi::parseHttpResponse;
using rbapi::parseHttpStatusLine;
using rbapi::parseQueryParam;
using rbapi::parseTargetSpec;
using rbapi::parseUrlHostPort;
using rbapi::retryDelayMs;
using rbapi::shouldRetryClaim;
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

static void testParseQueryParam() {
  std::string v;
  check(parseQueryParam("name=PROD", "name", v), "name gefunden");
  checkEq(v, "PROD", "name Wert");
  check(parseQueryParam("a=1&name=X%2FY", "name", v), "zweiter Param");
  checkEq(v, "X/Y", "%2F dekodiert");
  check(parseQueryParam("name=a+b", "name", v), "plus");
  checkEq(v, "a b", "plus -> space");
  check(!parseQueryParam("other=1", "name", v), "fehlender Key -> false");
  check(!parseQueryParam("", "name", v), "leere Query -> false");
  checkEq(v, "a b", "Wert unveraendert bei Miss");
}

static void testParseUrlHostPort() {
  std::string host;
  int port = 0;
  check(parseUrlHostPort("http://127.0.0.1:8095", host, port), "url mit port");
  checkEq(host, "127.0.0.1", "host");
  check(port == 8095, "port 8095");
  check(parseUrlHostPort("http://127.0.0.1", host, port), "url ohne port");
  check(port == 80, "default port 80");
  check(parseUrlHostPort("https://example.com", host, port), "https default");
  check(port == 443, "default port 443");
  check(parseUrlHostPort("https://example.com:8443/x", host, port), "mit pfad");
  checkEq(host, "example.com", "host mit pfad");
  check(port == 8443, "port mit pfad");
  check(!parseUrlHostPort("", host, port), "leer -> false");
  check(!parseUrlHostPort("http://host:0", host, port), "port 0 -> false");
  check(!parseUrlHostPort("http://host:abc", host, port), "port nicht numerisch");
}

static void testParseHttpResponse() {
  int code = 0;
  check(parseHttpStatusLine("HTTP/1.1 200 OK", code), "statuszeile ok");
  check(code == 200, "code 200");
  check(parseHttpStatusLine("HTTP/1.0 409 Conflict", code), "409");
  check(code == 409, "code 409");
  check(!parseHttpStatusLine("NOT HTTP", code), "keine statuszeile");
  check(!parseHttpStatusLine("HTTP/1.1 OK", code), "kein code");

  std::string body;
  check(parseHttpResponse("HTTP/1.1 200 OK\r\nX: 1\r\n\r\n{\"ok\":true}", code, body),
        "antwort parsen");
  check(code == 200, "antwort code");
  checkEq(body, "{\"ok\":true}", "antwort body");
  // Ohne Trenner: Body leer, Status trotzdem da.
  check(parseHttpResponse("HTTP/1.1 503 Busy", code, body), "ohne body");
  check(code == 503, "ohne body code");
  checkEq(body, "", "ohne body leer");
  check(!parseHttpResponse("kaputt", code, body), "muell -> false");
}

static void testRetryDecision() {
  check(isTransientClaimStatus(-1, ""), "connect-fehler transient");
  check(isTransientClaimStatus(503, "bridge_unhealthy"), "503 transient");
  check(isTransientClaimStatus(409, "none_parked"), "409 none_parked transient");
  check(!isTransientClaimStatus(409, "not_claimable"), "409 not_claimable endgueltig");
  check(!isTransientClaimStatus(401, "unauthorized"), "401 endgueltig");
  check(!isTransientClaimStatus(200, ""), "200 kein Fehler");

  // Budget: 4 Versuche, danach kein Retry mehr.
  check(shouldRetryClaim(1, 4, -1, ""), "versuch 1 -> retry");
  check(shouldRetryClaim(3, 4, 503, "bridge_unhealthy"), "versuch 3 -> retry");
  check(!shouldRetryClaim(4, 4, 503, "bridge_unhealthy"), "erschoepft -> kein retry");
  check(!shouldRetryClaim(1, 4, 409, "not_claimable"), "endgueltig -> kein retry");

  check(retryDelayMs(1) == 250, "delay 1");
  check(retryDelayMs(2) == 500, "delay 2");
  check(retryDelayMs(3) == 1000, "delay 3");
  check(retryDelayMs(9) == 1000, "delay cap");
  // Gesamtbudget der Wartephasen < 5 s.
  check(retryDelayMs(1) + retryDelayMs(2) + retryDelayMs(3) < 5000, "budget < 5s");
}

int main() {
  testParseTargetSpec();
  testJsonStringField();
  testJsonEscape();
  testParseQueryParam();
  testParseUrlHostPort();
  testParseHttpResponse();
  testRetryDecision();

  if (g_failures == 0) {
    std::printf("test_api_util: %d Checks OK\n", g_checks);
    return 0;
  }
  std::fprintf(stderr, "test_api_util: %d von %d Checks fehlgeschlagen\n",
               g_failures, g_checks);
  return 1;
}
