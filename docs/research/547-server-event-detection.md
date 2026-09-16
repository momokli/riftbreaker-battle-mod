# Server-Mod Event-Erkennung (Spike B1) — Issue #547

**Issue:** #547 · **Typ:** Research-Spike (docs-only, KEIN Feature-Implement, KEIN Merge)
**Stand:** 2026-09-16 · **Spiel-Build:** 2.0.58485
**Repo-Stand Referenz:** `c7623d2a526f248d98cd7abb70ab9724daccb9d9`, Mod-Blob `mod/lua/rbbattle_autoexec.lua` = `79949035aa8b879b3b3fb675204b007cb87b25bc`
**Timebox:** 0,5 Tag · **Deliverable:** dieses Dokument (PR `Refs #547`, kein Merge — Review/Merge: rift-pr-gate)

> Erzeugt aus dem Research-Handoff von `researcher` + `web-researcher`, dem Spike-Plan von
> `plan-builder` und dem Critic-Verlauf (Urteil Iteration 2: PASS). Die Engine-Lua-Zitate beziehen
> sich auf `/home/momo/rb-game/lua-src/lua/` (GOG-Extrakt 2.0.58485).

## Ergebnis in einem Satz

Alle fünf im Issue genannten Event-Namen existieren **nicht**; die realen Kandidaten
(`BuildingBuildEvent`, `BuildingBuildEndEvent`, `StartBuildingEvent`) sind statisch belegt, aber ob
sie auf einem **headless Dedi** bei einer **Vanilla-Client**-Aktion feuern, ist **unbelegt** und
bleibt als Player-Test offen — genau das ist der Gegenstand des Spike-Plans unten.

---

## Recherche (Beleglage: interne Quellen + Web)

## R1. Kernergebnis: alle Issue-Kandidaten sind falsch benannt

**Beide Quellenstränge unabhängig übereinstimmend:** Keiner der vier/fünf Kandidaten aus #547
existiert unter dem angegebenen Namen — weder im Wiki noch in der Vanilla-Lua-Source.

| Issue-Kandidat | Existenz | Realer Name (belegt) |
|---|---|---|
| `BuildingCompletedEvent` | **NEIN** | `BuildingBuildEndEvent` / `BuildingBuildEvent` |
| `BuildingPlacedEvent` | **NEIN** | `StartBuildingEvent` (Start) / `BuildingBuildEvent` (Ende) |
| `ResourceChangeEvent` | Reflection-Doc ja, **0 Firing/Register** | — |
| `ResourceObtainedEvent` | Reflection-Doc ja, **0 Firing/Register** | — |
| `ResearchUnlockedEvent` | Reflection-Doc ja, **0 Firing/Register**; **kein Research-Event im gesamten Lua-Baum** | — (Vanilla nutzt Polling) |

Belege: `grep` 0 Treffer in `/home/momo/rb-game/lua-src/lua/`; WIKI `modding-files/lua-files/autoexec.md`;
Fandom `Modding:Events`.

## R2. `RegisterGlobalEventHandler` — Signatur/Kontext

- **Signatur:** `RegisterGlobalEventHandler("EventName", function(arg) … end)` — belegt via WIKI `autoexec.md:26`; Web: einziges offizielles Beispiel `RegisterGlobalEventHandler("PlayerCreatedEvent", function(arg) ... end)` (Fandom Autoexec).
- **Keine formale Parameter-/Kontext-Doku** — weder Wiki noch Web sagen, ob Client, Dedi oder beide.
- **Mod-only:** in Vanilla-Lua **0×** vorhanden (Engine-Binding für `*_autoexec.lua`, ausgeführt „on map creation").
- **Mechanik in-game verifiziert:** `PlayerCreatedEvent` feuert zuverlässig (MOD `docs/findings.md:61` #14).
- **Kein `UnregisterGlobalEventHandler` dokumentiert** → Mod-Konvention: Guard-Flags (`api-deep-dive.md:174`).
- **Getter:** `evt:GetX()`; WIKI `misc/event-class.md`: „not every event function works for every event type" → Getter pro Event nur per Reflection/Player-Test sicher.
- **Server-Kontext existiert grundsätzlich:** Vanilla-Lua nutzt Globals `is_server`/`is_client`/`is_client_only` (43/22/1 Treffer) — **kein** Beleg für Event-Feuern auf Dedi.

## R3. Reale Events (Evidenz-Tabelle)

| Event | Belegt | Quelle | Payload | Player-ID? | Feuert? |
|---|---|---|---|---|---|
| `BuildingBuildEvent` | Wiki + Quelle | `buildings/building_base.lua:35` (Handler), `:444-446` (**QueueEvent**) | Entity, Upgraded, BuildingName, BuildingType — Source übergibt **5 Args inkl. `owner`**, Wiki listet 4 | **`owner`-Arg offen**; Getter `GetOwner`/`GetPlayerId` in Wiki gelistet | **ja (Vanilla feuert)** |
| `BuildingBuildEndEvent` | Wiki + Quelle | `building_base.lua:36`, `:1049-1050` | Entity, Upgraded, BuildingName, BuildingType | nein direkt → Entity-Lookup (`PlayerService:GetPlayerForEntity`, `building_base.lua:39`) | als Handler registriert |
| `StartBuildingEvent` | Wiki + Quelle | `misc/ghost_building.lua:24`, `buildings/building_creator.lua:112` (QueueEvent) | Entity, CubeEnt, **PlayerId**, EndCubeEnt, Upgrading, Effects | **ja `PlayerId`** — aber **kein** Blueprint-/Name | ja |
| `BuildingStartEvent` | Wiki + Quelle | `building_creator.lua:112` (8 Fundstellen) | … INVALID_ID, owner, … | `owner` | ja |
| `BuildingSellEvent` | Wiki + Quelle | `building_base.lua` | GetBlueprint, GetEntity | — | ja |
| `ResourceDiscoveredEvent` | Wiki + Quelle | `graph/logic/logic_wait_on_resource_discovered.lua` | Handler mit event-Objekt, Felder nicht einzeln dokumentiert | — | ja |
| `ResourceChangeEvent` / `ResourceObtainedEvent` / `ResearchUnlockedEvent` / `AddedToResearchEvent` | nur Reflection | WIKI `game-reflection/events/*.md` (388 Dateien) | s. o. | z. T. `TeamId` | **0 Firing in LUA → unbelegt** |
| `HourEvent` (Fallback) | in-game verifiziert | MOD Baustein 05 `:482`; `docs/research/api-deep-dive.md:72` | Entity, Hour | nein (globaler Takt) | ja |
| `PlayerCreatedEvent` | in-game verifiziert | `docs/findings.md:61` | Entity, PlayerId, PlayerInfo | **ja** | ja |
| `LuaGlobalEvent` | vanilla, 64× gefeuert | `missions/mission_base.lua:282`, `logic_event_send.lua:17` | argName1/2/3 | nein direkt | ja |

**Event-Katalog Vanilla:** 118 eindeutige `RegisterHandler`-Namen; reale `QueueEvent`-Firings u. a.
`BuildingBuildEvent` (2×), `BuildBuildingRequest` (8×), `OpenResearchRequest` (2×), `SellBuildingRequest` (6×).

## R4. Research/Forschen: kein Event — Polling ist der Vanilla-Weg

Kein Research-Completion-Event im gesamten Lua-Baum. Engine pollt per FSM-State (`interval=1`):
`PlayerService:IsResearchUnlocked(PlayerService:GetLeadingPlayer(), researchId)`
(`graph/logic/logic_wait_on_research_finished.lua`, `logic_if_research_finished.lua`).
Weitere Service-APIs: `PlayerService:GetResourceAmount`, `PlayerService:UnlockResearch`,
`BuildingService:CanBuildBuilding`, `BuildingService:CanAffordBuilding`.
→ **Polling über Services ist der in der Vanilla-Codebasis etablierte Erkennungsweg.**

## R5. Mod-Ist-Stand

- `mod/lua/rbbattle_autoexec.lua` (64 Zeilen, v0.34.3): registriert **keinen** `RegisterGlobalEventHandler`.
  Enthält `[RBBATTLE]`-Log-Helfer, **ein** `ConsoleService:RegisterCommand("rb_poc_send")` → `event=poc_send amount=10 resource=mythium`,
  sowie `Log("event=mod_load …")` als Lebenszeichen. Sandbox-Kommentar: kein `os.getenv`, kein `io.open`.
- Manifest `mod/{96745BE8-…}.manifest`: nur Lua-Mod, `version 0.34.3`.
- **Handler-Pattern existiert bereits als Baustein:** `bausteine/05-economy-loop/.../rbbattle_05_economy_autoexec.lua:479-483`
  (`pcall(function() RegisterGlobalEventHandler("EntityKilledEvent", …) RegisterGlobalEventHandler("HourEvent", …) end)`)
  — Status `bausteine/README.md:21`: „Code fertig — In-Game-Test offen".
- **Reporting-Pattern (Prod-belegt):** `[RBBATTLE] event=<name> …` via `LogService:Log`; Ernte per `docker logs … | grep -a RBBATTLE`
  (`docs/PLAYTEST_1.0.md:125,393-401`, `docs/DEPLOYMENT.md:305`). Mod lädt headless und loggt ins Dedi-Log (`docs/INGRESS_IO.md:11,173-175`).
- Sandbox: kein File-I/O (`io.open` crasht hart, `findings.md:16` #16), kein HTTP/Socket/`os.getenv`.

## R6. Headless-Dedi-Verhalten (Kernfrage): UNBEKANNT

**Beide Stränge: keine Quelle — offiziell oder Community — belegt oder widerlegt, dass Building-/Resource-Events
auf einem headless Dedi mit Vanilla-Client feuern.**

- Fandom `Modding:Events`: „The Riftbreaker back-end fires most of the events" — Event-Feuern ist an Backend-Interaktionen gebunden (Beispiel: Harvester-Drone feuert nie `BuildingSellEvent`).
- Multiplayer-Wiki sagt „headless dedicated server possibly coming in future"; Steam-Discussions nennen Dedi „still very much in beta"; Steam-Announcement bewirbt „full headless mode" → **Status widersprüchlich**.
- Community: „Mods work in coop but you need to have the same mods installed" — ob ein **reiner Server-Mod mit Vanilla-Clients** joinbar ist, ist **nicht belegt** (kein Vanilla-Client-Mod-Szenario gefunden).
- Keine Bugreports gefunden, die „Events feuern auf Dedi nicht" behaupten — aber auch keine, die das Gegenteil belegen.

## R7. Lücken / Risiken

1. Issue-Namen sind falsch → Plan/Issue müssen auf `BuildingBuildEvent` / `BuildingBuildEndEvent` / `StartBuildingEvent` korrigiert werden.
2. **Payload-Mismatch:** Wiki listet 4 Felder für `BuildingBuildEvent`, Source übergibt 5 Args inkl. `owner` — ob `owner` als Feld durchschlägt, ist offen (Reflection/Player-Test).
3. **Kein einzelnes Event liefert „Player X hat Y gebaut":** `BuildingBuildEvent` hat Name+Typ aber keine PlayerId; `StartBuildingEvent` hat `PlayerId` aber keinen Namen → Kombination oder Entity-Lookup nötig.
4. Feuern von Resource-/Research-Kandidaten unbelegt (0 Vanilla-Firing); Reflexion ≠ Broadcast-Reichweite.
5. Client/Server-Kontext statisch nicht bestimmbar (keine `IsServer`-Verzweigung im Lua).
6. **Dead Refs:** `clanker-gh issue view 543` und `546` → „Could not resolve to an Issue" (existieren nicht). Der „revertierte Workaround #543→#546" ist über Issue-Nummern **nicht** belegbar. #518 existiert und passt als Kontext.
7. Lifecycle/Doppel-Registrierung bei Mod-Reload unbelegt (kein Unregister).
8. `memory_search` im researcher-Kontext **ausgefallen** (`index metadata is missing`) → Memory-Vorwissen nicht abrufbar.
9. Wiki-Eventliste laut Autor „likely uncomplete" → Negativ-Treffer im Wiki allein kein Beweis (daher zusätzlich 0-Treffer-Check in der Spielquelle).
10. Keine offizielle Exor-Doku zum Dedicated-Server-Modding gefunden.

---

## Test-Split (verbindlich)

**OHNE Player prüfbar → host-/statisch:**
- Exakte Event-Namen + Payload-Felder (`BuildingBuildEvent`, `BuildingBuildEndEvent`, `StartBuildingEvent`).
- `RegisterGlobalEventHandler(name, fn)`-Aufrufkonvention + Autoexec-Kontext.
- Dass `BuildingBuildEvent` im Vanilla-Lua tatsächlich gefeuert wird (`building_base.lua:444`).
- Registrierungs-/Log-Muster `[RBBATTLE] event=…` und headless-Load des Mods (Dedi-Log).
- Player-Ableitung aus Entity (`PlayerService:GetPlayerForEntity`, `EntityService:GetTeam`).

**NUR mit Player prüfbar → OFFENER PUNKT (Momo/Matheo), NICHT als erledigt markieren:**
- Feuert `BuildingBuildEvent` / `StartBuildingEvent` auf dem **headless Dedi**, wenn ein entfernter **Vanilla-Client** baut/kauft?
- Existieren die `evt:Get*`-Getter für das jeweilige Event real?
- Feuern `ResourceChangeEvent` / `ResourceObtainedEvent` / `ResearchUnlockedEvent` überhaupt global?
- Sieht `RegisterGlobalEventHandler` auf dem Dedi dieselbe Event-Quelle wie im Client-Spiel?
- Ist ein reiner Server-Mod mit Vanilla-Clients überhaupt joinbar (Mod-Sync)?
- Frequenz/Reihenfolge von `HourEvent` (Fallback) und Mehrfach-Feuern.
- Ob `owner` (5. Arg) am `BuildingBuildEvent` durchschlägt.

## Plan — Spike-Vorgehen

### Plan: Spike B1 (Detailfassung)

_Dieser Abschnitt gibt den Spike-Plan in der überarbeiteten Fassung wieder — nach Einarbeitung der
5 Critic-Einwände + 3 Hinweisen (Critic-Urteil Iteration 2: PASS, s. Annex). Übernommen am 2026-09-16._

## Kontext

Der Issue-Kandidatensatz aus #547 (`BuildingCompletedEvent`, `BuildingPlacedEvent`, `ResourceChangeEvent`,
`ResourceObtainedEvent`, `ResearchUnlockedEvent`) ist **falsch benannt** und wurde durch R1 verworfen.
Reale, im Vanilla-Lua tatsächlich gefeuerte Events sind `BuildingBuildEvent`, `BuildingBuildEndEvent`,
`StartBuildingEvent`. Ob diese auf einem **headless Dedi** mit **Vanilla-Client** feuern, ist laut R6
unbelegt — genau das ist die Spike-Frage. Basis: Issue #547 sowie die in diesem Dokument
enthaltenen Recherche-Abschnitte R1–R7 + Test-Split. Mod-Stand: `mod/lua/rbbattle_autoexec.lua`
v0.34.3, registriert keinen GlobalEvent-Handler; das Handler-Muster liegt fertig, aber ungetestet
in Baustein 05.

**Reproduzierbarkeits-Anker (im Gateway-Checkout verifiziert am 2026-09-16):**
Referenz-Commit `c7623d2a526f248d98cd7abb70ab9724daccb9d9`, Blob-SHA1 der unveränderten Mod-Datei
`79949035aa8b879b3b3fb675204b007cb87b25bc` (`clanker-git hash-object mod/lua/rbbattle_autoexec.lua`).

**Dieser Plan ist kein Produktivcode-Plan.** Er beschreibt ein zeitboxiertes Spike-Experiment
(0,5 Tag) mit einem docs-only Deliverable. Der Spike-Code hat einen **expliziten Lebenszyklus**:
schreiben (Phase 1) → deployen/messen (Phase 2) → **zurückbauen (Phase 2 Schritt 5)** → im
Doku-Artefakt vollständig zitieren (Abschnitt 7 Punkt 3).

---

## 1. Ziel / Erfolgsausgänge / Abbruchkriterium

### Erfolgsausgänge (ein Ausgang wird erreicht; alle vier sind dokumentierbare Ergebnisse)

| Ausgang | Definition | Bewertung |
|---|---|---|
| **E1 Volltreffer** | Eine `[RBBATTLE] event=spike_*`-Log-Zeile im Dedi-Log geht auf eine **konkrete Aktion eines verbundenen Vanilla-Clients** zurück (Event-Name + mind. ein Identitätsfeld: `owner` / `playerid` / `entity`→Player-Lookup) **und** die Registrierung ist als `spike_reg … ok=true` geloggt. | Hypothese bestätigt |
| **E2 Teiltreffer** | Registrierung nachweislich erfolgreich (`spike_reg ok=true`) und ein Handler feuert mindestens einmal auf dem Dedi, aber **ohne** Player-Attribution (z. B. `BuildingBuildEvent` ohne `owner`-Durchschlag; nur `entity` vorhanden). | Hypothese teilweise bestätigt |
| **E3 Negativbefund** | Registrierung + Mod-Load + Handler-Liveness belegt, die zeitlich notierte Vanilla-Client-Aktion erfolgte, aber **kein** Spike-Build-Event feuerte. | **Valides Ergebnis**, kein Abbruch → Fallback-Bewertung (Abschnitt 4) + Negativdokumentation |
| **A0 Kein Client im Fenster** | Phase 2.2 (Server-Liveness) ist durchgeführt, aber innerhalb der Timebox stand **kein Vanilla-Client** (Momo/Matheo) zur Verfügung. | **Teilergebnis** (Server feuert / feuert nicht), Aktion ungetestet → wandert explizit als offener Punkt in die Doku (Abschnitt 9 B) |

> **Klarstellung:** E3 und A0 sind **Ergebnisse**, keine Abbrüche. Jeder der vier Ausgänge wird als
> Doku-Artefakt abgeliefert (Negativbefund ist ein valides Spike-Ergebnis).

### Abbruchkriterium (hart — nur diese drei Fälle)

Ein **Abbruch** liegt ausschließlich vor, wenn:
1. die **Timebox 0,5 Tag** (Definition: 4,0 h Arbeitszeit, s. Zeitbudget) gerissen ist, **oder**
2. der **Dedi nicht verfügbar / nicht erreichbar** ist (SSH/Container/Deploy-Pfad), **oder**
3. der **Mod headless nicht lädt** (kein `event=mod_load`-Marker) und die Ursache nicht in der
   Timebox behebbar ist.

Bei Abbruch wird trotzdem ein Doku-Artefakt mit Abbruchgrund, erreichtem Teilstand und Rohlog
(z. B. `spike_reg ok=true`, aber kein `mod_load`) abgeliefert. **E3 wird nie als Abbruch bezeichnet.**

### Explizit NICHT Erfolgskriterium
`event=poc_send` (ConsoleCommand-Weg, #379) zählt **nicht** — er ist Operator-getriggert und damit
der laut Request-Begründung verworfene Workaround. *(Die Referenzen #543/#546 existieren nicht
(R7.6); die Begründung ist geerbte Request-Begründung, nicht als Beleg verwendbar — s. Abschnitt 5.)*

---

## 2. Phasen (in Reihenfolge)

### Phase 0: Statische / host-seitige Belege (host, kein Dedi, kein Client)
- **Ziel:** Alles, was ohne Spieler und **ohne Dedi** prüfbar ist, vorab belegen — damit der
  Live-Test nur noch die offene Frage „feuert es auf dem Dedi?" klären muss.
- **Schritte:**
  1. Event-Namen + Payload-Felder gegen die Engine-Lua-Source verifizieren
     (`/home/momo/rb-game/lua-src/lua/buildings/building_base.lua:35,444`, `:36,1049`,
     `misc/ghost_building.lua:24`, `buildings/building_creator.lua:112`) — Aufwand: 0,25 h —
     **Completion:** Für `BuildingBuildEvent`, `BuildingBuildEndEvent`, `StartBuildingEvent` liegt je
     eine notierte Zeile „QueueEvent-Args aus Source" vor, inkl. Klärung, ob `owner` (5. Arg)
     im `QueueEvent`-Aufruf steht.
  2. `RegisterGlobalEventHandler`-Aufrufkonvention + Autoexec-Kontext aus WIKI/Bausteinstelle
     belegen (Wiki `autoexec.md:26`, Baustein-05-Muster `:479-483`) — Aufwand: 0,25 h —
     **Completion:** Signatur + „mod-only, `*_autoexec.lua`, on map creation" + „kein Unregister →
     Guard-Flag" stehen als Zitatbelege mit Datei:Zeile.
  3. Getter-Lage präjudizieren: welche `evt:Get*` für die drei Events laut Wiki/Reflection
     plausibel sind, welche unbekannt bleiben — Aufwand: 0,25 h —
     **Completion:** Getter-Kandidatenliste pro Event mit Kennzeichnung „belegt" / „unbekannt →
     muss im Spike per `pcall` defensiv abgefragt werden"; `EntityService:GetTeam` ist darin als
     Player-Ableitungsweg mitgeführt.
  4. **Ernte-Kommando statisch festlegen (kein Dedi-Zugriff in Phase 0)** — Aufwand: 0,1 h —
     **Completion:** Das exakte Ernte-Kommando
     `ssh planet 'docker logs riftbreaker-dedicated 2>&1 | grep -a RBBATTLE | tail -30'`
     ist aus der bestehenden Doku belegt (`docs/PLAYTEST_1.0.md:125,393-401`,
     `docs/DEPLOYMENT.md:305`) und als Kommando notiert. **Der Nachweis, dass der Marker real im
     Dedi-Log erscheint, ist NICHT Teil von Phase 0** — er ist Prüfauftrag von Phase 2.1.
- **Abhängigkeiten:** keine (Repo + Gateway-Checkout + bestehende Doku genügen).
- **Explizit außerhalb Phase 0:** jeder `ssh planet`-/Docker-Log-Zugriff (→ Phase 2.1).

### Phase 1: Spike-Handler im Mod (Code-Artefakt, deploybar)
- **Ziel:** Minimal-invasive Spike-Registrierung in `mod/lua/rbbattle_autoexec.lua`, die
  Registrierung **und** Feuern sichtbar macht — mit definiertem Lebenszyklus (Rückbau in Phase 2.5).
- **Umfang der Änderung (SPIKE — nicht Produktivcode):** Der vollständige, wörtlich zu übernehmende
  Code-Block steht als **Listing in Abschnitt 7, Punkt 3** (dort auch Checksummen/Referenz-Commit);
  hier nur die Struktur:
  - Guard-Flag `local spike_done = false` + einmalige Ausführung (kein Unregister vorhanden,
    Doppel-Registrierung bei Reload vermeiden).
  - `SpikeWrap(name, fn)` — jeder Handler zusätzlich per `pcall` gekapselt; Fehler im Handler-Body
    wird als `event=spike_err name=… err=…` geloggt (statt den Handler zu sprengen).
  - `SpikeRegister(name, fn)` — **je Event eine eigene Erfolgs-/Fehler-Log-Zeile**:
    - `RegisterGlobalEventHandler("BuildingBuildEvent", …)`
    - `RegisterGlobalEventHandler("BuildingBuildEndEvent", …)`
    - `RegisterGlobalEventHandler("StartBuildingEvent", …)`
    - `RegisterGlobalEventHandler("HourEvent", …)` (Liveness-Takt, belegt funktionierend)
    - Log danach: `event=spike_reg name=<Event> ok=true|false err=<msg>`
      → beweist, dass die Registrierung selbst nicht wirft (unterscheidet „feuert nicht" von
      „Registrierung kaputt").
  - Getter-Handling: **jeder Getter einzeln in `pcall`**, Ergebnis `"nil"` bei Fehler — keine
    unbekannte Getter-Signatur darf den Handler sprengen.
    Getter-/Lookup-Kandidaten: `evt:GetEntity()`, `evt:GetOwner()`, `evt:GetPlayerId()`,
    `evt:GetBlueprint()`, `evt:GetBuildingName()`, `evt:GetBuildingType()`, `evt:GetUpgraded()`,
    `evt:GetHour()`, `PlayerService:GetPlayerForEntity(entity)`, **`EntityService:GetTeam(entity)`**
    — jeweils `pcall`-gekapselt, kein Blindenaufruf.
  - `HourEvent`-Drosselung **mit Mechanik** (Tick-Zähler, nicht nur „knapp gehalten"):
    `local spike_hour_n = 0`, `local SPIKE_HOUR_CAP = 20`, `local spike_last_hour = nil`;
    geloggt wird nur, wenn `hour` sich ändert **oder** `spike_hour_n % 10 == 0`, und nur bis
    `spike_hour_n <= SPIKE_HOUR_CAP`. Liveness bleibt belegt (≥ 2 Zeilen mit unterschiedlichem
    `hour`), Log-Flood ist gedeckelt.
  - Log-Zeilenformat (jeweils **eine** Zeile pro Feuerung, parsebar):
    - `[RBBATTLE] event=spike_build entity=%s owner=%s player=%s bp=%s upg=%s`
    - `[RBBATTLE] event=spike_build_end entity=%s owner=%s player=%s bp=%s upg=%s`
    - `[RBBATTLE] event=spike_start_build entity=%s playerid=%s bp=%s team=%s`
    - `[RBBATTLE] event=spike_hour n=%d hour=%s` (gedrosselt per Tick-Zähler)
    - `[RBBATTLE] event=spike_err name=%s err=%s` (Handler-Body-Fehler)
  - Sonst **keine** Änderung am Mod: kein neues ConsoleCommand, keine Business-Logik, bestehende
    `rb_poc_send`-Registrierung und `event=mod_load`-Marker bleiben unangetastet.
  - Status-Kennzeichnung im Code-Kommentar: `SPIKE B1 (#547) — Versuchsaufbau, NICHT
    Produktivpfad; wird nach Phase 2 zurückgebaut (Plan Phase 2.5)`.
- **Aufwand:** 0,75 h — **Completion:** Mod-Datei enthält die Registrierungs- und Handler-Blöcke,
  jeder Getter `pcall`-gekapselt, jede Registrierung loggt ihr `ok=true|false`; Datei ist syntaktisch
  plausibel, der `mod_load`-Marker existiert weiterhin — **und** die Prüfsummen sind erfasst:
  Basis-Blob `79949035aa8b879b3b3fb675204b007cb87b25bc` (Referenz-Commit `c7623d2`) sowie
  Spike-Blob via `clanker-git -C /home/momo/repos/riftbreaker-battle-mod hash-object
  mod/lua/rbbattle_autoexec.lua` (Wert wird notiert und geht in Abschnitt 7 Punkt 3 des Artefakts).
- **Abhängigkeiten:** Phase 0 (Event-Namen + Getter-Liste fest).

### Phase 2: Live-Test auf dem headless Dedi (planet)
- **Ziel:** Die einzige offene Frage klären: feuert eines der realen Events auf dem Dedi mit
  verbundenem Vanilla-Client?
- **Schritte:**
  1. **Deploy + Baseline-Nachweis (hier — nicht in Phase 0):** Spike-Mod über den bestehenden
     Deploy-Pfad paketieren/deployen (`scripts/package_mod.sh`; `ansible-playbook -i
     deploy/inventory deploy/site.yml --ask-vault-pass`, `docs/DEPLOYMENT.md:74`) und Dedi neu
     laden; dann ernten:
     `ssh planet 'docker logs riftbreaker-dedicated 2>&1 | grep -a RBBATTLE | tail -30'` —
     Aufwand: 0,25 h — **Completion:** `event=mod_load version=…` ist **im Dedi-Log nachgewiesen**
     (Baseline) **und** alle vier `event=spike_reg name=… ok=…`-Zeilen liegen vor. Fehlt
     `mod_load`, gilt Abbruchfall 3 (Mod lädt nicht). Fehlt `spike_reg`, ist die Registrierung
     kaputt → zurück zu Phase 1 (nicht als „Event feuert nicht" fehldeuten).
  2. `event=spike_hour`-Takt nachweisen (reine Serverseite, kein Client nötig) — Aufwand: 0,1 h —
     **Completion:** mindestens zwei `spike_hour`-Zeilen mit unterschiedlichem `hour` im Log →
     belegt, dass `RegisterGlobalEventHandler` auf dem Dedi grundsätzlich feuert.
  3. **Spieler-Schritt (Momo/Matheo, offener Punkt):** Vanilla-Client verbinden, bauen/kaufen;
     danach erneut ernten — Aufwand: 0,25 h (Fenster) — **Completion:** entweder `spike_build` /
     `spike_build_end` / `spike_start_build` im Log (**E1/E2**) oder dokumentiertes Ausbleiben nach
     nachweislich geschehener Aktion (**E3**). Welche Aktion wann ausgeführt wurde, muss zeitlich
     notiert werden, sonst ist das Ausbleiben nicht beweisbar. Ist kein Client verfügbar, gilt
     Ausgang **A0** (Teilergebnis nach Schritt 2) — **kein Abbruch**.
  4. Befund klassifizieren (E1/E2/E3/A0) und rohes Log-Segment (30 Zeilen) als Beleg sichern —
     Aufwand: 0,1 h — **Completion:** Log-Ausschnitt + Zuordnung zu E1/E2/E3/A0 steht im
     Notizblock für Phase 4.
  5. **Rückbau des Spike-Blocks (Lebenszyklus-Abschluss, verbindlich):** — Aufwand: 0,2 h —
     **Completion:**
     a. `clanker-git -C /home/momo/repos/riftbreaker-battle-mod checkout --
        mod/lua/rbbattle_autoexec.lua` (Arbeitsbaum == Referenz-Commit-Inhalt).
     b. Verifikation: `hash-object` == `79949035aa8b879b3b3fb675204b007cb87b25bc` und
        `clanker-git status --porcelain` zeigt keinen Mod-Diff.
     c. Redeploy über denselben Deploy-Pfad wie Schritt 1 + Dedi neu laden.
     d. Ernte: `event=mod_load` weiterhin vorhanden, **seit Deploy-Zeitpunkt kein neues**
        `event=spike_reg` → Spike-Block ist aus dem laufenden Dedi entfernt.
     e. Statuskennzeichnung im Artefakt (Abschnitt 7 Punkt 3): „Spike-Code zurückgebaut,
        Arbeitsbaum == Referenz-Commit `c7623d2`, Branch enthält nur die Doku-Datei."
     **Fehlerzweig:** Ist der Rückbau nicht ausführbar (Dedi nicht erreichbar), bleibt der
     Spike-Code im lokalen Checkout und der Dedi-Stand wird im Artefakt **explizit als
     „Spike-Code noch aktiv" markiert** (mit Übergabe-Hinweis für das Folge-Issue) — kein stilles
     Liegenlassen.
- **Aufwand:** ca. 0,9 h (0,7 h Test + 0,2 h Rückbau) — **Completion:** Ein klassifizierter Befund
  inkl. Rohlog liegt vor **und** der Spike-Code ist entfernt (oder begründet als verblieben
  markiert).
- **Abhängigkeiten:** Phase 1; Schritt 3 benötigt einen Menschen mit Vanilla-Client → wenn nicht
  verfügbar in der Timebox: Phase 2 endet nach Schritt 2 mit Teilergebnis **A0** (Server feuert
  oder feuert nicht, Aktion ungetestet), Schritt 5 (Rückbau) wird trotzdem ausgeführt, der Rest
  wandert in „Offene Punkte".

### Phase 3: Fallback-Bewertung (bedingt — Trigger: Phase 2 = E3; **außerhalb der 0,5-Tag-Timebox**)
- **Ziel:** Prüfen, ob Polling die Hypothese noch erfüllt — und **ehrlich bewerten**, dass es das
  in der Regel nicht tut.
- **Timebox-Status:** Diese Phase liegt **außerhalb** der 0,5-Tag-Timebox (siehe Zeitbudget) und
  wird nur bei Ausgang E3 angegangen; bei E1/E2/A0 entfällt sie.
- **Schritte:**
  1. Fallback-Mechanik spezifizieren: `HourEvent` als Takt + Service-Abfragen
     `PlayerService:GetResourceAmount`, `PlayerService:IsResearchUnlocked`,
     `BuildingService:CanBuildBuilding` / `CanAffordBuilding`, jeweils `pcall`-gekapselt,
     Zustands-Snapshot-Differenz pro Tick als `[RBBATTLE] event=poll_delta …` loggen —
     Aufwand: 0,25 h — **Completion:** Polling-Variante ist als Alternativweg beschrieben
     (Beschreibung genügt, kein Zwang sie zu bauen, wenn E1 erreicht ist).
     *Falls sie gebaut wird, gilt derselbe Lebenszyklus wie Phase 1/2.5 (Code-Listing im Artefakt,
     Guard-Flag, Rückbau + Redeploy) — der Rückbau ist dann Teil dieser Phase.*
  2. Hypothesen-Bewertung schreiben — Aufwand: 0,25 h — **Completion:** explizites Urteil mit
     Begründung, das folgende Punkte adressiert:
     - Polling erkennt **Zustandsänderungen**, nicht die **Aktion** und nicht **den Akteur** →
       Hypothese „Event erkennt Player-Aktion" ist damit **nicht erfüllt**, nur abgeschwächt zu
       „Zustandsdelta erkannt".
     - Attribution fehlt: Multiplayer → keine Zuordnung „Player X hat gebaut/geforscht".
     - Latenz = Tick-Intervall (`interval=1` laut R4), Reihenfolge/Mehrfach-Feuern offen.
     - Falsch-Positive aus Fremdquellen (Passiv-Einkommen, KI, andere Spieler) sind nicht trennbar.
     → Ergebnis: Falls E1 ausbleibt, ist Fallback ein **Notnagel mit reduzierter Aussagekraft**,
     keine Erfüllung von B1.
- **Abhängigkeiten:** Phase 2.

### Phase 4: Doku + PR (docs-only)
- **Ziel:** Spike-Ergebnis reproduzierbar ablegen, ohne Merge.
- **Schritte:**
  1. Artefakt schreiben: `docs/research/547-server-event-detection.md` nach der Struktur in
     Abschnitt 7 — Aufwand: 0,5 h — **Completion:** Datei existiert, enthält Befund
     (E1/E2/E3/A0), Rohlog-Beleg, **vollständigen Spike-Block als Code-Listing in „Methodik" +
     Checksummen/Referenz-Commit + Rückbau-Status**, Test-Split-Tabelle, Harness-Grenzen,
     Offene Punkte.
  2. Branch + Commit + PR über `clanker-git` / `clanker-gh` (Gateways, Bot-Identity
     `momo-clanker[bot]`), Body mit `Refs #547` — Aufwand: 0,25 h — **Completion:** PR ist offen,
     Branch enthält **nur** die Doku-Datei (kein Mod-Code — durch Phase 2.5 garantiert),
     Body enthält `Refs #547` und die Kennzeichnung „Spike-Ergebnis, kein Produktivcode, KEIN Merge".
  3. Abschluss: PR an `rift-pr-gate` übergeben — Aufwand: 0,1 h — **Completion:** Review/Merge liegt
     bei rift-pr-gate; **kein** Merge durch diesen Plan, **kein** Issue-Close, **keine**
     Feature-Implementierung.
- **Abhängigkeiten:** Phase 2 (bzw. Phase 3) — Doku wird in jedem Fall geschrieben,
  auch bei Negativbefund/Teilergebnis/Abbruch.

### Zeitbudget

**Definition:** 0,5 Tag = **4,0 h Arbeitszeit** (explizite Annahme, damit das Budget prüfbar ist).

| Phase | Aufwand | In Timebox? |
|---|---|---|
| Phase 0 (statisch) | 0,85 h | ja |
| Phase 1 (Spike-Code) | 0,75 h | ja |
| Phase 2 (Deploy/Baseline, Liveness, Client-Fenster, Klassifikation **+ Rückbau 2.5**) | 0,90 h | ja |
| Phase 4 (Doku + PR + Handoff) | 0,85 h | ja |
| **Summe garantiert abschließbarer Phasen (0/1/2/4)** | **3,35 h** | **Puffer 0,65 h (≈16 %)** |
| Phase 3 (Fallback, **nur bei E3**) | 0,50 h | **nein — explizit außerhalb der Timebox** |
| Phase 3 gesamt außerhalb | — | eigener Folgeslot, ausgelöst nur durch E3 |

Der Puffer von **0,65 h** ist für Deploy/Mod-Reload/SSH-Retries und die menschliche Wartezeit in
Phase 2.3 reserviert. Reicht er nicht (z. B. mehrere Redeploys), greift das harte Abbruchkriterium
(Timebox) und das Ergebnis wird als Teilstand dokumentiert.

---

## 3. Ablauf des Spike-Experiments (SPIKE — kein Produktivcode)

| # | Aktion | Ort | Zweck |
|---|---|---|---|
| S1 | `RegisterGlobalEventHandler("BuildingBuildEvent", SpikeWrap(…))` in `pcall` | `mod/lua/rbbattle_autoexec.lua` | Hauptkandidat: feuert im Vanilla-Lua (`building_base.lua:444`) |
| S2 | `RegisterGlobalEventHandler("BuildingBuildEndEvent", …)` in `pcall` | dito | Ende-Signal, getrennte Registrierung nötig |
| S3 | `RegisterGlobalEventHandler("StartBuildingEvent", …)` in `pcall` | dito | einziger Kandidat **mit** `PlayerId` (aber ohne Blueprint) |
| S4 | `RegisterGlobalEventHandler("HourEvent", …)` in `pcall` | dito | Liveness-Kontrolle (gedrosselt per Tick-Zähler) |
| S5 | Erfolgs-Log je Registrierung | dito | `[RBBATTLE] event=spike_reg name=<Event> ok=true|false err=<msg>` |
| S6 | **Rückbau nach Phase 2** (Phase 2 Schritt 5) | Checkout + Dedi-Redeploy | Lebenszyklus: kein Spike-Code bleibt in laufendem Mod/Branch |

> Vollständiger, wörtlich zu übernehmender Code-Block inkl. Guard-Flag, `SpikeWrap`/`SpikeRegister`,
> Tick-Zähler-Drosselung und allen `pcall`-gekapselten Gettern: **Abschnitt 7, Punkt 3.**

**Log-Zeilen (Erwartung):**
```
[RBBATTLE] event=spike_reg name=BuildingBuildEvent ok=true
[RBBATTLE] event=spike_reg name=BuildingBuildEndEvent ok=true
[RBBATTLE] event=spike_reg name=StartBuildingEvent ok=true
[RBBATTLE] event=spike_reg name=HourEvent ok=true
[RBBATTLE] event=spike_hour n=1 hour=7
[RBBATTLE] event=spike_build entity=… owner=… player=… bp=… upg=…
[RBBATTLE] event=spike_build_end entity=… owner=… player=… bp=… upg=…
[RBBATTLE] event=spike_start_build entity=… playerid=… bp=… team=…
```

**Getter-/Lookup-Nutzung:** ausschließlich `evt:GetEntity()`, `evt:GetOwner()`, `evt:GetPlayerId()`,
`evt:GetBlueprint()`, `evt:GetBuildingName()`, `evt:GetBuildingType()`, `evt:GetUpgraded()`,
`evt:GetHour()`, `PlayerService:GetPlayerForEntity(entity)`, `EntityService:GetTeam(entity)` —
**jeder Aufruf einzeln in `pcall`**; Fehlschlag wird als `nil` geloggt, der Handler bricht nie ab.
(R2: „not every event function works for every event type".)

**Log-Ernte (nach jedem Testschritt):**
```
ssh planet 'docker logs riftbreaker-dedicated 2>&1 | grep -a RBBATTLE | tail -30'
```

**Anti-Pattern (nicht tun):** `ResourceChangeEvent` / `ResourceObtainedEvent` /
`ResearchUnlockedEvent` registrieren — 0 Firing + 0 Register in der Vanilla-Source (R1/R3);
sie kosten nur Testzeit. Ebenfalls nicht: `event=poc_send` als Erfolg werten.

**Guard:** Einmal-Flag gegen Doppel-Registrierung (kein `Unregister` dokumentiert, R2/R7.7).

---

## 4. Fallback, falls das Event auf dem Dedi nicht feuert

**Mechanik:** `HourEvent` (in-game verifiziert) als Takt → pro Tick Service-Abfragen in `pcall`:
`PlayerService:GetResourceAmount`, `PlayerService:IsResearchUnlocked`,
`BuildingService:CanBuildBuilding` / `CanAffordBuilding` → Snapshot-Differenz →
`[RBBATTLE] event=poll_delta …`.

**Bewertung gegen die Hypothese (Kernaussage):**

| Kriterium der Hypothese „Event erkennt Player-Aktion" | Fallback erfüllt? |
|---|---|
| Erkennt **dass** etwas passiert ist | teilweise (nur Zustandsdelta, nicht die Aktion) |
| Erkennt **welche** Aktion (bauen/kaufen/forschen) | nein — Delta ist ursachenblind |
| Erkennt **wer** (Player-Attribution) | nein — `HourEvent` liefert keine PlayerId |
| Abgrenzung von anderen Spielern / KI / Passiv-Einkommen | nein, ohne weitere Heuristik nicht trennbar |
| Latenz | Tick-Intervall, nicht ereignisnah |

**Ergebnis:** Der Fallback erfüllt B1 **nicht** — er verschiebt das Problem von
„Aktion erkennen" zu „Zustand raten". Er wird im Doku-Artefakt als möglicher, aber
hypothesen-schwächerer Alternativweg dokumentiert, **nicht** als Ersatzbefund.

---

## 5. Was NICHT geprüft wird (Harness-Grenzen)

| Nicht geprüft | Warum |
|---|---|
| Operator-/exec-getriggerte Erkennung | laut geerbter Request-Begründung verworfen; Operator ≠ Player. **Achtung:** Die Refs #543/#546 existieren nicht (R7.6) — die Begründung wird nur als Request-Kontext geführt und **nie als Beleg** zitiert; belastbarer Existenz-Kontext ist #518. |
| Client-Mod / Client-Sidecar | Harness verbietet es; Player muss vanilla bleiben |
| Lua-HTTP / File-IO als Transport | existiert in der Sandbox nicht (`io.open` crasht hart) |
| Produktiv-Implementierung / Backend-Anbindung | B1 ist ein Nachweis-Spike; Implementierung ist ein Folge-Issue |
| Merge / Issue-Close | ausdrücklich ausgeschlossen (Review liegt bei rift-pr-gate) |
| Client-seitige Event-Quelle | Client bleibt vanilla → Event-Quelle auf dem Dedi ist die Frage, nicht auf dem Client |
| `ResourceChangeEvent` / `ResourceObtainedEvent` / `ResearchUnlockedEvent` | **bewusst ausgeklammert** (0 Firing/0 Register, R1/R3) → eigene Fragestellung im Folge-Issue, nicht Teil dieses Spikes |
| Vollständigkeit der Wiki-Eventliste | Autor nennt sie selbst „likely uncomplete" (R7.9) → Wiki-Negativtreffer allein kein Beweis |

---

## 6. Test-Split-Tabelle

| Kategorie | Prüfbar | Gegenstand | Status |
|---|---|---|---|
| **Host/statisch** (kein Player, kein Dedi) | ja | Exakte Event-Namen + Payload-Felder (`BuildingBuildEvent`, `BuildingBuildEndEvent`, `StartBuildingEvent`) | belegbar — Phase 0.1 |
| **Host/statisch** | ja | `RegisterGlobalEventHandler(name, fn)`-Aufrufkonvention + Autoexec-Kontext | belegbar (Wiki + Baustein-05-Muster) — Phase 0.2 |
| **Host/statisch** | ja | `BuildingBuildEvent` wird im Vanilla-Lua gefeuert (`building_base.lua:444`) | belegbar — Phase 0.1 |
| **Host/statisch** | ja | Getter-Kandidatenliste inkl. `EntityService:GetTeam` als Player-Ableitungsweg | belegbar — Phase 0.3 |
| **statisch (Kommando)** | ja | Ernte-Kommando (`grep -a RBBATTLE`, `ssh planet …`) aus bestehender Doku | festgelegt — Phase 0.4 (nur das **Kommando**, kein Log-Nachweis) |
| **benötigt Dedi (Nachweis)** | ja, ohne Player | `event=mod_load`-Marker **real** im Dedi-Log (Baseline) + `event=spike_reg ok=true`, und headless-Load des Mods | **nachzuweisen** — Phase 2.1 (nicht Phase 0) |
| **Server-seitig, ohne Player** | ja | Feuert `HourEvent` auf dem Dedi überhaupt (Liveness von `RegisterGlobalEventHandler`)? | prüfbar in Phase 2.2 — **trennt „Handler/Registrierung kaputt" von „Event kommt nicht"** |
| **Host/statisch** | ja | Player-Ableitung aus Entity (`PlayerService:GetPlayerForEntity`, `EntityService:GetTeam`) | Aufruf belegbar; **Ergebnis** erst zur Laufzeit |
| **Nur mit Player — OFFENER PUNKT (Momo/Matheo), NICHT erledigt** | nur mit Player | Feuert `BuildingBuildEvent` / `StartBuildingEvent` auf dem headless Dedi bei Vanilla-Client-Aktion? | offen — Phase 2.3 |
| **Nur mit Player — OFFENER PUNKT** | nur mit Player | Existieren die `evt:Get*`-Getter für das jeweilige Event real? | offen — Phase 2.3 |
| **Nur mit Player — OFFENER PUNKT** | nur mit Player | Sieht `RegisterGlobalEventHandler` auf dem Dedi dieselbe Event-Quelle wie im Client-Spiel? | offen |
| **Nur mit Player — OFFENER PUNKT** | nur mit Player | Ist ein reiner Server-Mod mit Vanilla-Clients überhaupt joinbar (Mod-Sync)? | offen |
| **Nur mit Player — OFFENER PUNKT** | nur mit Player | Frequenz/Reihenfolge von `HourEvent`, Mehrfach-Feuern | offen |
| **Bewusst NICHT getestet (eigene Fragestellung, Folge-Issue)** | nein — per Konstruktion ausgeklammert | Feuern `ResourceChangeEvent` / `ResourceObtainedEvent` / `ResearchUnlockedEvent` global? | **nicht Teil dieses Spikes** (Anti-Pattern Abschnitt 3); eigene Fragestellung für ein Folge-Issue — **kein** „offener Player-Test" |

> Kein Eintrag in der „Nur mit Player"-Sektion gilt durch diesen Spike als erledigt, solange
> Phase 2.3 nicht durchgeführt wurde. Teilbefunde (Phase 2.2) werden als solche gekennzeichnet.
> Die Zeile „bewusst nicht getestet" ist **kein** offener Test, sondern eine absichtliche
> Scope-Entscheidung — sie kann auch mit Client nie geschlossen werden.

---

## 7. Artefakt-Plan

**Dateiname:** `docs/research/547-server-event-detection.md`
(Slug `547-server-event-detection`; Konvention der bestehenden Dateien wie `508-catalog-of-things.md`.)

**Dokumentstruktur:**
1. Header: Issue #547, Spike B1, Datum, Mod-Version + `RBB.ref`, **Referenz-Commit `c7623d2`**,
   Dedi-Image/Status, Timebox (4,0 h) + Hinweis, dass Phase 3 außerhalb lag.
2. Fragestellung + Hypothese + explizites Abbruchkriterium + **Ausgangsdefinition E1/E2/E3/A0**.
3. **Methodik — mit vollständigem Spike-Block als Code-Listing (verbindlich):**
   - Der **komplette** Spike-Code (Guard-Flag, `SpikeWrap`, `SpikeRegister`, alle vier
     Registrierungen, alle Handler mit `pcall`-gekapselten Gettern, Tick-Zähler-Drosselung) steht
     wörtlich als Lua-Codeblock im Dokument — nicht als Beschreibung, nicht als Diff.
   - **Reproduzierbarkeits-Angaben direkt darunter:**
     - Referenz-Commit: `c7623d2a526f248d98cd7abb70ab9724daccb9d9`
     - Basis-Blob der Mod-Datei: `79949035aa8b879b3b3fb675204b007cb87b25bc`
     - Spike-Blob (`hash-object` nach Phase 1): `<in Phase 1 erfasst>`
     - Getter-Nutzung mit `pcall`, Log-Zeilenformat, Ernte-Kommando
   - **Lebenszyklus-Status (Pflichtfeld):** „Spike-Block nach Phase 2 zurückgebaut —
     Arbeitsbaum == Referenz-Commit `c7623d2`, Dedi-Redeploy erfolgt (Phase 2 Schritt 5d bestätigt:
     kein neues `spike_reg` seit Deploy), Branch enthält nur diese Doku-Datei." **Alternativ** bei
     fehlgeschlagenem Rückbau: „Spike-Code noch im Checkout/Dedi aktiv — Grund: …; Übergabe an
     Folge-Issue."
   - **Vollständiger Spike-Block (wörtlich, so in die Mod-Datei eingefügt):**

     ```lua
     -- ---------------------------------------------------------------------------
     -- SPIKE B1 (#547) — Versuchsaufbau, NICHT Produktivpfad.
     -- Zweck: belegen, ob RegisterGlobalEventHandler auf dem headless Dedi feuert und
     -- ob eine Vanilla-Client-Aktion als Event sichtbar wird.
     -- Lebenszyklus: wird nach Phase 2 zurueckgebaut (Plan Phase 2 Schritt 5).
     -- ---------------------------------------------------------------------------
     local spike_done = false
     local spike_hour_n = 0       -- Drossel-Zaehler fuer HourEvent
     local spike_last_hour = nil
     local SPIKE_HOUR_CAP = 20    -- harte Obergrenze der hour-Logzeilen

     -- Jeder Getter einzeln in pcall; "nil" ist ein gueltiges Ergebnis (R2).
     local function SpikeCall(fn)
         local ok, res = pcall(fn)
         if not ok or res == nil then return "nil" end
         return tostring(res)
     end

     -- Handler-Body zusaetzlich kapseln: ein Fehler sprengt nie den Handler.
     local function SpikeWrap(name, fn)
         return function(evt)
             local ok, err = pcall(fn, evt)
             if not ok then
                 Log("event=spike_err name=%s err=%s", name, tostring(err))
             end
         end
     end

     local function OnSpikeBuild(evt)
         local entity = SpikeCall(function() return evt:GetEntity() end)
         local owner  = SpikeCall(function() return evt:GetOwner() end)
         local player = SpikeCall(function()
             return PlayerService:GetPlayerForEntity(evt:GetEntity())
         end)
         local bp  = SpikeCall(function() return evt:GetBlueprint() end)
         local upg = SpikeCall(function() return evt:GetUpgraded() end)
         Log("event=spike_build entity=%s owner=%s player=%s bp=%s upg=%s",
             entity, owner, player, bp, upg)
     end

     local function OnSpikeBuildEnd(evt)
         local entity = SpikeCall(function() return evt:GetEntity() end)
         local owner  = SpikeCall(function() return evt:GetOwner() end)
         local player = SpikeCall(function()
             return PlayerService:GetPlayerForEntity(evt:GetEntity())
         end)
         local bp  = SpikeCall(function() return evt:GetBlueprint() end)
         local upg = SpikeCall(function() return evt:GetUpgraded() end)
         Log("event=spike_build_end entity=%s owner=%s player=%s bp=%s upg=%s",
             entity, owner, player, bp, upg)
     end

     local function OnSpikeStartBuild(evt)
         local entity   = SpikeCall(function() return evt:GetEntity() end)
         local playerid = SpikeCall(function() return evt:GetPlayerId() end)
         local bp       = SpikeCall(function() return evt:GetBlueprint() end)
         local team     = SpikeCall(function() return EntityService:GetTeam(evt:GetEntity()) end)
         Log("event=spike_start_build entity=%s playerid=%s bp=%s team=%s",
             entity, playerid, bp, team)
     end

     local function OnSpikeHour(evt)
         spike_hour_n = spike_hour_n + 1
         local hour = SpikeCall(function() return evt:GetHour() end)
         if (hour ~= spike_last_hour or spike_hour_n % 10 == 0)
             and spike_hour_n <= SPIKE_HOUR_CAP then
             Log("event=spike_hour n=%d hour=%s", spike_hour_n, hour)
         end
         spike_last_hour = hour
     end

     local function SpikeRegister(name, fn)
         local ok, err = pcall(RegisterGlobalEventHandler, name, fn)
         Log("event=spike_reg name=%s ok=%s err=%s", name, tostring(ok),
             ok and "-" or tostring(err))
         return ok
     end

     -- Guard: kein Unregister dokumentiert (R2/R7.7) -> Doppel-Registrierung vermeiden.
     if not spike_done then
         spike_done = true
         SpikeRegister("BuildingBuildEvent", SpikeWrap("BuildingBuildEvent", OnSpikeBuild))
         SpikeRegister("BuildingBuildEndEvent", SpikeWrap("BuildingBuildEndEvent", OnSpikeBuildEnd))
         SpikeRegister("StartBuildingEvent", SpikeWrap("StartBuildingEvent", OnSpikeStartBuild))
         SpikeRegister("HourEvent", SpikeWrap("HourEvent", OnSpikeHour))
     end
     ```

4. **Ergebnis:** klassifiziert als E1/E2/E3/A0 mit exaktem **Rohlog-Ausschnitt** (~30 Zeilen) als Beleg.
5. Event-Namen-Korrektur: Issue-Kandidaten vs. reale Namen (R1-Tabelle).
6. Fallback-Bewertung (nur falls Phase 2 = E3) inkl. Hypothesen-Urteil.
7. Test-Split-Tabelle (Abschnitt 6), inkl. Kennzeichnung der offenen Player-Punkte und der
   bewusst ausgeklammerten Resource-/Research-Frage.
8. Harness-Grenzen (Abschnitt 5).
9. Offene Punkte (Abschnitt 8/9).
10. Konsequenz/Empfehlung für Folge-Issue (nur Empfehlung, keine Implementierung).

**Git/PR:**
- Ausschließlich über `clanker-git` / `clanker-gh` (Gateways) — **niemals** nackt.
- Branch: `docs/547-server-event-detection`; Commit-Scope: **nur** die eine Doku-Datei
  (Spike-Code wurde in Phase 2 Schritt 5 zurückgebaut, nicht committet).
- Bot-Identity: `momo-clanker[bot]`.
- PR-Body enthält: `Refs #547`, Kurzfassung Befund (E1/E2/E3/A0), Hinweis
  „docs-only, Spike-Ergebnis, kein Produktivcode".
- **KEIN Merge, kein Issue-Close, kein Feature-Implement** — Review/Merge ausschließlich
  über `rift-pr-gate`.

---

## 8. Risiken

- **Spike-Code bleibt liegen** (Lebenszyklus) — Mitigation: verbindlicher Rückbau-Schritt
  (Phase 2 Schritt 5) mit Verifikation gegen den Referenz-Blob; Branch-Commit-Scope ist auf die
  Doku-Datei begrenzt; vollständiges Code-Listing + Checksummen im Artefakt (Abschnitt 7 Punkt 3);
  Fehlerzweig „Rückbau nicht möglich" wird explizit markiert.
- **Event feuert auf Dedi nicht** — Mitigation: `HourEvent`-Liveness-Check (Phase 2.2) trennt
  „Handler/Registrierung kaputt" von „Event kommt nicht"; E3 + Negativbefund ist valides Ergebnis;
  Fallback (Phase 3) mit ehrlicher Bewertung.
- **Payload-Mismatch `owner` (5. Arg)** (R7.2) — Mitigation: `owner` **und** `GetOwner` **und**
  `GetPlayerForEntity`-Lookup **und** `EntityService:GetTeam` gleichzeitig loggen → mindestens ein
  Weg zeigt Attribution.
- **Kein einzelnes Event liefert „Player X hat Y gebaut"** (R7.3) — Mitigation: Kombination
  `BuildingBuildEvent` (Name/Typ) + `StartBuildingEvent` (`PlayerId`), im Doku-Artefakt als
  Nebenbefund festhalten; kein Feature-Design in diesem Spike.
- **Getter existiert nicht → Handler-Abbruch** — Mitigation: jeder Getter in `pcall` (Ergebnis
  `"nil"`), zusätzlich `SpikeWrap` als äußerer Schutz; `nil` ist gültiges Ergebnis.
- **Doppel-Registrierung bei Mod-Reload** (R7.7) — Mitigation: Guard-Flag; Doppel-Feuern im Log
  dokumentieren statt wegdiskutieren.
- **Kein Vanilla-Client-Spieler im Zeitfenster** (Phase 2.3) — Mitigation: definierter Ausgang
  **A0** (Teilergebnis + Rückbau trotzdem) → wandert explizit in „Offene Punkte"; **kein Abbruch**.
- **0,5-Tag-Timebox reißt** — Mitigation: Budget nur auf Phasen 0/1/2/4 gerechnet (3,35 h),
  Puffer 0,65 h beziffert; Phase 3 liegt außerhalb der Timebox und ist nur bei E3 triggerbar;
  Phase 0 ist der Puffer (rein statisch, jederzeit abschließbar); Doku in Phase 4 wird auch ohne
  vollständigen Live-Test geschrieben.
- **Dead Refs #543/#546** (R7.6) — Mitigation: im Doku-Artefakt nicht als Beleg zitieren, nur als
  geerbte Request-Begründung kennzeichnen; #518 als existierenden Kontext nennen.
- **`memory_search` im Vorgänger-Run ausgefallen** (R7.8) — Mitigation: Plan stützt sich
  ausschließlich auf das gemergte Research-Doc, keine Memory-Abhängigkeit.

---

## 9. Offene Punkte

**A. Aus R7 (Research-Lücken):**
1. Payload-Mismatch `BuildingBuildEvent`: Wiki 4 Felder vs. Source 5 Args inkl. `owner` — Durchschlag offen.
2. Kein Einzel-Event liefert vollständige „Player X hat Y gebaut"-Info (Kombination/Lookup nötig).
3. **Bewusst nicht getestet / eigene Fragestellung (Folge-Issue):** Feuern von
   `ResourceChangeEvent` / `ResourceObtainedEvent` / `ResearchUnlockedEvent` — per Anti-Pattern
   (Abschnitt 3) werden sie **nicht** registriert; Reflexion ≠ Broadcast-Reichweite, 0 Vanilla-Firing.
   Dieser Punkt ist **kein** offener Player-Test und per Konstruktion unschließbar; er gehört in ein
   eigenes Folge-Issue.
4. Client-/Server-Kontext statisch nicht bestimmbar (keine `IsServer`-Verzweigung im Lua).
5. Lifecycle bei Mod-Reload unbelegt (kein Unregister) — im Spike durch Guard-Flag + Rückbau
   (Phase 2 Schritt 5) beherrscht, aber nicht engine-seitig belegt.
6. Wiki-Eventliste laut Autor „likely uncomplete" → Negativtreffer allein kein Beweis.
7. Keine offizielle Exor-Doku zum Dedicated-Server-Modding gefunden.
8. Dead Refs #543/#546 nicht auflösbar — die „revertierte Workaround"-Begründung ist über
   Issue-Nummern **nicht** belegbar und wird nur als geerbte Request-Begründung geführt;
   nur #518 als Kontext vorhanden.
9. `memory_search` im researcher-Kontext ausgefallen (`index metadata is missing`) — Memory-Vorwissen
   in diesem Strang nicht abrufbar.

**B. Aus dem Test-Split (nur mit Player, NICHT erledigt):**
10. Feuert `BuildingBuildEvent` / `StartBuildingEvent` auf dem headless Dedi bei Vanilla-Client-Aktion?
11. Existieren die `evt:Get*`-Getter für das jeweilige Event real?
12. Sieht `RegisterGlobalEventHandler` auf dem Dedi dieselbe Event-Quelle wie im Client-Spiel?
13. Ist ein reiner Server-Mod mit Vanilla-Clients überhaupt joinbar (Mod-Sync)?
14. Frequenz/Reihenfolge von `HourEvent` (Fallback) und Mehrfach-Feuern.
15. Ob `owner` (5. Arg) am `BuildingBuildEvent` durchschlägt.

**C. Ausgangsabhängige Restpunkte:**
16. **A0-Fall:** Vanilla-Client-Aktion blieb im Zeitfenster ungetestet → der Befund ist ein
    Teilergebnis (Server-Liveness), Punkt 10 bleibt offen und ist im Artefakt als solcher markiert.
17. **Rückbau-Fall:** Sollte der Spike-Block nicht zurückgebaut werden können (Dedi nicht
    erreichbar), wird der verbleibende Stand im Artefakt mit Grund + Übergabehinweis markiert.

> Punkt 10 ist der Kernbefund dieses Spikes. Bleibt er offen (kein Vanilla-Client im Zeitfenster,
> Ausgang A0), ist das Ergebnis ausdrücklich **kein** „erledigt", sondern ein Teilergebnis mit
> klarer Kennzeichnung.
---

## Annex: Critic-Verlauf (Review-Prozess)

## Urteil: NEIN

1. **Problem:** Der Spike-Code in `mod/lua/rbbattle_autoexec.lua` hat keinen definierten Lebenszyklus: Phase 1 schreibt ihn hinein und deployt ihn, aber kein Schritt entfernt ihn danach — und weil der Branch laut Abschnitt 7 „nur die eine Doku-Datei" enthalten darf, existiert der Code, der den Rohlog erzeugt hat, in keiner Revision.
   **Betroffene Planstelle:** Phase 1 / Phase 2.1 / Abschnitt 7 (Dateiname + Struktur) / Abschnitt 6.
   **Korrektur:** (a) Vollständigen Spike-Block (Handler + Registrierung) als Code-Listing in die Artefakt-Struktur aufnehmen (Punkt 3 „Methodik") und Mod-Datei-Checksum bzw. Referenz-Commit dort festhalten; (b) eigenen Schritt ergänzen: nach Phase 2 den Spike-Block aus `rbbattle_autoexec.lua` wieder entfernen (Gateways-Checkout + Dedi-Redeploy) oder explizit begründet drinlassen — mit Statuskennzeichnung.

2. **Problem:** Phase 0 ist als „host, kein Dedi, kein Client" deklariert, Schritt 0.4 verlangt in der Completion aber einen „bereits nachgewiesenen" `event=mod_load`-Marker im **Dedi-Log** (Ernte via `ssh planet`). Entweder ist das eine ungeprüfte Vorannahme, oder Phase 0 braucht doch den Dedi — beides widerspricht der Phasenbeschreibung.
   **Betroffene Planstelle:** Phase 0 (Header + Schritt 4, Zeile ~189/209), Abschnitt 6 Zeile „`[RBBATTLE]`-Log-Muster + headless-Load des Mods im Dedi-Log".
   **Korrektur:** Schritt 0.4 auf das statische Festlegen des Ernte-Kommandos reduzieren; den Baseline-Nachweis als echten Prüfauftrag in Phase 2.1 verschieben; die Abschnitt-6-Zeile in „statisch (Kommando) / benötigt Dedi (Nachweis)" auftrennen.

3. **Problem:** `ResourceChangeEvent` / `ResourceObtainedEvent` / `ResearchUnlockedEvent` stehen in Abschnitt 6 als „Nur mit Player — OFFENER PUNKT", während das Anti-Pattern (Abschnitt 3) und Phase 3 sie bewusst **nicht** registrieren. Der markierte Player-Test kann diesen Punkt damit per Konstruktion nie schließen — Status „offen (Player-Test)" und „bewusst ausgeklammert" werden vermischt.
   **Betroffene Planstelle:** Abschnitt 6 (Zeile ~404), Abschnitt 9 A.3, Abschnitt 3 Anti-Pattern.
   **Korrektur:** Diese Zeile in Abschnitt 6 und Abschnitt 9 als „bewusst nicht getestet / eigene Fragestellung (Folge-Issue)" führen, nicht als offenen Player-Test.

4. **Problem:** Das Abbruchkriterium ist unscharf: „Event feuert nicht **und** Fallback erfüllt die Hypothese nicht → begründeter Abbruch" verknüpft Negativbefund (E3) mit dem Abbruchbegriff, obwohl E3 laut Abschnitt 1 selbst ein valides Ergebnis ist. Zusätzlich fehlt ein definierter Ausgang für „kein Vanilla-Client im Zeitfenster" (Phase 2 nennt nur „Teilergebnis").
   **Betroffene Planstelle:** Abschnitt 1 (Erfolgskriterien E1–E3 + Abbruchkriterium), Phase 2 (Abhängigkeiten).
   **Korrektur:** Abbruch = Timebox/Dedi nicht verfügbar/nicht ladbar. E3 = Negativbefund (Ergebnis, kein Abbruch). Neuen Ausgang A0 „kein Client im Fenster → Teilergebnis + offener Punkt" explizit aufführen.

5. **Problem:** Die Zeitbudget-Summe (3,8 h) enthält die **bedingte** Phase 3 (0,5 h) und lässt damit rechnerisch ~0,2 h Puffer für Deploy/Mod-Reload/SSH-Retries — bei einem Live-Experiment mit Redeploy und Mensch-im-Loop zu knapp, zumal Phase 2.3 von Momo/Matheo abhängt.
   **Betroffene Planstelle:** Zeitbudget-Summe am Ende von Abschnitt 2.
   **Korrektur:** Phase 3 explizit außerhalb der 0,5-Tag-Timebox führen (Trigger: nur bei E3) oder das Budget auf die statisch garantierbaren Phasen 0/1/2/4 begrenzen und den Puffer beziffern.

**Nicht blockierend, aber beachten:** `EntityService:GetTeam` erscheint in Abschnitt 6, fehlt aber in der Getter-/Lookup-Liste von Phase 1; `HourEvent`-Drosselung ist gefordert, aber ohne Mechanik (Tick-Zähler) spezifiziert; Abschnitt 5 zitiert „in #543→#546 verworfen", obwohl R7.6 diese Refs als nicht existent belegt — nur als geerbte Request-Begründung, nie als Beleg verwenden.

**Positiv:** Event-Namen-Korrektur aus R1 korrekt übernommen, Fallback ehrlich als hypothesen-schwach bewertet, Harness-Grenzen sauber eingehalten (kein Operator-Trigger, kein Client-Mod/Sidecar, kein Lua-HTTP/IO), PR-/Doku-Artefakt mit `Refs #547` und explizitem Merge-Verbot spezifiziert.

## Urteil Iteration 2: PASS

Alle 5 Einwände sind substanziell behoben, alle 3 Hinweise übernommen; keine neuen Blocker.

**Nachprüfung (Gateway-Checkout, 2026-09-16):** Referenz-Commit `c7623d2…` existiert, `hash-object mod/lua/rbbattle_autoexec.lua` == `79949035…` (exakt wie im Plan) → Phase-2.5-Verifikation ist ausführbar, nicht nur behauptet. `Log(fmt, ...)` nutzt `string.format` → das Listing ist mit dem realen Helper kompatibel. `scripts/package_mod.sh`, `deploy/site.yml`, `docs/DEPLOYMENT.md:74/305` existieren.

**Einwand-Abgleich:** (1) Phase 2 Schritt 5 (Rückbau + Blob-Verifikation + Redeploy + Fehlerzweig) + wörtliches Code-Listing + Checksummen in Abschnitt 7.3 → behoben. (2) Phase 0.4 nur noch Kommando-Festlegung, Baseline nach Phase 2.1, Test-Split-Zeile aufgeteilt → behoben. (3) Resource-/Research-Zeile als „bewusst nicht getestet (Folge-Issue)" markiert, Abschnitt 9 A.3 konsistent → behoben. (4) Hartes Abbruchkriterium (3 Fälle), E3 = valides Ergebnis, A0 explizit eingeführt → behoben. (5) Phase 3 außerhalb der Timebox, Summe 0/1/2/4 = 3,35 h, Puffer 0,65 h beziffert; Arithmetik stimmt → behoben. Hinweise: `EntityService:GetTeam` in Phase 1 + Abschnitt 6; `HourEvent`-Drossel per Tick-Zähler im Listing; #543/#546 nur als geerbte Begründung, #518 als Existenz-Kontext → übernommen. Original-Request (Spike, docs-only, kein Implement, kein Merge, `Refs #547`), Harness-Grenzen und Test-Split (nur-mit-Player bleibt offen) sind gewahrt.

**Nicht blockierende Restrisiken:** (a) Der Doc-Kopf über `# RESEARCH-INPUT` trägt noch die alte Zeile „ODER begründeter Abbruch (Event feuert nicht …)" und widerspricht damit leicht Abschnitt 1 („E3 wird nie als Abbruch bezeichnet") — Abschnitt 1 ist die operative Definition; Kopfzeile bei Gelegenheit angleichen. (b) Das Guard-Flag `spike_done` ist eine Datei-Local und dedupliziert nur bei einmaligem Load, nicht bei Re-Execution; Folge ist lediglich doppeltes Loggen, das der Plan als dokumentierbar deklariert. (c) Phase 2.2 verlangt „zwei `spike_hour`-Zeilen mit unterschiedlichem `hour`"; fehlt der Getter `GetHour`, liefert die Zähl-Drossel weiterhin ≥2 Zeilen (n=10/20), aber mit `hour=nil` — Liveness bleibt beweisbar, die Formulierung ist dann nur strenger als nötig.
