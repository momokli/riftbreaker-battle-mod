# Dedicated IO — Direct C++ Reads (pure memory, no `lua_*`)

Build **2.0.58485** (GOG == Dedi, byte-identical). Companion to
`docs/research/dedicated-io-re-findings.md` (#363/#364/#370/#376) and the
`riftbreaker-re` skill. Scope: which game-state fields have a real C++
representation reachable **without the Lua VM**, and which are **Lua-only**.

Method: `llvm-pdbutil dump -publics` on the mac PDB (symbols only — the TPI/IPI
type streams are empty), RVA conversion per the skill
(`.text` `0001:` -> `0x1000` + decimal; `.rdata` `0002:` -> `0x2DA2000` +
decimal), and `tools/re/disasm.py` for layout. Lua paths cross-checked against
`/home/momo/rb-game/lua-src/lua/` (planet).

## Summary table

| state field | C++ object | offset / RVA | read method | notes |
|---|---|---|---|---|
| HQ health (current HP) | `HealthComponent` (ECS) | `comp[+0x00]` (float) | `HealthService::GetHealth(id)` RVA `0xF9BBB0` | id via `FindService::FindEntityByType("headquarters")` |
| HQ health (max HP) | `HealthComponent` (ECS) | `comp[+0x04]` (float) | `HealthService::GetMaxHealth(id)` RVA `0xF9C360` | same component |
| HQ alive / dead | `HealthComponent` (ECS) | `comp[+0x00]` (float) | `HealthService::IsAlive(id)` RVA `0xF9E130` | returns `current > 0.0f` |
| HQ health % | `HealthComponent` (ECS) | `comp[+0x00]/comp[+0x04]` | `HealthService::GetHealthInPercentage(id)` RVA `0xF9BC20` | `current/max` |
| player count | `Exor::Vector<unsigned int>` (returned by value) | `vec[+0x10]` (size_t) | `PlayerService::GetConnectedPlayers()` RVA `0xF27960` | count is **computed** (filter), no single static field |
| score | — | — | — | **no simple native field**; see "Score" section |
| `currentDifficultyLevel` | — | — | — | **Lua-only** (field on `dom_mananger` self table) |
| `time_to_next` | — | — | — | **Lua-only** (derived from Lua timers) |
| `round` | — | — | — | **Lua-only / mod-local** (`RBB.round`) |
| mission name / biome | `MissionService` (exists) | — | Lua service getters | not a single offset; not traced here |
| DOM state name (`dom_state`) | `StateMachine` (C++) | `sm[+0x50]` = index | `StateMachine::GetCurrentStateName` RVA `0x1DAEB50` | already covered in #376 skill |

## 1. HQ health (solid — full chain recovered)

Lua path: `FindService:FindEntityByType("headquarters")` -> entity id, then
`HealthService:GetHealth(hqEntity)` / `GetMaxHealth` / `IsAlive`.

### Object graph

```
HealthService*   (+0x08) = World*
  -> World*       (+0x30) = ECS entity-component store
      -> lookup(World+0x30, entityId, typeHash, &out)  -> HealthComponent*
          -> HealthComponent[+0x00] = current HP  (float)
          -> HealthComponent[+0x04] = max HP     (float)
```

### Evidence (disasm)

`HealthService::GetHealth` (RVA `0xF9BBB0`):

```
mov  rbx, [rcx+8]        ; rbx = HealthService[+8]  == World*
mov  edi, edx            ; edi = entity id (uint)
...
lea  rcx, [rbx+0x30]     ; container = World + 0x30
call 0x1808fcea0         ; cached component type-hash getter
mov  r8,  rax
lea  r9,  [rsp+0x30]
mov  edx, edi            ; entity id
lea  rcx, [rbx+0x30]
call 0x181dd06f0         ; lookup(container, id, typeHash, &out) -> HealthComponent*
test rax, rax
je   ...                 ; not found -> default (0.0)
movss xmm0, [rax]        ; current HP  = *(float*)comp[+0x00]
ret
```

`HealthService::GetMaxHealth` (RVA `0xF9C360`) is identical except it reads
`movss xmm0, [rax+4]` (max HP @ `+0x04`). `IsAlive` (RVA `0xF9E130`) does
`comiss 0.0, [rax] ; setb al` (alive iff `current > 0.0f`).
`GetHealthInPercentage` (RVA `0xF9BC20`) does `[rax] / [rax+4]`.

So the **HealthComponent struct** (at least) is:

| offset | type | meaning |
|---|---|---|
| `+0x00` | float | current HP |
| `+0x04` | float | max HP |

`HealthService[+0x08]` is `World*` (same service base-class layout as the
already-live-confirmed `PlayerService[+0x08] = World*`; the `+0x30` component
store is the same one `GetPlayerAccount(World*, ...)` reads through).

### How to reach the object

- **Service instances:** resolve by vftable scan (same pattern as
  `PlayerService`): `HealthService` vftable RVA `0x2E95760`,
  `FindService` vftable RVA `0x2E94C98`. (`PlayerService` vftable
  `0x2E8E910` is the known-good reference.)
- **Entity id:** `FindService::FindEntityByType(char const*)` RVA `0x1C0E420`
  returns the first entity of the given type; `0xFFFFFFFF` when none (matches
  `INVALID_ID`). (Namespace note: `FindService` is `Exor::FindService`;
  `HealthService`/`PlayerService` are `Riftbreaker::…`.)
- **Component pointer:** either call `HealthService::GetHealth/GetMaxHealth/
  IsAlive` directly (C++ call, not `lua_*`), or reproduce the lookup:
  `lookup(World+0x30, entityId, typeHash, &out)` via RVA `0x1DD06F0` (the
  type-hash getter is RVA `0x8FCEA0`, a per-component template instantiation;
  its lazy-init path references constant `0xbdd8d10a`).

**Bottom line:** HQ hp / max / alive are fully reachable as direct C++ reads.
Recommended: call `HealthService::GetHealth(entityId)` (RVA `0xF9BBB0`) after
resolving `HealthService*` and the HQ entity id — this mirrors the existing
carbonium read (which calls `GetPlayerAccount`).

## 2. Player count (partial — count is computed, not a static field)

Lua path: `dom_mananger:GetPlayersCounter()` = `#PlayerService:GetConnectedPlayers()`.

C++ path:

```
PlayerService::GetConnectedPlayers()   RVA 0xF27960
  this[+0x08] = World*
  -> Riftbreaker::GetConnectedPlayers(World*)   RVA 0xC5EE70  (free function)
      -> returns Exor::Vector<unsigned int>
         -> vec[+0x08] = data ptr, vec[+0x10] = count (element = 4-byte uint)
```

`Riftbreaker::GetConnectedPlayers` (RVA `0xC5EE70`) iterates an all-players
container (entries are **0x58 bytes** each — see `imul rdi, [r15+0x10], 0x58` in
the fill helper at `0x1DF3F50`) and filters by connected/team, appending to the
output vector. The output `Exor::Vector<unsigned int>` has the count at
`vec[+0x10]` (confirmed by the `lea rbp, [rdi + rax*4]` / `sub rdx, rcx` /
`mov [r15+0x10], rdx` resize logic).

Related (all return `Vector<unsigned int>` by value):
`PlayerService::GetAllPlayers` RVA `0xF27560`,
`PlayerService::GetPlayersFromTeam` RVA `0xF2A0D0`,
`Riftbreaker::GetConnectedPlayersFromTeam` RVA `0xC5EFD0`.

**Important honesty note:** there is **no single static "connected player
count" field** at a fixed offset. The count is derived by filtering the
all-players container. A "pure offset" read of the count is therefore not
possible; the practical read is to call `PlayerService::GetConnectedPlayers()`
(RVA `0xF27960`) and read `vec[+0x10]` (exactly one C++ call + one deref), or
walk the same filter yourself. The underlying session store is
`ServerGameplaySessions` (`GetSessions` -> `UnorderedMap<NetConnection*,
unique_ptr<ServerGameplaySession>>`; no public count getter), reached from
`World + 0xC0` (type-hash `0x107eb8b1`).

## 3. Score (no simple native field)

Requested target "`PlayerService` / `ResourceAccount` / round score" — I checked
all three:

- `PlayerService` has **no** `score`/`GetScore`/`roundScore` member (full method
  list inspected; nothing score-shaped).
- `ResourceAccount` layout is `{ +0x08 basket-array, +0x10 count, +0x20
  max-map }` — no score member.
- `round` is `RBB.round`, a mod-local Lua variable (see below).

The only native "score"-shaped objects are:

1. **`Riftbreaker::ScoreboardPlayerInfo`** (multiplayer scoreboard). 0x60-byte
   struct, default ctor RVA `0x12A0370`:
   - `+0x00` type-info pointer
   - `+0x08` `Exor::UtfString` (name; SSO, size `@+0x18`, cap `@+0x20` = `0xf`)
   - `+0x28` `Exor::UtfString` (2nd string)
   - `+0x50`, `+0x58` two 8-byte fields (likely score + team/kills — names not
     recoverable from the stripped PDB)
   Reached via `HudScoreboard::UpdateScoreBoard` (RVA `0x12BAF10`, reads
   `HudScoreboard[+0x50]` then a `Vector<ScoreboardPlayerInfo>`).
2. **`Riftbreaker::StatisticsService` / `StatisticsPlayerInfo` /**
   `StatisticsStatsGroup` / `StatisticsStatsEntry`** — the campaign statistics
   subsystem (kills, resources, time …). Multi-level map/vector aggregates, no
   single "score" offset.

**Bottom line:** the egress "score" has **no single native C++ field** to read.
It is either (a) a custom battle-mod economy value (pool/points, computed in
Lua, no C++ representation), or (b) must be mapped to the multiplayer
`ScoreboardPlayerInfo` / campaign `Statistics*` aggregates, which are complex.
Recommendation: keep "score" as a Lua/computed value for now and do not chase a
non-existent native offset.

## 4. `currentDifficultyLevel` / `time_to_next` — Lua-only (confirmed)

- `dom_mananger` is a Lua class: `class 'dom_mananger' (event_manager)` and
  `event_manager` is `class 'event_manager' (LuaGraphNode)`. Its only C++
  representation is the `LuaGraphNode` base (vftable `0x2F46D70`, `+0x20`
  luabind object -> Lua self table).
- `currentDifficultyLevel` is assigned in Lua (`self.currentDifficultyLevel = 0`
  in `init`, incremented in `OnExitDifficultyIncrease`, clamped in
  `SetMaxDifficultyLevel`). It is a **field on the Lua self table**, not a C++
  member.
- `time_to_next` does not exist as any field at all — it is **computed** in
  Lua/C++-mirror as `max(cooldownTimer, idleTimer, waitForSpawnTimer)` (and
  `GetDurationLimit - GetDuration` during `wait`). The underlying timers are
  also Lua fields on the DOM state machine.
- The only C++ "difficulty" concept is `DifficultyService` /
  `CampaignState::GetDifficulty` (`DifficultyDef*`, i.e. the mission difficulty
  preset name/strength) — **not** the DOM wave counter `currentDifficultyLevel`.

**Bottom line:** `currentDifficultyLevel` and `time_to_next` have **no direct
C++ offset**. They must stay on the `lua_*` path (game-thread marshaled, as in
#376/#378) or be derived from the C++ `StateMachine` (`dom_state`) + Lua timer
fields. This is a hard constraint, not a missing symbol.

## 5. Cheap extras

- **HQ alive/dead (C++ offset):** yes — `HealthComponent[+0x00] > 0.0f`, or
  `HealthService::IsAlive` RVA `0xF9E130` (see section 1).
- **DOM state name (C++ offset):** yes — `StateMachine[+0x50]` current-state
  index + `GetCurrentStateName` RVA `0x1DAEB50` (already documented in #376).
- **Mission state / round:** `round` is mod-local Lua (`RBB.round`), no C++.
  Mission identity (name/biome) lives in `MissionService` (service getters,
  still Lua-bound in the current egress); no single integer "mission state"
  offset found.
- **Player count:** see section 2 (computed vector, no static field).

## Lua-only vs. real C++ offset (the honest split)

**Real C++ offsets (pure read possible, no `lua_*`):**

| field | how |
|---|---|
| HQ hp / max / alive | `HealthService::GetHealth/MaxHealth/IsAlive` + `FindEntityByType` (or raw `HealthComponent` deref) |
| carbonium / carbonium_max / resources | existing carbonium path (#363/#365/#370) |
| `dom_state` | `StateMachine` + `GetCurrentStateName` (#376) |

**Lua-only (no direct read; must stay `lua_*` on the game thread):**

| field | why |
|---|---|
| `currentDifficultyLevel` (wave) | Lua field on `dom_mananger` self table |
| `time_to_next` | Lua-derived (max of Lua timers) |
| `time_to_next_difficulty` | Lua `StateMachine` + Lua timer fields |
| `round`, `players`, `commenced`, `mode` | mod-local `RBB` Lua state |
| `score` (battle-mod economy) | custom Lua value; no native equivalent |

## RVA / offset appendix (build 2.0.58485)

| symbol | RVA |
|---|---|
| `HealthService::GetHealth(uint)` | `0xF9BBB0` |
| `HealthService::GetMaxHealth(uint)` | `0xF9C360` |
| `HealthService::IsAlive(uint)` | `0xF9E130` |
| `HealthService::GetHealthInPercentage(uint)` | `0xF9BC20` |
| `HealthService` vftable | `0x2E95760` |
| `FindService::FindEntityByType(char const*)` | `0x1C0E420` |
| `FindService` vftable | `0x2E94C98` |
| `PlayerService::GetConnectedPlayers()` | `0xF27960` |
| `PlayerService::GetAllPlayers()` | `0xF27560` |
| `PlayerService::GetPlayersFromTeam(TeamId)` | `0xF2A0D0` |
| `Riftbreaker::GetConnectedPlayers(World*)` (free) | `0xC5EE70` |
| `PlayerService` vftable (known-good) | `0x2E8E910` |
| ECS component lookup `(store, id, typeHash, &out)` | `0x1DD06F0` |
| Health component type-hash getter | `0x8FCEA0` |
| `StateMachine::GetCurrentStateName` | `0x1DAEB50` |
| `LuaGraphNode` vftable (known-good) | `0x2F46D70` |

**Layouts:**

- `HealthService[+0x08]` = `World*`; `PlayerService[+0x08]` = `World*`
  (service base class).
- `World[+0x30]` = ECS entity-component store.
- `HealthComponent`: `[+0x00]` current HP (float), `[+0x04]` max HP (float).
- `Exor::Vector<T>`: `[+0x08]` data ptr, `[+0x10]` count (`T` = `unsigned int`
  -> 4-byte elements).
- `ScoreboardPlayerInfo` (0x60 B): `[+0x08]`/`[+0x28]` UtfStrings,
  `[+0x50]`/`[+0x58]` two 8-byte value fields.
