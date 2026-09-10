# API Deep-Dive — Economy-Loop (Baustein 05)

Stand: 08.09.2026. Recherche für Baustein 05 (`economy-loop`): Punkte sammeln
(Kills / Survival-Zeit), Punkte ausgeben (Kreaturen-Wellen kaufen).

## Quellen

| Kürzel | Quelle | Stand |
|---|---|---|
| WIKI | exorstudios/riftbreaker-wiki, Clone `06aa48f` (`docs/lua-services`, `docs/misc`, `docs/game-reflection/events`, `docs/modding-files/lua-files`) | 08.09.2026 |
| LILLY | echte Workshop-Mods (github.com/lilly1987/Riftbreaker-mods, lokal `research/Riftbreaker-mods`) — Autor hat In-Game getestet | 08.09.2026 |
| PONO | PonomarevDmitry-Wiki-Signatur-Dumps (lokal `research/ponomarev/EntityService*.md`) | 08.09.2026 |
| KAGI | 2 Queries (Kill-Event-Beispiele; DB-Persistenz) — nur generische Steam/Fandom-Treffer, **keine neuen Belege** | 08.09.2026 |
| VERIF | In-Game verifiziert durch Momo (s. `docs/findings.md`, Spike-Test 07.–08.09.2026) | 08.09.2026 |

Sicherheitsstufen: **verifiziert** (In-Game oder offizielles Doku-Beispiel),
**wahrscheinlich** (Konvention + Reflection-Beleg, kein In-Game-Beleg),
**unbekannt** (nur vermutet).

---

## 1. Ressourcen / Score (Spieler-Konto)

**Kernbefund: Es gibt keinen verifizierten Lua-Service, der das globale
Spieler-Ressourcen-Konto (carbonium, ironium, cobalt, …) liest oder
verändert.** Der Wiki-Ordner `resource-service` ist ein leerer Stub
(`docs/lua-services/resource-service/`, nur index + leere examples-Seite).

Gefundene API (alle **nicht** das Spieler-Konto):

| API | Signatur (Beleg) | Bedeutung | Sicherheit | Quelle |
|---|---|---|---|---|
| `EntityService:GetResourceAmount(entity)` | `(Entity): Pair<name,amount>` — `.first` = Ressourcen-Name | Menge **eines Ressourcen-Vorkommens** (Mine/Node, `ResourceComponent`), nicht des Kontos | wahrscheinlich | PONO `EntityService2.md` („GetResourceAmount"), LILLY `start give building` (nutzt `.first` = Name) |
| `EntityService:ChangeResourceAmount(entity, amount)` | `(Entity, float): void` | Menge eines Vorkommens ändern; Kontext nicht belegt | unbekannt | PONO `EntityService2.md` |
| `ResourceManager:GetResource(reflection_type, resource_name)` | `(string, string): …` | **Blueprint-/Reflection-Datenbank** laden (z. B. `MissionDef`), kein Konto | verifiziert (Doku) | WIKI `docs/misc/resource-manager.md`; LILLY `start give building` (Z. 260) |
| `ResourceManager:ResourceExists(type, name)` | `(string, string): bool` | Existenz-Check Reflection-Typ | verifiziert (Doku) | WIKI `docs/misc/resource-manager.md` |
| `ResourceChangeEvent` / `ResourceObtainedEvent` | Felder: `Entity`, `ResourceBasket` bzw. `Resource` | Benachrichtigung über Ressourcen-Änderungen an Gebäuden | unbekannt (Getter/Reichweite unklar) | WIKI `docs/game-reflection/events/resource_change_event.md`, `resource_obtained_event.md` |

Ressourcen-Namen in Spiel-Daten (aus LILLY `start give building`, Z. 190–202):
`carbonium`, `steel`, `cobalt`, `palladium`, `uranium_ore`, `titanium`,
`geothermal`, `flammable_gas`, `mud`, `morphium`, `magma`, `sludge`, `water`.

**Entscheidung Baustein 05:** Economy läuft NICHT über Spiel-Ressourcen.
Eigenes Punktesystem im Mod (Int-Zähler), gespiegelt in eine
Global-Database (s. §3). Begründung: kein verifizierter Konto-Zugriff;
eigene Punkte sind auf den Duel-Modus übertragbar (Server-Scoreboard), ohne
die Basis-Ökonomie des Spielers zu beschädigen.

---

## 2. Events (RegisterGlobalEventHandler)

Mechanik **verifiziert**: `_autoexec.lua` kann via globaler Funktion
`RegisterGlobalEventHandler("EventName", fn)` auf Events hören
(WIKI `docs/modding-files/lua-files/autoexec.md`; VERIF „PlayerCreatedEvent“
läuft zuverlässig, `docs/findings.md` #14; LILLY nutzt es für
`CheatUsedEvent`, `HarvestStartEvent`, `PlayerTeamCreatedEvent`,
`PlayerInitializedEvent`, `PlayerControlledEntityChangeEvent` u. a.).

**Namenskonvention (verifiziert):** Event-Datei `cheat_used_event.md` →
Event-Name `"CheatUsedEvent"` (PascalCase aus Snake-Case).
Relevante Events, alle aus WIKI `docs/game-reflection/events/`:

| Event (Name) | Datei im WIKI | Felder (Reflection) | Sicherheit (Handler-Feuerung) |
|---|---|---|---|
| `PlayerCreatedEvent` | `player_created_event.md` | Entity, PlayerId, PlayerInfo | **verifiziert** (findings #14) |
| `PlayerInitializedEvent` | `player_initialized_event.md` | Entity, PlayerId | verifiziert (LILLY test) |
| `EntityKilledEvent` | `entity_killed_event.md` | Entity, **Blueprint**, DamageType, Team, TypeMask, Owner, Killer, **KillerPlayer** | wahrscheinlich (Standard-Event, Broadcast wie oben) |
| `DamageEvent` | `damage_event.md` | Entity, DamageValue, DamageType, Owner, Creator | wahrscheinlich |
| `UnitPlayerDamageEvent` | `unit_player_damage_event.md` | Entity, DamageValue, DamageType, EntityBlueprint, CreatorBlueprint | wahrscheinlich |
| `PlayerDiedEvent` | `player_died_event.md` | Entity, PlayerId, Killer, … | wahrscheinlich |
| `HourEvent` | `hour_event.md` | Entity, **Hour** (int) | wahrscheinlich (Spielzeit-Tick) |
| `DayStartedEvent` / `NightStartedEvent` | `day_started_event.md`, `night_started_event.md` | Entity | wahrscheinlich |
| `ResourceChangeEvent` / `ResourceObtainedEvent` | s. §1 | s. §1 | unbekannt |
| `ResearchUnlockedEvent` | `research_unlocked_event.md` | Entity, Name, Awards, TeamId | wahrscheinlich |

**Event-Getter:** WIKI `docs/misc/event-class.md` — Event-Objekte haben
`evt:GetX()` je Feld, **aber**: „Not every event function works for every
event type“ → Getter pro Event nur über den Workspace-Editor-Reflection
sicher bestimmbar. Verifizierte Beispiele: `evt:GetEntity()`,
`evt:GetPlayerId()` (LILLY test), `evt:GetTeamId()` (LILLY test,
`PlayerTeamCreatedEvent`), `evt:GetCheatName()` (LILLY test).
Für `EntityKilledEvent` werden `evt:GetEntity()` / `evt:GetBlueprint()`
erwartet (Felder vorhanden, Konvention deckungsgleich) — **wahrscheinlich**.

**Kein „Wave-Event“** und kein Score-Event im Event-Register (388 Event-Dateien
durchsucht; Score nur als HUD-Klasse `hud_scoreboard` vorhanden).

**Konsequenz für Baustein 05 (Punktequelle):**
- Kill-Pfad über `EntityKilledEvent` ist **wahrscheinlich**, nicht verifiziert
  (kein In-Game-Beleg für dieses Event außerhalb des Workspace-Editors).
- Deshalb implementiert der Baustein **beide** Quellen und schaltet selbst um:
  Start im Modus `auto` → Einkommen über `HourEvent`-Tick (Fallback, sicher
  dokumentierter globaler Takt). Der erste **fehlerfrei** verarbeitete Kill
  verifiziert die Event-API zur Laufzeit → Umschaltung auf Kill-Einkommen.
  Ein Fehler im Kill-Handler (pcall) → dauerhaft Fallback-Tick + Log-Warnung.

---

## 3. Save / Globale Variablen (Persistenz)

**Kein `GetGlobalVar`/`SetGlobalVar`** im Wiki. Persistenz-Äquivalent sind
**Databases** (Key-Value mit `Has*`/`Get*`/`Set*`/`RemoveKey`/`Clear`,
Typen Int/Float/String):

| API | Signatur | Bedeutung | Sicherheit | Quelle |
|---|---|---|---|---|
| `PlayerService:GetOrCreateGlobalDatabase(name)` | `(string): Database` | Profil-weite Global-DB (Spiel-beispiel: XP-System `g_lvl_system_database`) | **verifiziert** (Doku-Beispiel) | WIKI `docs/misc/database-class.md` |
| `CampaignService:GetCampaignData()` | `(): Database` | DB der laufenden Kampagne (überlebt Save/Load der Karte) | **verifiziert** (Doku + LILLY: „schon angewendet“-Marker) | LILLY diverse Mods (z. B. `Building Health x 100`) |
| `EntityService:GetDatabase(entity)` / `GetBlueprintDatabase(bp)` | `(Entity)/(string): Database` | Entity-/Blueprint-DB | verifiziert (Doku) | WIKI `lua-services/entity-service`, LILLY test |

DB-Methoden (WIKI `docs/misc/database-class.md`): `HasInt(key)`,
`GetInt(key)`, `GetIntOrDefault(key, default)`, `SetInt(key, v)`,
`RemoveKey(key)`, `Clear()`, analog `String`/`Float`.

**Persistenz über Sessions:** **verifiziert (Session-/Server-Neustart, beobachtend).**
Koreanischer Kommentar im LILLY-Test zu einer Global-DB: „nach Spielstart
immer geteilt“ (= pro laufendem Spiel geteilt). Der Produktiv-Log des
Dev-SP-Servers (:6321) belegt die Persistenz über **zwei echte
Session-/Server-Neustarts**: `farmed` ging 0 (erster Boot `status=new`,
16:53) → 155 (nächster Boot `status=resume`, 17:37) → 160 (dritter Boot
`status=resume`, 18:14). Der Baustein schreibt Punkte als Spiegel in
`PlayerService:GetOrCreateGlobalDatabase("rbbattle_05_economy")` (integrierter
Mod: `rbbattle_economy`) und liest sie beim Laden zurück; `rb_economy reset`
setzt auf 0 zurück. DB-Ausfälle sind per pcall toleriert (Mod läuft dann
rein im Speicher).

**Stand #65 (RE-Verifikation, 10.09.2026):** Die Persistenz über einen
**Session-/Server-Neustart ist beobachtet** (s. o.: `farmed` 0→155→160 über
zwei Neustarts, Boot-Log `status=resume`). Der Spar-Pool `pool` teilt
denselben Save-/Load-Pfad wie `farmed` — `EconomySave` schreibt
`pool`+`farmed`+`converted`+`converts` in einem pcall-Block, `EconomyLoad`
liest sie gemeinsam → die Pool-Persistenz ist damit auf Mechanik-Ebene
belegt. **Nicht direkt beobachtet** ist ein literal `pool > 0`, der einen
Reload überlebt: in Prod wurde nie konvertiert, `pool` blieb stets 0. Der
kontrollierte AC1-Test ist technisch headless machbar — der Konsolen-Pfad
ist auf :6321 verifiziert (`exec_cmd_client "rb_convert 160"` →
`ConsoleService::ExecuteCommand`, rbbridge-Log zeigt erfolgreichen Dispatch;
`rb_status`/`event=status` liest `pool`) — aber der abschließende
Map-/Session-Reload ist ein **destruktiver Eingriff** auf dem Live-Dedi und
bleibt bis zur Freigabe offen (AGENTS: restart = Freigabe nötig; Spieler-Check
vorher). Der defensive Fallback (AC3) bleibt umgesetzt: `EconomySave` bei jeder
Änderung + `EconomyCheckpoint()` an der Rundengrenze
(`event=economy_checkpoint`), statisch getestet in
`tests/lua-static/persistence.test.js`.

---

## 4. Research / Upgrades (Kurzfassung)

| API | Signatur | Sicherheit | Quelle |
|---|---|---|---|
| `PlayerService:UnlockResearch(name)` | `(string): void` — Namen `gui/menu/research/name/<id>` | verifiziert (LILLY `Research All`) | LILLY `Research All/lua/research_all_autoexec.lua` (Z. 438) |
| `BuildingService:UnlockBuilding(path)` | `(string): void` — Blueprint-Pfad | **verifiziert** (offizielles Autoexec-Beispiel) | WIKI `docs/modding-files/lua-files/autoexec.md` |
| Events | `AddedToResearchEvent`, `ResearchUnlockedEvent` (Felder: Name, TeamId) | wahrscheinlich | WIKI `docs/game-reflection/events/` |
| Request | `AddToResearchRequest`, `MoveResearchInQueueRequest` | wahrscheinlich | WIKI `docs/game-reflection/events/` |

Nicht benötigt für Baustein 05 (kein Research-Gate in der Economy) —
dokumentiert für spätere Bausteine (Upgrade-Käufe gegen Punkte).

---

## 5. Verifikations-Matrix für Baustein 05

| Thema | Ergebnis | Nächster Schritt |
|---|---|---|
| Ressourcen-Konto lesen/schreiben | **NEIN** — kein verifizierter API-Zugriff aufs Spieler-Konto; eigenes Punktesystem | — |
| Kill-Event (Punkte je Kill) | **JA (wahrscheinlich)** — `EntityKilledEvent` existiert, Broadcast-Mechanik verifiziert, Getter `GetEntity`/`GetBlueprint` per Konvention; **nicht In-Game belegt** | Laufzeit-Selbsttest im Mod (auto→kill-Umschaltung) + In-Game-Test |
| Tick-Event (Fallback) | **JA (wahrscheinlich)** — `HourEvent` (globaler Spielzeit-Takt, Feld `Hour`) | In-Game: Frequenz von `HourEvent` beobachten |
| Persistenz zwischen Sessions | **JA (verifiziert, Session-Neustart beobachtet)** — Global-Database statt GlobalVars; `farmed` 0→155→160 über zwei Neustarts (`status=resume`); Pool teilt denselben Save/Load-Pfad | Kontrolliert: `rb_convert` → Pool>0 → Reload → `rb_status` — gated auf destruktiven Reload (Freigabe) |
| Research/Upgrades | **JA** — `UnlockResearch`/`UnlockBuilding` | späterer Baustein |

## 6. Offene Punkte

- Gibt es `UnregisterGlobalEventHandler`? Nicht dokumentiert → Mod nutzt
  Guard-Flags statt Unregister (Handler bleibt registriert, early-return).
- `EntityKilledEvent`-Feuerung auch für Gebäude/Wrackteile? (Relevant erst bei
  Gegner-Basis-Schaden in späteren Bausteinen; hier egal: Punkte nur für
  selbst gespawnte, getrackte Kreaturen-IDs.)
- `HourEvent`-Getter (`GetHour()`) und Frequenz in-game unbestätigt.
- Global-DB überlebt Map-Neustart/Reload in-place + literal `pool > 0`? Session-Neustart ist verifiziert (s. §3); der kontrollierte Reload-Test bleibt gated auf Freigabe.
