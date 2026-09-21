# #798 — Event-Level des Base-Game-DOM (`currentEventLevel` / `event_manager`)

**Spike (reine Recherche/Doku), Timebox 0,5 Tag.** Refs **#796** (DOM-Replica-Spec §2 #9 /
§7.2 — hier wird diese Dimension als offene Lücke markiert) · **#515** (Modus-Landschaft) ·
**#646** (Kreaturen-Inkrement, Muster-Doc).
Quelle: Game-Pack `/srv/rbgame/packs/00_win_data.zip` (Build **2.0.58485**, byte-identisch
GOG == Dedi), extrahiert read-only auf planet per `unzip -p`. Datei-Zitat =
`lua/missions/…`-Pfad aus dem Pack (reine Lua-Auswertung, kein DLL/PDB nötig).

## Frage

Was ist das **Event-Level** des Base-Game-DOM — wie wird `currentEventLevel` gesetzt und
incrementiert, was treibt `event_manager` damit, und läuft es 1:1 mit dem Difficulty-Level
oder mit eigenem Timing? Was ist für Solo (1 Spieler) relevant?

## TL;DR

- **`currentEventLevel` ist ein zweiter, zum Difficulty-Level paralleler Zähler** im
  `event_manager` (Basis-Klasse von `dom_mananger`). Start `1`, Increment **exakt im selben
  Trigger** wie `currentDifficultyLevel` (`OnExitDifficultyIncrease`), **identischer Clamp**
  auf `freezedDifficultyLevel`. ⇒ **kein eigenes Intervall/Timing — es läuft 1:1 mit dem
  Difficulty-Level mit** (beide steigen gleichzeitig, beide enden bei max 9).
- Der `event_manager` treibt damit **drei Dinge**: (1) **Events** (`rules.gameEvents`),
  (2) **Objectives/Quests** (`rules.objectivesLogic`), (3) **Ressourcen-Skalierung** von
  `add_resource`/`remove_resource` (Prozentsatz wächst linear mit dem Event-Level).
- **Filter:** `gameEvents` wird je Event-Level über `minEventLevel`/`maxEventLevel` gefiltert
  (plus `gameStates`/`type`/Streaming-Session); `objectivesLogic` nur über `minDifficultyLevel`
  (das `maxDifficultyLevel`-Feld der Tabelle ist **toter Code**).
- **Survival-Timing:** Events/Objectives feuern **nur** in `prepare_spawn` (Vorwarnfenster,
  `eventsPerPrepareState = 1`); `idleTime = 0` und `eventsPerIdleState = 0` machen den
  Idle-Zweig tot. Objective bei **20 %**, Event bei **35 %** des Vorwarnfensters
  (`idleTimeObjectiveMul = 0.2` / `idleTimeEventMul = 0.35`).
- **Solo:** der Event-Manager ist weitgehend solo-agnostisch — Ressourcen/Ammo/Research
  adressieren `PlayerService:GetLeadingPlayer()`; das Label-System (`PrepareLabels`,
  `participants`/`label_name`/`labels_percentage_use`) markiert nur im Koop/Streaming
  Teilnehmer, in Solo bleibt `participants = ""` und der Prozentsatz `0`.

---

## 1 · Zwei Level, ein Trigger — `currentEventLevel` vs `currentDifficultyLevel`

`dom_mananger` **erbt** von `event_manager` — es ist dieselbe Instanz, kein separater Service:

```lua
local event_manager = require( "lua/missions/v2/event_manager.lua" )
class 'dom_mananger' ( event_manager )        -- dom_manager.lua:6
```

`event_manager:init()` setzt den Zähler auf **1** (`event_manager.lua:16`):

```lua
self.currentEventLevel = 1
```

Das Increment ist eine eigene, minimale Funktion (`event_manager.lua:118–127`):

```lua
function event_manager:IncreamentEventLevel( freezedDifficultyLevel )

	self.currentEventLevel = self.currentEventLevel + 1

	if ( self.currentEventLevel > freezedDifficultyLevel ) then
		LogService:Log( "event_manager:IncreamentEventLevel() : Event level will not rise. Clamped to  " .. tostring( freezedDifficultyLevel ) )
		self.currentEventLevel = freezedDifficultyLevel
	end

	LogService:Log( "event_manager:IncreamentEventLevel() - Event level : " .. tostring( self.currentEventLevel ) )
end
```

Aufgerufen wird sie **exakt an derselben Stelle** wie das Difficulty-Increment — in
`dom_mananger:OnExitDifficultyIncrease` (`dom_manager.lua:943–966`):

```lua
function dom_mananger:OnExitDifficultyIncrease( state )

	self:VerboseLog("OnExitDifficultyIncrease : Changing difficulty level." )

	if ( self.currentDifficultyLevel < self.maxDifficultyLevel ) then

		self.currentDifficultyLevel = self.currentDifficultyLevel + 1

		if ( self.currentDifficultyLevel > self.freezedDifficultyLevel ) then
			self.currentDifficultyLevel = self.freezedDifficultyLevel
		end

		self:VerboseLog("OnExitDifficultyIncrease : Difficulty level : " .. tostring( self.currentDifficultyLevel ) )

		self:IncreamentEventLevel( self.freezedDifficultyLevel )   -- dom_manager.lua:958

		self.difficultyIncrease:ChangeState( "difficulty_increase" )
	else
		self:VerboseLog("OnExitDifficultyIncrease : Difficulty level is max - " .. tostring( self.currentDifficultyLevel ) )
	end

	self:IncreaseCreaturesBaseDifficulty()
end
```

**Befund:** beide Zähler steigen **gemeinsam** (+1 im selben Aufruf), beide werden auf
`self.freezedDifficultyLevel` geklemmt. Der zweite Klemm-Pfad ist `OnLuaGlobalEvent`
(`DOMMaxDifficultyLevel`-Event), der **beide** Zähler nachzieht (`dom_manager.lua:834/837`):

```lua
if ( self.currentEventLevel > self.freezedDifficultyLevel ) then
	self.currentEventLevel = self.freezedDifficultyLevel
end
```

⇒ Es gibt **keinen** eigenständigen Event-Level-Timer. Das Event-Level ist schlicht der
**Spiegel des Difficulty-Levels** und hat denselben Lebenszyklus (Start 1, Ende ≤ 9).

> `freezedDifficultyLevel` (init `= self.maxDifficultyLevel = 9`) ist die Obergrenze, die per
> LuaEvent `DOMMaxDifficultyLevel` zur Laufzeit gesenkt werden kann (vgl. #515 §3).

---

## 2 · Der `event_manager` — Init & Konstanten

`event_manager:init()` (`event_manager.lua:10–74`) legt die relevanten Konstanten fest
(alle via `dom_mananger`-Instanz gültig):

| Feld | Wert | Bedeutung |
|---|---|---|
| `currentEventLevel` | `1` | Start-Event-Level |
| `resourcePercentageStep` | `3` | +X %-Punkte Ressourcen-Event pro Event-Level |
| `idleTimeEventMul` | `0.35` | Event feuert bei 35 % des Vorwarn-/Idle-Fensters |
| `idleTimeObjectiveMul` | `0.2` | Objective feuert bei 20 % des Fensters |
| `objectiveBaseTimeBetweenNext` | `400` | Basis-Abstand zwischen Objectives (s) |
| `objectiveMinRandTimeBetweenNext` / `…Max…` | `-100` / `100` | Random-Jitter auf den Objective-Abstand |
| `addResourcesOnRunOutTime` | `1200` | Cooldown für Ressourcen-Nachschub bei Run-Out |

`FillInitialParamsEventManager()` (`event_manager.lua:87–116`) legt fest:

```lua
self.excludeResourceList = { "uranium", "uranium_ore", "titanium", "palladium", "cobalt" }
self.resourceEvents = { "spawn_resource_comet", "spawn_resource_earthquake" }
self.availableResourcesToSpawn = { "carbon_vein", "iron_vein" }
```

`baseTimeBetweenObjectives` wird aus `rules.baseTimeBetweenObjectives` überschrieben, wenn
gesetzt (Survival setzt es **nicht** ⇒ Default `400` bleibt, vgl. #515 §4).

---

## 3 · Was `currentEventLevel` filtert — `gameEvents` & `objectivesLogic`

### 3.1 Events — `PrepareEvents(gameState)`

`event_manager:PrepareEvents()` (`event_manager.lua:384–609`) kopiert `rules.gameEvents`,
reichert `add_resource`/`remove_resource` an (jeweils ein Extra-Duplikat, damit sie
häufiger fallen), und **entfernt** dann jeden Eintrag, der eine Bedingung verletzt. Der
Event-Level-Filter ist `event_manager.lua:438–447`:

```lua
if ( ( data.minEventLevel ~= nil ) and ( data.minEventLevel > self.currentEventLevel ) ) then
	-- current event level … is not enough. Required : …
	table.insert( tableTmp, data )
end

if ( ( data.maxEventLevel ~= nil ) and ( data.maxEventLevel < self.currentEventLevel ) ) then
	-- current event level … is to high. Required : …
	table.insert( tableTmp, data )
end
```

Daneben gefiltert wird auf `gameStates` (Liste `"ATTACK|IDLE|STREAMING"` vs
`"IDLE|NO_STREAMING"`), `type` (`POSITIVE`/`NEGATIVE` via `AreNegativeEventsEnabled`) und
Streaming-Session. **Jedes Weather-/Attack-Event existiert doppelt** — einmal als
`…|STREAMING`- und einmal als `…|NO_STREAMING`-Variante (s. §4).

### 3.2 Objectives/Quests — `CheckObjective`

`event_manager:CheckObjective(checkLastObjectiveSpawnTime, checkCurrentEventLevel)`
(`event_manager.lua:305–382`) filtert `rules.objectivesLogic` **nur** über
`minDifficultyLevel` (`event_manager.lua:348`):

```lua
if ( data.minDifficultyLevel > self.currentEventLevel ) then
	-- Current event level … is not enough. Required : …
	shouldRemove = true
end
```

> **Befund (toter Code):** `rules.objectivesLogic` trägt auf manchen Einträgen ein
> `maxDifficultyLevel` (z. B. `destroy_nest_canoptrix_single` mit `maxDifficultyLevel = 5`),
> aber `CheckObjective` **liest es nie** (grep `maxDifficultyLevel` → nur dieser eine
> Rules-Eintrag, kein Konsument). Der Objective-Pool schrumpft also **nie wieder** mit
> steigendem Event-Level — er wächst nur (neue Objectives kommen ab `minDifficultyLevel` frei).

### 3.3 Ressourcen-Skalierung — linear mit dem Event-Level

`add_resource`/`remove_resource` übersetzen sich in `CheckResourceToAdd/Remove`
(`event_manager.lua:657` / `:787`) in eine Menge, die **linear vom Event-Level** abhängt:

```lua
data.amount = PlayerService:GetResourceLimit(leadingPlayer, resourceName )
	* ( ( data.basePercentage + ( self.resourcePercentageStep * ( self.currentEventLevel - 1 ) ) ) / 100 )
```

Mit `basePercentage` aus den Rules (`add_resource` = 30, `remove_resource` = 20) und
`resourcePercentageStep = 3` ergibt sich je Event-Level:

| Event-Level | `add_resource` (%-vom Limit) | `remove_resource` (%-vom Limit) |
|---|---|---|
| 1 | 30 % | 20 % |
| 2 | 33 % | 23 % |
| 3 | 36 % | 26 % |
| … | +3 %/Level | +3 %/Level |
| 9 | 54 % | 44 % |

⇒ Das Event-Level skaliert **nicht nur** die Event-/Objective-Auswahl, sondern auch die
**Menge** der Ressourcen-Belohnungen/-Abzüge.

---

## 4 · Die konkreten Daten (jungle default)

Quelle: `lua/missions/survival/v2/dom_survival_jungle_rules_default.lua` (`rules.gameEvents`
`:10–97`, `rules.objectivesLogic` `:194–200`). `_normal.lua` **überschreibt weder**
`gameEvents` noch `objectivesLogic` (grep leer) — für diese Schlüssel gilt `_normal` ==
`_default` (analog #646 §2).

### 4.1 `rules.gameEvents` — Meta-/Reward-Events (ohne `logicFile`)

```lua
{ action = "new_objective",       type = "POSITIVE", gameStates="IDLE|STREAMING",          minEventLevel = 3 },
{ action = "change_time_of_day",  type = "NEGATIVE", gameStates="ATTACK|IDLE|STREAMING",   minEventLevel = 3 },
{ action = "add_resource",        type = "POSITIVE", gameStates="ATTACK|IDLE|STREAMING",   minEventLevel = 1, basePercentage = 30 },
{ action = "remove_resource",     type = "NEGATIVE", gameStates="ATTACK|IDLE|STREAMING",   minEventLevel = 1, basePercentage = 20 },
{ action = "stronger_attack",     type = "NEGATIVE", gameStates="ATTACK|STREAMING",        minEventLevel = 1, amount = 2 },
{ action = "cancel_the_attack",   type = "POSITIVE", gameStates="ATTACK|STREAMING",        minEventLevel = 1 },
{ action = "unlock_research",     type = "POSITIVE", gameStates="ATTACK|IDLE|STREAMING",   minEventLevel = 1 },
{ action = "full_ammo",           type = "POSITIVE", gameStates="ATTACK|STREAMING",        minEventLevel = 2 },
{ action = "remove_ammo",         type = "NEGATIVE", gameStates="ATTACK|STREAMING",        minEventLevel = 2 },
{ action = "boss_attack",         type = "NEGATIVE", gameStates="ATTACK|STREAMING",        minEventLevel = 4 },
```

Diese Actions werden in `SpawnEvent` (`event_manager.lua:1145–1273`) auf DOM-Zustand
gemappt — relevant für die Replica:

| Action | Effekt in `SpawnEvent` |
|---|---|
| `new_objective` | `SpawnObjective()` |
| `add_resource` / `remove_resource` | `PlayerService:AddResourceAmount(GetLeadingPlayer(), …, ±amount)` |
| `cancel_the_attack` | `self.cancelTheAttack = true` (⇒ `OnEnterSpawn` überspringt die Welle) |
| `stronger_attack` | `self.extraAttacks = amount` + `participantsPercentageUse = 10` (⇒ Extra-Welle) |
| `full_ammo` / `remove_ammo` / `extra_ammo` | `AddAmmo(±100 …)` |
| `boss_attack` | `self.spawnBoss = true` + `participantsPercentageUse = 100` (⇒ Boss-Welle) |
| `unlock_research` | `PlayerService:UnlockResearch(…)` |

### 4.2 `rules.gameEvents` — Creature-Attack-Events (je Event-Level-Band)

```lua
{ action = "shegret_attack",           type = "NEGATIVE", …, minEventLevel = 2, maxEventLevel = 4, logicFile="logic/event/shegret_attack.logic", weight = 3, bindingParams = { attack_strength = "normal" } },
{ action = "shegret_attack_hard",      …, minEventLevel = 5, maxEventLevel = 7, …, bindingParams = { attack_strength = "hard" } },
{ action = "shegret_attack_very_hard", …, minEventLevel = 8, maxEventLevel = 9, …, bindingParams = { attack_strength = "very_hard" } },
{ action = "kermon_attack",            …, minEventLevel = 4, maxEventLevel = 5, …, weight = 1, bindingParams = { attack_strength = "normal" } },
{ action = "kermon_attack_hard",       …, minEventLevel = 6, maxEventLevel = 7, …, weight = 1, bindingParams = { attack_strength = "hard" } },
{ action = "kermon_attack_very_hard",  …, minEventLevel = 8, maxEventLevel = 9, …, weight = 1, bindingParams = { attack_strength = "very_hard" } },
{ action = "phirian_attack",           …, minEventLevel = 3, maxEventLevel = 9, …, weight = 1 },
```

Jede Zeile existiert doppelt (STREAMING + NO_STREAMING). Das Muster ist konsistent: **eine
Kreaturen-Familie skaliert über drei `attack_strength`-Stufen, geschaltet über
`minEventLevel`/`maxEventLevel`-Bänder.**

### 4.3 `rules.gameEvents` — Weather-Events (gekürzt, nur Schwellen)

Alle mit `logicFile="logic/weather/…"` und `minTime`/`maxTime` (Dauer) bzw. `weight`.
Auswahl (minEventLevel in Klammern, `P` = POSITIVE, sonst NEGATIVE):

| Event | min lvl | Event | min lvl |
|---|---|---|---|
| `spawn_rain` / `spawn_fog` / `spawn_wind_weak` / `spawn_wind_strong`(P) / `spawn_fireflies`(P) | 1 | `spawn_blue_hail` / `spawn_ion_storm`(P) / `spawn_resource_comet`(P) / `spawn_comet_boss_mudroner_*` | 4 |
| `spawn_thunderstorm` / `spawn_wind_none` / `spawn_meteor_shower` / `spawn_firestorm` / `spawn_tornado_near_player`(1–2) | 2 | `spawn_blood_moon` / `spawn_solar_eclipse` / `spawn_resource_earthquake`(P) | 5 |
| `spawn_earthquake` / `spawn_blue_moon`(P) / `spawn_super_moon`(P) / `spawn_tornado_near_base` / `spawn_tornado_*_near_base` | 3 | — | — |

### 4.4 `rules.objectivesLogic`

```lua
rules.objectivesLogic =
{
	{ name = "logic/objectives/kill_elite_dynamic.logic",           minDifficultyLevel = 3 },
	{ name = "logic/objectives/destroy_nest_canoptrix_single.logic", minDifficultyLevel = 3, maxDifficultyLevel = 5 },
	{ name = "logic/objectives/destroy_nest_canoptrix_multiple.logic", minDifficultyLevel = 6 },
	{ name = "logic/objectives/destroy_creeper.logic",              minDifficultyLevel = 6 },
}
```

Gates: ab Event-Level 3 kommen `kill_elite` + `canoptrix_single` frei, ab 6
`canoptrix_multiple` + `creeper`. `maxObjectivesAtOnce = 2` begrenzt die gleichzeitig
aktiven Objectives (`event_manager.lua:320`).

### 4.5 `rules.addResourcesOnRunOut`

```lua
rules.addResourcesOnRunOut =
{
	{ name = "carbon_vein", runOutPercentageOnMap = 45, minToSpawn = 30000, maxToSpawn = 45000 },
	{ name = "iron_vein",   runOutPercentageOnMap = 45, minToSpawn = 30000, maxToSpawn = 45000 },
}
```

Nur im `IDLE`-Zweig von `StartAnEvent` (`event_manager.lua:932`), wenn eine Ader unter
45 % Kartenbestand fällt ⇒ spawn `spawn_resource_comet`/`spawn_resource_earthquake`
(Cooldown `addResourcesOnRunOutTime = 1200`). Im Survival (`idleTime = 0`) praktisch tot.

---

## 5 · Timing — wann feuern Events/Objectives im Survival-Takt

Der `event_manager` hat **keinen** eigenen Spawner-State; er wird vom `dom_mananger`-Takt
getriggert. Einsprungstellen (grep `StartAnEvent`/`StartObjective` in `dom_manager.lua`):

| Ort | Aufruf | State |
|---|---|---|
| `OnExecutePrepareSpawn` `:1311` / `:1316` | `StartAnEvent("IDLE")` / `StartObjective()` | `prepare_spawn` (Vorwarnfenster) |
| `OnExecuteIdle` `:1419` / `:1426` | `StartAnEvent("IDLE")` / `StartObjective()` | `idle` |
| `OnEnterStreaming` `:1759` | `StartAnEvent("ATTACK")` | `streaming` |

**Survival-Gate (`dom_survival_jungle_rules_default.lua:4–6`):**

```lua
rules.maxObjectivesAtOnce = 2
rules.eventsPerIdleState = 0
rules.eventsPerPrepareState = 1 -- [0,1]
```

⇒ `eventsPerIdleState = 0` + `idleTime = 0` (alle 9 Level) machen den Idle-Zweig tot
(`idleTimer` startet bei 0 und wird sofort negativ ⇒ Übergang `prepare_spawn`). Es bleiben
nur die `prepare_spawn`-Trigger.

**Fenster-Berechnung** (`OnEnterPrepareSpawn`, `dom_manager.lua:1251–1257`):

```lua
self.waitForSpawnTimer = self:GetPrepareSpawnTime()                              -- default 360 s
self.eventActivateTime  = self.waitForSpawnTimer - ( self.waitForSpawnTimer * self.idleTimeEventMul )    -- 360 - 126 = 234
self.objectiveActivateTime = self.waitForSpawnTimer - ( self.waitForSpawnTimer * self.idleTimeObjectiveMul ) -- 360 - 72 = 288
```

`OnExecutePrepareSpawn` zählt `waitForSpawnTimer` herunter und feuert, sobald er unter die
Schwellen fällt (`dom_manager.lua:1311–1318`):

```lua
if ( ( self.waitForSpawnTimer < self.eventActivateTime ) and ( self.eventActivated == false ) ) then
	self:StartAnEvent( "IDLE" )
	self.eventActivated = true
end
if ( ( self.waitForSpawnTimer < self.objectiveActivateTime ) and ( self.objectiveActivated == false ) ) then
	self:StartObjective()
	self.objectiveActivated = true
end
```

Da der Timer **abwärts** läuft, feuert das **Objective zuerst** (bei `waitForSpawnTimer <
288`, d. h. nach **72 s = 20 %** des Fensters) und das **Event danach** (bei `234`, d. h.
nach **126 s = 35 %**). Im Survival (Vorwarnzeit 360 s) liegt also die Quest früher im
Fenster als das Event, beide einmal pro Attack-Zyklus.

`StartAnEvent` (`event_manager.lua:932–1050`) macht ohne Streaming-Session: `PrepareEvents`
→ `eventChanceRoll = RandInt(0,100)` vs `eventChance = 100` (immer true) → ein Event per
`GetEventByWeight` (weighted random) → `SpawnEvent`. `StartObjective` (`:1100`) →
`CheckObjective(true, true)` (Abstands-Check + `maxObjectivesAtOnce` + Event-Level-Filter)
→ `SpawnObjective` (zufälliges Objective aus `objectiveAvailableList`).

> **Event-Level als Ganzes hat kein eigenes Zeitraster** — die Event-*Frequenz* hängt am
> Wave-Takt (`prepareSpawnTime`), das Event-*Level* (welche Events/Objectives erlaubt sind)
> hängt am Difficulty-Timer (§1). Beides läuft über denselben Zähler `currentEventLevel`.

---

## 6 · Solo vs Koop

Der `event_manager` ist **nicht** spielerzahl-verzweigt (kein `GetPlayersCounter`-Zweig wie
bei den Wellen, vgl. #515 §5). Die einzigen Spieler-Bezüge:

- **Ziel-Spieler:** `add_resource`/`remove_resource`/`unlock_research` adressieren
  `PlayerService:GetLeadingPlayer()`; `AddAmmo` iteriert `PlayerService:GetAllPlayers()`
  (`event_manager.lua:268–278`). Solo ⇒ ein einziger Spieler, keine Verzweigung.
- **Label-Mechanik** (`PrepareLabels`, `dom_manager.lua:1601–1605`):
  `self.data:SetString("labels", labels)` / `label_name` / `labels_percentage_use`. Sie
  markiert **nur** bei `boss_attack`/`stronger_attack`-Events Teilnehmer (`participants` +
  `participantsPercentageUse` 100 bzw. 10) — im Koop/Streaming werden einzelne Spieler als
  Ziel gelabelt. **Solo bleibt `participants = ""` und `participantsPercentageUse = 0`**
  (Reset in `OnEnterSpawn`, `dom_manager.lua:1723–1724`), d. h. keine Labels.

⇒ Für die Solo-Replica ist der Koop-/Label-Pfad **tot**; relevant bleiben die
Event-/Objective-Gates (§4) und die Ressourcen-Skalierung (§3.3), die allein am
`currentEventLevel` hängen.

---

## 7 · Offene Punkte

- **`objectivesLogic.maxDifficultyLevel` ist toter Code** (kein Konsument in
  `event_manager.lua`) — der Objective-Pool schrumpft mit steigendem Event-Level **nicht**.
  Ob das ein Bug oder Absicht ist, ist aus Lua nicht entscheidbar (vgl. C++/Logik nicht
  sichtbar).
- **`GetEventByWeight`-Randbedingung:** `weight`-Default ist `1.0`; viele Weather-Events
  setzen explizit `weight = 0.5`/`0.25` (seltener). Die exakte Wahrscheinlichkeitsverteilung
  ist eine Funktion des gefilterten `gameEvents`-Pools und damit Event-Level- **und**
  `gameState`-abhängig — hier nur strukturell, nicht numerisch ausgewertet.
- **`spawn_resource_comet`/`spawn_resource_earthquake`** als `resourceEvents` (Run-Out-Pfad)
  vs. als reguläre `gameEvents`-Einträge — zwei getrennte Pfade, im Survival (`idleTime = 0`)
  ist der Run-Out-Pfad praktisch inaktiv.
- **Nicht aus Lua belegt:** `DifficultyService:AreNegativeEventsEnabled()` (Gatter für
  NEGATIVE-Events), `GameStreamingService:*` (Streaming-Session) — C++-Getter, Werte nicht
  sichtbar.
- **Biom-Varianten:** nur jungle-default ausgewertet; andere Biome (`_acid`/`_desert`/…)
  haben eigene `gameEvents`/`objectivesLogic` (vgl. #515 §2) — hier bewusst außerhalb Scope.

## Refs

#796 (DOM-Replica-Spec §2 #9 / §7.2) · #515 (Modus-Landschaft / `GetPlayersCounter`) ·
#646 (Kreaturen-Inkrement, Muster-Doc) ·
`docs/DOM_REPLICA.md` · `docs/research/515-survival-modes.md` ·
`docs/research/646-creature-difficulty-increment.md`.
Quellen (Build 2.0.58485, `/srv/rbgame/packs/00_win_data.zip`):
`lua/missions/v2/dom_manager.lua`, `lua/missions/v2/event_manager.lua`,
`lua/missions/survival/v2/dom_survival_jungle_rules_default.lua`.
