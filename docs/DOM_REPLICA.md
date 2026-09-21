# DOM-Replica — Base-Game-Difficulty extern 1:1 nachbauen (Spec)

**Status:** Arbeits-Spec (Design) · **Stand:** 2026-09-21
**Kontext:** Session-/Referee-Entwurf · Attack-Cycle (`deploy/attack-cycle/`) · Issue-Familie #508/#514/#513/#512/#759

## 1. Entscheidung & Ziel

Die Base-Game-Difficulty (DOM, `dom_mananger`) wird **extern nachgebaut** — in unserem
Attack-Cycle/Referee — statt die native DOM laufen zu lassen. Grund: volle Kontrolle,
Personas/Enemy-Sends/Self-Send als eigene Schicht obenauf, Session-Recording sauber
an einem Ort.

**Fidelity-Anforderung:** Bosse, Event-Levels, Wellen-pro-Attack, Extra-/MP-Wellen,
Escalation — alles ist Teil der Spec. **Fehlt etwas, ist das eine Lücke, keine bewusste
Entscheidung.** (Klarstellung: `attack N = wave N` ist NICHT das Ziel, sondern eine
heutige Lücke.)

## 2. Was `currentDifficultyLevel` (1–9) im Base Game treibt

Der Difficulty-Level ist der **Index in parallel eskalierende Tabellen** — nicht „welche
Welle", sondern ein konsistenter Schwierigkeits-Tier quer durch alle Dimensionen:

| #   | Dimension        | Mechanik                                                                         | Beleg                                                |
| --- | ---------------- | -------------------------------------------------------------------------------- | ---------------------------------------------------- |
| 1   | **Escalation**   | `timeToNextDifficultyLevel[level]`                                               | `dom_manager.lua` `OnEnter/OnExitDifficultyIncrease` |
| 2   | **Wave-Pool**    | `rules.waves[group][level]` — welche Units                                       | `GetWavePool` `:1035`                                |
| 3   | **Attack-Count** | `maxAttackCountPerDifficulty[level]` — wie viele Wellen/Attack                   | `GetAttackCount` `:1111`                             |
| 4   | **Boss**         | `rules.bosses[level]` + `attack_boss_dynamic.logic`                              | `GetBossPool` `:1080`                                |
| 5   | **Extra-Wellen** | `rules.extraWaves[level]` (`stronger_attack`)                                    | `GetExtraWavePool`                                   |
| 6   | **MP-Wellen**    | `rules.multiplayerWaves[level]` (Elite-Boss)                                     | `GetMultiplayerAttackCount` `:1116`                  |
| 7   | **Timing**       | `cooldownAfterAttacks[level]` + `prepareSpawnTime[level]` + `idleTime[level]`    | `DumpDomProgress`                                    |
| 8   | **Kreaturen-HP** | `creatureDifficultyIncrementPerDOMDifficulty[level]` → `CreaturesBaseDifficulty` | `IncreaseCreaturesBaseDifficulty` `:1180`            |
| 9   | **Event-Level**  | `currentEventLevel` / `IncreamentEventLevel` → `event_manager` (Objectives)      | `:958`                                               |

> **#8 ist für Survival irrelevant:** einziger Konsument von `CreaturesBaseDifficulty`
> ist der Anoryxian-Boss (DLC2-Campaign), der im Survival **nie** spawnbar ist
> (`docs/research/creatures-base-difficulty-mechanik.md`). Bei der Replica weglassen.

## 3. Timing-Werte (verifiziert, `dom_survival_jungle_rules_*.lua` + `dom_manager.lua`)

### 3.1 Escalation — `timeToNextDifficultyLevel` (s)

| Difficulty | 1→2                | 2→3 … 8→9 |
| ---------- | ------------------ | --------- |
| default    | 200                | 600       |
| normal     | (erbt default) 200 | 600       |
| hard       | 200                | 600       |
| brutal     | 200                | 600       |
| easy       | **300**            | **720**   |

Kumulierte Zeitfenster (default): L1 `0–200s`, L2 `200–800s`, L3 `800–1400s`, L4
`1400–2000s`, … (je `+600s`).

### 3.2 Attack-Frequenz — `prepareSpawnTime` (s) + `cooldownAfterAttacks` (s)

| Difficulty             | `prepareSpawnTime` |
| ---------------------- | ------------------ |
| default                | 360                |
| easy                   | 480                |
| normal / hard / brutal | 420                |

`cooldownAfterAttacks` (default, von allen geerbt): `[60, 90, 120, 180, 180, 180, 240, 240, 240]`
je Level 1–9. `idleTime` = `0` (Survival).

**Attack-Intervall = `cooldown + idle + prepareSpawnTime`** (level-abhängig).

### 3.3 Attack-Count — `maxAttackCountPerDifficulty`

| Level   | 1   | 2   | 3   | 4   | 5   | 6   | 7   | 8   | 9     |
| ------- | --- | --- | --- | --- | --- | --- | --- | --- | ----- |
| default | 1   | 2   | 2   | 3   | 3   | 3   | 3   | 3   | **4** |
| normal  | 1   | 2   | 2   | 2   | 2   | 2   | 3   | 3   | 3     |

### 3.4 Simulierter Angriffs-Zeitplan (attack → wave, je Difficulty)

Identisch zur Spiel-eigenen `DumpDomProgress`-Logik gerechnet:

| Attack | default | easy | normal | hard | brutal |
| ------ | ------- | ---- | ------ | ---- | ------ |
| 1      | w1      | w1   | w1     | w1   | w1     |
| 2      | w2      | w2   | w2     | w2   | w2     |
| 3      | w3      | w3   | w3     | w3   | w3     |
| 4      | w3      | w3   | w4     | w4   | w4     |
| 5      | w4      | w4   | w5     | w5   | w5     |
| 6      | w5      | w5   | w6     | w6   | w6     |
| 7      | w6      | w6   | w7     | w7   | w7     |
| 8      | w7      | w7   | w8     | w8   | w8     |
| 9      | w8      | w8   | w9     | w9   | w9     |
| 10     | w9      | w9   | w9     | w9   | w9     |

**Befund:** normal/hard/brutal = exakt `attack N = wave N` (Cap w9). default/easy haben
ab attack4 einen 1-Schritt-Lag. Der Difficulty-Timer ist kalibriert, um genau das zu
produzieren — er ist ein umständlicher Weg zu „attack N ≈ wave N".

## 4. State-Machines (zu replizieren)

`dom_mananger:init()` (`v2/dom_manager.lua:106–120`) hat drei:

| SM                   | States                                                                                   | Survival?            |
| -------------------- | ---------------------------------------------------------------------------------------- | -------------------- |
| `spawner`            | `spawn`/`wait`/`cooldown_after_spawn`/`prepare_spawn`/`idle`/`streaming`/`sleep`/`dummy` | ✅ Kern              |
| `difficultyIncrease` | `difficulty_increase`                                                                    | ✅ Kern              |
| `upgradeHQ`          | `hq_entry_logic`/`hq_attack_logic`/`hq_exit_logic`                                       | ❌ **Campaign-only** |

**Start:** `Activated()` → `currentDifficultyLevel = 1`, `difficultyIncrease → difficulty_increase`,
`spawner → wait` (→ `SetSuspended(true)`, wartet auf Warmup/HQ-Placement).

**Wellen-Pick:** `OnEnterSpawn` → `SpawnWavesForDifficultyLevel(self.currentDifficultyLevel)`
→ `GetWavePool(level)` + `GetAttackCount(level)` + `GetBossPool(level)`. **Wave-Level =
`currentDifficultyLevel`, nicht Attack-Nummer.**

**Restart/Ende:** nativ via `restart_map` (Pending-Flag → In-Prozess-Map-Reload, Economy 0)
bzw. `end_game` (`FinishCurrentMission`). Siehe `docs/research/native-round-reset.md`, #516/#519.

## 5. Wellen-Komposition (bestehendes Research — hier nur referenziert)

Die **Daten** (welche Units je Level, HP, Pools) sind weitgehend fertig dokumentiert:

- **`docs/research/213-wave-richtwert.md`** — Wave-Pools je Level, `id_1`-Zusammensetzung, HP, Richtwert-Formel.
- **`docs/research/736-wellen-hp-pool-vollstaendig.md`** — vollständiger Pool (alle `id_*`), korrigierte Formel `count × units_in_one_spawn`, HP-Ranges, Carbonium-Kostenkurven. **Ersetzt #213 §2/§4-Datenbasis.**
- **`docs/research/hard-difficulty-wellen-hp-pool.md`** / **`brutal-difficulty-wellen-hp-pool.md`** — Hard/Brutal-Pools + Vergleich.
- **`docs/research/multiplayer-additional-boss-wave.md`** — dritter Wellen-Kanal `multiplayerWaves` → Elite-Boss (27-Einträge-Pool, 7 Familien, `min_difficulty_factor`-HP-Leiter 2.5k–50k).
- **`docs/research/646-creature-difficulty-increment.md`** — `creatureDifficultyIncrementPerDOMDifficulty`-Rohdaten.
- **`docs/research/creatures-base-difficulty-mechanik.md`** — Anoryxian-Boss (für Survival irrelevant, s. §2 #8).
- **`docs/research/680-biter-battles-wave-mechanics.md`** — Biter-Battles als Sandbox-Referenz.
- **`docs/research/515-survival-modes.md`** — Modus-Landschaft + Solo/Koop-Verzweigung (`GetPlayersCounter`).

## 6. Gap-Analyse — unser Mod vs. Base Game

| Dimension                      | Base Game                         | Unser Mod (aktuell)       | Status      |
| ------------------------------ | --------------------------------- | ------------------------- | ----------- |
| Escalation                     | 200→600 (je Difficulty)           | 200 **flach**             | ❌          |
| Wave-Pool je Level             | 9 Level × Pools (alle `id_*`)     | 1 Pool/Level, `WAVE_COST` | ⚠️          |
| **Attack-Count**               | 1→4 Wellen/Attack                 | **1 Welle/Attack**        | ❌          |
| **Boss**                       | Level 8/9 (`attack_boss_dynamic`) | —                         | ❌          |
| **Extra-/MP-Wellen**           | `extraWaves`/`multiplayerWaves`   | —                         | ❌          |
| **Event-Level**                | `event_manager` (Objectives)      | —                         | ❌          |
| Personas/Enemy-Sends/Self-Send | —                                 | ✅ (unsere Erweiterung)   | nur bei uns |

## 7. Offene Fragen / Entscheidungen

1. **„attack N" ist UNSER Anchor, keine Base-Game-Größe.** Personas sind nach
   Attack-Nummer indiziert — das ist der Kern des Konzepts: „beim N-ten Attack
   schicke ich XY ZUSÄTZLICH". Die Persona **emuliert einen Gegner, der Sends
   kauft**, und ist eine Schicht **obenauf** der Natural Waves (nicht die Natural
   Waves selbst). `attack N` = unser Scheduler-Tick. Keine Reconciliation nötig.
   Offen bleibt nur die **Natural-Wave-Seite**: welches Difficulty-Level die Natural
   Wave beim N-ten Tick hat (eigener Difficulty-Timer, nicht attack-indiziert).
2. **Event-Level / `event_manager`** — Objectives/Belohnungen sind strukturell bekannt,
   aber **noch nicht im Detail dokumentiert** (kein Research-Doc). Braucht eigenen Spike.
3. **Biom-Varianten** (`_acid`/`_desert`/`_swamp`/…) — bewusst außerhalb bisheriger Scopes
   (#658/#736). Entscheiden: erst Jungle (Default) voll, Rest später.
4. **`_alpha`/`_ultra`**-Wellen-Varianten — gleiche Frage.
5. **`difficulty_factor`-Laufzeitquelle** für Boss-HP (`spawn_species_difficulty_*` immer
   `"0"` im Lua) — offen (#750 §7.1), vermutlich nativ.
6. **Campaign-Pfad `upgradeHQ`** — Survival hat ihn nicht. Replica: weglassen, aber
   bewusst dokumentieren (nicht stillschweigend).
7. **Solo vs. Koop** — `GetPlayersCounter()==1` ⇒ MP-Zweig tot. Replica: Solo-first,
   MP-Wellen als späterer Schritt.
8. **Difficulty-Varianten** — easy/normal/hard/brutal als konfigurierbare Profile
   (statt Hardcode). Normal ist unser Ziel-Referenzwerk.

## 8. Issue-Mapping (Bestehendes Research)

| Issue                    | Thema                                  | Doc                                   |
| ------------------------ | -------------------------------------- | ------------------------------------- |
| #515                     | Survival-Modi-Struktur                 | `515-survival-modes.md` ✅            |
| #508                     | Catalog of Things (Wellen/Bosse/Sends) | offen                                 |
| #514                     | Wave-Takt `dom_mananger`               | **dieses Doc §3**                     |
| #513                     | Mission-Flows-Katalog                  | offen                                 |
| #512                     | Player-Count                           | offen                                 |
| #516                     | Round-Reset `restart_map`              | `native-round-reset.md`               |
| #519                     | `end_game` nativ                       | offen                                 |
| #520                     | pause/resume `SetSuspended`            | `SYNC_START.md`                       |
| #213/#736/#646/#658/#699 | Wellen-Komposition/HP                  | `213-…`/`736-…`/`646-…`               |
| #750                     | MP-Boss-Kanal                          | `multiplayer-additional-boss-wave.md` |
| #759                     | eigene Mod-Mission (DOM weglassen)     | ← Richtungsfrage, s. §1               |
| #782                     | Difficulty-Timer Balance (200s flat)   | ← löst sich durch §3                  |

## 9. Nächste Schritte (issue-first)

1. Fixieren §7.1 (attack-N-Definition) → dann Persona-Anchoring.
2. Spike: `event_manager` / Event-Level (§7.2).
3. Attack-Count + Boss + Extra-/MP-Wellen in `attack_cycle.py` nachziehen (Daten aus §5).
4. Difficulty-Varianten als Profil-Datenstruktur (§7.8).
