#ifndef RBBATTLE_ROUTE_RULES_H
#define RBBATTLE_ROUTE_RULES_H

// Reine Routing-Logik fuer den GNS-Entry-Relay (Issue #843).
//
// Bewusst OHNE Win32/GNS-Abhaengigkeiten: so laeuft sie als Host-Test in der CI
// (siehe test_route_rules.cpp) und nicht nur unter Wine auf planet.
//
// Regelformat (Routen-Datei, `<key> = <ip:port>` je Zeile):
//
//   *-dev     = 127.0.0.1:6324     Suffix-Wildcard: alles, was auf "-dev" endet
//   *-staging = 127.0.0.1:6323
//   str:AB12  = 127.0.0.1:6322     exakter Key (z. B. GNS-Identitaet)
//   *         = 127.0.0.1:6322     Default (greift immer, niedrigste Prioritaet)
//
// Reihenfolge der Auswertung: exakt > laengster Suffix-Treffer > Default.

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace rbroute {

struct Endpoint {
  std::string ip;  // "a.b.c.d"
  uint16_t port = 0;

  std::string str() const { return ip + ":" + std::to_string(port); }
  bool operator==(const Endpoint &o) const {
    return ip == o.ip && port == o.port;
  }
};

// "a.b.c.d:port" -> Endpoint. Oktette muessen 0..255 sein, Port 1..65535.
inline bool parseEndpoint(const std::string &spec, Endpoint &out) {
  const std::size_t colon = spec.rfind(':');
  if (colon == std::string::npos) {
    return false;
  }
  const std::string ipPart = spec.substr(0, colon);
  const std::string portPart = spec.substr(colon + 1);

  if (portPart.empty() || portPart.size() > 5) {
    return false;
  }
  long port = 0;
  for (char c : portPart) {
    if (c < '0' || c > '9') {
      return false;
    }
    port = port * 10 + (c - '0');
    if (port > 65535) {
      return false;
    }
  }
  if (port == 0) {
    return false;
  }

  std::string octet;
  int parts = 0;
  for (std::size_t i = 0; i <= ipPart.size(); ++i) {
    if (i == ipPart.size() || ipPart[i] == '.') {
      if (octet.empty() || octet.size() > 3) {
        return false;
      }
      long value = 0;
      for (char c : octet) {
        if (c < '0' || c > '9') {
          return false;
        }
        value = value * 10 + (c - '0');
      }
      if (value > 255) {
        return false;
      }
      ++parts;
      octet.clear();
    } else {
      octet.push_back(ipPart[i]);
    }
  }
  if (parts != 4) {
    return false;
  }

  out.ip = ipPart;
  out.port = static_cast<uint16_t>(port);
  return true;
}

// "a.b.c.d" -> 0xddccbbaa (Host-Byte-Reihenfolge, wie SteamNetworkingIPAddr).
inline bool ipToU32(const std::string &ip, uint32_t &out) {
  uint32_t value = 0;
  int parts = 0;
  std::string octet;
  for (std::size_t i = 0; i <= ip.size(); ++i) {
    if (i == ip.size() || ip[i] == '.') {
      if (octet.empty() || octet.size() > 3) {
        return false;
      }
      long v = 0;
      for (char c : octet) {
        if (c < '0' || c > '9') {
          return false;
        }
        v = v * 10 + (c - '0');
      }
      if (v > 255) {
        return false;
      }
      value = (value << 8) | static_cast<uint32_t>(v);
      ++parts;
      octet.clear();
    } else {
      octet.push_back(ip[i]);
    }
  }
  if (parts != 4) {
    return false;
  }
  out = value;
  return true;
}

struct Rule {
  std::string key;  // exakter Key, "*-<suffix>" oder "*"
  Endpoint target;

  bool isDefault() const { return key == "*"; }
  bool isSuffix() const { return key.size() > 1 && key[0] == '*'; }
};

class Table {
 public:
  void clear() { rules_.clear(); }

  void add(const std::string &key, const Endpoint &target) {
    Rule rule;
    rule.key = key;
    rule.target = target;
    // Gleicher Key ersetzt die vorige Regel (letzte Zeile gewinnt).
    for (Rule &existing : rules_) {
      if (existing.key == key) {
        existing.target = target;
        return;
      }
    }
    rules_.push_back(rule);
  }

  std::size_t size() const { return rules_.size(); }
  const std::vector<Rule> &rules() const { return rules_; }

  const Rule *defaultRule() const {
    for (const Rule &r : rules_) {
      if (r.isDefault()) {
        return &r;
      }
    }
    return nullptr;
  }

  // exakt > laengster Suffix > Default. nullptr, wenn nichts passt.
  const Rule *match(const std::string &key) const {
    for (const Rule &r : rules_) {
      if (r.key == key) {
        return &r;
      }
    }
    const Rule *best = nullptr;
    std::size_t bestLen = 0;
    for (const Rule &r : rules_) {
      if (!r.isSuffix()) {
        continue;
      }
      const std::string suffix = r.key.substr(1);  // "*" -> "", siehe isDefault
      if (suffix.empty() || key.size() < suffix.size()) {
        continue;
      }
      if (key.compare(key.size() - suffix.size(), suffix.size(), suffix) == 0 &&
          suffix.size() > bestLen) {
        bestLen = suffix.size();
        best = &r;
      }
    }
    if (best != nullptr) {
      return best;
    }
    return defaultRule();
  }

  // Wie match(), aber der Default zaehlt NICHT als Treffer. Damit laesst sich
  // fragen: "gibt es eine spezifische Regel fuer diesen Key?".
  const Rule *matchSpecific(const std::string &key) const {
    const Rule *r = match(key);
    return (r != nullptr && !r->isDefault()) ? r : nullptr;
  }

 private:
  std::vector<Rule> rules_;
};

// Druckbare ASCII-Laeufe im Payload (der Spielername steht als laengen-
// praefixierter Klartext im BINSER-Handshake, siehe #831).
inline std::vector<std::string> extractTokens(const std::string &payload,
                                              std::size_t minLen = 3,
                                              std::size_t maxLen = 64) {
  std::vector<std::string> out;
  std::string cur;
  for (unsigned char c : payload) {
    if (c >= 32 && c < 127) {
      cur.push_back(static_cast<char>(c));
      if (cur.size() >= maxLen) {
        out.push_back(cur);
        cur.clear();
      }
    } else {
      if (cur.size() >= minLen) {
        out.push_back(cur);
      }
      cur.clear();
    }
  }
  if (cur.size() >= minLen) {
    out.push_back(cur);
  }
  return out;
}

}  // namespace rbroute

#endif  // RBBATTLE_ROUTE_RULES_H
