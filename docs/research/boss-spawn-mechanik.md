# Boss-Spawn-Mechanik — der echte Boss ist `multiplayerWaves`, nicht `rules.bosses`

**Stand:** 2026-09-21 · **Spiel-Build:** Pack `00_win_data.zip` (2.0.58485)
**Korrigiert:** frühere Einordnung „Boss ab Level 8/9 via `rules.bosses`" (aus #736-Notiz + `DOM_REPLICA.md` §2/§3.3) war **falsch**.

**Kern:** Der Boss (`attack_boss_dynamic.logic`) in einem **Coop-Survival-Match ohne
Streaming** kommt **nicht** aus `rules.bosses` — der hängt am `boss_attack`-Event, das
`gameStates="ATTACK|STREAMING"` trägt (STREAMING ohne NO_STREAMING → nur mit
Streaming-Session). Der tatsächliche Boss ist der `multiplayerWaves`-Kanal
(Elite-Boss), deterministisch ab Level 2 (Coop, default) bzw. 5 (normal).

---

## 1 · `rules.bosses` feuert nur mit Streaming-Session (`boss_attack`-Event)

`rules.bosses[level]` (dom_survival_jungle_rules_default.lua:496) ist für **alle
Level 1–9 identisch** (`attack_boss_dynamic.logic`) — der Pool ist also **kein**
Level-Gate.

Gefeuert wird er ausschließlich über `spawnBoss`:

```lua
-- event_manager.lua:1266 (im Event-Handler):
elseif ( translatedEventName == "boss_attack" ) then
    self.spawnBoss = true
    self.participants = self:PickRandomParticipant( participants )
    self.participantsPercentageUse = 100
```

```lua
-- dom_manager.lua:1699 (SpawnWavesForDifficultyLevel):
if ( self.spawnBoss == true ) then
    self:SpawnWave( 1, borderSpawnPointGroupName, self:GetBossPool(), ... )
    -- GetBossPool -> rules.bosses[level] -> attack_boss_dynamic.logic
end
```

Der `boss_attack`-Trigger ist ein **Game-Event** mit **nur einer** Variante:

```lua
-- rules.gameEvents (default, Zeile 21):
{ action = "boss_attack", type = "NEGATIVE", gameStates = "ATTACK|STREAMING", minEventLevel = 4 },
```

`gameStates = "ATTACK|STREAMING"` enthält **zwei** Token: `ATTACK` (Phase) und
`STREAMING` (Streaming-Modus). `event_manager:PrepareEvents` filtert Events mit
`STREAMING` aber **ohne** `NO_STREAMING` bei `streamActive == false` heraus
(„Only allowed with streaming session") → im Non-Streaming-Match feuert `boss_attack`
**nie**. Gleiches gilt für `stronger_attack` (Zeile 16, `ATTACK|STREAMING`,
→ `extraAttacks` → `rules.extraWaves`).

Zum Vergleich: die Weather-Events (`spawn_earthquake`, `spawn_thunderstorm`, …)
existieren **doppelt** (`STREAMING` + `NO_STREAMING`) — die Action-Events
(`boss_attack`, `stronger_attack`, `add_resource`, …) dagegen **nur** `STREAMING`.

---

## 2 · Der echte Boss: `rules.multiplayerWaves`

`rules.multiplayerWaves[level]` (default, Zeile 339) enthält je Level
`{ additionalWaves, waves = { attack_boss_dynamic.logic } }`. Gefeuert wird er
**deterministisch** (nicht event-basiert) direkt in `SpawnWavesForDifficultyLevel`:

```lua
-- dom_manager.lua:1688:
if ( self.rules.multiplayerWaves ~= nil ) then
    local multiplayerAttackCount = self:GetMultiplayerAttackCount( self.currentDifficultyLevel )
    if ( multiplayerAttackCount > 0 ) then
        self:SpawnWave( multiplayerAttackCount, ..., self:GetMultiplayerWavePool(...), ... )
    end
end
```

```lua
-- dom_manager.lua:1116:
function dom_mananger:GetMultiplayerAttackCount( currentDifficultyLevel )
    local playersCounter = self:GetPlayersCounter()
    if ( playersCounter > 1 ) then
        return Clamp( self.rules.multiplayerWaves[currentDifficultyLevel].additionalWaves + 1, 0, 1 )
    else
        return Clamp( self.rules.multiplayerWaves[currentDifficultyLevel].additionalWaves, 0, 1 )
    end
end
```

### `additionalWaves` je Level — Coop `clamp(x+1, 0, 1)` vs. Solo `clamp(x, 0, 1)`

| Level                         | 1   | 2   | 3   | 4   | 5   | 6   | 7   | 8   | 9   |
| ----------------------------- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **default** `additionalWaves` | -1  | 0   | 0   | 0   | 0   | 1   | 1   | 1   | 1   |
| **Coop** → Boss?              | –   | ✅  | ✅  | ✅  | ✅  | ✅  | ✅  | ✅  | ✅  |
| **Solo** → Boss?              | –   | –   | –   | –   | –   | ✅  | ✅  | ✅  | ✅  |
| **normal** `additionalWaves`  | -1  | -1  | -1  | -1  | 0   | 0   | 1   | 1   | 1   |
| **Coop** → Boss?              | –   | –   | –   | –   | ✅  | ✅  | ✅  | ✅  | ✅  |
| **Solo** → Boss?              | –   | –   | –   | –   | –   | –   | ✅  | ✅  | ✅  |

⇒ **Coop-Boss ab Level 2 (default) bzw. 5 (normal)** — das ist unser Replica-Target.
(Solo ab 6/7.) Der Inline-Kommentar („Multiplayer Additional waves are disabled in
single player mode") ist **falsch**: bei `additionalWaves = 1` feuert der Boss auch im
Solo (siehe #750).

---

## 3 · Die drei (bzw. vier) Boss-/Wellen-Kanäle im Überblick

| Kanal                    | Trigger                                                   | Non-Streaming?               |
| ------------------------ | --------------------------------------------------------- | ---------------------------- |
| `rules.waves`            | Natural-Wellen je Level (`SpawnWavesForDifficultyLevel`)  | ✅                           |
| `rules.multiplayerWaves` | Elite-Boss, deterministisch (`GetMultiplayerAttackCount`) | ✅ **ab L2 (Coop, default)** |
| `rules.bosses`           | `boss_attack`-Event (`spawnBoss`)                         | ❌ nur Streaming-Session     |
| `rules.extraWaves`       | `stronger_attack`-Event (`extraAttacks`)                  | ❌ nur Streaming-Session     |

---

## 4 · Konsequenz für die Replica

- **Boss** = `multiplayerWaves`-Elite-Boss, `attack_boss_dynamic.logic`, **Coop ab Level 2 (default) / 5 (normal)**, **ein** Boss je Attack.
- **`rules.bosses` / `boss_attack` / `stronger_attack`** = nur mit Streaming-Session → **nicht** nachbauen.
- Die event-getriebene Seite gehört ohnehin zum Event-Manager (§7.2 `DOM_REPLICA.md`),
  der für uns nur die `NO_STREAMING`-Weather-Events relevant macht.

## 5 · Offene Punkte

1. **`difficulty_factor`-Laufzeitquelle** des Bosses (`spawn_species_difficulty_*` immer `"0"` im Lua) — weiterhin offen (#750 §7.1).
2. **Boss-HP-Leiter** (`min_difficulty_factor` → `extra_unit_modifiers.health`, 2.5k–50k) — siehe `docs/research/multiplayer-additional-boss-wave.md` §5.
3. Ob es neben `multiplayerWaves` noch einen weiteren deterministischen Boss-Pfad in
   anderen Biome-/Regelwerken gibt (nur jungle/default + normal hier verifiziert).

**Ref:** #736 · #750 · #515 · `docs/research/multiplayer-additional-boss-wave.md` ·
`docs/research/creatures-base-difficulty-mechanik.md` · `DOM_REPLICA.md` §2/§3.3.
