# Mission-Flows-Katalog: `logic/dom/*.logic` + `ActivateMissionFlow` (Build 2.0.58485)

Spike zu Issue **#513** (Teil von #508). RE-/Daten-Recherche, **keine**
Feature-Implementierung. Stand: 2026-09-15, lokal (Daten-Packs + DLL)
gegen Build **2.0.58485** (GOG == Dedi, byte-identisch zu `planet`).

Ziel: beantworten, welche Mission-Flows es unter `logic/dom/` gibt, was sie
wirken, und ob sie für unser **Wave-Send** nutzbar sind — plus ein
Disasm-Beleg, was `MissionService::ActivateMissionFlow` /
`DeactivateMissionFlow` wirklich tun.

## 0. Kurzfassung (TL;DR)

- `logic/dom/*.logic` sind **reine Ankündigungs-/HUD-/Audio-/Dialog-Flows**
  ("Achtung, Angriff in X s"). Sie enthalten **keinen** Entity-/Unit-Spawner.
- Der **echte Wellen-Spawn** liegt **nicht** in `logic/dom/`, sondern in
  `logic/missions/survival/attack_level_<L>_id_<N>_<biome>.logic` (per
  `rules.waves` → `dom_manager:SpawnWave`).
- `MissionService::ActivateMissionFlow` (RVA `0xF93280`) und
  `DeactivateMissionFlow` (RVA `0xF960E0`) sind **dünne Resolver-Wrapper**:
  `this[+8] = World*` → `TypeRegistry::GetType<MissionSystem>` →
  `World::FindSystem(TypeHash)` → `MissionSystem::ActivateMissionFlow`
  (`0x33A7E0`) bzw. `MissionSystem::DeactivateMissionFlow` (`0x33C890`).
- **Für Wave-Send ist `logic/dom/*` nicht nutzbar** (kein Spawn). Nutzbar ist
  es als **HUD-Gegenstück** (Countdown/Objective), wenn eine Welle von außen
  getriggert wird.

## 1. Disasm-Beleg: was `ActivateMissionFlow` / `DeactivateMissionFlow` starten

Tooling: `RBB_DLL=<...>/riftbreaker_dll_win_release.dll \
~/.venvs/rb-re/bin/python tools/re/disasm.py <RVA> <n>` (capstone + pefile).
Symbolnamen aus dem `S_PUB32`-Stream der PDB (`publics_dll.txt`, RVA-Formel
`.text` = `0x1000` + decimal, siehe `.agents/skills/riftbreaker-re/SKILL.md`).

### Symbole (PDB-publics → RVA)

| RVA | Symbol (demangled) |
|---|---|
| `0xF93280` | `UtfString MissionService::ActivateMissionFlow(UtfString const&, UtfString const&, UtfString const&, Database*)` |
| `0xF960E0` | `void MissionService::DeactivateMissionFlow(UtfString const&)` |
| `0x33A7E0` | `UtfString MissionSystem::ActivateMissionFlow(UtfStringView, UtfString const&, UtfString const&, Database*)` |
| `0x33C890` | `void MissionSystem::DeactivateMissionFlow(UtfStringView)` |
| `0x1CDB3B0` | `Exor::World::FindSystem(TypeHash)` |
| `0x30A160` | `TypeRegistry::GetType<MissionSystem>` |
| `0x2573DC0` | `Exor::GetTypeRegistry()` |

### `MissionService::ActivateMissionFlow` — `0xF93280`

```
mov  rbx,[rcx+8]            ; rcx=this(MissionService) → this[+8] = World*
call 0x182573dc0            ; Exor::GetTypeRegistry()
mov  rcx,rax
call 0x18030a160            ; TypeRegistry::GetType<MissionSystem>() → Type*
mov  edx,[rax+0x30]         ; +0x30 = TypeHash(MissionSystem)
mov  rcx,rbx
call 0x181cdb3b0            ; World::FindSystem(TypeHash) → MissionSystem*
test rax,rax
je   <leerer UtfString>     ; System nicht gefunden → leere Rückgabe
...
call 0x18033a7e0            ; MissionSystem::ActivateMissionFlow(...)
```

- **Signatur (4-Arg-Overload):** `(UtfString const& name, UtfString const&
  logicPath, UtfString const& start_point, Database* data)` — Rückgabe ist ein
  `UtfString` über versteckten Out-Pointer (`rdi`).
- Rückgabe = der **registrierte Flow-Name** (bei `name==""` generiert das Spiel
  einen). Genau dieser String wird in Lua als `currentLogicFile` /
  `onUpgradeHqLogicEndFileName` gemerkt und später an `DeactivateMissionFlow`
  gegeben (siehe `dom_manager.lua`).
- Die Methode selbst **startet nichts** — sie löst nur `World → MissionSystem`
  auf und delegiert. Die eigentliche Aktivierung liegt in `0x33A7E0`.

### `MissionService::DeactivateMissionFlow` — `0xF960E0`

```
mov  rbx,[rcx+8]            ; World*
call 0x182573dc0            ; GetTypeRegistry()
call 0x18030a160            ; GetType<MissionSystem>
mov  edx,[rax+0x30]
call 0x181cdb3b0            ; World::FindSystem → MissionSystem*
...
call 0x18033c890            ; MissionSystem::DeactivateMissionFlow(UtfStringView)
```

- **Signatur:** `(UtfString const& name)`; identische Resolver-Kette.

### Konsequenz für die Bridge

- `activate_mission_flow` / `deactivate_mission_flow` (bereits im
  Readiness-Gate von #479 gelistet) treffen genau diese zwei Wrapper.
- Der Flow wird **nur** über `logicPath` (Arg 2) bestimmt; Arg 1 (`name`) ist
  der Handle zum späteren Deaktivieren. Arg 3 (`start_point`, i.d.R.
  `"default"`) wählt den Einstiegs-Self-ID im Flow-Graph; Arg 4 (`Database*`)
  injiziert Parameter (u.a. `time_max` / `spawn_point`).
- **Thread-Hinweis:** die Funktionen sind pure C++-Resolver, aber sie rufen in
  `MissionSystem` Graph-/Lua-Code an → wie gehabt nur über das Readiness-Gate
  (#479) und mit Vorsicht auf dem richtigen Thread aufrufen.

## 2. Was `logic/dom/*.logic` wirklich sind (Node-/Script-Inventar)

Alle `.logic`-Dateien sind Text-Graph-Templates (`FlowGraphTemplate`,
`version "5"`). Über den gesamten `logic/dom/`-Satz treten **nur** diese
Node-Typen auf:

| Node-Typ | Rolle |
|---|---|
| `LuaBehaviourNode` | Träger eines Lua-Scripts (+ `database`-Params) |
| `NodeSelectorNode` | Verzweigung nach Zustand (z.B. `logic_if_biome`, `logic_random_output`) |
| `OrBehaviourNode` | ODER-Verknüpfung |
| `DialogGroupNode` | Dialoggruppe |
| `EndFlowGraphNode` | Flow-Ende (`event_end.lua`) |

Verwendete Lua-Scripts (alle vorliegenden dom-Flows):

- `lua/graph/mission_state/event_start.lua`, `…/event_end.lua`
- `lua/graph/objective/objective_spawn.lua` (HUD-Objective mit Timer, **kein Unit-Spawn**)
- `lua/graph/audio/audio_adaptive_music_system.lua`, `…/audio_music_change_playlist.lua`
- `lua/graph/interface/interface_dialogue_show.lua`
- `lua/graph/logic/logic_if_biome.lua`, `…/logic_random_output.lua`,
  `…/logic_wait.lua`, `…/logic_if_global_variable.lua`,
  `…/logic_event_send.lua`

→ **Kein** `entity_spawn*` / `unit_spawn*` / Spawner-Node in irgendeinem
`logic/dom`-Flow. Die Flows sind reine **Ankündigung + HUD-Countdown +
Musik/Dialog** ("wave incoming"). Bestätigt durch `grep` über die Node-Typen
und die Script-Listen aller 12 dom-Dateien.

## 3. Katalog

### 3.1 Vorhandene Dateien

`logic/dom/` in `00_win_data.zip` (Basis) und `03_win_data.zip` (Override).
Beide Pack-Versionen unterscheiden sich (03 überschreibt), daher hier je Datei
die Herkunft. `.bindings`-Dateien deklarieren den externen Parameter
`time_max` (Float).

| Flow-Datei | Pack(s) | bindings `time_max` |
|---|---|---|
| `attack_level_1_entry.logic` | 00, 03 | ja (0.000) |
| `attack_level_2_entry.logic` | 00, 03 | nein (leer) |
| `attack_level_1_prepare.logic` | 00 | nein (leer) |
| `attack_level_2_prepare.logic` | 00 | nein (leer) |
| `major_attack_1_entry.logic` | 00 | ja (0.000) |
| `major_attack_1_exit.logic` | 00 | ja (0) |
| `hq_upgrade_level_1_entry.logic` | 00, 03 | ja (0.000) |
| `hq_upgrade_level_1_exit.logic` | 00 | ja (0) |
| `hq_upgrade_level_2_entry.logic` | 00 | ja (0.000) |
| `hq_upgrade_level_2_exit.logic` | 00 | ja (0) |
| `hq_upgrade_level_3_entry.logic` | 00 | ja (0.000) |
| `hq_upgrade_level_3_exit.logic` | 00 | ja (0) |

(In `00_win_data.zip` sind `hq_upgrade_level_2_entry` und `level_3_entry`
byte-identisch — md5 `9fad49…`/`efd7ef…` sind verschieden, aber Inhalt/Struktur
gleich bis auf `self_id`-Kollision; `hq_upgrade_level_1_exit` und
`level_3_exit` haben dieselbe Größe 2980 B.)

### 3.2 Was jeder Flow wirkt (Inhalt)

Gemeinsames Schema jedes dom-Flows:

1. `event_start.lua` (`self_id "default"`) — Einstieg; setzt
   `rule_headquarters_survive "1"`.
2. HUD-Objective (`objective_spawn.lua` + `lua/objectives/generic_timer.lua`)
   mit `display_name`/`time_max` → der HUD-Countdown.
3. Musik-/Dialog-Nodes, per `logic_random_output` / `logic_if_biome` verzweigt.
4. `event_end.lua` — Flow-Ende.

Konkret je Flow:

| Flow | HUD-Objective (`display_name`) | `time_max` | Besonderheit |
|---|---|---|---|
| `attack_level_1_entry` | `…/generic/attack_incoming` | 30 (hart) | `logic_if_biome jungle` |
| `attack_level_2_entry` | `…/generic/attack_incoming` | 45 (hart) | kein Biome-Zweig |
| `attack_level_1_prepare` | — (nur Dialog/Audio) | n/a | 6 Dialog-Random-Outputs + OR-Gate |
| `attack_level_2_prepare` | — (nur Dialog/Audio) | n/a | idem |
| `major_attack_1_entry` | `…/generic/attack_incoming` | via binding | `logic_if_biome` caverns/swamp + `logic_event_send "OnMajorAttackStart"` |
| `major_attack_1_exit` | — | 0 | nur Musik + `logic_event_send` |
| `hq_upgrade_level_1_entry` | `…/upgrade_hq/lvl1_prepare_for_attack` (+ `attack_incoming`) | via binding (prepareTime) | Voice `mech_upgrade_hq_03`/`ashley_upgrade_hq_03` |
| `hq_upgrade_level_1_exit` | — | 0 | Voice `…_upgrade_hq_04` |
| `hq_upgrade_level_2_entry` | `…/upgrade_hq/lvl1_prepare_for_attack` | via binding | wie lvl1 |
| `hq_upgrade_level_2_exit` | — | 0 | Dialoggruppe |
| `hq_upgrade_level_3_entry` | wie lvl2 | via binding | wie lvl2 |
| `hq_upgrade_level_3_exit` | — | 0 | wie lvl1_exit |

`major_attack_1_entry` ist der einzige dom-Flow mit **Event-Egress**:
`logic_event_send.lua`, `event_name "OnMajorAttackStart"` (`is_event_local 0`).

### 3.3 Auslöser / Einstieg / Spawn / Team / Parameter

„Team" gibt es in keinem dom-Flow — Team-Zugehörigkeit lebt ausschließlich in
`rules.waves` (Spawn-Target `type "headquarters"`, `spawn_type
"RandomBorderInDistance"`).

| Flow | Einstieg (wer ruft `ActivateMissionFlow`) | Spawn? | Team? | Parameter |
|---|---|---|---|---|
| `attack_level_1_entry` / `attack_level_2_entry` | `dom_manager:OnEnterSpawn` → `rules.wavesEntryDefinitions[currentDifficultyLevel]` | **nein** | n/a | `time_max` (30/45), Biome-Zweig |
| `attack_level_1_prepare` / `attack_level_2_prepare` | `dom_manager:OnEnterPrepareSpawn` → `rules.prepareAttackDefinitions[currentDifficultyLevel]` (3-Arg, ohne `data`) | **nein** | n/a | nur Dialog/Audio |
| `major_attack_1_entry` | `dom_manager:OnHqEnterEntryLogic` → `rules.majorAttackLogic[1].entryLogic` | **nein** | n/a | `time_max` = `prepareTime`; Event `OnMajorAttackStart` |
| `major_attack_1_exit` | `dom_manager:OnHqEnterExitLogic` → `rules.majorAttackLogic[1].exitLogic` | **nein** | n/a | `time_max` = 0 |
| `hq_upgrade_level_{1,2,3}_entry` | `dom_manager:OnHqEnterEntryLogic` → `rules.buildingsUpgradeStartsLogic[i].entryLogic` | **nein** | n/a | `time_max` = `prepareTime` (aus `data`) |
| `hq_upgrade_level_{1,2,3}_exit` | `dom_manager:OnHqEnterExitLogic` → `…exitLogic` | **nein** | n/a | `time_max` = 0 |

Quellen (Lua): `lua/missions/v2/dom_manager.lua`
· `:1262` (prepare), `:1264-1265` (`prepareAttackDefinitions`),
`:1498`/`:1594` (hq entry/exit), `:1704` (`wavesEntryDefinitions`).
`dom_manager:OnMissionFlowDeactivatedEvent` (`:892`) nimmt den Flow-Namen aus
`event:GetName()` und räumt `spawnedAttacks`/`upgradeHqWaves` auf; bei
`onUpgradeHqLogicEndFileName` wechselt der HQ-State zurück.

### 3.4 Ablage der Zuordnung in den Rules

Die Pfad-Strings stehen in den Missions-Rules, verdrahtet über vier Tabellen:

| Rule-Tabelle | Ziel (dom-Flows) | gelesen in `dom_manager` |
|---|---|---|
| `rules.wavesEntryDefinitions[]` (String-Array, Index = Difficulty 1..9) | `attack_level_1_entry`, `attack_level_2_entry` | `OnEnterSpawn` (`:1704`) |
| `rules.prepareAttackDefinitions[]` (String-Array) | `attack_level_1_prepare`, `attack_level_2_prepare` | `OnEnterPrepareSpawn` (`:1264`) |
| `rules.majorAttackLogic[]` (`{level, minLevel, prepareTime, entryLogic, exitLogic}`) | `major_attack_1_entry/exit` | `:697-700` |
| `rules.buildingsUpgradeStartsLogic[]` (`{name, level, prepareTime, entryLogic, exitLogic}`) | `hq_upgrade_level_{1,2,3}_entry/exit` | `:874-877` |

Beobachtungen zur **Aktivität**:

- **Survival v2** (`dom_survival_*_rules_*.lua`): `wavesEntryDefinitions` aktiv
  (immer `attack_level_1_entry` bzw. `attack_level_2_entry`);
  `buildingsUpgradeStartsLogic` ist dort **auskommentiert** (HQ-Upgrade-Flows
  in Survival ungenutzt); `prepareAttackDefinitions` nur auskommentiert
  (z.B. `dom_survival_caverns_rules_default.lua:179`).
- **Campaign** (`dom_*_resource_outpost_rules_*.lua`, `dom_headquarters_*`,
  `dom_jungle/desert/acid/...`): `majorAttackLogic` + `prepareAttackDefinitions`
  aktiv (Großangriff/Prepare-Sequenzen).
- **Prolog** (`dom_prologue_rules_*.lua`) nutzt ein **anderes Schema**
  (`{ name = "headquarters_lvl_2", logic = "logic/hq_upgrade/upgrade_lvl1.logic" }`),
  das die v2-`dom_manager`-Felder `entryLogic`/`exitLogic` **nicht** liest →
  Prolog-HQ-Flows liegen unter `logic/hq_upgrade/`, nicht `logic/dom/`.
- Campaign-Outposts verweisen teils auf `logic/missions/campaigns/story/…`
  statt `logic/dom/`.

### 3.5 Der echte Wellen-Spawn (Abgrenzung)

`dom_manager:SpawnWave` / `SpawnWavesForDifficultyLevel` ziehen aus
`rules.waves[groupId][difficulty]` Einträge der Form

```lua
{ name = "logic/missions/survival/attack_level_1_id_1_desert.logic",
  spawn_type = "RandomBorderInDistance", spawn_type_value = nil,
  target_type = "Type", target_type_value = "headquarters",
  target_min_radius = 180.0, target_max_radius = 350.0 }
```

und rufen `MissionService:ActivateMissionFlow("", waveData.name, "default",
self.data)` mit `self.data.spawn_point = <RandomBorderInDistance-Ziel>`. Der
Spawn kommt also **aus `logic/missions/survival/attack_level_*_id_*`**, nicht
aus `logic/dom`.

## 4. Ergebnis-Tabelle: Flow → Wirkung → nutzbar für Wave-Send?

| Flow-Name | Wirkung | Nutzbar für Wave-Send |
|---|---|---|
| `attack_level_1_entry.logic` | HUD „attack_incoming" (30 s) + Dialog/Musik + Biome-Zweig | **nein** (spawnt nichts) |
| `attack_level_2_entry.logic` | HUD „attack_incoming" (45 s) + Dialog/Musik | **nein** |
| `attack_level_1_prepare.logic` | Dialog-/Audio-Ankündigung (kein HUD-Timer) | **nein** |
| `attack_level_2_prepare.logic` | Dialog-/Audio-Ankündigung | **nein** |
| `major_attack_1_entry.logic` | HUD-Countdown + Biome-Musik + Event `OnMajorAttackStart` | **nein** (Event nur; Spawn extern) |
| `major_attack_1_exit.logic` | Musik-Ausklang + Event | **nein** |
| `hq_upgrade_level_1_entry.logic` | HUD „HQ-Upgrade, Prepare for attack" + Voice | **nein** |
| `hq_upgrade_level_1_exit.logic` | Voice/Dialog „HQ-Upgrade fertig" | **nein** |
| `hq_upgrade_level_2_entry.logic` | wie lvl1 (Entry) | **nein** |
| `hq_upgrade_level_2_exit.logic` | Dialoggruppe | **nein** |
| `hq_upgrade_level_3_entry.logic` | wie lvl2 | **nein** |
| `hq_upgrade_level_3_exit.logic` | wie lvl1_exit | **nein** |

**Nutzbar für Wave-Send:** keiner der `logic/dom`-Flows (kein Unit-Spawn).
Der sendbare Wellen-Flow ist ein `logic/missions/survival/attack_level_<L>_id_<N>_<biome>.logic`
aus `rules.waves`, aktivierbar über `MissionService:ActivateMissionFlow("", <pfad>, "default", data)`
(data mit `spawn_point`).

## 5. Praxis-Hinweise für die Bridge (kein Code in diesem Spike)

- Console-Command existiert im Spiel: `activate_mission_flow <name> [logicPath]`
  (`lua/commands/mission.lua`). Deaktivieren läuft über
  `MissionService:DeactivateMissionFlow(<name>)`; im Spiel-Console-Set gibt es
  dafür **keinen** registrierten Command (bestätigt in
  `dedicated-io-re-findings.md`, Phase „Control-Workflow").
- Um einen dom-Flow als HUD-Gegenstück zu einer von außen gesendeten Welle zu
  nutzen, muss der **Wave-Spawn-Flow** (aus `rules.waves`) aktiviert werden;
  `attack_level_*_entry` liefert nur die Ankündigung.

## 6. Offene Punkte / nicht abgedeckt (Timebox 0,5 Tag)

- **Byte-genaue Diff-Analyse** 00- vs. 03-Pack-Varianten (03 überschreibt) —
  hier nur Inventar + md5, kein inhaltlicher Diff.
- **Dedizierter-Server-Wirksamkeit** der HQ-/Prepare-Flows nicht live getestet
  (Server läuft Survival; die Flows sind in Survival v2 auskommentiert).
- **Parameter-Semantik** von `start_point` (Self-ID im Graph) nur strukturell
  belegt (Bindings/`self_id`), nicht für jeden Flow einzeln durchexerziert.
- `logic/hq_upgrade/*.logic` (Prolog-Campaign) wurde **nicht** katalogisiert
  (außerhalb von `logic/dom/`, siehe §3.4).

## 7. Refs

- Issue #513 (Spike), Parent #508 (Catalog of Things), #385
  (ActivateMissionFlow-Primitiv), #479 (Readiness-Gate).
- `.agents/skills/riftbreaker-re/SKILL.md`, `docs/research/dedicated-io-re-findings.md`.
- Daten: `tools/re/rbpack.py` über `00_win_data.zip` / `03_win_data.zip`.
- Disasm: `tools/re/disasm.py` gegen `riftbreaker_dll_win_release.dll` (2.0.58485).
