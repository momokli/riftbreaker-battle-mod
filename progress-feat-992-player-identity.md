# Progress — Issue #992: Player-Identitaet abstrahieren (Client-Identitaet, account-ready)

Fokus-Milestone: 1.0.13 · Basis: main @ a64217d (v1.0.12) · PR-Ziel: main
Branch: `feat/992-player-identity`

## Ziel (aus Issue)
- `PlayerIdentity`-Schnittstelle/-Objekt, gefuellt aus dem Client-Protokoll
  (`steamid:<id>` bzw. anonym `str:<hex>`).
- `authorized` = passiv: verbunden + Identitaet gesendet ⇒ Relay darf routen/provisionieren.
- Session = die Verbindung (kein persistenter Account).
- Bindung: Identitaet → Session → (provisionierte) Instanz.
- Abnahme: Zwei Clients (Steam + GOG/anonym) → stabile, unterscheidbare Identitaet;
  kein Login noetig; GOG funktioniert.

## Vorpruefung (Dispatch-Gueltigkeit)
- Kein PR mit `Closes #992` vorhanden (PR-Suche leer).
- Kein `PlayerIdentity` im Code. → Feature real zu bauen. Pipeline startet.

## Ist-Stand (Recherche)
- Identitaet entsteht im C++-Relay `tools/gns-proxy/gns_probe.cpp` aus dem GNS-Connect
  (`str:<hex>` generic string, stabil pro Installation) bzw. `steamid:<id>`.
- Als **roher String** `identitaet` weitergereicht an Python-Dienste:
  `deploy/capsule/capsule_flow.py`, `capsule_service.py`, `deploy/parked/*`, `deploy/provisioner/*`.
- Host-testbare reine Logik-Module existieren bereits (`route_rules.h` + `test_route_rules.cpp`, CI).
- Routen-Key = Identitaetsstring; Prefix-Check (`str:`/`steamid:`) inline in gns_probe.cpp (Z. 965).

## Plan

### Spec (verifiziert)

- **Woher:** Identitaet entsteht im Relay aus dem GNS-Connect: `pIdentityToString("&info.m_identityRemote)` → `str:<hex>` (anonymer generic-string, stabil pro Installation/GOG) bzw. `steamid:<id>` (`gns_probe.cpp`, Z. ~817–825). Danach **roher String** `identitaet` in `Session::identity` / `SessionRecord::identity` und via HTTP-API an die Python-Dienste (`deploy/capsule/capsule_flow.py`, `capsule_service.py`).
- **Prefix-Check heute inline:** `gns_probe.cpp` Z. 965–966 (`compare(0,4,"str:")` / `compare(0,8,"steamid:")`) — genau der zu abstrahierende Punkt.
- **Muster fuer host-testbare Logik:** `route_rules.h` + `test_route_rules.cpp`, `api_util.h` + `test_api_util.cpp`; CI kompiliert sie mit `g++ -std=c++17` und fuehrt sie aus (`.github/workflows/ci.yml` Z. 195–207) — kein Wine/Spiel.
- **Semantik:** `authorized` = passiv (`connected && identity_gueltig`). Session = die Verbindung (`Session`/`SessionRecord`, kein persistenter Account). Bindung existiert bereits: Identitaet → `SessionRecord`/`g_sessions` → `g_pins`/`g_soloClaims` → provisionierte Instanz.
- **Kanonische Form:** muss stabil & unterscheidbar sein, **rückwaertskompatibel** zum heutigen Routen-Key (`str:…` / `steamid:…`), sonst brechen Routen-Datei, Pins, Solo-Claims.

### Entwurf (kein Produktionscode)

```
namespace rbident {
  enum class Kind { Steam, Generic, Account /* reserviert, account-ready */ };
  struct PlayerIdentity {
    Kind kind; std::string raw; std::string canonical; bool valid;
  };
  // steamid:<id>  -> Steam,   canonical "steamid:<id>"
  // str:<hex>     -> Generic, canonical "str:<hex>" (hex lowercase normalisiert)
  // sonst/leer/kein Prefix -> invalid (valid=false)
  bool parsePlayerIdentity(const std::string &raw, PlayerIdentity &out);
  bool isIdentityLike(const std::string &raw);      // ersetzt den Inline-Check
  bool isAuthorized(bool connected, const PlayerIdentity &id);
}
```

`canonical` bleibt fuer Steam/Generic **identisch zu `raw`** (nur Hex-Normalisierung), damit Routen/Pins/Claims kompatibel bleiben. Unterscheidbarkeit: `kind` trennt Steam vs. anonym; canonical traegt weiterhin den Kind-Prefix. `Account` ist nur ein reservierter Kind-Wert + Parser-Zweig (`account:<id>`) als Andockpunkt — keine echte Account-Logik.

### Betroffene Dateien

- `tools/gns-proxy/player_identity.h` (NEU — reine Logik, ohne Win32)
- `tools/gns-proxy/test_player_identity.cpp` (NEU — Host-Test, red→green)
- `tools/gns-proxy/gns_probe.cpp` (Inline-Prefix-Check Z. 965 ersetzen; `Kind` an `Session`/`SessionRecord` durchreichen)
- `.github/workflows/ci.yml` (Host-Test registrieren, analog Z. 195–207)
- `deploy/capsule/identity.py` (NEU — duenne Python-Spiegel-Normalisierung fuer `identitaet`)
- `deploy/capsule/test_identity.py` (NEU — unittest, laeuft in ci.yml)
- `deploy/capsule/capsule_flow.py`, `deploy/capsule/capsule_service.py` (canonical statt roher String an der Schnittstelle)

### User Stories (in Reihenfolge)

1. **PlayerIdentity-Wertobjekt + Parser (rein, host-testbar)**
   - Neuer Header `player_identity.h` in `namespace rbident`: `Kind`, `PlayerIdentity`, `parsePlayerIdentity`, `isIdentityLike`.
   - Test `test_player_identity.cpp` **schreibt zuerst fehlende Faelle** (rot), dann Implementierung bis gruen.
   - Akzeptanzkriterien: `steamid:123`→Steam/valid; `str:AB12`→Generic/valid; `str:ab12`→canonical `str:ab12` (Hex normalisiert); leer/unbekannt→invalid; `isIdentityLike` true fuer beide Prefixe, false fuer Spielnamen.
   - Betroffene Dateien: `tools/gns-proxy/player_identity.h`, `test_player_identity.cpp`. LoC ~120+120.

2. **Kanonische Form + passives authorized-Praedikat**
   - `canonical` stabil/unterscheidbar; `isAuthorized(connected, id)` = `connected && id.valid`.
   - Akzeptanzkriterien: Steam- und Generic-Client liefern nie denselben canonical; zwei Generic-Installationen unterscheidbar; `authorized` true ohne jede Zusatz-Bestaetigung, false wenn nicht verbunden/invalid.
   - Betroffene Dateien: `player_identity.h`, `test_player_identity.cpp`. LoC ~40.

3. **Relay nutzt die Abstraktion (Inline-Check raus)**
   - Z. 965 durch `rbident::isIdentityLike` ersetzen; bei Connect `PlayerIdentity` parsen und `kind` in `Session`/`SessionRecord` + `SessionInfo` fuehren; Routen-/Session-Key bleibt `canonical` (kompatibel).
   - Akzeptanzkriterien: bestehende Routen `/pins`/Solo-Claims unveraendert; `steamid`- und `str:`-Sessions laufen wie heute; kein Wine-Regress.
   - Betroffene Dateien: `gns_probe.cpp`. LoC ~40.

4. **CI-Host-Test registrieren (rot→gruen belegt)**
   - Schritt in `ci.yml` analog route_rules/api_util; Guard `needs.changes.outputs.test == 'true'`.
   - Akzeptanzkriterien: CI faellt mit Parser ohne Neu-Gueltigkeit, gruen nach Story 1+2; kein Wine noetig.
   - Betroffene Dateien: `.github/workflows/ci.yml`. LoC ~6.

5. **Python-Seam: Identitaet kanonisch durchreichen (account-ready)**
   - `deploy/capsule/identity.py` spiegelt Parser/Normalisierung fuer die Python-Schnittstelle; `capsule_flow.py`/`capsule_service.py` nutzen canonical.
   - Akzeptanzkriterien: `str:AB12` und `steamid:7` liefern unterschiedliche canonical; `test_identity.py` deckt dieselben Faelle wie der C++-Test; `test_capsule_flow`/`test_capsule_service` unveraendert gruen.
   - Betroffene Dateien: `deploy/capsule/identity.py`, `test_identity.py`, `capsule_flow.py`, `capsule_service.py`. LoC ~90.

6. **Account-ready Andockpunkt dokumentieren (kein Account-Code)**
   - `Kind::Account` + `account:<id>`-Zweig im Parser (nur Parse+Test), Kommentar zur Erweiterung Identitaet→Session→Instanz.
   - Akzeptanzkriterien: `account:<id>` parst als `Account`/valid, fliesst aber in keine Provisionierung; keine echten Accounts.
   - Betroffene Dateien: `player_identity.h`, `test_player_identity.cpp`, `identity.py`. LoC ~25.

### Implementierungs-Reihenfolge
1 → 2 → 3 → 4 (C++ Kern + CI, rot→gruen) → 5 (Python-Seam) → 6 (Account-Andockpunkt).

### Teststrategie
- **Host/CI (primär, red→green):** `test_player_identity.cpp` (g++, kein Wine) + `test_identity.py`; Regression: bestehende `test_route_rules`/`test_api_util`/`test_capsule_*` bleiben gruen.
- **Live-Smoke (Tester-Stage, planet):** zwei Clients — Steam (`steamid:…`) und GOG/anonym (`str:…`) — gleichzeitig verbinden; Relay-Log `client-identitaet:` zeigt zwei stabile, unterscheidbare Keys; Routing/Provisionierung ohne Login; zweiter Connect desselben Clients → gleicher canonical.

### Abnahmekriterien (Issue)
- Zwei Clients (Steam + GOG/anonym) → stabile, unterscheidbare Identitaet. ✔ Stories 1–3, Live-Smoke.
- Kein Login noetig; GOG funktioniert. ✔ passives `authorized`, canonical rueckwaertskompatibel.
- Host-Test (red→green) belegt die Abstraktion. ✔ Stories 1–2, 4.

### Risiken
- **Kanonische Form ≠ heutiger Routen-Key** → Routen/Pins/Claims brechen. Gegenmassnahme: canonical fuer Steam/Generic byte-gleich zu raw halten, nur Hex lowercase.
- **Generic `str:<hex>` ist „stabil pro Installation", nicht global eindeutig** → Kollisionen zweier identischer Installationen moeglich; `kind`/canonical garantieren nur Unterscheidbarkeit unterschiedlicher Clients (im Scope). Dokumentieren.
- **Dual-Implementierung C++/Python** laeuft auseinander. Gegenmassnahme: identische Testfall-Tabelle in beiden Tests.
- **`steamid:`-Praefix-Annahmen** koennen je GNS-Version variieren → Parser tolerant halten (nur Prefix+Rest-Length), unbekannte Formen = invalid statt Crash.

## Baseline

Branch: `feat/992-player-identity` von `origin/main` (main @ `a64217db0786ad37a5951063256c625c3c4fe88f`, v1.0.12).
Alle Baselines GRUEN vor Feature-Beginn:

- **C++ Host-Tests (CI, g++ -std=c++17 -Wall -Wextra):**
  - `test_route_rules.cpp` → `test_route_rules: 44 Checks OK` (rc=0)
  - `test_api_util.cpp` → `test_api_util: 214 Checks OK` (rc=0)
- **Python-Tests `deploy/capsule/`:** `python3 -m unittest discover -s deploy/capsule -p 'test_*.py'`
  → `Ran 56 tests ... OK` (rc=0). (`pytest` ist auf planet nicht installiert; CI nutzt unittest.)

Setup bereit fuer Stage 3 (Developer).

## Stages
- [x] 1 planner
- [x] 2 setup
- [x] 3 developer
- [ ] 4 verifier
- [ ] 5 tester
- [ ] 6 developer (PR)
- [ ] 7 reviewer

## Log
- 2026-09-29: Dispatch gueltig, Pipeline gestartet.
- 2026-09-29 (Stage 3, Developer):
  - Stories 1–6 implementiert. Red→green belegt: `test_player_identity.cpp`
    zuerst rot (Header fehlt, rc=1), nach `player_identity.h` gruen
    (35 Checks OK, rc=0).
  - NEU `tools/gns-proxy/player_identity.h` (rbident::Kind/PlayerIdentity/
    parsePlayerIdentity/isIdentityLike/isAuthorized; canonical Steam/Account
    byte-gleich raw, Generic Hex-lowercase).
  - NEU `tools/gns-proxy/test_player_identity.cpp` (35 Checks).
  - `tools/gns-proxy/gns_probe.cpp`: Inline-Prefix-Check -> `rbident::isIdentityLike`;
    Connect parst `PlayerIdentity` (kind an Session/SessionRecord/SessionInfo,
    `kind` im /sessions-JSON); Session-/Routen-Key = canonical. `-fsyntax-only`
    mit x86_64-w64-mingw32-g++ rc=0.
  - `.github/workflows/ci.yml`: Host-Test #992 + deploy/capsule-unittest-Suite
    registriert; changes-Gate um `deploy/capsule/` erweitert.
  - NEU `deploy/capsule/identity.py` + `test_identity.py` (identische
    Testfall-Tabelle, 11 Tests). `capsule_flow.open` reicht `identitaet`
    kanonisch durch; `test_capsule_flow`-Assertion auf `str:ab12` angepasst
    (Kanonisierung).
  - Regression gruen: test_route_rules 44, test_api_util 214,
    deploy/capsule 67 Tests OK.
  - Account (`account:<id>`) nur Parse+Test, keine Provisionierungslogik.

## Verify (Stage 4, Verifier)

**Verdict: PASS** — Diff `origin/main...origin/feat/992-player-identity` (9 Dateien, +650/−7).

### Belege (selbst ausgefuehrt)
- C++: `g++ -std=c++17 -Wall -Wextra -O1` + Run:
  - `test_player_identity.cpp` → `35 Checks OK` (rc=0)
  - `test_route_rules.cpp` → `44 Checks OK` (rc=0)
  - `test_api_util.cpp` → `214 Checks OK` (rc=0)
- Python: `python3 -m unittest discover -s deploy/capsule -p 'test_*.py'` → `Ran 67 tests ... OK` (rc=0)
- Relay kompiliert: `x86_64-w64-mingw32-g++ -std=c++17 -Wall -Wextra -fsyntax-only -Iinclude gns_probe.cpp` → rc=0 (nur 23 vorbestehende `-Wcast-function-type`-Warnungen).
- CI-Diff konsistent zum Muster (`if: needs.changes.outputs.test == 'true'`, gleiche Compiler-Flags, unittest-discover); changes-Gate um `deploy/capsule/` erweitert.

### Soll-Abgleich
- `PlayerIdentity`-Abstraktion (Kind steam|generic|account, raw, canonical, valid) ✔
- canonical rueckwaertskompatibel: Steam/Account byte-gleich `raw`, Generic nur Hex-lowercase → Routen-/Pin-/Claim-Key (`str:…`/`steamid:…`) unveraendert ✔
- `isAuthorized` passiv (`connected && valid`) ✔
- Inline-Prefix-Check in `gns_probe.cpp` Z.965 ersetzt durch `rbident::isIdentityLike`; `account:` zusaetzlich ausgeschlossen ✔
- Kein toter Arm (`identityKindName` deckt alle Kind-Werte; Parser erreicht alle Zweige) ✔
- Unbekannte Formen → `valid=false` statt Crash; `canonicalize(None)`→None, unbekannt → raw durchgereicht ✔
- Session-/Routen-Key = `canonical`; `rec.kind`/`kindName` in `/sessions`-JSON ✔

### Anmerkungen (nicht blockierend)
- Keine Secrets/Debug-Prints im Diff; keine Injection (Logs `%s`, Identitaet lokal formatiert).
- Neu-Dateien ohne Trailing-Newline (Stil, kein Defekt).
- `capsule_service.py` unveraendert: reicht `identitaet` an `coordinator.open` durch, das kanonisiert — Semantik ok.

## Test (Stage 5, Tester)

**Verdict: PASS** — Abstraktion integriert, Issue-Abnahme erfuellt, Regression gruen.

### Belege (selbst ausgefuehrt, Branch `feat/992-player-identity`)

- **C++ (g++ -std=c++17 -Wall -Wextra -O1, host, kein Wine):**
  - `test_player_identity` → `35 Checks OK` (rc=0)
  - `test_route_rules` → `44 Checks OK` (rc=0)
  - `test_api_util` → `214 Checks OK` (rc=0)
- **Python (`python3 -m unittest discover -s deploy/capsule -p 'test_*.py'`):**
  → `Ran 67 tests ... OK` (rc=0) — inkl. `test_identity`, `test_capsule_flow`, `test_capsule_service`.
  Regression: Baseline war 56 Tests; +11 sind die neuen `test_identity`-Faelle.

### E2E-Smoke — Host/Modul-Integration (kein Live-Relay/Spiel)

**Ersatz begruendet:** Der laufende Relay (`gns_probe.exe`, Wine, API `127.0.0.1:9200`)
ist der **installierte Build** unter `/opt/gns-relay`, NICHT der Branch-Code — ein Live-Smoke
wuerde die #992-Abstraktion nicht treffen. Zwei echte Spiel-Clients (Steam + GOG) sind hier
nicht startbar. Deshalb Modul-Integrationstest der Schnittstelle mit den vorhandenen Fakes
(`test_capsule_flow.FakeParked/FakeBridge/FakeCycle`), Kette
`rohe Identitaet -> canonicalize -> capsule_flow.open -> Session-/Routen-Key`.

Ergebnis `SMOKE PASS` (14/14 Checks, rc=0):
- `steamid:<id>` → `Kind.STEAM/valid`; `str:<hex>` → `Kind.GENERIC/valid`; Spielname → nicht identity-like.
- canonical **unterscheidbar** (Steam ≠ GOG); Steam-canonical byte-gleich `raw` (rueckwaertskompatibel);
  GOG-canonical = `str:` + lowercase-hex; canonical **idempotent** (2. Connect gleicher Client → gleich).
- `is_authorized(True, id)` = True **ohne Login**; False wenn nicht verbunden.
- `capsule_flow.open(identitaet=…)` speichert den canonical als Session-/Routen-Key; A/B unterscheidbar;
  zweiter Connect desselben Clients → identischer Key.

### Abnahme aus Issue

- ✅ Zwei Clients (Steam + GOG/anonym) → stabile, unterscheidbare canonical, kein Login.
- ✅ Zweiter Connect desselben Clients → gleiche canonical.

### Regress

- ✅ Keine bestehenden Tests rot; Suite 67/67 OK; Relay `-fsyntax-only` rc=0 (Verifier).

Keine neuen Testdateien noetig (Module-Smoke ad hoc unter `/tmp` ausgefuehrt, nicht committet).
