#ifndef RBBATTLE_API_UTIL_H
#define RBBATTLE_API_UTIL_H

// Kleine, reine Helfer fuer die Steuer-API + Web-UI des GNS-Entry-Relays
// (Issue #857). Bewusst OHNE Win32-/Socket-Abhaengigkeiten, damit sie als
// Host-Test in der CI laufen (test_api_util.cpp) und nicht nur unter Wine.

#include "route_rules.h"

#include <cstddef>
#include <cstdio>
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

// Soll nach `attempt` Versuchen (1-basiert, bereits gemacht) ein weiterer
// Versuch folgen? Nur bei transientem Fehler und solange das Budget reicht.
inline bool shouldRetryClaim(int attempt, int maxAttempts, int httpStatus,
                             const std::string &reason) {
  if (attempt >= maxAttempts) {
    return false;
  }
  return isTransientClaimStatus(httpStatus, reason);
}

// Wartezeit vor dem naechsten Versuch: 250 ms, 500 ms, 1000 ms … Cap 1000 ms.
// Summe ueber 4 Versuche (3 Wartephasen) = 1750 ms < 5 s Budget.
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

} // namespace rbapi

#endif // RBBATTLE_API_UTIL_H
