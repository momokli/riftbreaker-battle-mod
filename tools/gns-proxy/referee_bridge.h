#ifndef RBBATTLE_REFEREE_BRIDGE_H
#define RBBATTLE_REFEREE_BRIDGE_H

// Relay <-> Referee-Bruecke (Issue #1024).
//
// Bewusst OHNE Win32/Socket-Abhaengigkeiten: reine, host-testbare Logik fuer
// den Lobby-Referee-Client des GNS-Relays (siehe test_referee_bridge.cpp).
// Der Relay (gns_probe.cpp) exponiert `GET /referee/state` und
// `POST /referee/ready` und spricht dafuer das Referee-Backend
// (`tournament/src/api.rs`: `GET /state` auth-frei, `POST /ready` Bearer) ueber
// den bestehenden Outbound-HTTP-Client an.
//
// Hier liegt nur das, was ohne Socket entscheidbar ist:
//   * Config-Parse (URL -> IPv4:port, nur Literal — kein DNS),
//   * Body-Bau fuer `POST /ready`,
//   * kontextabhaengige Ready-Route (Solo -> Capsule, VS -> Referee, #1025),
//   * robustes Lesen der `/state`-Antwort (fehlende Felder = leer, kein Crash),
//   * das Fehler-Mapping auf die Lobby-Semantik (503/502/ok/durchreichen).
//
// Wiederverwendet werden `rbapi::parseUrlHostPort` / `jsonStringField` /
// `jsonEscape` aus api_util.h und `rbroute::parseEndpoint` aus route_rules.h.

#include "api_util.h"
#include "route_rules.h"

#include <cstddef>
#include <string>
#include <vector>

namespace rbref {

// --- Fehler-Mapping auf die Lobby-Semantik ---------------------------------
//
// `configured` = ist RBB_REFEREE_URL/--referee-url gesetzt? `httpStatus` =
// Ergebnis des Outbound-Calls (<=0 = Verbindungs-/Timeout-Fehler).
enum class RefStatus {
  Unconfigured,   // kein Referee konfiguriert -> 503 referee_unconfigured
  Unreachable,    // konfiguriert, aber nicht erreichbar -> 502 referee_unreachable
  Ok,             // 200 -> Body durchreichen
  BackendStatus,  // andere Backend-Antwort (401/409/5xx) -> unveraendert durchreichen
};

inline const char *refStatusName(RefStatus s) {
  switch (s) {
  case RefStatus::Unconfigured: return "referee_unconfigured";
  case RefStatus::Unreachable: return "referee_unreachable";
  case RefStatus::Ok: return "ok";
  case RefStatus::BackendStatus: return "backend_status";
  }
  return "";
}

// HTTP-Code fuer die abgeleiteten Faelle. Bei BackendStatus ist der echte
// Backend-Code massgeblich (hier nur der generische Fallback 502).
inline int refStatusHttp(RefStatus s) {
  switch (s) {
  case RefStatus::Unconfigured: return 503;
  case RefStatus::Unreachable: return 502;
  case RefStatus::Ok: return 200;
  case RefStatus::BackendStatus: return 502;
  }
  return 502;
}

inline RefStatus mapRefereeError(bool configured, int httpStatus) {
  if (!configured) {
    return RefStatus::Unconfigured;
  }
  if (httpStatus <= 0) {
    return RefStatus::Unreachable;
  }
  if (httpStatus == 200) {
    return RefStatus::Ok;
  }
  return RefStatus::BackendStatus;
}

// --- Config-Parse ----------------------------------------------------------
//
// `http://<IPv4>:port` -> host + port. Nur ein IPv4-Literal ist erlaubt (der
// Outbound-Client nutzt inet_pton), kein Hostname/DNS. Rueckgabe false, wenn
// die URL kein gueltiger IPv4-Endpoint ist.
inline bool parseRefereeConfig(const std::string &url, std::string &host,
                               int &port) {
  if (!rbapi::parseUrlHostPort(url, host, port)) {
    return false;
  }
  rbroute::Endpoint parsed;
  return rbroute::parseEndpoint(host + ":" + std::to_string(port), parsed);
}

// --- Ready-Body -------------------------------------------------------------
//
// Kanonischer Body fuer den Referee-`POST /ready`: `{"world":"A"}`. Eine
// optionale Identitaet wird additiv angehaengt (Escaping via jsonEscape).
inline std::string buildReadyBody(const std::string &world,
                                  const std::string &identitaet = "") {
  std::string body = "{\"world\":\"" + rbapi::jsonEscape(world) + "\"";
  if (!identitaet.empty()) {
    body += ",\"identitaet\":\"" + rbapi::jsonEscape(identitaet) + "\"";
  }
  body += "}";
  return body;
}

// --- Ready-Routing (Issue #1025) -------------------------------------------
//
// Kontextabhaengige Verzweigung des Relay-`POST /ready`:
//   Solo (kein VS-Kontext / leere Welt) -> Capsule `POST /capsule/ready`,
//   VS   (Welt "A"|"B" aufgeloest)      -> Referee `POST /ready {world}`,
//   Welt gesetzt, aber ungueltig         -> 400 bad_request.
// Die Entscheidung ist bewusst rein (keine Sockets/Win32) und damit host-testbar.
enum class ReadyRoute {
  Capsule,   // Solo-Pfad, bit-identisch zum bisherigen Verhalten
  Referee,   // VS-Pfad zum GO-Kern des Referees
  BadWorld,  // Welt gesetzt, aber nicht "A"/"B"
};

inline const char *readyRouteName(ReadyRoute r) {
  switch (r) {
  case ReadyRoute::Capsule: return "capsule";
  case ReadyRoute::Referee: return "referee";
  case ReadyRoute::BadWorld: return "bad_world";
  }
  return "";
}

// `hasWorld` = Request/Queue-Kontext beansprucht eine VS-Welt. Ohne Welt
// (Solo) bleibt es bit-identisch beim Capsule-Zweig; eine leere Welt zaehlt
// ebenfalls als Solo (Default bleibt Capsule). Nur ein nicht-leerer,
// unbekannter Wert ist ein Fehler.
inline ReadyRoute resolveReadyRoute(bool hasWorld, const std::string &world) {
  if (!hasWorld || world.empty()) {
    return ReadyRoute::Capsule;
  }
  if (world == "A" || world == "B") {
    return ReadyRoute::Referee;
  }
  return ReadyRoute::BadWorld;
}

// --- /state-Antwort lesen ---------------------------------------------------
//
// Erstes JSON-Objekt hinter `"key"` als Substring (Klammer-Matching, Strings
// werden respektiert). Leer, wenn kein Objekt folgt. Bewusst klein — reicht
// fuer die flache/verschachtelte Struktur der Referee-Antwort.
inline std::string jsonObjectSlice(const std::string &body,
                                   const std::string &key) {
  const std::string needle = "\"" + key + "\"";
  const std::size_t pos = body.find(needle);
  if (pos == std::string::npos) {
    return "";
  }
  const std::size_t open = body.find('{', pos + needle.size());
  if (open == std::string::npos) {
    return "";
  }
  int depth = 0;
  bool inStr = false;
  for (std::size_t i = open; i < body.size(); ++i) {
    const char c = body[i];
    if (inStr) {
      if (c == '\\') {
        ++i;  // Escape-Zeichen ueberspringen
        continue;
      }
      if (c == '"') {
        inStr = false;
      }
      continue;
    }
    if (c == '"') {
      inStr = true;
    } else if (c == '{') {
      ++depth;
    } else if (c == '}') {
      --depth;
      if (depth == 0) {
        return body.substr(open + 1, i - open - 1);
      }
    }
  }
  return "";
}

// `"key": true|false` aus einem Body lesen. false, wenn kein echtes JSON-Bool
// folgt (Default bleibt unberuehrt).
inline bool jsonBoolField(const std::string &body, const std::string &key,
                          bool &out) {
  const std::string needle = "\"" + key + "\"";
  std::size_t pos = body.find(needle);
  if (pos == std::string::npos) {
    return false;
  }
  pos = body.find(':', pos + needle.size());
  if (pos == std::string::npos) {
    return false;
  }
  ++pos;
  while (pos < body.size() && (body[pos] == ' ' || body[pos] == '\t' ||
                               body[pos] == '\n' || body[pos] == '\r')) {
    ++pos;
  }
  if (body.compare(pos, 4, "true") == 0) {
    out = true;
    return true;
  }
  if (body.compare(pos, 5, "false") == 0) {
    out = false;
    return true;
  }
  return false;
}

// Liest die relevante Sicht aus der Referee-`/state`-Antwort:
//   phase  = `phase` (leer, wenn fehlt),
//   winner = `winner` (leer bei `null`/fehlt — kein Crash),
//   players = `teams.A.player` + `teams.B.player` in Reihenfolge (nur nicht-leere),
//   both_ready = beide Teams haben `ready: true` (fehlende Teams -> false).
// Rueckgabe true, wenn die Antwort ueberhaupt wie eine State-Sicht aussieht
// (phase oder teams vorhanden). Fehlende/kaputte Felder leeren die Ausgaben.
inline bool parseRefereeState(const std::string &body, std::string &phase,
                              std::string &winner,
                              std::vector<std::string> &players,
                              bool &both_ready) {
  phase.clear();
  winner.clear();
  players.clear();
  both_ready = false;

  const bool hasPhase = rbapi::jsonStringField(body, "phase", phase);
  std::string w;
  if (rbapi::jsonStringField(body, "winner", w) && !w.empty()) {
    winner = w;  // `winner: null` liefert keinen String -> bleibt leer
  }

  const std::string teams = jsonObjectSlice(body, "teams");
  bool readyA = false;
  bool readyB = false;
  if (!teams.empty()) {
    const std::string ta = jsonObjectSlice(teams, "A");
    const std::string tb = jsonObjectSlice(teams, "B");
    if (!ta.empty()) {
      std::string p;
      if (rbapi::jsonStringField(ta, "player", p) && !p.empty()) {
        players.push_back(p);
      }
      jsonBoolField(ta, "ready", readyA);
    }
    if (!tb.empty()) {
      std::string p;
      if (rbapi::jsonStringField(tb, "player", p) && !p.empty()) {
        players.push_back(p);
      }
      jsonBoolField(tb, "ready", readyB);
    }
    both_ready = !ta.empty() && !tb.empty() && readyA && readyB;
  }

  return hasPhase || !teams.empty();
}

}  // namespace rbref

#endif  // RBBATTLE_REFEREE_BRIDGE_H
