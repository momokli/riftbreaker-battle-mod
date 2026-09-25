#ifndef RBBATTLE_API_UTIL_H
#define RBBATTLE_API_UTIL_H

// Kleine, reine Helfer fuer die Steuer-API + Web-UI des GNS-Entry-Relays
// (Issue #857). Bewusst OHNE Win32-/Socket-Abhaengigkeiten, damit sie als
// Host-Test in der CI laufen (test_api_util.cpp) und nicht nur unter Wine.

#include "route_rules.h"

#include <algorithm>
#include <cstddef>
#include <cstdio>
#include <functional>
#include <string>

namespace rbapi {

// "NAME=ip:port" -> name + Endpoint (die Buttons der UI, z. B.
// "PROD=127.0.0.1:6322"). Name darf nicht leer sein, das Ziel muss ein
// gueltiger IPv4-Endpoint sein.
inline bool parseTargetSpec(const std::string &spec, std::string &name,
                            rbroute::Endpoint &out) {
  const std::size_t eq = spec.find('=');
  if (eq == std::string::npos || eq == 0 || eq + 1 >= spec.size()) {
    return false;
  }
  name = spec.substr(0, eq);
  return rbroute::parseEndpoint(spec.substr(eq + 1), out);
}

// Minimaler JSON-String-Leser fuer die winzige PoC-API: sucht `"key"` und liest
// den folgenden String-Wert inklusive der ueblichen Escapes. Reicht fuer
// `{"identitaet":"…","target":"…"}`; bewusst kein allgemeiner JSON-Parser.
inline bool jsonStringField(const std::string &body, const std::string &key,
                            std::string &out) {
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
  if (pos >= body.size() || body[pos] != '"') {
    return false;
  }
  ++pos;

  std::string result;
  while (pos < body.size()) {
    const char c = body[pos++];
    if (c == '"') {
      out = result;
      return true;
    }
    if (c == '\\' && pos < body.size()) {
      const char esc = body[pos++];
      switch (esc) {
      case 'n': result.push_back('\n'); break;
      case 't': result.push_back('\t'); break;
      case 'r': result.push_back('\r'); break;
      case 'b': result.push_back('\b'); break;
      case 'f': result.push_back('\f'); break;
      case '/': result.push_back('/'); break;
      case '\\': result.push_back('\\'); break;
      case '"': result.push_back('"'); break;
      case 'u': {
        // \uXXXX -> ASCII (PoC; alles andere wird '?').
        if (pos + 4 <= body.size()) {
          unsigned code = 0;
          bool ok = true;
          for (int i = 0; i < 4; ++i) {
            const char h = body[pos + i];
            int v = -1;
            if (h >= '0' && h <= '9') {
              v = h - '0';
            } else if (h >= 'a' && h <= 'f') {
              v = h - 'a' + 10;
            } else if (h >= 'A' && h <= 'F') {
              v = h - 'A' + 10;
            } else {
              ok = false;
              break;
            }
            code = code * 16 + static_cast<unsigned>(v);
          }
          if (ok) {
            pos += 4;
            result.push_back(code < 128 ? static_cast<char>(code) : '?');
            break;
          }
        }
        result.push_back('?');
        break;
      }
      default: result.push_back(esc); break;
      }
    } else {
      result.push_back(c);
    }
  }
  return false;
}

// --- Dynamische Backends + Outbound-HTTP (Issue #929) -----------------------
//
// Reine Helfer fuer die neuen Endpunkte: Query-Param-Lesen (`DELETE
// /backends?name=…`), URL->host/port, HTTP-Antwort-Parsing und die
// Retry-Entscheidung von `/solo`. Bewusst OHNE Socket/Win32 — host-testbar.

// `%XX`/`+` dekodieren (application/x-www-form-urlencoded). Ungueltige
// Prozentfolgen bleiben unveraendert stehen (tolerant, kein Crash).
inline std::string urlDecode(const std::string &in) {
  std::string out;
  out.reserve(in.size());
  for (std::size_t i = 0; i < in.size(); ++i) {
    const char c = in[i];
    if (c == '+') {
      out.push_back(' ');
      continue;
    }
    if (c == '%' && i + 2 < in.size()) {
      int hi = -1;
      int lo = -1;
      const char a = in[i + 1];
      const char b = in[i + 2];
      if (a >= '0' && a <= '9') hi = a - '0';
      else if (a >= 'a' && a <= 'f') hi = a - 'a' + 10;
      else if (a >= 'A' && a <= 'F') hi = a - 'A' + 10;
      if (b >= '0' && b <= '9') lo = b - '0';
      else if (b >= 'a' && b <= 'f') lo = b - 'a' + 10;
      else if (b >= 'A' && b <= 'F') lo = b - 'A' + 10;
      if (hi >= 0 && lo >= 0) {
        out.push_back(static_cast<char>(hi * 16 + lo));
        i += 2;
        continue;
      }
    }
    out.push_back(c);
  }
  return out;
}

// Ersten `key=value`-Treffer aus einer Query (`a=1&name=X`) lesen und beide
// Seiten URL-dekodieren. true nur, wenn `key` vorkommt (Wert darf leer sein).
inline bool parseQueryParam(const std::string &query, const std::string &key,
                            std::string &value) {
  std::size_t pos = 0;
  while (pos <= query.size()) {
    std::size_t amp = query.find('&', pos);
    if (amp == std::string::npos) {
      amp = query.size();
    }
    const std::string pair = query.substr(pos, amp - pos);
    const std::size_t eq = pair.find('=');
    const std::string k =
        urlDecode(eq == std::string::npos ? pair : pair.substr(0, eq));
    if (k == key) {
      value = urlDecode(eq == std::string::npos ? std::string()
                                                : pair.substr(eq + 1));
      return true;
    }
    if (amp == query.size()) {
      break;
    }
    pos = amp + 1;
  }
  return false;
}

// `http://host:port` (oder `https://host`) -> host + port. Ohne Port: 80 bzw.
// 443. Pfad/Query hinter dem host werden ignoriert. Port 1..65535.
inline bool parseUrlHostPort(const std::string &url, std::string &host,
                             int &port) {
  std::size_t start = 0;
  int defaultPort = 80;
  if (url.compare(0, 7, "http://") == 0) {
    start = 7;
  } else if (url.compare(0, 8, "https://") == 0) {
    start = 8;
    defaultPort = 443;
  }
  std::size_t end = url.size();
  const std::size_t slash = url.find('/', start);
  if (slash != std::string::npos) {
    end = slash;
  }
  const std::string authority = url.substr(start, end - start);
  if (authority.empty()) {
    return false;
  }
  const std::size_t colon = authority.rfind(':');
  if (colon == std::string::npos) {
    host = authority;
    port = defaultPort;
    return !host.empty();
  }
  host = authority.substr(0, colon);
  const std::string portPart = authority.substr(colon + 1);
  if (host.empty() || portPart.empty() || portPart.size() > 5) {
    return false;
  }
  long p = 0;
  for (char c : portPart) {
    if (c < '0' || c > '9') {
      return false;
    }
    p = p * 10 + (c - '0');
    if (p > 65535) {
      return false;
    }
  }
  if (p == 0) {
    return false;
  }
  port = static_cast<int>(p);
  return true;
}

// Statuszeile `HTTP/1.1 200 OK` -> 200. false, wenn keine 3-stellige Zahl folgt.
inline bool parseHttpStatusLine(const std::string &line, int &code) {
  if (line.compare(0, 5, "HTTP/") != 0) {
    return false;
  }
  const std::size_t sp = line.find(' ');
  if (sp == std::string::npos || sp + 3 >= line.size()) {
    return false;
  }
  const char a = line[sp + 1];
  const char b = line[sp + 2];
  const char c = line[sp + 3];
  if (a < '0' || a > '9' || b < '0' || b > '9' || c < '0' || c > '9') {
    return false;
  }
  code = (a - '0') * 100 + (b - '0') * 10 + (c - '0');
  return true;
}

// Rohe HTTP-Antwort in Status + Body trennen (Header/Body an `\r\n\r\n`).
inline bool parseHttpResponse(const std::string &raw, int &code,
                              std::string &body) {
  const std::size_t split = raw.find("\r\n\r\n");
  const std::string head = raw.substr(0, split);
  const std::size_t lineEnd = head.find("\r\n");
  const std::string statusLine =
      head.substr(0, lineEnd == std::string::npos ? head.size() : lineEnd);
  if (!parseHttpStatusLine(statusLine, code)) {
    return false;
  }
  body = (split == std::string::npos) ? std::string() : raw.substr(split + 4);
  return true;
}

// Ist ein Claim-Fehler transient (Backend faehrt noch hoch) und soll wiederholt
// werden? `httpStatus == -1` = Verbindungsfehler; `503` = Bridge/dienst nicht
// bereit; `409 none_parked` = noch keine geparkte Instanz. Alles andere (z. B.
// `409 not_claimable`, `401`) ist endgueltig.
inline bool isTransientClaimStatus(int httpStatus, const std::string &reason) {
  if (httpStatus == -1) {
    return true;
  }
  if (httpStatus == 503) {
    return true;
  }
  if (httpStatus == 409 && reason == "none_parked") {
    return true;
  }
  return false;
}

// Wartezeit vor dem naechsten Versuch: 250 ms, 500 ms, 1000 ms … Cap 1000 ms.
// runSoloClaim prueft die Wartezeit gegen das Rest-Budget — so bleibt die
// Gesamtlaufzeit hart <= SoloBudget::totalMs (statt einer Versuchszahl).
inline int retryDelayMs(int attempt) {
  int delay = 250;
  for (int i = 1; i < attempt && delay < 1000; ++i) {
    delay *= 2;
  }
  return delay > 1000 ? 1000 : delay;
}

// String fuer die JSON-Ausgabe escapen (Anfuehrungszeichen, Backslash,
// Steuerzeichen) — die Identitaet/IP kommen vom Client.
inline std::string jsonEscape(const std::string &s) {
  std::string out;
  out.reserve(s.size() + 8);
  for (char c : s) {
    switch (c) {
    case '"': out += "\\\""; break;
    case '\\': out += "\\\\"; break;
    case '\n': out += "\\n"; break;
    case '\r': out += "\\r"; break;
    case '\t': out += "\\t"; break;
    default:
      if (static_cast<unsigned char>(c) < 0x20) {
        char buf[8];
        std::snprintf(buf, sizeof(buf), "\\u%04x",
                      static_cast<unsigned char>(c));
        out += buf;
      } else {
        out.push_back(c);
      }
    }
  }
  return out;
}

// --- Solo-Phasenmodell + /solo-Body (Issue #930) -----------------------------
//
// Reine, host-testbare Ableitung des sichtbaren Solo-Status aus dem, was der
// Relay ohnehin weiss (Claim-Zustand + Client-/Backend-Verbindung + Pause-
// Signal). Bewusst OHNE Socket/Win32 — laeuft als Host-Test in der CI.

enum class SoloPhase {
  None,          // nicht geclaimt
  Provisioned,   // geclaimt, aber (noch) kein Client verbunden
  Underway,      // Client verbunden, Backend-Connect laeuft noch
  InGamePaused,  // Client + Backend verbunden, Spiel angehalten
  Running,       // Client + Backend verbunden, Spiel laeuft
};

// Reihenfolge der Pruefungen = die Uebergaenge im Solo-Leben. `gamePaused`
// wird nur ausgewertet, wenn Client UND Backend verbunden sind.
inline SoloPhase deriveSoloPhase(bool claimed, bool clientConnected,
                                 bool backendConnected, bool gamePaused) {
  if (!claimed) {
    return SoloPhase::None;
  }
  if (!clientConnected) {
    return SoloPhase::Provisioned;
  }
  if (!backendConnected) {
    return SoloPhase::Underway;
  }
  return gamePaused ? SoloPhase::InGamePaused : SoloPhase::Running;
}

// Stabile JSON-Strings fuer die UI (leer bei None).
inline const char *soloPhaseName(SoloPhase phase) {
  switch (phase) {
  case SoloPhase::Provisioned: return "provisioned";
  case SoloPhase::Underway: return "underway";
  case SoloPhase::InGamePaused: return "in_game_paused";
  case SoloPhase::Running: return "running";
  case SoloPhase::None: break;
  }
  return "";
}

// `"self_send": true|false` aus dem /solo-Body lesen. Fehlt das Feld (oder ist
// es kein JSON-Bool), gilt der Default `true` = heutiges Verhalten
// (claim + pin). Rueckgabe true nur, wenn ein echtes JSON-Bool gefunden wurde.
inline bool parseSoloSelfSend(const std::string &body, bool &out) {
  out = true;  // Default: heutiges Verhalten
  const std::string needle = "\"self_send\"";
  const std::size_t pos = body.find(needle);
  if (pos == std::string::npos) {
    return false;
  }
  std::size_t p = body.find(':', pos + needle.size());
  if (p == std::string::npos) {
    return false;
  }
  ++p;
  while (p < body.size() && (body[p] == ' ' || body[p] == '\t' ||
                             body[p] == '\n' || body[p] == '\r')) {
    ++p;
  }
  if (body.compare(p, 4, "true") == 0) {
    out = true;
    return true;
  }
  if (body.compare(p, 5, "false") == 0) {
    out = false;
    return true;
  }
  return false;  // Nicht-Bool -> Default bleibt stehen
}

// `"instance": "..."` aus dem /solo-Body lesen (Issue #936, Muster wie
// parseSoloSelfSend). Rueckgabe true NUR bei einem nicht-leeren JSON-String;
// `out` wird vorher geleert. Fehlendes, leeres oder nicht-string-Feld -> false
// (heutiges Verhalten: kein Join, neuer Claim).
inline bool parseSoloInstance(const std::string &body, std::string &out) {
  out.clear();
  const std::string needle = "\"instance\"";
  const std::size_t pos = body.find(needle);
  if (pos == std::string::npos) {
    return false;
  }
  std::size_t p = body.find(':', pos + needle.size());
  if (p == std::string::npos) {
    return false;
  }
  ++p;
  while (p < body.size() && (body[p] == ' ' || body[p] == '\t' ||
                             body[p] == '\n' || body[p] == '\r')) {
    ++p;
  }
  if (p >= body.size() || body[p] != '"') {
    return false;  // kein String (Zahl/null/Array/Objekt) -> Default
  }
  ++p;
  std::string value;
  while (p < body.size()) {
    const char c = body[p];
    if (c == '"') {
      out = value;
      return !out.empty();  // leerer String zaehlt nicht als Instanz
    }
    if (c == '\\' && p + 1 < body.size()) {
      value += body[p + 1];  // einfache Escapes (Instanznamen sind schlicht)
      p += 2;
      continue;
    }
    value += c;
    ++p;
  }
  return false;  // unterminierter String -> Default
}

// --- /solo-Aufnahme-Entscheidung (Issue #936) -------------------------------
//
// Reine, host-testbare Entscheidung, ob ein `/solo`-Request einen NEUEN Claim
// ausloest oder einer bereits geclaimten Solo-Instanz beitritt. Der Relay haelt
// die Gruppen-Wahrheit (`g_soloGroups`: instance -> Identitaeten); die
// Kapazitaet deckelt er auf `--max-players` (Default 4 = Server-Default).
enum class SoloJoinDecision {
  NewClaim,        // kein instance genannt -> heutiges Verhalten
  JoinExisting,    // bekannte Instanz, freie Kapazitaet -> Join ohne Claim
  Full,            // bekannte Instanz, memberCount >= maxPlayers -> 409
  UnknownInstance, // instance genannt, aber keine Gruppe -> 409
  AlreadyMember,   // Identitaet ist schon Mitglied -> idempotent
};

// Stabile JSON-Strings fuer die Fehlerpfade (nur Full/UnknownInstance haben
// einen Body mit `reason`; Join/NewClaim/AlreadyMember sind Erfolg).
inline const char *soloJoinDecisionName(SoloJoinDecision d) {
  switch (d) {
  case SoloJoinDecision::Full: return "instance_full";
  case SoloJoinDecision::UnknownInstance: return "unknown_instance";
  case SoloJoinDecision::NewClaim: return "new_claim";
  case SoloJoinDecision::JoinExisting: return "join_existing";
  case SoloJoinDecision::AlreadyMember: return "already_member";
  }
  return "";
}

// `instanceRequested` = der /solo-Body nennt eine nicht-leere `instance`.
// `memberCount` = aktuelle Mitgliederzahl der Gruppe; <= 0 bedeutet, dass zu
// dieser Instanz keine Gruppe existiert (unbekannt). `alreadyMember` = die
// Identitaet steht bereits in der Gruppe. Reihenfolge: Idempotenz zuerst (ein
// Mitglied wird nie abgewiesen, verbraucht keinen Platz), danach Kapazitaet.
inline SoloJoinDecision decideSoloAction(bool instanceRequested, int memberCount,
                                        bool alreadyMember, int maxPlayers) {
  if (!instanceRequested) {
    return SoloJoinDecision::NewClaim;
  }
  if (alreadyMember) {
    return SoloJoinDecision::AlreadyMember;
  }
  if (memberCount <= 0) {
    return SoloJoinDecision::UnknownInstance;
  }
  if (maxPlayers < 1) {
    maxPlayers = 1;  // degeneriertes Limit -> mindestens 1
  }
  if (memberCount >= maxPlayers) {
    return SoloJoinDecision::Full;
  }
  return SoloJoinDecision::JoinExisting;
}

// --- /solo-Orchestrierung (Issue #929) --------------------------------------
//
// Reine (socket-freie) Abbildung des Relay-`/solo`-Pfads: Claim-Aufruf per
// Callback, Retry/Backoff bei transienten Fehlern, Fehler-Mapping auf die
// Parked-Semantik. In gns_probe.cpp liefert der Callback den WinSock-Outbound-
// POST; im Host-Test ein Fake (Fake-Parked-Dienst). Retry-Erschoepfung und das
// 409/503/502-Mapping sind damit hermetisch testbar (test_api_util.cpp).
//
// Das Latenz-Budget ist HART (Deadline-getrieben, nicht Versuchszahl): die
// Schleife rechnet vor jedem Versuch `remaining = totalMs - elapsed` und bricht
// ab, sobald weniger als `minAttemptMs` uebrig ist. Jeder Versuch bekommt
// `min(remaining, attemptMs)` als Timeout — der Outbound-Client (gns_probe.cpp)
// nutzt das als Summe fuer connect+recv, sodass ein Versuch nie laenger laeuft.
// Damit ist die Gesamt-Wall-Clock garantiert <= totalMs (Default 4,5 s < 5 s).

struct HttpResp {
  int status = -1;  // -1 = Verbindungs-/Timeout-Fehler (transient)
  std::string body;
};

// Hartes Latenz-Budget fuer den /solo-Claim (Wall-Clock).
struct SoloBudget {
  int totalMs = 4500;      // Obergrenze fuer die GESAMTE Claim-Schleife
  int attemptMs = 1500;    // Obergrenze fuer einen einzelnen Versuch
  int minAttemptMs = 250;  // kein Versuch mehr, wenn weniger Rest-Budget
};

// Claim-Callback: `path` + `payload` + pro Versuch `timeoutMs` (Rest-Budget,
// bereits auf attemptMs gedeckelt). Muss innerhalb von timeoutMs zurueckkehren.
using ClaimFn = std::function<HttpResp(const std::string &path,
                                       const std::string &payload,
                                       int timeoutMs)>;

struct SoloOutcome {
  int httpCode = 502;
  std::string body;      // Antwort-Body (bei Erfolg leer — baut der Aufrufer)
  bool pinIdentity = false;  // true -> Identitaet auf `endpoint` pinnen
  std::string endpoint;  // GNS-UDP-Ziel im Erfolgsfall
  std::string instance;  // Parked-Instanzname (falls geliefert)
  std::string reason;    // "backend_starting" bei Retry-Erschoepfung, sonst Grund
};

// Claim-Callback liefert (status, body) eines Parked-`POST /claim`; `nowMs`
// liefert eine monotone Millisekunden-Uhr (im Host-Test eine Fake-Clock, unter
// Wine std::chrono::steady_clock); `sleepFn` bekommt die Wartezeit in ms (im
// Host-Test eine Aufzeichnung, unter Wine `Sleep`). Identitaet/env sind bereits
// geprueft — hier nur Claim + Mapping. Die Schleife ist deadline-getrieben und
// garantiert eine Gesamt-Wall-Clock <= budget.totalMs.
inline SoloOutcome runSoloClaim(
    const std::string &env, const ClaimFn &claim, const SoloBudget &budget,
    const std::function<long long()> &nowMs,
    const std::function<void(int)> &sleepFn,
    const std::string &claimPath = "/claim") {
  const std::string claimBody =
      env.empty() ? std::string("{}")
                  : "{\"env\":\"" + jsonEscape(env) + "\"}";
  const long long start = nowMs ? nowMs() : 0;
  int status = -1;
  std::string respBody;
  std::string reason;
  std::string endpointStr;
  std::string instance;
  rbroute::Endpoint endpoint;
  bool haveEndpoint = false;
  int attempt = 0;

  for (;;) {
    const long long elapsed = (nowMs ? nowMs() : 0) - start;
    const long long remaining = static_cast<long long>(budget.totalMs) - elapsed;
    if (remaining < budget.minAttemptMs) {
      break;  // zu wenig Rest-Budget fuer einen sinnvollen Versuch
    }
    ++attempt;
    const int attemptTimeout = static_cast<int>(
        std::min<long long>(remaining, budget.attemptMs));
    const HttpResp r = claim(claimPath, claimBody, attemptTimeout);
    status = r.status;
    respBody = r.body;
    reason.clear();
    jsonStringField(respBody, "reason", reason);
    haveEndpoint = false;
    if (status == 200) {
      if (jsonStringField(respBody, "gns_endpoint", endpointStr) &&
          !endpointStr.empty() && rbroute::parseEndpoint(endpointStr, endpoint)) {
        jsonStringField(respBody, "instance", instance);
        haveEndpoint = true;
        break;
      }
      // 200 ohne brauchbares Ziel ist nicht retryfaehig (Fehlkonfiguration).
      status = -2;
      reason = "no_endpoint";
      break;
    }
    if (!isTransientClaimStatus(status, reason)) {
      break;  // endgueltiger Fehler -> kein Retry
    }
    // Sleep gegen das Rest-Budget pruefen: nie laenger schlafen als uebrig.
    const long long remainingBeforeSleep =
        static_cast<long long>(budget.totalMs) - ((nowMs ? nowMs() : 0) - start);
    if (remainingBeforeSleep <= 0) {
      break;
    }
    int delay = retryDelayMs(attempt);
    if (delay > remainingBeforeSleep) {
      delay = static_cast<int>(remainingBeforeSleep);
    }
    if (sleepFn && delay > 0) {
      sleepFn(delay);
    }
  }

  SoloOutcome out;
  if (haveEndpoint) {
    out.httpCode = 200;
    out.pinIdentity = true;
    out.endpoint = endpoint.str();
    out.instance = instance;
    return out;
  }
  if (isTransientClaimStatus(status, reason)) {
    // Budget erschoepft und letzter Fehler war transient -> Backend startet
    // noch; der Client darf es erneut versuchen.
    out.httpCode = 503;
    out.reason = "backend_starting";
    out.body = "{\"ok\":false,\"reason\":\"backend_starting\",\"retry\":true}";
    return out;
  }
  // Endgueltige Fehler auf die Parked-Semantik abbilden. 503 ist per
  // isTransientClaimStatus immer transient und landet daher nie hier.
  int outStatus = 502;
  std::string outReason = "bad_gateway";
  if (status == 409) {
    outStatus = 409;
    outReason = reason.empty() ? std::string("not_claimable") : reason;
  }
  out.httpCode = outStatus;
  out.reason = outReason;
  out.body = "{\"ok\":false,\"reason\":\"" + jsonEscape(outReason) + "\"}";
  return out;
}

} // namespace rbapi

#endif // RBBATTLE_API_UTIL_H
