# Dedicated IO — Native C++ WRITE functions (game-control)

Reverse-engineered map of the **native C++ functions** behind game-control, so
the DLL can later add typed native WRITE commands that bypass
`ConsoleService::ExecuteCommand` -> Lua. This is the "pure C++ WRITE" half of
issue #378 (the "pure C++ WRITE" of the read/write pair; the read half is
already live via #363/#365/#370/#376).

Build: **2.0.58485** (GOG == Dedi, byte-identical). Tools: `llvm-pdbutil dump
-publics` (stripped PDB, `S_PUB32` names+addresses only), `tools/re/disasm.py`.

## Conventions

- **RVA** = image-relative; VA = ImageBase `0x180000000` + RVA.
- **`this`** = MSVC x64 `__thiscall` (`rcx`); args in `rdx`, `r8`, `r9`, then
  stack.
- **`UtfString`** = `Exor::UtfString<char, Exor::utf_traits<char>, ...>`
  (SSO, data at `+8`, size at `+0x18`, capacity at `+0x20`; see
  `io-write-poc.md`).
- **Thread-safety:** every mutator below touches game state and **should** run on
  the **game thread**. There is **no marshal in `main`** — no `g_pending_cmd`, no
  `ConsoleService::Update` vtable-detour, no `exec`/`lua_*` in `rbbridge.c` (all
  removed with the C++-direct-only refactor, #387/#446). Today the bridge calls
  these mutators **inline on the pipe thread** (guarded direct call), which is an
  open live risk — see **[dedicated-io-thread-model.md](dedicated-io-thread-model.md)**
  (thread model, readiness gate #479, crash evidence #436). Note the **#378
  correction**: `ConsoleService::Update` (`0x1C1FBA0`) is a **worker** thread —
  fine for pure C++ reads/writes, but **unusable for `lua_*`**. (The exception is
  `PlayerService::AddResourceAmount` `0xF1E3D0`, empirically pipe-thread-safe,
  but that is not a service-mutator in the DOM/mission sense.)

## Function map

| Lua command / path                                                                                                                                                 | native C++ function                                                          | RVA                                                         | signature                                                                                       | `this` source                                                                                                        | args                                                           | notes                                                                                                                                                      |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------- | ----------------------------------------------------------- | ----------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `dom_mananger:SetSuspended(bool)` / `self:SetSuspended(...)`                                                                                                       | `Exor::LuaGraphNode::SetSuspended`                                           | `0x1BA6CB0`                                                 | `void (bool)`                                                                                   | `dom_mananger` instance = `LuaGraphNode*` (vftable `0x2F46D70`); resolve via vftable-scan + `[+0x20]` luabind object | `bool suspended`                                               | Body is literally `mov byte [rcx+0xF1], dl; ret`. Sets the suspended flag; while set, `LuaGraphNode::Update` (`0x1BAA140`) returns early.                  |
| `CampaignService:OperateDOMPlanetaryJump(bool)` (in `PauseDOM`, `FinishSurvival`, `OnEnterWait`, `OnExitPrepareSpawn`, `OnExitSleep`, …)                           | `Riftbreaker::CampaignService::OperateDOMPlanetaryJump`                      | `0x1014990`                                                 | `void (bool)`                                                                                   | `CampaignService*` (vftable `0x2E9C340`)                                                                             | `bool jump` — `true` = planetary-jump/paused, `false` = active | `mov rcx,[rcx+8]; call 0x180fe4b40; mov [rax+1], bl`. `[this+8]` = `World*`.                                                                               |
| `dom_mananger:SpawnWavesForDifficultyLevel(level, shouldAdd)` -> `SpawnWave`/`SpawnPreparedWave` -> `MissionService:ActivateMissionFlow(...)`                      | `Riftbreaker::MissionService::ActivateMissionFlow` (Database\* overload)     | `0xF93280`                                                  | `UtfString (UtfString const&, UtfString const&, UtfString const&, Database*)`                   | `MissionService*` (vftable `0x2E962A0`); `[this+8]` = `World*`                                                       | `name`, `logicFile`, `mode` ("default"), `data` (Database\*)   | The real workhorse. Delegates to `MissionSystem::ActivateMissionFlow` (`0x33A7E0`). Returns the flow name/id (stored into `self.spawnedAttacks`).          |
| `MissionService:ActivateMissionFlow(...)` (Lua-bound variant)                                                                                                      | `Riftbreaker::MissionService::ActivateMissionFlow` (luabind object overload) | `0xF93160`                                                  | `UtfString (UtfString const&, UtfString const&, UtfString const&, luabind::adl::object const&)` | `MissionService*`                                                                                                    | 3x `UtfString` + `luabind::object` data                        | Lua-facing wrapper; the `Database*` overload above is the one to call from C++.                                                                            |
| `MissionService:ActivateMissionFlow(...)` (other overloads)                                                                                                        | `Riftbreaker::MissionService::ActivateMissionFlow`                           | `0xF93130` (3x UtfString), `0xF93350` (2x), `0xF93420` (1x) | `UtfString (...)`                                                                               | `MissionService*`                                                                                                    | —                                                              | Convenience overloads; all funnel into the same core.                                                                                                      |
| `MissionService:DeactivateMissionFlow(name)` (`ClosePrepareForTheAttack`, `OnEnterStreaming`)                                                                      | `Riftbreaker::MissionService::DeactivateMissionFlow`                         | `0xF960E0`                                                  | `void (UtfString const&)`                                                                       | `MissionService*`; `[this+8]` = `World*`                                                                             | `name` = flow id returned by `ActivateMissionFlow`             | Delegates to `MissionSystem::DeactivateMissionFlow(UtfStringView)` (`0x33C890`).                                                                           |
| `MissionService:IsGraphActive(name)`                                                                                                                               | `Riftbreaker::MissionService::IsGraphActive`                                 | `0xF9E1F0`                                                  | `bool (UtfString const&)`                                                                       | `MissionService*`                                                                                                    | `name`                                                         | Read-only predicate, useful to guard writes.                                                                                                               |
| `debug_win_game`/`debug_lose_game` -> `QueueEvent("LuaGlobalEvent", ..., "win_game"/"lose_game")` -> `mission_base:_OnLuaGlobalEvent` -> `OnMissionFinish(status)` | `Riftbreaker::MissionService::FinishCurrentMission`                          | `0xF9A190`                                                  | `void (MissionStatus)`                                                                          | `MissionService*`; `[this+8]` = `World*`                                                                             | `MissionStatus status` (`MISSION_STATUS_WIN`/`_LOSE`/…)        | The win/lose end-of-mission. For a typed native WRITE, call this directly instead of `QueueEvent("LuaGlobalEvent", ...)`.                                  |
| `debug_end_game` -> `QueueEvent("ShowEndGameRequest", event_sink, MISSION_STATUS_WIN)`                                                                             | _(no direct C++ fn — event dispatch)_                                        | —                                                           | —                                                                                               | —                                                                                                                    | —                                                              | `ShowEndGameRequest` is a generic event, not a service method. Equivalent native WRITE = `FinishCurrentMission(MISSION_STATUS_WIN)` (or `ShowEndGameHud`). |
| `MissionService:ShowEndGameHud(5.0, false)` (`OnRespawnFailedEvent`)                                                                                               | `Riftbreaker::MissionService::ShowEndGameHud`                                | `0xFA8C00`                                                  | `void (float, bool)`                                                                            | `MissionService*`                                                                                                    | `float delay`, `bool ???` (inverted: `xor ebx,1`)              | Second arg is inverted before use; Lua passes `false`.                                                                                                     |
| `MissionService:DeactivateAllFlows()` (death path)                                                                                                                 | `Riftbreaker::MissionService::DeactivateAllFlows`                            | `0xF95FF0`                                                  | `void ()`                                                                                       | `MissionService*`                                                                                                    | —                                                              | Delegates to `MissionSystem` (`0x33C820` via `jmp`).                                                                                                       |
| `MissionService:GetCurrentMissionFailedAction()`                                                                                                                   | `Riftbreaker::MissionService::GetCurrentMissionFailedAction`                 | `0xF9B500`                                                  | `MissionFinishedAction ()`                                                                      | `MissionService*`                                                                                                    | —                                                              | Reads `[[this+8]+0x400]`.                                                                                                                                  |
| `MissionService:GetCurrentMissionName()`                                                                                                                           | `Riftbreaker::MissionService::GetCurrentMissionName`                         | `0xF9B520`                                                  | `UtfString ()`                                                                                  | `MissionService*`                                                                                                    | —                                                              | Read-only getter.                                                                                                                                          |
| `spawner:ChangeState("...")` (wave state machine)                                                                                                                  | `Exor::LuaStateMachine::ChangeState`                                         | `0x1F9EB20`                                                 | `LuaState* (char const*)`                                                                       | `spawner` = `LuaStateMachine*` (field on `dom_mananger`)                                                             | `char const* stateName`                                        | Drives `spawner`/`difficultyIncrease` state transitions.                                                                                                   |
| `spawner:AddState(name, {enter=…, exit=…})`                                                                                                                        | `Exor::LuaStateMachine::AddState`                                            | `0x1F9E1F0`                                                 | `void (char const*, luabind::adl::object const&)`                                               | `LuaStateMachine*`                                                                                                   | `name`, `luabind::object` callbacks                            | Builds the state machine (boot-time).                                                                                                                      |
| `self:CreateStateMachine()`                                                                                                                                        | `Exor::LuaEntityObject::CreateStateMachine`                                  | `0x1B3AE50`                                                 | `LuaStateMachine* ()`                                                                           | `dom_mananger` (inherited via `LuaGraphNode` chain)                                                                  | —                                                              | The only 0-arg `CreateStateMachine` returning `LuaStateMachine*`; inherited (see Unresolved).                                                              |
| `spawner:GetCurrentState()` (returns name)                                                                                                                         | `Exor::StateMachine::GetCurrentStateName`                                    | `0x1DAEB50`                                                 | `UtfString ()`                                                                                  | `LuaStateMachine*` (base `StateMachine`)                                                                             | —                                                              | Read-only; `StateMachine + 0x50` = current state index.                                                                                                    |
| `spawner:GetState(name)`                                                                                                                                           | `Exor::StateMachine::GetState`                                               | `0x1DAF3D0`                                                 | `State* (unsigned int)` (protected)                                                             | `StateMachine*`                                                                                                      | `uint index`                                                   | Read-only.                                                                                                                                                 |
| per-frame state update                                                                                                                                             | `Exor::StateMachine::Update`                                                 | `0x1DB6780`                                                 | `void (float)`                                                                                  | `StateMachine*`                                                                                                      | `float dt`                                                     | Advances the active state.                                                                                                                                 |

## Wave-spawn path (Lua -> C++)

```
dom_mananger:OnEnterSpawn / OnEnterPrepareSpawn
  └─ SpawnWavesForDifficultyLevel(level, shouldAdd)
       ├─ SpawnWave(...) / SpawnPreparedWave(...)
       │    └─ MissionService:ActivateMissionFlow(name, waveName, "default", self.data)
       │         RVA 0xF93280 (Database* overload)
       │           └─ MissionSystem::ActivateMissionFlow  RVA 0x33A7E0
       │                (UtfStringView name, UtfString logicFile, UtfString mode, Database*)
       └─ MissionService:ActivateMissionFlow("", wavesEntryDefinitions[level].name, "default", self.data)
```

- The **spawn itself** (creature instantiation) is the `MissionSystem::ActivateMissionFlow`
  (`0x33A7E0`) -> flow-graph execution of the `.logic` file. There is no dedicated
  "spawn service" call in this path — waves are implemented as mission-flow graphs.
- **State transitions** around spawning go through `LuaStateMachine::ChangeState`
  (`0x1F9EB20`): `spawner:ChangeState("prepare_spawn"|"streaming"|"spawn"|"cooldown_after_spawn"|"sleep"|…)`
  and `difficultyIncrease:ChangeState("difficulty_increase")`.
- **`debug_dom_manager_spawn_wave_level N`** (the existing command) only calls
  `SpawnWavesForDifficultyLevel(N, false)` in `dom_mananger:Update`; it does NOT
  bump `currentDifficultyLevel` — the timer increments difficulty on its own
  (`OnExitDifficultyIncrease`). A native WRITE "spawn wave level N" therefore maps
  to `MissionService::ActivateMissionFlow` with the level-N `wavesEntryDefinitions`
  name, not a state-machine jump.

## End-game (win / lose)

```
debug_win_game  -> QueueEvent("LuaGlobalEvent", event_sink, "win_game", {})
debug_lose_game -> QueueEvent("LuaGlobalEvent", event_sink, "lose_game", {})
   └─ mission_base:_OnLuaGlobalEvent -> OnMissionFinish(MISSION_STATUS_WIN/LOSE)
        └─ MissionService::FinishCurrentMission(status)   RVA 0xF9A190

debug_end_game  -> QueueEvent("ShowEndGameRequest", event_sink, MISSION_STATUS_WIN)
                   (generic event; native equivalent = FinishCurrentMission)

death           -> MissionService:ShowEndGameHud(5.0, false)   RVA 0xFA8C00
                   + MissionService:GetCurrentMissionFailedAction()  RVA 0xF9B500
                   + (if != MFA_REMAIN) MissionService:DeactivateAllFlows()  RVA 0xF95FF0
```

For a typed native `end_game`/`win_game`/`lose_game` WRITE, the target is
`MissionService::FinishCurrentMission` (`0xF9A190`), passing the `MissionStatus`
enum value (see Unresolved for the numeric values).

## Service-instance resolution

Two viable strategies:

### A. vftable-scan (recommended — mirrors the existing `resolve_console_instance`)

The services are singletons. Scan committed readable memory (VirtualQuery loop,
8-byte aligned) for a QWORD equal to `module_base + vftable_rva`; the first hit is
the instance (`instance[0] == &vftable`). Pure read-only memory access, no
`World::GetSystem`, no boot-time page-fault, no constructor args needed.

| service                                   | vftable symbol                           | vftable RVA               |
| ----------------------------------------- | ---------------------------------------- | ------------------------- |
| `Riftbreaker::MissionService`             | `??_7MissionService@Riftbreaker@@6B@`    | `0x2E962A0`               |
| `Riftbreaker::CampaignService`            | `??_7CampaignService@Riftbreaker@@6B@`   | `0x2E9C340`               |
| `Riftbreaker::DifficultyService`          | `??_7DifficultyService@Riftbreaker@@6B@` | `0x2E9C758`               |
| `Riftbreaker::PlayerService`              | `??_7PlayerService@Riftbreaker@@6B@`     | `0x2E8E910`               |
| `Exor::ConsoleService`                    | `??_7ConsoleService@Exor@@6B@`           | `0x2F23C80`               |
| `Exor::LuaGraphNode` (`dom_mananger`)     | `??_7LuaGraphNode@Exor@@6B@`             | `0x2F46D70`               |
| `Riftbreaker::MissionSystem` (base views) | `??_7MissionSystem@Riftbreaker@@6B…`     | `0x2DCF5F0` / `0x2DCF6A0` |

### B. `World::GetSystem<LuaSystem>` -> `LuaSystem::CreateService<T>` (the "official" path)

```
World*  (from PlayerService + 0x8, or MissionService/CampaignService + 0x8)
  └─ World::GetSystem<LuaSystem>(world)               RVA 0x194EDA0  -> LuaSystem*
       └─ LuaSystem::CreateService<MissionService>()  RVA 0x1943E70  -> MissionService*
       └─ LuaSystem::CreateService<DifficultyService>() RVA 0x1940840 -> DifficultyService*
       └─ LuaSystem::CreateService<ConsoleService>()  RVA 0x1940460  -> ConsoleService*
       └─ LuaSystem::CreateService<CampaignService>(CampaignState*&&)  RVA 0x19400C0 -> CampaignService*
```

- `World::GetSystem<T>()` (`0x194EDA0`) is a thin template: `GetType<T>()` ->
  `[Type+0x30]` TypeHash, then tail-jumps to the generic
  `World::GetSystem(TypeHash)` at RVA `0x1CDB3B0`.
- Generic `World::GetSystem(TypeHash)` layout: `World+0x18` = system array,
  `World+0x20` = count; each entry is `0x40` bytes with `entry[0]` = `TypeHash`
  (u32) and `entry[0x30]` = system pointer.
- `LuaSystem::CreateService<T>()` is a **get-or-create** singleton per `LuaSystem`;
  the container is a sorted vector at `[LuaSystem+0x420]` (ptr `+0x428`, size
  `+0x430`), keyed by the service TypeHash. It returns the existing instance if
  present, else constructs + inserts it.

**Tradeoff:** path B needs a `World*` (another hop) and, for `CampaignService`, a
`CampaignState*&&` constructor argument — plus `World::GetSystem(TypeHash)`
page-faults during boot while the system map is still being built (same crash class
documented in #376). Path A (vftable-scan) is a single hop, needs no `World*`, no
`CampaignState`, and is already proven in `rbbridge.c`. Prefer **A**; keep B as the
documented fallback / for resolving `MissionSystem` (which is NOT a Lua singleton).

## Layout facts (from disasm)

- `MissionService + 0x8` = `World*` (all `Activate*`/`Finish*` methods do
  `[rcx+8]` -> `World::GetSystem(TypeHash)` with the `MissionSystem` type hash).
- `CampaignService + 0x8` = `World*` (same pattern in `OperateDOMPlanetaryJump`).
- `LuaGraphNode + 0xF1` = suspended flag (written by `SetSuspended`).
- `LuaGraphNode + 0x20` = `luabind::object { lua_State* @+0, int ref @+8 }` (the
  Lua self table) — used by the existing resolver.

## Unresolved

1. **`MissionStatus` enum numeric values** (`MISSION_STATUS_NONE`/`_IN_PROGRESS`/
   `_WIN`/`_LOSE`). These are C++ enum constants pushed into Lua in
   `MissionService::RegisterLua` (`0xFA4DB0`) via `lua_pushnumber`; the exact
   `double` values were not extracted. Resolve by disassembling `RegisterLua` +
   reading the `movsd xmm1,[rip+…]` constants, or a live `tostring(MISSION_STATUS_WIN)`.
2. **`MissionFinishedAction` enum values** (`MFA_REMAIN` and friends) returned by
   `GetCurrentMissionFailedAction` (`0xF9B500`).
3. **`CampaignService` construction**: path B's `CreateService<CampaignService>`
   requires a `CampaignState*&&`; the `CampaignState` acquisition path is not
   documented (vftable-scan sidesteps it).
4. **`CreateStateMachine` on `LuaGraphNode`**: resolved to
   `LuaEntityObject::CreateStateMachine` (`0x1B3AE50`) by elimination (no
   `LuaGraphNode::CreateStateMachine` symbol exists), but the exact base-class
   inheritance (`LuaGraphNode -> LuaEntityObject`) was not RTTI-verified.
5. **`self.data` (`Exor::Database*` fuer `ActivateMissionFlow`)**: seit #386
   koennen wir das Payload-Objekt **selbst bauen** (`malloc(0x60)` + Default-Ctor
   + `SetString`, AOB-aufgeloest) und als `data` durchreichen. Der konkrete
   `data`-Feld-**Offset** auf `dom_mananger` bleibt ungemappt (nicht noetig:
   wir bauen ein eigenes Objekt); `SetFloat("time_max", ...)` ist ebenfalls
   noch nicht verdrahtet (siehe Punkt 6).
6. **HUD "next wave in X" countdown** is the mission-flow `time_max` (set via
   `data:SetFloat("time_max", …)` in Lua) — a native `ActivateMissionFlow` without
   setting `time_max` spawns the wave but shows no HUD timer.

## #385 — `ActivateMissionFlow` als natives Primitive (AOB, LIVE)

Umgesetzt in `rbbridge.c`: `activate_mission_flow` ruft den Workhorse direkt
(kein Lua/Console), die Adresse kommt aus einer **AOB-Signatur** (kein festes
RVA), die Service-Instanz aus dem vftable-Scan.

**AOB-Signatur** (`RBBRIDGE_ACTIVATE_SIG`, 33 B, planet 2026-09-15: genau
1 Treffer im `.text`, entspricht RVA `0xF93280`):

```
48 89 5C 24 08 48 89 6C 24 18 48 89 74 24 20 57 48 83 EC 50
49 8B E9 49 8B F0 48 8B FA 48 8B 59 08
```

**Aufrufkonvention des Database\*-Overloads** (verifiziert am Disasm):
`this=RCX`, hidden-ret-`UtfString*`=RDX, `name`=R8, `logicFile`=R9,
`mode`=[rsp+0x20], `data`=[rsp+0x28]. Der 3-Arg-Overload (`0xF93130`) ist nur
ein Shim, der den Workhorse mit `data=NULL` ruft — NULL ist also ein vom Spiel
selbst benutzter, gueltiger `Database*`-Wert (→ #385 ohne #386 nutzbar).

**`mode` ist immer `"default"`** (#447): das Spiel-eigene Lua uebergibt als
3. Argument ausschliesslich `"default"`; ein anderer Wert (z. B. `"hard"`)
killt die DLL-Pipe dauerhaft (`/health` → `pipe:false`, danach jedes
`get_state` → `pipe_unavailable`, Container bleibt healthy). `rbbridge`
lehnt Nicht-`default` daher mit
`{"event":"activate_mission_flow_result","ok":false,"reason":"bad_mode"}` ab
(`mission_flow_mode_ok`, host-getestet), das Cockpit sendet fest `"default"`.

**Helfer-RVAs** (klein, fest — wie die PlayerService-RVAs):

| Symbol | RVA |
| --- | --- |
| `??_7MissionService@Riftbreaker@@6B@` (vftable) | `0x2E962A0` |
| `UtfString(char const*)` (Ctor) | `0x3AE1E0` |
| `~UtfString()` (Dtor) | `0x26F1F0` |
| `MissionService::IsGraphActive(UtfString const&)` | `0xF9E1F0` |

`UtfString`-Layout (aus Ctor-Disasm): `+0x00` Allocator-Proxy, `+0x08`
SSO-Puffer/Heap-Ptr, `+0x18` size, `+0x20` capacity. Die Argumente werden ueber
den **Spiel-eigenen Ctor** gebaut (Logic-Pfade > 15 Zeichen → Heap), nicht von
Hand.

**Live belegt** (planet, `riftbreaker-dedicated`, 2026-09-15, AOB→`fn_rva=0xF93280`):

```
POST /activate_mission_flow {"logic":"logic/dom/attack_level_1_entry.logic"}
 -> {"event":"activate_mission_flow_result","ok":true,
     "flow":"logic/dom/attack_level_1_entry.logic##000070E2E2690000##1"}
POST /get_state
 -> ...,"mission_flow":"logic/dom/attack_level_1_entry.logic##...##1",
       "mission_flow_active":false   (spaeter: true = IsGraphActive)
```

Kein Crash; die `no_account`-Antworten (`Welt laedt noch`) liefern die
Mission-Flow-Felder mit (Read haengt nicht am Spieler-Account).

## #386 — `Exor::Database*`-Payload (AOB, C++-only)

Umgesetzt in `rbbridge.c` (kein Lua/Console): `activate_mission_flow` baut bei
 gesetztem `spawn_point` ein eigenes `Exor::Database` (0x60 B, **kein**
vftable) und reicht es als 4. Stack-Parameter (`data`, `[rsp+0x28]`) an
`MissionService::ActivateMissionFlow` durch. Ohne `spawn_point` bleibt
`data=NULL` (exakt #385-Verhalten).

`Exor::Database`-Layout (Disasm, Build 2.0.58485): 0x60 B = 3x 0x20 Container;
kein vftable. Methoden: Default-Ctor `0x2C6550`, `SetString(UtfString const&,
UtfString const&)` `0x25956B0`, `GetString(UtfString const&)` -> `UtfString const&`
`0x2591FD0`. Details + Klassen-Layout:
`docs/research/database-object-re-findings.md`.

**AOB-Signaturen** (planet 2026-09-15, `.text`-first; rel32 der E8-Calls per
Maske entschaerft):

| Signatur | Bytes | Treffer im `.text` |
| --- | --- | --- |
| `RBBRIDGE_DB_SETSTRING_SIG` | `4C 89 44 24 18 53 48 83 EC 30 49 8B D8 48 8B 42 18 48 83 C2 08 48 83 7A 18 0F` | 1 (`0x25956B0`) |
| `RBBRIDGE_DB_GETSTRING_SIG` | `40 53 48 81 EC 80 00 00 00 48 8B DA E8 ?? ?? ?? ?? 48 85 C0 74 09 48 81 C4 80 00 00 00 5B C3` | 1 (`0x2591FD0`) |
| `RBBRIDGE_DB_CTOR_SIG` | `48 89 5C 24 18 48 89 74 24 20 48 89 4C 24 08 57 48 83 EC 20 48 8B F9 E8 ?? ?? ?? ??` | **258** (nicht eindeutig!) |

**Ctor-Anker statt Prolog-Scan:** der `Database::Database()`-Body ist
byte-identisch mit `??0EntityStatComponent@Riftbreaker@@QEAA@XZ` u. a. Darum
loest `resolve_db_ctor_fn` den Ctor ueber die `new 0x60`-Call-Site auf
(`B9 60 00 00 00 E8 ..` … `48 8B C8 E8 <rel32>`), behaelt von den
Ctor-Kandidaten nur die mit passendem Prolog und akzeptiert **genau einen**
Treffer; sonst `NULL`
(kein Aufruf).

**Read-Leg:** `get_state` liefert `mission_flow_payload{spawn_point}` aus dem
geparkten Objekt via `Database::GetString`; bei Nicht-Fund faellt das Feld auf
`null` bzw. den zuletzt gesetzten Wert zurueck — nie ein Crash.

**Live-Status:** Der Aufruf laeuft (wie #385) im Pipe-Thread (`guarded
Direktaufruf`); ob das Spiel den Payload sichtbar uebernimmt, ist nur mit
Player pruefbar (offener Punkt).

## #388 — `CampaignService` Creatures-Base-Difficulty (Read + Write)

Low-level C++ primitive for the campaign difficulty (creature scaling).
Found by `llvm-pdbutil dump -publics` (PDB, build 2.0.58485) + disasm on
planet (2026-09-15). **No Lua, no Console.**

### Symbols / RVAs

| demangled symbol | PDB `addr` | RVA | notes |
| --- | --- | --- | --- |
| `??_7CampaignService@Riftbreaker@@6B@` | `0002:1024832` (.rdata) | `0x2E9C340` | vftable — only used for **instance** resolution |
| `Riftbreaker::CampaignService::GetCreaturesBaseDifficulty(void)` | `0001:16820704` | `0x100B9E0` | `float` return (XMM0) |
| `Riftbreaker::CampaignService::SetCreaturesBaseDifficulty(float)` | `0001:16900352` | `0x101F100` | absolute set |
| `Riftbreaker::CampaignService::IncreaseCreaturesBaseDifficulty(float)` | `0001:16844976` | `0x10118B0` | delta `+=` |
| `Riftbreaker::CampaignService::DecreaseCreaturesBaseDifficulty(float)` | `0001:16806272` | `0x1008180` | delta `-=` |

All four are **public non-virtual** members (mangled `QEAA…`), i.e. they are
called **directly** (`call rel32`), NOT through the vftable. The vftable is
only needed to locate the `CampaignService` instance via the QWORD scan.

> ⚠️ The issue text names `RevertCreaturesBaseDifficulty` — **no such symbol
exists** in the binary. The actual pair is `Decrease…` (delta) +
`Set…` (absolute). Both are implemented.

### Layout (from the disasm of all four)

```
CampaignService (vftable RVA 0x2E9C340)
  + 0x10  -> ptr to the campaign-params object
              + 0x584 = float creatures_base_difficulty
```

### Call convention (MSVC x64)

`this`=RCX, the `float` argument in **XMM1** (integer slot 0 is taken by
`this`; FP args are numbered independently), return value in XMM0. Pure C++
-> **thread-agnostic** (#378): safe from the pipe thread, no `lua_*`.

### AOB signatures (planet-verified unique in `.text`, exactly 1 hit each)

```
Get  48 8B 41 10 F3 0F 10 80 84 05 00 00 C3
Set  48 8B 41 10 F3 0F 11 88 84 05 00 00 C3
Inc  48 8B 41 10 F3 0F 58 88 84 05 00 00 F3 0F 11 88 84 05 00 00 C3
Dec  48 8B 41 10 F3 0F 10 80 84 05 00 00 F3 0F 5C C1 F3 0F 11 80 84 05 00 00 C3
```

Runtime addresses come **only** from these AOB scans (`RBBRIDGE_DIFF_*_SIG` in
`rbbridge.c`); the RVAs above are verification notes. No rel32 operands in the
prologues -> no wildcard mask needed.

### Bridge / UI

- `rbbridge.c`: `dispatch_creatures_difficulty()` (ops `set|increase|decrease`),
  pipe cmd `creatures_difficulty`; `get_state` adds
  `creatures_base_difficulty` (number or `null`).
- `pipe_bridge.c`: `POST /creatures_difficulty` `{op,value}` -> bridges to the
  pipe (`value` sent as string).
- `cockpit.html`: section *creatures base difficulty* (read + set/increase/
  decrease buttons).

Graceful failure: missing module / signature / instance -> `ok:false`, **no**
call is made. `resolve_campaign_diff()` additionally requires
`this+0x10` to be readable before any method is invoked. Its cache is
re-validated against module base/size, the instance vftable **and** the four
function prologues (layout hot-patch at unchanged module base -> re-scan).

## #519 — `end_game` (Win/Lose) nativ: `MissionService::FinishCurrentMission`

Natives Match-Ende (Win/Lose) ohne Lua/Console. `debug_win_game`/
`debug_lose_game` (Lua) landen am Ende in `MissionService::FinishCurrentMission`;
fuer den typisierten WRITE wird diese Funktion direkt gerufen.

### Symbol / Signatur (PDB, Build 2.0.58485, planet 2026-09-15)

| demangled symbol | PDB `addr` | RVA | notes |
| --- | --- | --- | --- |
| `?FinishCurrentMission@MissionService@Riftbreaker@@QEAAXW4MissionStatus@2@@Z` | `0001:16355728` | `0xF9A190` | `void`, public **non-virtual**, 1 Arg (`MissionStatus`, 4 B) |
| `?ShowEndGameHud@MissionService@Riftbreaker@@QEAAXM_N@Z` | `0001:16415744` | `0xFA8C00` | HUD-Endschein; nicht der End-Write |
| `?GetCurrentMissionFailedAction@MissionService@Riftbreaker@@QEAA?AW4MissionFinishedAction@2@XZ` | `0001:16360704` | `0xF9B500` | read-only |

RVA-Umrechnung: `RVA = 0x1000 + 16355728 = 0xF9A190` (`.text`-Formel).
Aufrufkonvention (MSVC x64): **this=RCX, status=EDX** (Rueckgabe void).

### Klassen-Layout (Disasm `0xF9A190`, Prolog)

```
48 89 5C 24 10   mov  [rsp+0x10], rbx
57               push rdi
48 81 EC 90 00 00 00  sub rsp,0x90
8B DA            mov  ebx,edx          ; status (MissionStatus)
48 8B 79 08      mov  rdi,[rcx+8]      ; MissionService + 0x8 = Exor::World*
48 8B CF         mov  rcx,rdi
E8 ..            call <World::GetSystem>
```

Der Body baut eine `MissionStatusChangeRequest` (`status @ +0x30`,
`bool @ +0x34`) und reicht sie an das `MissionSystem` weiter. **Kein `lua_*`**
im Pfad -> reines C++, thread-agnostisch (#378); der Pipe-Thread ruft direkt
(wie #389 `DeactivateMissionFlow`). Kein Vtable-Detour.

### `MissionStatus`-Werte (Disasm `MissionService::RegisterLua` @ `0xFA4DB0`)

Die vier Lua-Globals werden per `lua_pushnumber` gesetzt; Konstanten aus dem
`RegisterLua`-Disasm:

| Lua-Global | Wert |
| --- | --- |
| `MISSION_STATUS_WIN` | `0` (`xorps xmm1,xmm1`) |
| `MISSION_STATUS_LOSE` | `1` (`movsd 1.0`) |
| `MISSION_STATUS_IN_PROGRESS` | `2` (`movsd 2.0`) |
| `MISSION_STATUS_NONE` | `3` (`movsd 3.0`) |

### AOB statt fester Adresse

`RBBRIDGE_FINISHMISSION_SIG` (22 B Prolog, kein rel32 -> keine Maske):

```
48 89 5C 24 10 57 48 81 EC 90 00 00 00 8B DA 48 8B 79 08 48 8B CF
```

Gegenprobe planet 2026-09-15: genau **1 Treffer** im `.text` @ RVA `0xF9A190`.

### Bridge / UI (#394 Full-Chain)

- `rbbridge.c`: `end_game_status_from_str()` (host-getestet) +
  `dispatch_end_game()` (AOB-Signatur + vftable-Instanz-Scan), pipe cmd
  `end_game` `{result:"win"|"lose"}` -> `{"event":"end_game_result","ok":true,
  "result":...,"status":0|1}`.
- Read-Leg: `get_state` liefert `end_game` (`null` oder
  `{"result":"win","status":0}`), platziert **vor** den Account-Branches ->
  auch in `no_playerservice`/`no_world`/`no_account` sichtbar.
- `pipe_bridge.c`: `POST /end_game` `{result}` -> Pipe; 400 bei fehlendem/
  unbekanntem `result`, 503/500 wie die uebrigen Writes.
- `cockpit.html`: Sektion *Match End (Win/Lose)* (Readout `last end` +
  Buttons `win`/`lose`).

Graceful: unbekanntes `result` / fehlende Signatur / fehlende Instanz ->
`ok:false`, **kein** Call. Wine-Gotcha (`GetModuleHandleA` GLE=126) ist durch
`resolve_module` (Stufen a-e inkl. `sigbase`) bereits abgedeckt.

**Offener Punkt (Player-Test Momo/Matheo):** die sichtbare Wirkung im Spiel
(Match endet mit Win/Lose, End-Screen/HUD) ist nur mit verbundenem Spieler
pruefbar (`get_state` ist auf leerem Dedi `no_account`). Live belegt ist der
native Call (`end_game_result ok:true`) + Readback-Feld.

## References

- `docs/research/dedicated-io-re-findings.md` (read path, `World::GetSystem`,
  `LuaGraphNode` resolver, StateMachine).
- `docs/research/io-write-poc.md` (`AddResourceAmount`, `UtfString` layout).
- `tools/re/disasm.py`.
- Lua source: `lua/missions/v2/dom_manager.lua`, `event_manager.lua`,
  `missions/mission_base.lua`, `commands/debug.lua`.
