#ifndef RBBATTLE_PLAYER_IDENTITY_H
#define RBBATTLE_PLAYER_IDENTITY_H

// Client-Identitaet abstrahiert (Issue #992).
//
// Bewusst OHNE Win32/GNS-Abhaengigkeiten: reine Logik, laeuft als Host-Test in
// der CI (siehe test_player_identity.cpp) und nicht nur unter Wine auf planet.
//
// Woher: der GNS-Entry-Relay (gns_probe.cpp) bekommt die Identitaet mit dem
// Connect (`pIdentityToString(&info.m_identityRemote, …)`):
//
//   steamid:<id>  -> Kind::Steam   (Steam-Client, stabile SteamID)
//   str:<hex>     -> Kind::Generic (anonymer generic-string, stabil pro
//                                   Installation — z. B. GOG)
//   account:<id>  -> Kind::Account (reservierter Andockpunkt — KEINE echte
//                                   Account-/Provisionierungslogik)
//
// `authorized` ist passiv: verbunden + gueltige Identitaet ⇒ der Relay darf
// routen. Kein Login noetig. Eine Session = die Verbindung (kein persistenter
// Account); die Bindung Identitaet -> Session -> (provisionierte) Instanz
// entsteht im Relay ueber Session/SessionRecord und g_pins/g_soloClaims.
//
// Kompatibilitaet (bindend): `canonical` ist fuer Steam/Generic/Account
// byte-gleich zu `raw` — nur Hex wird lowercase normalisiert. Damit bleibt der
// heutige Routen-/Pin-/Claim-Key (`str:…` / `steamid:…`) unveraendert.

#include <string>

namespace rbident {

enum class Kind {
  Steam,    // steamid:<id>
  Generic,  // str:<hex> (anonym, stabil pro Installation)
  Account,  // account:<id> (reserviert, account-ready)
};

struct PlayerIdentity {
  Kind kind = Kind::Generic;
  std::string raw;        // exakt wie empfangen
  std::string canonical;  // normalisierter Routen-/Pin-/Claim-Key
  bool valid = false;     // false = kein bekanntes Identitaetsformat
};

// Erkennt die bekannten Identitaets-Praefixe (`steamid:`/`str:`/`account:`).
// Ersetzt den frueheren Inline-Check in gns_probe.cpp (Spielnamen -> false).
inline bool isIdentityLike(const std::string &raw) {
  return raw.compare(0, 8, "steamid:") == 0 ||
         raw.compare(0, 4, "str:") == 0 ||
         raw.compare(0, 8, "account:") == 0;
}

// Zerlegt `raw` in eine PlayerIdentity. Ruft true genau dann, wenn `out.valid`.
//
// Tolerant gehalten: nur Praefix + nicht-leerer Rest werden geprueft, der Rest
// wird NICHT inhaltlich validiert (unbekannte Formen = invalid statt Crash).
// Fuer Kind::Generic wird der Rest lowercase normalisiert (Hex), damit
// `str:AB12` und `str:ab12` denselben canonical ergeben.
inline bool parsePlayerIdentity(const std::string &raw, PlayerIdentity &out) {
  out = PlayerIdentity{};
  out.raw = raw;

  if (raw.compare(0, 8, "steamid:") == 0 && raw.size() > 8) {
    out.kind = Kind::Steam;
    out.canonical = raw;  // byte-gleich (SteamIDs sind Ziffern)
    out.valid = true;
    return true;
  }
  if (raw.compare(0, 4, "str:") == 0 && raw.size() > 4) {
    out.kind = Kind::Generic;
    out.canonical = "str:";
    // Nur ASCII-Hex-Buchstaben lowercase (Ziffern/Praefix unberuehrt).
    for (std::size_t i = 4; i < raw.size(); ++i) {
      char c = raw[i];
      if (c >= 'A' && c <= 'F') {
        c = static_cast<char>(c - 'A' + 'a');
      }
      out.canonical.push_back(c);
    }
    out.valid = true;
    return true;
  }
  if (raw.compare(0, 8, "account:") == 0 && raw.size() > 8) {
    out.kind = Kind::Account;
    out.canonical = raw;  // Andockpunkt; keine Account-Logik hier
    out.valid = true;
    return true;
  }

  out.valid = false;
  return false;
}

// Passives Authorisierungs-Praedikat: verbunden + gueltige Identitaet.
// Keine Zusatz-Bestaetigung, kein Login (Issue #992).
inline bool isAuthorized(bool connected, const PlayerIdentity &id) {
  return connected && id.valid;
}

}  // namespace rbident

#endif  // RBBATTLE_PLAYER_IDENTITY_H