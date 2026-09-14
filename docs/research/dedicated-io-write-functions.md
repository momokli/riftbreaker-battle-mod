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
- **Thread-safety:** every mutator below touches game state and MUST run on the
  **game thread**. Marshal through the existing `ConsoleService::Update`
  vtable-detour (`0x1C1FBA0`) exactly like the current `debug_dom_*` commands —
  never call these from the pipe thread. (The exception is
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
5. **`self.data` (the `Exor::Database*` for `ActivateMissionFlow`)**: a pure C++
   "start wave" also needs the dom-manager's `data` Database (and, for a timed
   HUD wave, `data:SetFloat("time_max", …)`). Its C++ field offset on
   `dom_mananger` is not yet mapped (today the Lua mod sets it on the game thread).
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
call is made. `resolve_campaign_service()` additionally requires
`this+0x10` to be readable before any method is invoked.

## References

- `docs/research/dedicated-io-re-findings.md` (read path, `World::GetSystem`,
  `LuaGraphNode` resolver, StateMachine).
- `docs/research/io-write-poc.md` (`AddResourceAmount`, `UtfString` layout).
- `tools/re/disasm.py`.
- Lua source: `lua/missions/v2/dom_manager.lua`, `event_manager.lua`,
  `missions/mission_base.lua`, `commands/debug.lua`.
