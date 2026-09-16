# #515 — Survival-Modi-Struktur: Solo vs Koop vs Campaign (`dom_mananger`)

**Spike (reine Recherche/Doku), Timebox 0,5 Tag.** Refs **#508** (Catalog of Things, §6) ·
**#514** (Wave-Takt) · **#476** (Naturwellen aus).
Quelle: `lua-src` (planet/lan, `/home/momo/rb-game/lua-src/lua/`), **Build-agnostisch**
(reine Lua-Auswertung, kein DLL/PDB nötig). Datei-Zitat = `lua/missions/…`-Pfad aus `lua-src`.

## Frage

Wie sind Survival / Koop-Survival / Campaign vom Lua-State-Manager (`dom_mananger`)
aufgebaut — welche Modi existieren, wo unterscheiden sie sich, was ist für Solo / 1.0
relevant?

## TL;DR

- **Es gibt genau EINE Wellen-Engine** (`dom_mananger`, `v2/dom_manager.lua`, 1793 Zeilen)
  für **alle** Modi. Der Modus wird **nicht** in der Engine unterschieden, sondern
  ausschließlich über die **geladene Rules-Datei** + **Mission-Script** + **Difficulty-Variante**.
- Der Modus-Einhängepunkt ist immer identisch:
  `MissionService:AddGameRule("lua/missions/v2/dom_manager.lua", rulesPath)`
  (Survival `survival_<biome>.lua:32–34`; Campaign/Open analog).
- **Modi-Familien:** Survival (Solo **und** Koop) · Open-Campaign/Headquarters (Free Play) ·
  Story-Campaign · Prologue · (Dev-Benchmarks).
- **Solo vs Koop ist eine Laufzeit-Verzweigung im selben Modus** — kein eigener Modus.
  Der Kern ist `self:GetPlayersCounter()` (= `#PlayerService:GetConnectedPlayers()`,
  geklemmt auf 4). >1 Spieler ändert Intermission, Cooldown, Kreaturen-Stärke und schaltet
  einen zusätzlichen „Multiplayer-Wave" frei.
- **Solo/1.0:** Dedicated = Host + typischerweise 1 Spieler ⇒ alle `playersCounter > 1`-Zweige
  sind **tot**; relevant bleiben die `[playerCount == 1]`-Werte und die deklarativen
  Difficulty-Schalter (`sandbox`/`natural_waves`).

---

## 1 · Ein Engine, viele Modi — wo der Modus herkommt

`dom_mananger:init()` liest **nur** aus `self.rules` (die geladene Tabelle) und den
`DifficultyService`-Gettern. Es gibt **keine** `if mode == "survival"`-Verzweigung in der
Engine. Die Modus-Unterscheidung liegt vor der Engine:

| Schritt | Mechanik | Quelle |
|---|---|---|
| 1. Mission-Script | pro Modus/Biom eine eigene Lua-Datei (`survival_<biome>.lua`, `headquarters_<biome>.lua`, `…_scout.lua`, `…_resource_outpost.lua`, `prologue.lua`) | `lua/missions/…` |
| 2. Rules-Pfad | `GetRulesForDifficulty("<prefix>")` wählt `<prefix><difficulty>.lua` | `utils/rules_utils.lua:11` |
| 3. Engine-Bindung | `MissionService:AddGameRule("lua/missions/v2/dom_manager.lua", rulesPath)` — immer dieselbe Engine-Datei | `survival_desert.lua:34` |
| 4. Laufzeit-Difficulty | Engine ruft `DifficultyService:*` (Waves an/aus, Wave-Strength, Intermission, Mission-Infinite/Duration/Warmup) | `dom_manager.lua:149–158` u. a. |
| 5. Campaign-Typ (C++) | `CampaignService:GetCurrentCampaignType()` — Lua vergleicht nur `"survival"` / `"story"` | `survival_base.lua:20`, `mission_base.lua:286` |

`GetRulesForDifficulty` (`utils/rules_utils.lua:11–35`) löst die Variante auf:
`GetDomRulesScriptPostfix()` → sonst `IsCustomDifficulty()` ⇒ `"custom"` → sonst
`GetCurrentDifficultyName()` (easy/normal/hard/brutal…) → sonst Fallback `"default"`.

> **Ergebnis:** Der *Modus* ist Daten (Rule-Datei), nicht Code. Ein Modus-Wechsel ist ein
> anderer `rulesPath` im selben State-Manager.

---

## 2 · Modus-Landschaft (die Familien)

| Familie | Mission-Script | Rules-Ort | Campaign-Type | Besonderheit |
|---|---|---|---|---|
| **Survival** (Solo **&** Koop) | `survival/survival_<biome>.lua` (+ `survival_base.lua`) | `survival/v2/dom_survival_<biome>_rules_*` (v2), Legacy v1 in `survival/` | `"survival"` | 8 Biome × 7 Difficulty-Varianten = **56** Dateien; `saplings`-Inventar-Gate |
| **Open-Campaign / Headquarters** (Free Play) | `campaigns/open/headquarters/headquarters_<biome>.lua` | `campaigns/open/headquarters/dom_headquarters_<biome>_rules_*` | (nicht in Lua-Literalen — C++ „open") | HQ-Fokus, `baseTimeBetweenObjectives = 2400` |
| **Story-Campaign** | `campaigns/story/**` (`headquarters.lua`, `<biome>_scout.lua`, `<biome>_resource_outpost.lua`, `<biome>_find_*.lua`) | `campaigns/story/**/dom_*_rules_*` + `story/v2/<biome>/dom_template_*` | `"story"` | pro Biom unterschiedliche Szenario-DOMs + `template_universal.lua` |
| **Prologue** (Tutorial) | `campaigns/prologue/prologue.lua` | `campaigns/prologue[/v2]/dom_prologue_rules_*` | — | `pauseAttacks = true`, `maxObjectivesAtOnce = 0` (keine Naturwellen) |
| **Benchmarks** (Dev) | `missions/benchmarks/full_base.lua` | — | — | `AddGameRule` **auskommentiert** — kein DOM |

Biom-Set (Survival): `acid, caverns, desert, ice, jungle, magma, metallic, swamp`.
Struktur der Story-Szenarien (pro Biom): `dom_template_<biome>_resource_rules_*` (HQ/Basis),
`dom_<biome>_resource_outpost_rules_*`, `dom_<biome>_scout_rules_default`,
`dom_<biome>_find_new|rare|samples_resource_rules_default`.

**Koop-Survival** ist **kein** eigener Modus in dieser Liste — es ist **Survival** mit
`playersCounter > 1` (siehe §5).

---

## 3 · Der gemeinsame State-Manager

Drei State-Machines in `dom_mananger:init()` (`v2/dom_manager.lua:106–120`):

| State-Machine | States | Zweck |
|---|---|---|
| `spawner` | `spawn`, `wait`, `cooldown_after_spawn`, `prepare_spawn`, `idle`, `streaming`, `sleep`, `dummy_state` | Wellen-Takt |
| `upgradeHQ` | `hq_entry_logic`, `hq_attack_logic`, `hq_exit_logic` | HQ-Upgrade / Major-Attack (nur Campaign-Rules) |
| `difficultyIncrease` | `difficulty_increase` | Difficulty-Stufen 1..9 (`timeToNextDifficultyLevel`) |

Zyklus (Survival, `pauseAttacks == false`):

```
wait(5s) ─► streaming ─► spawn ─► cooldown_after_spawn ─► idle ─► prepare_spawn ─► streaming ─► …
```

- `maxDifficultyLevel = 9`, Start `currentDifficultyLevel = 1` (`Activated()`).
- `freezedDifficultyLevel` = Obergrenze (klemmt `currentDifficultyLevel`), setzbar per
  LuaEvent `DOMMaxDifficultyLevel`.
- `wait` ist **suspendiert** (SetSuspended) — die erste Welle hängt am Ende des Warmups /
  HQ-Placement, nicht am Wave-Timer (#514, #508 §1.2).
- `sleep` (fix `sleepSafeTime = 1200`) wird bei HQ-Upgrade/Major-Attack genutzt → nur
  Campaign-Pfad relevant.

> Der Takt selbst (Timer, HUD-Countdown, `interval` → `prepareSpawnTime`) ist im Rahmen
> von **#514** (Wave-Takt, Spike geplant, noch nicht geschrieben) dokumentiert und hier
> **nicht** dupliziert. Die Engine-Details (Wellen-Pools, Bosse, Spawn-Punkte) stehen im
> Rahmen von **#508** (Catalog of Things, Spike geplant, noch nicht geschrieben).
> Beide Docs liegen noch nicht in `docs/research/` vor — bis dahin gelten die Refs als
> Issue-Referenzen ohne Dateilink.

---

## 4 · Wo sich die Modi unterscheiden (Rules-Schlüssel)

Die Rules-Dateien teilen dasselbe Schema; die Modi unterscheiden sich über **welche Keys
gesetzt/weggelassen** werden. Vergleich (Default-Varianten):

| Key | **Survival** (`dom_survival_desert_rules_default`) | **Open/HQ** (`dom_headquarters_desert_rules_default`) | **Story-Template** (`dom_template_metallic_resource_rules_default`) | **Prologue v2** |
|---|---|---|---|---|
| `prepareAttacks` | **`false`** | `true` | `true` | `true` |
| `eventsPerIdleState` | **`0`** | `2` | `2` | `0` |
| `eventsPerPrepareState` | `1` | `0` | `0` | `1` |
| `maxObjectivesAtOnce` | `2` | `1` | `1` | `0` |
| `baseTimeBetweenObjectives` | nicht gesetzt (Default 400 aus Engine) | `2400` | `1800` | – |
| `idleTime[1..9]` | **alle `0`** | > 0 | > 0 | > 0 |
| `majorAttackLogic` | **fehlt** | (in HQ-Rules) | **vorhanden** | – |
| `buildingsUpgradeStartsLogic` | (auskommentiert/leer) | vorhanden | vorhanden | leer |
| `prepareAttackDefinitions` | – | vorhanden | vorhanden | – |
| `bosses`-Logic | `logic/missions/survival/attack_boss_dynamic.logic` | `headquarters_boss_attack.logic` | `headquarters_boss_attack.logic` | – |
| `pauseAttacks` | `false` (außer `sandbox`-Difficulty) | `false` | `false` | **`true`** |

**Lesart:**

- **Survival** ist der *nackte* Wellen-Takt: keine HQ-Upgrade-Logik, keine Idle-Phase
  (`idleTime == 0`), keine Major-Attacks — nur `prepare_spawn → streaming → spawn →
  cooldown` im 9-stufigen Difficulty-Aufstieg. `eventsPerIdleState = 0` (kein Idle-Event),
  `eventsPerPrepareState = 1` (ein Event in der Vorwarnzeit).
- **Campaign/Open/HQ** schaltet den HQ-Pfad (`upgradeHQ`-SM), Idle-Events und Objectives
  hinzu. `prepareAttacks = true` bedeutet: die Welle wird schon in `prepare_spawn`
  vorbereitet (`PrepareWave` + Marker), nicht erst im `spawn` (Survival).
- **Prologue** fährt `pauseAttacks = true` ⇒ **keine** Naturwellen (Tutorial).
- **`prepareAttacks`-Wirkung im Code:** `OnEnterPrepareSpawn` füllt nur bei `true`
  `self.preparedAttacks`; `OnEnterSpawn` nimmt dann `SpawnPreparedWave` statt `SpawnWave`.

Mission-Flow beim Start (Survival-Muster, `survival_jungle.lua:11–34`):

```lua
database:SetFloat("mission_duration", DifficultyService:GetMissionDuration())
database:SetFloat("final_wave_time", DifficultyService:GetMissionDuration() - 300)
database:SetFloat("warmup_duration", DifficultyService:GetWarmupDuration())
database:SetString("mission_infinite", DifficultyService:IsMissionInfinite() and "1" or "0")
MissionService:ActivateMissionFlow("", "logic/missions/survival/default.logic", "default", database)
```

⇒ `mission_duration` / `warmup_duration` / `mission_infinite` kommen aus der **Difficulty**,
nicht aus dem Modus-Script — sie sind der deklarative Endlos-/Zeit-Schalter
(`sandbox` ⇒ `mission_infinite 1`, `mission_duration 0`; vgl. #476).

---

## 5 · Koop-Unterschiede (Solo vs `playersCounter > 1`)

Alles hängt an **einem** Zähler:

```lua
function dom_mananger:GetPlayersCounter()
    local playersCount = #PlayerService:GetConnectedPlayers()
    if playersCount > 4 then return 4 end
    return playersCount
end                                        -- dom_manager.lua:1126–1132
```

| Wirkung | Solo (`== 1`) | Koop (`> 1`) | Quelle |
|---|---|---|---|
| **Intermission** (`prepareSpawnTime`) | voller Rules-Wert | `− (players−1) * DifficultyService:GetWaveIntermissionMultiplier()` | `GetPrepareSpawnTime` `:1135–1146` |
| **Cooldown** nach Spawn | voller Rules-Wert | `÷ (1 + players * DifficultyService:GetWaveCooldownPerPlayerFactor())` | `GetCooldownAfterAttacksTime` `:1148–1158` |
| **Multiplayer-Wave** | `clamp(additionalWaves, 0, 1)` | `clamp(additionalWaves + 1, 0, 1)` | `GetMultiplayerAttackCount` `:1115–1124` |
| **Kreaturen-Basis-Difficulty** | Index `[1]` aus `creatureDifficultyIncrementPerDOMDifficulty` | Index `[playersCounter]` (2..4) | `Increase/Revert/UpdateCreaturesBaseDifficulty` `:1180–1212` |
| **Wave-Pool** | nur `rules.waves[group]` | zusätzlich `rules.multiplayerWaves`-Pool | `GetMultiplayerWavePool` `:1087–1102` |

Details:

- **`multiplayerWaves`** ist ein eigener Pool (`rules.multiplayerWaves[1..9]`, Länge **muss**
  = 9 sein, sonst `Assert`). Er enthält pro Level `{ additionalWaves = N, waves = {…} }`
  (typisch 1 Boss-Wave `attack_boss_dynamic.logic`). In Survival-Default ist
  `additionalWaves = -1` für Level 1 und `0` für 2..9.
  - Solo: `clamp(-1,0,1) = 0` bzw. `clamp(0,0,1) = 0` ⇒ **kein** MP-Wave.
  - Koop: `clamp(-1+1,0,1) = 0` (L1) bzw. `clamp(0+1,0,1) = 1` (L2..9) ⇒ **1 zusätzliche
    Wave** ab Level 2.
  - Eingehängt in `prepare_spawn` (`:1279`), HQ-Entry/Attack (`:1526/:1566`) und `spawn`
    (`:1688`).
- **Spielerzahl-Änderung zur Laufzeit** (Join/Leave) wird in `Update()` erkannt
  (`:176–180`): bei Wechsel erst `RevertCreaturesBaseDifficulty()` (alle bisherigen
  Inkremente abziehen), dann `UpdateCreaturesBaseDifficulty()` (mit neuem Index neu
  aufsummieren). ⇒ Kreaturen-Stärke ist **spielerzahl-gekoppelt** und wird live neu
  gerechnet.
- **Event-Manager** adressiert ebenfalls Spieler: viele Events nutzen
  `PlayerService:GetLeadingPlayer()` (Ressourcen/Ammo/Research) bzw. `GetAllPlayers()`
  (z. B. `full_ammo`). Die „Multiplayer-Label" `participants` /
  `participantsPercentageUse` (`PrepareLabels`) markieren Teilnehmer im Koop.
- **Quest-/Objective-Events** mit `gameStates="…|NO_STREAMING"` vs `"…|STREAMING"` sind
  **nicht** Solo/Koop, sondern Streaming-Session-Varianten („Riftbreaker-Streaming").

---

## 6 · Difficulty-Varianten der Rules + v1/v2

Pro Modus/Biom liegen mehrere Dateien mit gleichem Basisnamen und variierendem Suffix:

`default` · `easy` · `normal` · `hard` · `brutal` · `custom` · `sandbox`

Auswahl über `GetRulesForDifficulty` (§1). Muster (Survival, desert):

- `_default.lua` = Basis (setzt Pools, alle Timer, Bosse, `gameEvents`).
- `_easy/_normal/_hard/_brutal.lua` = `require` die Default und **überschreiben gezielt**
  (`timeToNextDifficultyLevel`, `prepareSpawnTime`, `maxAttackCountPerDifficulty`,
  `creatureDifficultyIncrementPerDOMDifficulty`, teils `waves` mit `_alpha`/`_ultra`).
  - `normal` überschreibt z. B. nur `prepareSpawnTime` + `maxAttackCountPerDifficulty`.
  - `easy` zusätzlich `maxObjectivesAtOnce = 1`.
  - `hard`/`brutal` zusätzlich `waves`, `objectivesLogic`, `creatureDifficulty…`.
- `_custom.lua` = liest `DifficultyService:GetWaveStrength()` und delegiert an
  easy/normal/hard/brutal/default; überschreibt dann `prepareSpawnTime` (alle 9 Level) mit
  `GetWaveIntermissionTime()` und skaliert `maxAttackCountPerDifficulty[i] *= GetAttacksCountMultiplier()`.
- `_sandbox.lua` = `require` Default + leert `buildingsUpgradeStartsLogic`; der
  „Waves-aus"-Effekt kommt **nicht** aus der Datei, sondern aus
  `DifficultyService:GetWaveStrength() == "sandbox"` ⇒ `pauseAttacks = true`
  (`dom_manager.lua:149–158`, Log ` sandbox mode on - pausing attacks.`).

**v1 vs v2:** Der Missions-Code lädt **immer** `…/v2/…` (z. B. `survival_*:32`). Die
v1-Dateien unter `missions/survival/dom_survival_*_rules_*.lua` existieren parallel, decken
aber **nur 4 Biome** (acid, desert, jungle, magma) ab und sind **Legacy** — ice, metallic,
caverns, swamp haben **nur** v2. ⇒ **v2 ist die maßgebliche Quelle**; v1 nicht auswerten.

---

## 7 · Solo / 1.0-Relevanz

1. **Dedicated 1.0 = Solo-Host.** `GetPlayersCounter() == 1` ⇒ §5-Koop-Pfad inaktiv:
   keine `multiplayerWaves`, volle Intermission/Cooldown, Kreaturen-Index `[1]`.
   Wer Koop-Verhalten testen will, braucht **>1 verbundenen Client**; ein zweiter
   Server-Player ist kein Config-Flag.
2. **Modus-Steuerung ist deklarativ, nicht State-Gefummel:** `set difficulty "sandbox"`
   (Waves aus, Endlos, `mission_duration 0`) bzw. `natural_waves` (#476) — beides
   **server-seitig**, nicht pro Spieler.
3. **Der Readout ist modus-agnostisch:** `get_state` liefert `wave` / `dom_state` /
   `time_to_next` (DOM-Spawner-Timer, #514) **plus** die Service-Getter
   `mission name/biome/warmup/mission_duration/infinite/wave_strength/waves_disabled/
   creatures_difficulty` (`dedicated-io-re-findings.md`). Damit lässt sich der Modus zur
   Laufzeit erkennen (z. B. `infinite=1` + `waves_disabled=1` ⇒ Sandbox/Free-Play).
4. **`wave_strength` ist der stärkste Modus-Indikator** in den Survival-Rules:
   `sandbox` ⇒ `pauseAttacks`; `custom` ⇒ `_custom`-Rules-Pfad. Kein „Koop-Modus"-Feld.
5. **Free-Play/Blank-Slate (#476)** ist genau der Fall `pauseAttacks == true` +
   Mission ohne Naturwellen: Survival-Rules, aber `wave_strength=sandbox` — nicht ein
   eigener Modus.
6. **Campaign ≠ Survival in der Engine:** Campaign-Rules aktivieren `majorAttackLogic`,
   `prepareAttacks=true`, Idle-Events und den HQ-Pfad. Für 1.0-Solo-Dedicated ist nur der
   Survival-(Open-)Pfad ohne HQ-Upgrade-Komplexität relevant.

---

## 8 · Offene Punkte

- **Campaign-Type-Literale:** In Lua nur `"survival"` (`survival_base.lua:20`) und
  `"story"` (`mission_base.lua:286`) belegt. Der **Open-Campaign**-Typ der
  `campaigns/open/headquarters`-Familie ist **nicht** als Lua-Literal sichtbar (C++-Seite,
  `CampaignService:GetCurrentCampaignType()`) — hier aus Missions-Baum/Pfad **abgeleitet**,
  nicht hart belegt.
- **`DifficultyService`-Default-Werte** (`GetWaveIntermissionMultiplier`,
  `GetWaveCooldownPerPlayerFactor`, `GetAttacksCountMultiplier`, `GetMissionDuration`,
  `GetWarmupDuration`) sind C++-Getter; konkrete Zahlen nicht aus Lua belegt (nur Verwendung).
- **Live-Verifikation Koop:** `playersCounter`-Pfad (MP-Wave, Kreaturen-Index) ist
  aus dem Code hergeleitet, **nicht** live mit 2+ Clients verifiziert.
- **`participants`/`participantsPercentageUse`-Semantik** (Label-Markierung) ist
  code-abgeleitet, nicht live bestätigt (vgl. #508 §7.3).
- **Story-Szenario-DOMs** (`<biome>_scout`, `<biome>_find_*`, `…_resource_outpost`) sind
  hier als Familie erfasst, ihre Regel-Unterschiede **einzeln** noch nicht differenziert.

## Refs

#508 (Catalog of Things, §1 Waves / §6 Survival-Modi) · #514 (Wave-Takt `dom_mananger`) ·
#476 (Vanilla-Naturwellen-Schalter / Sandbox) · #278 (DOM-Timer-Readout) ·
`docs/research/dedicated-io-re-findings.md` (Read-Out-Felder) ·
Quellen: `lua-src/lua/missions/v2/dom_manager.lua`, `…/v2/event_manager.lua`,
`…/survival/**`, `…/campaigns/**`, `…/utils/rules_utils.lua`.
