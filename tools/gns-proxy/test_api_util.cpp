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
using rbapi::deriveSoloPhase;
using rbapi::parseQueryParam;
using rbapi::parseSoloInstance;
using rbapi::parseSoloSelfSend;
using rbapi::parseTargetSpec;
using rbapi::parseUrlHostPort;
using rbapi::SoloPhase;
using rbapi::soloPhaseName;
using rbapi::retryDelayMs;
using rbapi::runSoloClaim;
using rbapi::SoloBudget;
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

  check(retryDelayMs(1) == 250, "delay 1");
  check(retryDelayMs(2) == 500, "delay 2");
  check(retryDelayMs(3) == 1000, "delay 3");
  check(retryDelayMs(9) == 1000, "delay cap");
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

// --- Solo-Phasenmodell + self_send (Issue #930) -----------------------------

static void testDeriveSoloPhase() {
  // !claimed -> None (alle anderen Eingaben egal).
  check(deriveSoloPhase(false, false, false, false) == SoloPhase::None,
        "nicht geclaimt -> None");
  check(deriveSoloPhase(false, true, true, true) == SoloPhase::None,
        "nicht geclaimt (verbunden) -> None");
  // claimed && !client -> Provisioned.
  check(deriveSoloPhase(true, false, false, false) == SoloPhase::Provisioned,
        "geclaimt ohne client -> Provisioned");
  check(deriveSoloPhase(true, false, true, true) == SoloPhase::Provisioned,
        "geclaimt ohne client (paused) -> Provisioned");
  // claimed && client && !backend -> Underway.
  check(deriveSoloPhase(true, true, false, false) == SoloPhase::Underway,
        "client, kein backend -> Underway");
  check(deriveSoloPhase(true, true, false, true) == SoloPhase::Underway,
        "client, kein backend (paused) -> Underway");
  // claimed && client && backend && paused -> InGamePaused.
  check(deriveSoloPhase(true, true, true, true) == SoloPhase::InGamePaused,
        "verbunden + paused -> InGamePaused");
  // sonst -> Running.
  check(deriveSoloPhase(true, true, true, false) == SoloPhase::Running,
        "verbunden + laeuft -> Running");
}

static void testSoloPhaseName() {
  checkEq(soloPhaseName(SoloPhase::None), "", "None -> leer");
  checkEq(soloPhaseName(SoloPhase::Provisioned), "provisioned",
          "Provisioned Name");
  checkEq(soloPhaseName(SoloPhase::Underway), "underway", "Underway Name");
  checkEq(soloPhaseName(SoloPhase::InGamePaused), "in_game_paused",
          "InGamePaused Name");
  checkEq(soloPhaseName(SoloPhase::Running), "running", "Running Name");
}

static void testParseSoloSelfSend() {
  bool out = false;
  // Feld fehlt -> Default true, kein Treffer.
  check(!parseSoloSelfSend("{\"identitaet\":\"str:A\"}", out),
        "fehlendes self_send -> false");
  check(out, "fehlendes self_send -> Default true");
  check(!parseSoloSelfSend("", out), "leerer Body -> false");
  check(out, "leerer Body -> Default true");
  // Explizites true/false.
  check(parseSoloSelfSend("{\"self_send\":true}", out), "true gefunden");
  check(out, "true -> out true");
  check(parseSoloSelfSend("{\"self_send\":false}", out), "false gefunden");
  check(!out, "false -> out false");
  // Whitespace tolerant.
  check(parseSoloSelfSend("{ \"self_send\" :  false }", out),
        "Whitespace + false");
  check(!out, "Whitespace false -> out false");
  // Nicht-Bool (String/Zahl) -> kein Treffer, Default bleibt.
  out = false;
  check(!parseSoloSelfSend("{\"self_send\":\"no\"}", out),
        "String -> kein Bool");
  check(out, "String -> Default true");
  out = false;
  check(!parseSoloSelfSend("{\"self_send\":1}", out), "Zahl -> kein Bool");
  check(out, "Zahl -> Default true");
}

// Issue #936: optionales `instance`-Feld im /solo-Body (Join einer bestehenden
// Solo-Instanz). Muster wie parseSoloSelfSend, aber ein nicht-leerer String.
static void testParseSoloInstance() {
  std::string out;
  check(parseSoloInstance("{\"identitaet\":\"str:A\",\"instance\":\"parked-7\"}", out),
        "instance gefunden");
  checkEq(out, "parked-7", "instance Wert");
  // Whitespace tolerant.
  out.clear();
  check(parseSoloInstance("{ \"instance\" : \"parked-9\" }", out),
        "instance mit Whitespace");
  checkEq(out, "parked-9", "instance Wert (WS)");
  // Fehlendes Feld -> false, out leer.
  out = "alt";
  check(!parseSoloInstance("{\"identitaet\":\"str:A\"}", out),
        "fehlendes instance -> false");
  checkEq(out, "", "fehlendes instance -> out leer");
  check(!parseSoloInstance("", out), "leerer Body -> false");
  checkEq(out, "", "leerer Body -> out leer");
  // Leerer String zaehlt nicht als Instanz -> false.
  out = "alt";
  check(!parseSoloInstance("{\"instance\":\"\"}", out),
        "leeres instance -> false");
  checkEq(out, "", "leeres instance -> out leer");
  // Nicht-String -> false, out unberuehrt/leer.
  out = "alt";
  check(!parseSoloInstance("{\"instance\":42}", out), "Zahl -> false");
  checkEq(out, "", "Zahl -> out leer");
}

// Issue #936: reine Aufnahme-Entscheidung fuer /solo. `instanceRequested` =
// der Body nennt eine nicht-leere `instance`; `memberCount` = aktuelle
// Mitgliederzahl der Gruppe (<=0 bedeutet: Instanz unbekannt).
static void testDecideSoloAction() {
  using rbapi::decideSoloAction;
  using rbapi::SoloJoinDecision;
  // Kein instance-Feld -> unveraendertes Verhalten (neuer Claim).
  check(decideSoloAction(false, 0, false, 4) == SoloJoinDecision::NewClaim,
        "ohne instance -> NewClaim");
  check(decideSoloAction(false, 3, false, 4) == SoloJoinDecision::NewClaim,
        "ohne instance (count ignoriert) -> NewClaim");
  // Bekannte Instanz mit freier Kapazitaet -> Join.
  check(decideSoloAction(true, 1, false, 4) == SoloJoinDecision::JoinExisting,
        "bekannt, 1/4 -> JoinExisting");
  check(decideSoloAction(true, 3, false, 4) == SoloJoinDecision::JoinExisting,
        "bekannt, 3/4 -> JoinExisting");
  // Grenze: memberCount == maxPlayers -> Full.
  check(decideSoloAction(true, 4, false, 4) == SoloJoinDecision::Full,
        "memberCount == maxPlayers -> Full");
  check(decideSoloAction(true, 5, false, 4) == SoloJoinDecision::Full,
        "memberCount > maxPlayers -> Full");
  // Unbekannte Instanz: named, aber keine Gruppe (count 0) -> UnknownInstance.
  check(decideSoloAction(true, 0, false, 4) == SoloJoinDecision::UnknownInstance,
        "unbekannte Instanz -> UnknownInstance");
  check(decideSoloAction(true, -1, false, 4) == SoloJoinDecision::UnknownInstance,
        "negativer count -> UnknownInstance");
  // Idempotenter Rejoin: bereits Mitglied, auch wenn die Instanz voll ist.
  check(decideSoloAction(true, 4, true, 4) == SoloJoinDecision::AlreadyMember,
        "bereits Mitglied (voll) -> AlreadyMember");
  check(decideSoloAction(true, 2, true, 4) == SoloJoinDecision::AlreadyMember,
        "bereits Mitglied -> AlreadyMember");
  // Grenze maxPlayers=1: Join ist unmoeglich (1 Mitglied = voll).
  check(decideSoloAction(true, 1, false, 1) == SoloJoinDecision::Full,
        "maxPlayers=1, count 1 -> Full");
  check(decideSoloAction(true, 0, false, 1) == SoloJoinDecision::UnknownInstance,
        "maxPlayers=1, count 0 -> UnknownInstance");
  // Degeneriertes maxPlayers (<1) wird auf 1 geklemmt.
  check(decideSoloAction(true, 0, false, 0) == SoloJoinDecision::UnknownInstance,
        "maxPlayers=0, count 0 -> UnknownInstance");
  // Grenz-/Negativfaelle (Stage 5, #936): das Klemmen von maxPlayers<1 wirkt
  // erst bei memberCount>0 (bei count 0 greift vorher UnknownInstance), sonst
  // waere der Klemmpfad ungetestet.
  check(decideSoloAction(true, 1, false, 0) == SoloJoinDecision::Full,
        "maxPlayers=0 geklemmt auf 1, count 1 -> Full");
  check(decideSoloAction(true, 1, false, -3) == SoloJoinDecision::Full,
        "maxPlayers=-3 geklemmt auf 1, count 1 -> Full");
  // Idempotenz hat Vorrang auch bei inkonsistentem Zustand (Mitglied, aber
  // Gruppe unbekannt/leer): kein Fehler, kein Doppelzaehlen.
  check(decideSoloAction(true, 0, true, 4) == SoloJoinDecision::AlreadyMember,
        "Mitglied trotz count 0 -> AlreadyMember");
  check(decideSoloAction(true, -1, true, 4) == SoloJoinDecision::AlreadyMember,
        "Mitglied trotz negativem count -> AlreadyMember");
  // Namen sind stabil und nicht leer.
  checkEq(rbapi::soloJoinDecisionName(SoloJoinDecision::Full), "instance_full",
          "name Full");
  checkEq(rbapi::soloJoinDecisionName(SoloJoinDecision::UnknownInstance),
          "unknown_instance", "name UnknownInstance");
}

// --- /solo-Claim (Issue #929) -----------------------------------------------

// Fake-Parked-Dienst: liefert eine vorgegebene (status, body)-Folge, simuliert
// den Zeitverbrauch (Fake-Clock) und protokolliert Aufrufe, Body und das pro
// Versuch uebergebene Timeout. Ersetzt den echten WinSock-Outbound-POST.
struct FakeClaim {
  std::vector<HttpResp> responses;
  std::size_t idx = 0;
  int calls = 0;
  std::vector<std::string> paths;
  std::vector<std::string> payloads;
  std::vector<int> timeouts;
  long long *clock = nullptr;   // Fake-Clock, die der Claim vorspult
  long long consumeMs = -1;     // <0 = volles timeoutMs verbrauchen

  HttpResp operator()(const std::string &path, const std::string &payload,
                      int timeoutMs) {
    ++calls;
    paths.push_back(path);
    payloads.push_back(payload);
    timeouts.push_back(timeoutMs);
    if (clock != nullptr) {
      long long used = consumeMs < 0 ? timeoutMs : consumeMs;
      if (used > timeoutMs) {
        used = timeoutMs;
      }
      *clock += used;
    }
    if (idx < responses.size()) {
      return responses[idx++];
    }
    return HttpResp{-1, ""};  // unerwarteter Extra-Aufruf -> Verbindungsfehler
  }
};

static std::vector<int> g_sleeps;

// Fuehrt runSoloClaim mit Fake-Clock aus: Claim und Sleep spulen dieselbe Uhr
// vor, sodass die Wall-Clock exakt nachvollziehbar ist. `claimPath` ist der
// konfigurierbare Claim-Pfad (Default Parked `/claim`; #931 Kapsel
// `/capsule/open`).
static SoloOutcome runSolo(const std::string &env, FakeClaim &claim,
                           long long &clock,
                           const SoloBudget &budget = SoloBudget{},
                           const std::string &claimPath = "/claim") {
  g_sleeps.clear();
  claim.clock = &clock;
  const auto nowMs = [&clock]() -> long long { return clock; };
  const auto sleepFn = [&clock](int ms) {
    g_sleeps.push_back(ms);
    clock += ms;
  };
  return runSoloClaim(env, std::ref(claim), budget, nowMs, sleepFn, claimPath);
}

static void testSoloSuccess() {
  FakeClaim claim;
  claim.consumeMs = 10;
  claim.responses.push_back(
      HttpResp{200, "{\"ok\":true,\"instance\":\"parked-1\","
                    "\"gns_endpoint\":\"127.0.0.1:32768\"}"});
  long long clock = 0;
  SoloOutcome out = runSolo("", claim, clock);
  check(out.httpCode == 200, "solo ok -> 200");
  check(out.pinIdentity, "solo ok -> pin");
  checkEq(out.endpoint, "127.0.0.1:32768", "solo ziel");
  checkEq(out.instance, "parked-1", "solo instanz");
  check(claim.calls == 1, "solo ok -> 1 claim");
  check(g_sleeps.empty(), "solo ok -> kein sleep");
  checkEq(claim.payloads[0], "{}", "leerer env -> {} Body");
  // Erster Versuch bekommt min(totalMs, attemptMs) = attemptMs.
  check(claim.timeouts[0] == 1500, "erster Timeout = attemptMs");
}

static void testSoloEnvPayload() {
  FakeClaim claim;
  claim.consumeMs = 10;
  claim.responses.push_back(
      HttpResp{200, "{\"gns_endpoint\":\"127.0.0.1:32100\"}"});
  long long clock = 0;
  SoloOutcome out = runSolo("staging", claim, clock);
  check(out.pinIdentity, "env-Lauf pinnt");
  checkEq(claim.payloads[0], "{\"env\":\"staging\"}", "env im Body");
  checkEq(out.instance, "", "ohne instance-Feld leer");
}

// Issue #931: der Claim-Pfad ist konfigurierbar. Default bleibt Parked
// `/claim`; mit Kapsel-URL ruft der Relay `/capsule/open` (Claim ohne resume).
static void testSoloClaimPathConfigurable() {
  FakeClaim parked;
  parked.consumeMs = 10;
  parked.responses.push_back(
      HttpResp{200, "{\"ok\":true,\"instance\":\"parked-1\","
                    "\"gns_endpoint\":\"127.0.0.1:32768\"}"});
  long long clock = 0;
  runSolo("", parked, clock);
  checkEq(parked.paths[0], "/claim", "Default-Pfad bleibt /claim (#929)");

  FakeClaim capsule;
  capsule.consumeMs = 10;
  capsule.responses.push_back(
      HttpResp{200, "{\"ok\":true,\"phase\":\"claimed\","
                    "\"instance\":\"parked-2\","
                    "\"gns_endpoint\":\"127.0.0.1:41001\"}"});
  long long clock2 = 0;
  SoloOutcome out = runSolo("", capsule, clock2, SoloBudget{}, "/capsule/open");
  checkEq(capsule.paths[0], "/capsule/open", "Kapsel-Pfad /capsule/open");
  check(out.pinIdentity, "Kapsel-200 pinnt");
  checkEq(out.endpoint, "127.0.0.1:41001", "Kapsel-Ziel");
  checkEq(out.instance, "parked-2", "Kapsel-instance");
}

static void testSoloRetryThenSuccess() {
  FakeClaim claim;
  claim.consumeMs = 100;
  claim.responses.push_back(HttpResp{503, "{\"reason\":\"bridge_unhealthy\"}"});
  claim.responses.push_back(HttpResp{503, "{\"reason\":\"bridge_unhealthy\"}"});
  claim.responses.push_back(
      HttpResp{200, "{\"instance\":\"p\",\"gns_endpoint\":\"127.0.0.1:40000\"}"});
  long long clock = 0;
  SoloOutcome out = runSolo("", claim, clock);
  check(out.pinIdentity, "retry->erfolg pinnt");
  checkEq(out.endpoint, "127.0.0.1:40000", "retry ziel");
  check(claim.calls == 3, "retry -> 3 claims");
  check(g_sleeps.size() == 2, "retry -> 2 sleeps");
  check(g_sleeps[0] == 250 && g_sleeps[1] == 500, "backoff 250/500");
  check(clock < 4500, "retry-Erfolg unter Budget");
}

static void testSoloExhaustion() {
  // 409 none_parked bleibt transient; jeder Versuch verbraucht sein volles
  // Timeout -> Budget erschoepft, Ergebnis 503 backend_starting.
  FakeClaim claim;
  claim.consumeMs = -1;
  for (int i = 0; i < 6; ++i) {
    claim.responses.push_back(HttpResp{409, "{\"reason\":\"none_parked\"}"});
  }
  long long clock = 0;
  SoloOutcome out = runSolo("", claim, clock);
  check(out.httpCode == 503, "erschoepft -> 503");
  check(!out.pinIdentity, "erschoepft -> kein pin");
  checkEq(out.reason, "backend_starting", "erschoepft reason");
  check(out.body.find("\"retry\":true") != std::string::npos,
        "erschoepft retry:true");
  check(clock <= 4500, "erschoepft wall-clock <= totalMs");
}

static void testSoloConnectionErrorExhaustion() {
  // -1 (Connect-/Timeout-Fehler) ist transient -> ebenfalls 503 retry:true.
  FakeClaim claim;
  claim.consumeMs = -1;
  long long clock = 0;
  SoloOutcome out = runSolo("", claim, clock);  // keine Antworten -> immer -1
  check(out.httpCode == 503, "connect-fehler erschoepft -> 503");
  checkEq(out.reason, "backend_starting", "connect reason");
  check(clock <= 4500, "connect wall-clock <= totalMs");
  check(claim.calls >= 2, "connect -> mehrere Versuche");
}

static void testSoloBudgetHardBound() {
  // Jeder Versuch verbraucht sein volles Timeout: Gesamt-Wall-Clock ist HART
  // <= totalMs, die uebergebenen Timeouts schrumpfen mit dem Rest-Budget.
  FakeClaim claim;
  claim.consumeMs = -1;
  long long clock = 0;
  SoloOutcome out = runSolo("", claim, clock);
  check(clock <= 4500, "wall-clock hart <= totalMs");
  check(out.httpCode == 503, "budget erschoepft -> 503");
  checkEq(out.reason, "backend_starting", "budget reason");
  bool shrinking = false;
  int prev = 1 << 30;
  for (int t : claim.timeouts) {
    check(t <= 1500, "jeder Timeout <= attemptMs");
    if (t < prev) {
      shrinking = true;
    }
    prev = t;
  }
  check(shrinking, "Timeouts schrumpfen mit Rest-Budget");
  check(claim.timeouts.back() < claim.timeouts.front(),
        "letzter Timeout < erster");
}

static void testSoloBudgetMinAttemptCutoff() {
  // Nur noch ein Versuch passt: totalMs=300 < attemptMs, danach bricht die
  // Schleife ab (Rest-Budget < minAttemptMs).
  FakeClaim claim;
  claim.consumeMs = -1;
  long long clock = 0;
  SoloBudget budget;
  budget.totalMs = 300;
  budget.attemptMs = 1500;
  budget.minAttemptMs = 250;
  SoloOutcome out = runSolo("", claim, clock, budget);
  check(clock <= 300, "mini-Budget wall-clock <= totalMs");
  check(claim.calls == 1, "mini-Budget -> genau 1 Versuch");
  check(claim.timeouts[0] == 300, "Timeout = Rest-Budget (300)");
  check(out.httpCode == 503, "mini-Budget erschoepft -> 503");
}

static void testSoloBudgetSleepRespectsRemaining() {
  // Nach dem 1. Versuch (1500 ms) bleibt 500 ms: Sleep 250 ms passt, danach
  // ein letzter Versuch mit exakt dem Rest (250 ms) -> Summe genau 2000 ms.
  FakeClaim claim;
  claim.consumeMs = -1;
  long long clock = 0;
  SoloBudget budget;
  budget.totalMs = 2000;
  budget.attemptMs = 1500;
  budget.minAttemptMs = 250;
  SoloOutcome out = runSolo("", claim, clock, budget);
  check(clock <= 2000, "sleep wall-clock <= totalMs");
  check(claim.calls == 2, "sleep -> 2 Versuche");
  check(claim.timeouts[0] == 1500 && claim.timeouts[1] == 250,
        "Timeouts 1500/250");
  check(out.httpCode == 503, "sleep erschoepft -> 503");
}

static void testSoloTerminalErrors() {
  // 409 not_claimable ist endgueltig -> genau 1 Versuch, 409.
  FakeClaim claim;
  claim.consumeMs = 10;
  claim.responses.push_back(HttpResp{409, "{\"reason\":\"not_claimable\"}"});
  long long clock = 0;
  SoloOutcome out = runSolo("", claim, clock);
  check(out.httpCode == 409, "not_claimable -> 409");
  checkEq(out.reason, "not_claimable", "not_claimable reason");
  check(claim.calls == 1, "not_claimable -> kein retry");
  check(g_sleeps.empty(), "not_claimable -> kein sleep");

  // 200 ohne brauchbares Ziel -> 502 bad_gateway, kein Retry.
  FakeClaim claim2;
  claim2.consumeMs = 10;
  claim2.responses.push_back(HttpResp{200, "{\"ok\":true}"});
  long long clock2 = 0;
  SoloOutcome out2 = runSolo("", claim2, clock2);
  check(out2.httpCode == 502, "200 ohne ziel -> 502");
  checkEq(out2.reason, "bad_gateway", "200 ohne ziel reason");
  check(claim2.calls == 1, "200 ohne ziel -> kein retry");

  // 200 mit ungueltigem Endpoint (Port 0) -> 502.
  FakeClaim claim3;
  claim3.consumeMs = 10;
  claim3.responses.push_back(HttpResp{200, "{\"gns_endpoint\":\"127.0.0.1:0\"}"});
  long long clock3 = 0;
  SoloOutcome out3 = runSolo("", claim3, clock3);
  check(out3.httpCode == 502, "port 0 -> 502");

  // 401 (falscher/fehlender Token) ist keine Parked-Semantik -> 502.
  FakeClaim claim4;
  claim4.consumeMs = 10;
  claim4.responses.push_back(HttpResp{401, "{\"reason\":\"unauthorized\"}"});
  long long clock4 = 0;
  SoloOutcome out4 = runSolo("", claim4, clock4);
  check(out4.httpCode == 502, "401 -> 502");
  check(claim4.calls == 1, "401 -> kein retry");
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
  testDeriveSoloPhase();
  testSoloPhaseName();
  testParseSoloSelfSend();
  testParseSoloInstance();
  testDecideSoloAction();
  testSoloSuccess();
  testSoloEnvPayload();
  testSoloClaimPathConfigurable();
  testSoloRetryThenSuccess();
  testSoloExhaustion();
  testSoloConnectionErrorExhaustion();
  testSoloBudgetHardBound();
  testSoloBudgetMinAttemptCutoff();
  testSoloBudgetSleepRespectsRemaining();
  testSoloTerminalErrors();

  if (g_failures == 0) {
    std::printf("test_api_util: %d Checks OK\n", g_checks);
    return 0;
  }
  std::fprintf(stderr, "test_api_util: %d von %d Checks fehlgeschlagen\n",
               g_failures, g_checks);
  return 1;
}
