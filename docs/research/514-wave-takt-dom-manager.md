# #514 — Wave-Takt: `dom_mananger` — interval, first-wave-delay, Survival-Rules

**Spike (reine Recherche/Doku).** Refs #278 · #376 · #508.
Quelle: `lua-src` (planet/lan, `/home/momo/rb-game/lua-src/lua/`), Build-agnostisch
(kein DLL/PDB nötig — reine Lua-Auswertung).

## Frage

Wie tickt die Welle konkret: `interval`, `first-wave-delay`, `GetPrepareSpawnTime`,
Survival-Rules? Welcher Wert ist der *echte* Wellen-Timer und woher kommt der
Countdown, den der Spieler sieht?

## TL;DR (Antwort)

- Es gibt in den Survival-Rules **kein** Feld `interval`, `first-wave-delay` oder
  `repeat`. Die Takt-Geber heißen **`prepareSpawnTime`**, **`cooldownAfterAttacks`**,
  **`idleTime`** und **`timeToNextDifficultyLevel`**.
- Der **sichtbare Countdown** („nächster Angriff in X") ist der State-Timer
  **`waitForSpawnTimer`** im State `prepare_spawn`, gespeist aus
  `dom_mananger:GetPrepareSpawnTime()` = **`rules.prepareSpawnTime[currentDifficultyLevel]`**.
  Er wird als `time_max` in den Mission-Flow `logic/missions/survival/next_attack_in.logic`
  gepusht — **das ist der echte Wellen-Timer**.
- **`interval`** ist kein Rules-Feld, sondern ein **Spieler-Setting**, das in den
  `_custom`-Rules in `prepareSpawnTime` geschrieben wird
  (`DifficultyService:GetWaveIntermissionTime()`).
- **`first-wave-delay`** existiert nicht als Feld. Die erste Welle läuft über den
  Initial-State `wait` (fix 5 s, suspended) → `streaming` → `spawn` — sie wird also
  **nicht** durch `prepareSpawnTime` getaktet, sondern durch das Ende des
  Streamings/der Warmup-Phase.
- **`repeat`** = der State-Machine-Zyklus selbst (siehe unten), kein eigenes Feld.

## Ist-Stand der Rules (Feldnamen, die es wirklich gibt)

Enumeriert über alle 56 Dateien `lua/missions/survival/v2/dom_survival_*_rules_*.lua`:

`waves` · `wavesEntryDefinitions` · `extraWaves` · `bosses` · `multiplayerWaves` ·
`prepareAttackDefinitions` · `prepareAttacks` · `pauseAttacks` · `gameEvents` ·
`objectivesLogic` · `addResourcesOnRunOut` · `creatureDifficultyIncrementPerDOMDifficulty` ·
`buildingsUpgradeStartsLogic` · `eventsPerIdleState` · `eventsPerPrepareState` ·
`maxObjectivesAtOnce` · `maxAttackCountPerDifficulty` · **`prepareSpawnTime`** ·
**`cooldownAfterAttacks`** · **`idleTime`** · **`timeToNextDifficultyLevel`**.

→ Kein `interval` / `firstWave*` / `repeat` vorhanden.

## Der `spawner`-State-Machine-Zyklus (das ist der „repeat")

Alle States + Handler in `lua/missions/v2/dom_manager.lua:106–113` (Klasse
`dom_mananger`). Takt-relevant:

| State | Dauer-Timer | Quelle | Übergang |
|---|---|---|---|
| `wait` (Initial) | `SetDurationLimit(5)` fest | — | `OnExitWait` → `streaming` (bzw. `idle`/`prepare_spawn` bei `pauseAttacks`) |
| `streaming` | — | — | → `spawn`, sobald kein Stream-Event mehr läuft |
| `spawn` | sofort | — | spawnt Welle(n) (`SpawnWavesForDifficultyLevel`) → `cooldown_after_spawn` |
| `cooldown_after_spawn` | `cooldownTimer` | `rules.cooldownAfterAttacks[level]` (÷ MP-Faktor) | → `idle` |
| `idle` | `idleTimer` | `rules.idleTime[level]` | → `prepare_spawn` |
| `prepare_spawn` | `waitForSpawnTimer` | `GetPrepareSpawnTime()` = `rules.prepareSpawnTime[level]` | → `streaming` (bzw. `dummy_state`) |
| `sleep` | `sleepSafeTimer` | fix 1200 | HQ-Upgrade/Tod |
| `dummy_state` | — | — | nur `pauseAttacks`-Schleife |

Ablauf pro Welle (Survival, `pauseAttacks == false`):

```
spawn ─► cooldown_after_spawn ─► idle ─► prepare_spawn ─► streaming ─► spawn ─► …
```

Die Summe der drei Rule-Timer (`cooldownAfterAttacks + idleTime + prepareSpawnTime`)
ist auch das interne Modell im Code: `dom_mananger:DumpDomProgress()`
(`dom_manager.lua:562–621`) rechnet genau `attackTime += cooldown + idle + prepare`.

## Woher der echte Countdown kommt (HUD / Mission-Flow)

`OnEnterPrepareSpawn` (`dom_manager.lua:1246–1290`):

```lua
self.waitForSpawnTimer = self:GetPrepareSpawnTime()
self.data:SetFloat( "time_max", self.waitForSpawnTimer )
MissionService:ActivateMissionFlow(
    self.objectivePrepareForTheAttacLogicFileName,             -- DB-Callback-Name
    "logic/missions/survival/next_attack_in.logic",            -- HUD-Countdown-Logic
    "default", self.data )
```

`OnExecutePrepareSpawn` dekrementiert `waitForSpawnTimer -= dt`; `< 0` → `streaming`.

⇒ **Der Spieler-Countdown = `waitForSpawnTimer` = `prepareSpawnTime[aktuelles
Difficulty-Level]`.** Das ist derselbe `time_max`-Wert, der in
`dedicated-io-re-findings.md` (Phase C) als „HUD next wave" / bekannter Gap
beschrieben ist.

`GetPrepareSpawnTime()` (`dom_manager.lua:1135–1146`):

```lua
local intermissionTimer = self.rules.prepareSpawnTime[self.currentDifficultyLevel]
if playersCounter > 1 then
    intermissionTimer = intermissionTimer - ((playersCounter-1) * DifficultyService:GetWaveIntermissionMultiplier())
end
return intermissionTimer
```

`GetCooldownAfterAttacksTime()` (`1148–1158`) skaliert analog mit
`DifficultyService:GetWaveCooldownPerPlayerFactor()`.

## `interval` (Spieler-Setting) → `prepareSpawnTime`

Die `_custom`-Rules (`dom_survival_*_rules_custom.lua`) überschreiben
`rules.prepareSpawnTime` für **alle 9 Level** mit einem einzigen Wert:

```lua
local waveTime = DifficultyService:GetWaveIntermissionTime()
rules.prepareSpawnTime = { waveTime, waveTime, … , waveTime }   -- 9×
```

und skalieren `maxAttackCountPerDifficulty[i] *= DifficultyService:GetAttacksCountMultiplier()`.

⇒ Das UI-Setting „Wave interval" ist **`GetWaveIntermissionTime()`**, das 1:1 in
`prepareSpawnTime` landet — es ist also derselbe „echte" Timer, nur vorkonfiguriert.

## `first-wave-delay` — existiert nicht als Feld

`dom_mananger:Activated()` (`dom_manager.lua:929`) setzt `currentDifficultyLevel = 1`,
startet `difficulty_increase` und geht in den State **`wait`**.
`OnEnterWait` (`1166–1175`) macht `SetSuspended(true)` + `OperateDOMPlanetaryJump(true)`
und `SetDurationLimit(5)`.

Die erste Welle wird damit **nicht** durch `prepareSpawnTime` getaktet, sondern:

1. `wait` (fix 5 s) → `OnExitWait` → `streaming`,
2. `streaming` → `spawn`, sobald `GameStreamingService:IsInStreamEvent() == false`,
3. extern gegated: HQ-Platzierung (Mod-seitig `RBB.commenced`) bzw. Warmup-Phase
   (`warmup_duration` aus der `survival_*`-Mission-DB, #508).

Bei `pauseAttacks == true` (DifficultyService `waves_disabled` / `wave_strength == "sandbox"`)
läuft statt Spawns nur `idle ⇄ dummy_state`/`prepare_spawn` → 0 Naturwellen (siehe #476).

## Difficulty-Timer ist separat

`timeToNextDifficultyLevel[level]` taktet die `difficultyIncrease`-StateMachine
(`OnEnterDifficultyIncrease` → `SetDurationLimit(...)`), nicht die Wellen. Live-Wert
`200 s` für Level 1 (`dom_manager.lua:939–940`), danach 600 s.

## Beispiel-Zahlen (desert, v2)

| Feld | default | normal | hard |
|---|---|---|---|
| `prepareSpawnTime[1..9]` | 360 | 420 | 420 |
| `cooldownAfterAttacks` | 60,90,120,180,180,180,240,240,240 | (default) | (default) |
| `idleTime` | 0 (überall) | 0 | 0 |
| `timeToNextDifficultyLevel` | 200, 600×8 | 200, 600×8 | 200, 600×8 |

⇒ Survival-Wellen-Zyklus Level 1 (default): `cooldown 60 + idle 0 + prepare 360`
= **420 s** zwischen zwei Angriffs-Starts; der sichtbare Countdown läuft nur über
die `prepare`-Teilspanne (360 s).

## Konsequenz für den Bridge-Readout (#278 / #376)

- Der DOM-Readout `time_to_next` (State-abhängig, `max` der aktiven Timer) liefert
  je nach State `cooldownTimer` / `idleTimer` / `waitForSpawnTimer`.
- Der HUD-Countdown ist **nicht** direkt ein DOM-Feld, sondern der beim
  `ActivateMissionFlow("…next_attack_in.logic")` gesetzte `time_max`-Wert
  (= `waitForSpawnTimer` zum Aktivierungszeitpunkt).
- Wer den „echten" Next-Wave-Countdown exakt braucht, nimmt **`prepareSpawnTime`**
  als Nennwert und liest im `prepare_spawn`-State den DOM-Timer; in allen anderen
  States ist er noch nicht aktiv.

## Offene Punkte

- **Live-Verifikation** von `time_max` (HUD) vs. `waitForSpawnTimer` (DOM) über den
  `dom_sampler.sh`-Zeitreihen-Lauf (#376) steht aus — reine Lua-Herleitung hier.
- `DifficultyService:GetWaveIntermissionTime()` / `GetWaveCooldownPerPlayerFactor()`
  / `GetWaveIntermissionMultiplier()` sind C++-Getter; konkrete Default-Werte nicht
  aus Lua belegt (nur ihre Verwendung).
- Der Warmup-/HQ-Gate-Pfad (`warmup_duration`, `logic/missions/survival/default.logic`,
  Mod-`RBB.commenced`) ist kompilierter Logic-Graph + Mod-Layer, nicht in `lua-src`
  vollständig einsehbar — hier aus #508/#476 abgeleitet.

## Refs

#278 (Vorarbeit DOM-Timer-Readout) · #376 (Lua-Capture/Object-Modell) · #508
(Catalog of Things) · #476 (Vanilla-Naturwellen-Schalter).
