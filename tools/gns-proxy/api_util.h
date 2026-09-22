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
