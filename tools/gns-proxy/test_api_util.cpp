// Host-Test fuer api_util.h (Issue #857) — laeuft in der CI auf ubuntu-latest
// (g++ -std=c++17), ohne Wine/Windows/Spiel. Reine Logik, keine Dependencies.

#include "api_util.h"

#include <cstdio>
#include <string>
#include <vector>

using rbapi::HttpResp;
using rbapi::isTransientClaimStatus;
using rbapi::jsonEscape;
using rbapi::jsonStringField;
using rbapi::parseHttpResponse;
using rbapi::parseHttpStatusLine;
using rbapi::parseQueryParam;
using rbapi::parseTargetSpec;
using rbapi::parseUrlHostPort;
using rbapi::retryDelayMs;
using rbapi::runSoloClaim;
using rbapi::shouldRetryClaim;
using rbapi::SoloOutcome;
using rbapi::urlDecode;
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

static void testUrlDecode() {
  checkEq(urlDecode("plain"), "plain", "urlDecode plain");
  checkEq(urlDecode("a%2Fb"), "a/b", "%2F");
  checkEq(urlDecode("a+b"), "a b", "+ -> space");
  checkEq(urlDecode("%41%42"), "AB", "hex");
  // Ungueltige Prozentfolge bleibt tolerant stehen (kein Crash).
  checkEq(urlDecode("%zz"), "%zz", "ungueltig bleibt");
  checkEq(urlDecode("trailing%"), "trailing%", "einsames %");
}

// Fake-Parked-Dienst: liefert eine vorgegebene (status, body)-Folge und
// protokolliert Aufrufe + Body. Ersetzt den echten WinSock-Outbound-POST.
struct FakeClaim {
  std::vector<HttpResp> responses;
  std::size_t idx = 0;
  int calls = 0;
  std::vector<std::string> payloads;
  HttpResp operator()(const std::string &, const std::string &payload) {
    ++calls;
    payloads.push_back(payload);
    if (idx < responses.size()) {
      return responses[idx++];
    }
    return HttpResp{-1, ""};  // unerwarteter Extra-Aufruf -> Verbindungsfehler
  }
};

static std::vector<int> g_sleeps;
static void collectSleep(int ms) { g_sleeps.push_back(ms); }

static SoloOutcome runSolo(const std::string &env, FakeClaim &claim,
                           int maxAttempts = 4) {
  g_sleeps.clear();
  return runSoloClaim(env, std::ref(claim), maxAttempts, collectSleep);
}

static void testSoloSuccess() {
  FakeClaim claim;
  claim.responses.push_back(
      HttpResp{200, "{\"ok\":true,\"instance\":\"parked-1\","
                    "\"gns_endpoint\":\"127.0.0.1:32768\"}"});
  SoloOutcome out = runSolo("", claim);
  check(out.httpCode == 200, "solo ok -> 200");
  check(out.pinIdentity, "solo ok -> pin");
  checkEq(out.endpoint, "127.0.0.1:32768", "solo ziel");
  checkEq(out.instance, "parked-1", "solo instanz");
  check(claim.calls == 1, "solo ok -> 1 claim");
  check(g_sleeps.empty(), "solo ok -> kein sleep");
  checkEq(claim.payloads[0], "{}", "leerer env -> {} Body");
}

static void testSoloEnvPayload() {
  FakeClaim claim;
  claim.responses.push_back(
      HttpResp{200, "{\"gns_endpoint\":\"127.0.0.1:32100\"}"});
  SoloOutcome out = runSolo("staging", claim);
  check(out.pinIdentity, "env-Lauf pinnt");
  checkEq(claim.payloads[0], "{\"env\":\"staging\"}", "env im Body");
  checkEq(out.instance, "", "ohne instance-Feld leer");
}

static void testSoloRetryThenSuccess() {
  FakeClaim claim;
  claim.responses.push_back(HttpResp{503, "{\"reason\":\"bridge_unhealthy\"}"});
  claim.responses.push_back(HttpResp{503, "{\"reason\":\"bridge_unhealthy\"}"});
  claim.responses.push_back(
      HttpResp{200, "{\"instance\":\"p\",\"gns_endpoint\":\"127.0.0.1:40000\"}"});
  SoloOutcome out = runSolo("", claim);
  check(out.pinIdentity, "retry->erfolg pinnt");
  checkEq(out.endpoint, "127.0.0.1:40000", "retry ziel");
  check(claim.calls == 3, "retry -> 3 claims");
  check(g_sleeps.size() == 2, "retry -> 2 sleeps");
  check(g_sleeps[0] == 250 && g_sleeps[1] == 500, "backoff 250/500");
}

static void testSoloExhaustion() {
  // 409 none_parked bleibt transient -> alle 4 Versuche, dann 503 retry:true.
  FakeClaim claim;
  for (int i = 0; i < 4; ++i) {
    claim.responses.push_back(HttpResp{409, "{\"reason\":\"none_parked\"}"});
  }
  SoloOutcome out = runSolo("", claim);
  check(out.httpCode == 503, "erschoepft -> 503");
  check(!out.pinIdentity, "erschoepft -> kein pin");
  checkEq(out.reason, "backend_starting", "erschoepft reason");
  check(out.body.find("\"retry\":true") != std::string::npos,
        "erschoepft retry:true");
  check(claim.calls == 4, "erschoepft -> 4 claims");
  check(g_sleeps.size() == 3, "erschoepft -> 3 sleeps");
  check(g_sleeps[2] == 1000, "letzter backoff 1000");
}

static void testSoloConnectionErrorExhaustion() {
  // -1 (Connect-/Timeout-Fehler) ist transient -> ebenfalls 503 retry:true.
  FakeClaim claim;
  SoloOutcome out = runSolo("", claim);  // keine Antworten -> immer -1
  check(out.httpCode == 503, "connect-fehler erschoepft -> 503");
  checkEq(out.reason, "backend_starting", "connect reason");
  check(claim.calls == 4, "connect -> 4 claims");
}

static void testSoloTerminalErrors() {
  // 409 not_claimable ist endgueltig -> genau 1 Versuch, 409.
  FakeClaim claim;
  claim.responses.push_back(HttpResp{409, "{\"reason\":\"not_claimable\"}"});
  SoloOutcome out = runSolo("", claim);
  check(out.httpCode == 409, "not_claimable -> 409");
  checkEq(out.reason, "not_claimable", "not_claimable reason");
  check(claim.calls == 1, "not_claimable -> kein retry");
  check(g_sleeps.empty(), "not_claimable -> kein sleep");

  // 200 ohne brauchbares Ziel -> 502 bad_gateway, kein Retry.
  FakeClaim claim2;
  claim2.responses.push_back(HttpResp{200, "{\"ok\":true}"});
  SoloOutcome out2 = runSolo("", claim2);
  check(out2.httpCode == 502, "200 ohne ziel -> 502");
  checkEq(out2.reason, "bad_gateway", "200 ohne ziel reason");
  check(claim2.calls == 1, "200 ohne ziel -> kein retry");

  // 200 mit ungueltigem Endpoint (Port 0) -> 502.
  FakeClaim claim3;
  claim3.responses.push_back(HttpResp{200, "{\"gns_endpoint\":\"127.0.0.1:0\"}"});
  SoloOutcome out3 = runSolo("", claim3);
  check(out3.httpCode == 502, "port 0 -> 502");

  // 401 (falscher/fehlender Token) ist keine Parked-Semantik -> 502.
  FakeClaim claim4;
  claim4.responses.push_back(HttpResp{401, "{\"reason\":\"unauthorized\"}"});
  SoloOutcome out4 = runSolo("", claim4);
  check(out4.httpCode == 502, "401 -> 502");
  check(claim4.calls == 1, "401 -> kein retry");
}

static void testSoloBudgetVariant() {
  // maxAttempts=2: nach 2 transienten Fehlern Erschoepfung (1 Sleep).
  FakeClaim claim;
  SoloOutcome out = runSolo("", claim, 2);
  check(claim.calls == 2, "maxAttempts=2 -> 2 claims");
  check(g_sleeps.size() == 1, "maxAttempts=2 -> 1 sleep");
  check(out.httpCode == 503, "maxAttempts=2 erschoepft -> 503");
}

int main() {
  testParseTargetSpec();
  testJsonStringField();
  testJsonEscape();
  testParseQueryParam();
  testParseUrlHostPort();
  testParseHttpResponse();
  testRetryDecision();
  testUrlDecode();
  testSoloSuccess();
  testSoloEnvPayload();
  testSoloRetryThenSuccess();
  testSoloExhaustion();
  testSoloConnectionErrorExhaustion();
  testSoloTerminalErrors();
  testSoloBudgetVariant();

  if (g_failures == 0) {
    std::printf("test_api_util: %d Checks OK\n", g_checks);
    return 0;
  }
  std::fprintf(stderr, "test_api_util: %d von %d Checks fehlgeschlagen\n",
               g_failures, g_checks);
  return 1;
}
