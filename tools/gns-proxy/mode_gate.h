#ifndef RBBATTLE_MODE_GATE_H
#define RBBATTLE_MODE_GATE_H

// Modus-Gate des GNS-Entry-Relays (Issue #1093).
//
// Bewusst OHNE Win32/Socket-Abhaengigkeiten: reine, host-testbare Logik
// (siehe test_mode_gate.cpp). Der Relay (gns_probe.cpp) liest den Modus aus
// `--mode solo|versus` bzw. der Env-Variable `RBB_MODE` und benutzt diese
// Helfer, um (a) nur den passenden Pfad in der Lobby-UI auszuliefern und
// (b) den nicht passenden Server-Pfad hart abzuweisen (kein rein kosmetisches
// Gate).
//
// Vokabular / Mapping:
//   extern (Produkt + Flag) : "solo" | "versus"
//   Attack-Cycle-Sidecar    : "solo" | "vs"   (RBB_MODE der Sidecar)
//   Default (nicht gesetzt) : beide Pfade     -> Mode::Both = heutiges
//   Verhalten
//
// `versus` und `vs` sind derselbe Pfad; der Parser akzeptiert beide (und
// behandelt "both"/leer als Default), damit Lobby-Sprache und Sidecar-Sprache
// nicht auseinanderlaufen.

#include <cctype>
#include <string>

namespace rbmode {

// Welcher Produkt-Pfad ist freigeschaltet?
enum class Mode {
  Both,   // Default: beide Pfade (heutiges Verhalten, keine Aenderung)
  Solo,   // nur der Solo-Pfad (POST /solo)
  Versus, // nur der Versus-/Queue-Pfad (POST /queue + /queue/*)
};

// Externe Schreibweise (Flag/Env) -> Mode. Akzeptiert (case-insensitiv, ohne
// umgebende Whitespaces): "solo", "versus"/"vs", "both"/"all"/leer (=Both).
// Rueckgabe false NUR bei unbekanntem Wert -> der Aufrufer wird fail-loud.
inline bool parseMode(const std::string &raw, Mode &out) {
  std::string v;
  for (std::size_t i = 0; i < raw.size(); ++i) {
    const char c = raw[i];
    if (c == ' ' || c == '\t' || c == '\n' || c == '\r') {
      continue;
    }
    v.push_back(static_cast<char>(std::tolower(static_cast<unsigned char>(c))));
  }
  if (v.empty() || v == "both" || v == "all") {
    out = Mode::Both;
    return true;
  }
  if (v == "solo") {
    out = Mode::Solo;
    return true;
  }
  if (v == "versus" || v == "vs") {
    out = Mode::Versus;
    return true;
  }
  return false;
}

// Kanonischer Name fuer Logs/UI/data-mode. "both" blendet nichts aus.
inline const char *modeName(Mode m) {
  switch (m) {
  case Mode::Solo:
    return "solo";
  case Mode::Versus:
    return "versus";
  case Mode::Both:
    return "both";
  }
  return "both";
}

// Der Solo-Pfad: genau `POST /solo` (Claim/Join einer Solo-Instanz).
inline bool isSoloRoute(const std::string &method, const std::string &path) {
  return method == "POST" && path == "/solo";
}

// Der Versus-Pfad: die gesamte `/queue`-Familie (einreihen/verlassen/finish/
// rematch/status). Prefix-Match, damit neue /queue/*-Routen nicht durchfallen.
inline bool isVersusRoute(const std::string &, const std::string &path) {
  return path == "/queue" || path.compare(0, 7, "/queue/") == 0;
}

// Serverseitige Durchsetzung: darf `method path` im Modus `m` bedient werden?
// Both -> immer; Solo -> nur ohne /queue; Versus -> nur ohne /solo. Alle
// uebrigen (geteilten) Routen wie /route, /ready, /referee/*, /sessions bleiben
// unberuehrt, weil sie beiden Pfaden dienen.
inline bool routeAllowedInMode(Mode m, const std::string &method,
                               const std::string &path) {
  if (m == Mode::Solo) {
    return !isVersusRoute(method, path);
  }
  if (m == Mode::Versus) {
    return !isSoloRoute(method, path);
  }
  return true;
}

} // namespace rbmode

#endif // RBBATTLE_MODE_GATE_H
