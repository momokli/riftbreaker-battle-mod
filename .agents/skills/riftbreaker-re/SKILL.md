---
name: riftbreaker-re
description: Reverse-engineering the Riftbreaker DLL (riftbreaker_dll_win_release.dll) for ingress/egress C++ direct reads in rbbridge.c — symbol lookup (llvm-pdbutil), RVA conversion, disasm (tools/re/disasm.py), and the live test workflow on planet. Use when adding a new C++-read field (wave/time-to-next/resources), resolving offsets/RVAs, or running the live inject-and-verify loop.
---

# Riftbreaker DLL Reverse Engineering (ingress/egress C++ direct reads)

For work on the dedicated-IO bridge (`server/dll/rbbridge.c`):
reading game state directly out of `riftbreaker_dll_win_release.dll` without
log-tailing. Build 2.0.58485 (GOG == Dedi, byte-identical on mac + planet).

## When to use

- Adding a new `get_state`/`add_resource`-style C++ read (offsets, RVAs, Lua
  fields).
- Resolving a class layout, vftable RVA, or a `lua_*` C-API function address.
- Running the live inject → pipe → curl validation loop on planet.

## Tooling (mac + planet)

- **DLL/PDB (byte-identical on both):**
  - mac (CrossOver): `~/Library/Application Support/CrossOver/Bottles/gams/drive_c/Program Files (x86)/The Riftbreaker/bin/riftbreaker_dll_win_release.{dll,pdb}`
  - planet: `/srv/rbgame/bin/riftbreaker_dll_win_release.{dll,pdb}`
- **Symbols:** `llvm-pdbutil dump -publics "$PDB"` (rich `S_PUB32` stream; the
  TPI/IPI type streams are empty — no struct layouts, only names+addresses).
  `llvm-pdbutil` lives in `/opt/homebrew/opt/llvm/bin/` (mac) / on PATH (planet).
- **Disasm:** `~/.venvs/rb-re/bin/python tools/re/disasm.py <RVA> [count]`
  (capstone + pefile). RVA is hex; it prints `VA = ImageBase + RVA`.
- Section headers: `llvm-pdbutil dump -section-headers "$PDB"`.

## RVA conversion (CRITICAL — the `addr = SSSS:OOOOOOOO` output is DECIMAL offset)

`llvm-pdbutil dump -publics` prints, under each symbol:
`flags = function, addr = 0001:15848400`. The value after `:` is a **decimal**
section-relative offset. Convert with the section base:

| Section  | `addr` prefix | base RVA    | formula                     |
| -------- | ------------- | ----------- | --------------------------- |
| `.text`  | `0001:`       | `0x1000`    | RVA = `0x1000` + decimal    |
| `.rdata` | `0002:`       | `0x2DA2000` | RVA = `0x2DA2000` + decimal |
| `.data`  | `0003:`       | `0x3EE8000` | RVA = `0x3EE8000` + decimal |

Sanity check: `PlayerService::AddResourceAmount` addr `0001:15848400` →
`0x1000 + 15848400 = 0xF1E3D0` (known-good RVA). vftables/RTTI `??_7`/`??_R0`
are in `.rdata`/`.data`, so use the right base — a `.text` formula on a vftable
gives garbage.

## Verified findings (build 2.0.58485, live-confirmed where noted)

### carbonium read path (live-confirmed #363/#365/#370)

```
PlayerService vftable RVA 0x2E8E910   (instance[0] == base + this)
  PlayerService + 0x8  = World*
  GetPlayerAccount(World*, playerId=0)  = RVA 0xC60050  -> ResourceAccount*
  ResourceAccount + 0x8  = sorted array, +0x10 = count; entries 16 B =
    { uint32 StringHash, int64 ResourceValue }  (fixed-point x10^6)
  carbonium StringHash = 0x659cc791
  ResourceAccount + 0x20 = UnorderedMap<StringHash, float max>  (carbonium_max)
    lookup RVA 0x28AC00(container, &out, &hash); node = out[0]; float max @ node+0xC
    scale global RVA 0x4794210 (runtime 1e6)
```

### LuaGraphNode (`dom_mananger` is one; live-confirmed #376)

```
class chain: dom_mananger -> event_manager -> LuaGraphNode
  (dom_manager.lua / event_manager.lua: "class 'X' ( base )"; __init does
   LuaGraphNode.__init(self, self) -> the Ctor-arg is the Lua self table)
LuaGraphNode vftable RVA 0x2F46D70
  LuaGraphNode + 0x20  = luabind::object { lua_State* @+0, int registry-ref @+8 }
                         = the Lua self table  (Ctor 0x1B33630 -> object-copy 0x1DA9F10)
  +0xF0/F1 = finished/suspended flags; +0xB8/+0xC0 = child-node vector
  LuaGraphNodeSelector vftable RVA 0x2F46DB8 (subclass)
```

### lua\_\* C API (Lua 5.1 fork, statically linked, public symbols) — live-working

```
lua_rawgeti   0x290CFA0   (void L, int idx, int n)
lua_getfield  0x290C550   (void L, int idx, const char* k)
lua_tonumber  0x290D790   (double)
lua_tointeger 0x290D670   (long long)
lua_settop    0x290D4D0   (void L, int idx)
lua_gettop    0x290C710   (int)
lua_pushvalue 0x290CF20
LUA_REGISTRYINDEX = -10000 (0xFFFFD8F0); LUA_GLOBALSINDEX = -10002
```

Reading a Lua table field (from a `luabind::object` {L, ref}):
`lua_rawgeti(L, -10000, ref)` → push table; `lua_getfield(L, tbl, name)` →
push field; `lua_tonumber/lua_tointeger(L, idx)`; `lua_settop` to unwind.
Use **absolute** stack indices (`tbl = lua_gettop(L)` after the rawgeti).

### lua_State\* via World — RACY from pipe thread, do NOT use (deprecated #376)

```
World::GetSystem<LuaSystem>() = RVA 0x194EDA0  (this=World*) -> LuaSystem*
LuaSystem + 0x200 = Exor::Lua* ; Lua + 0x10 = lua_State*
```

⚠️ Only safe on the **game thread**. Calling it from the pipe thread during
boot page-faults in `World::GetSystem(TypeHash)` (the World's system map is
still being built). **Applied resolver instead:** scan memory for the
`LuaGraphNode` vftable (`base + 0x2F46D70`) and read `+0x20` → `lua_State*`
(pure read-only `VirtualQuery` + `safe_read_u64`, no World/system-map access).

### StateMachine (DOM `spawner`; `spawner:GetCurrentState()` name)

```
StateMachine + 0x50 = current state index (int, -1 = none)
StateMachine + 0x8  = hashmap (index -> State*), lookup RVA 0x28AC00
  node = out[0]; State* @ node+0x10; state NAME UtfString @ state+0x18
StateMachine::GetCurrentStateName() RVA 0x1DAEB50 (returns UtfString by out-ptr)
```

`dom_mananger` state-dependent countdown (all mutually exclusive, live):
`cooldownTimer` (cooldown_after_spawn) / `idleTimer` (idle) /
`waitForSpawnTimer` (prepare_spawn); `time_to_next` = max of the three, ceil.
`sleep` (player dead) has no countdown. `wave` = `currentDifficultyLevel` (1..9).

### Full state egress (LIVE #376 — mod hook + C cache)

Thread-safe architecture: the Lua mod (`client-mod/lua/rbbattle_autoexec.lua` →
`PatchDomCapture`) wraps `dom_mananger:Update` on the **game thread**, builds a
JSON snapshot (`BuildStateJson`), and calls the C-registered
`_G.rbbridge_capture_state(json)` (registered once via the memory-read
`lua_State*` resolver + `lua_pushcclosure`/`lua_setfield` into
`LUA_GLOBALSINDEX`). The DLL caches the string under a spinlock; the pipe thread
only reads the cache. `get_state` returns
`{ok, carbonium, carbonium_max, resources[], state{...}}`.

Key `state` fields (raw game values — interface layer only, no re-semantization):

| field                                | source / meaning                                                                                                                                               |
| ------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `wave`                               | `dom_mananger.currentDifficultyLevel` (1..9)                                                                                                                   |
| `dom_state`                          | `spawner:GetCurrentState()` (`wait`/`idle`/`prepare_spawn`/`cooldown_after_spawn`/`sleep`)                                                                     |
| `time_to_next`                       | `DomTimeToNext` = state-dependent timer (max of cooldown/idle/prepare; `wait` = fixed 5s via `GetDurationLimit-GetDuration`)                                   |
| `time_to_next_difficulty`            | remaining of `difficultyIncrease` state (fixed 200s during `wait`)                                                                                             |
| `difficulty_state`                   | `difficultyIncrease:GetCurrentState()`                                                                                                                         |
| `players`/`commenced`/`mode`/`round` | mod-local round/match state (`RBB`)                                                                                                                            |
| `hq_hp`/`hq_dead`                    | `RBB.hq`                                                                                                                                                       |
| service getters                      | `CampaignService`/`DifficultyService`/`MissionService` (mission name/biome/warmup/mission_duration/infinite/wave_strength/waves_disabled/creatures_difficulty) |

Known gap: the HUD "next wave in X" (`MissionService:ActivateMissionFlow`,
`time_max`) is NOT in this snapshot yet — `time_to_next` is the DOM spawner
timer, not the HUD mission-flow countdown.

### Thread model (CORRECTED #378 — read before any Lua work)

The dedicated server runs game logic across a **worker-thread pool**
(`Exor::TaskWorldExecutor::SubmitSystemTasks`). The Lua VM runs on ONE thread
(the "Lua/main thread"). These are DIFFERENT threads.

- `ConsoleService::Update(float)` (`0x1C1FBA0`) → **worker thread**.
- `LuaGraphNode::Update(float)` (`0x1BAA140`) → **worker thread**.
- ⇒ **any `lua_*` call inside a C++ vftable detour on those functions is UNSAFE**
  and crashes (confirmed #378: 0x30/0x110 NULL-deref + garbage-pointer page
  faults during boot, no player connected).

The ONLY safe place to touch the Lua stack is the **Lua/main thread**. The only
reliable trigger there is a **Lua hook**: the PLAYERMOD wraps
`dom_mananger:Update` (Lua method → Lua/main thread) and calls a DLL-registered
C function. That is the working `a0bdbf3` architecture — use it for Lua reads,
NOT a C++ detour.

### WRITE path (#376 — native + exec commands)

`ConsoleService::ExecuteCommand` dispatches the command handler **inline on the
caller's thread** (disasm-verified) — a Lua command on the pipe thread crashes
the server (same class as the read crash).

RVAs (build 2.0.58485):

| symbol                                        | RVA         | meaning                                                                             |
| --------------------------------------------- | ----------- | ----------------------------------------------------------------------------------- |
| `ConsoleService::ExecuteCommand(char const*)` | `0x1C0BEF0` | dispatches inline (NOT thread-safe)                                                 |
| `ConsoleService::Update(float)`               | `0x1C1FBA0` | **worker-thread** update (TaskWorldExecutor); NOT safe for lua\_\*                  |
| `LuaGraphNode::SetSuspended(bool)`            | `0x1BA6CB0` | pure C++ flag write (`[this+0xF1]`); safe from any thread                           |
| `MissionService::FinishCurrentMission(int)`   | `0xF9A190`  | pure C++ (needs resolved MissionService + World)                                    |
| `LuaGraphNode::Update(float)`                 | `0x1BAA140` | **worker-thread** update; `[this+0xF1]` suspend check, returns early when suspended |

Architecture (native WRITE is thread-agnostic, Lua is NOT):

- `dispatch_exec` (pipe thread) writes the command into a spinlock buffer
  (`g_pending_cmd`), never calls `ExecuteCommand`.
- `install_update_hook()` scans the `ConsoleService` vtable for
  `base + 0x1C1FBA0` and patches that slot to `detour_console_update`
  (VirtualProtect).
- Native WRITE (`pause_dom`/`resume_dom` via `SetSuspended`, `end_game` via
  `FinishCurrentMission`) is pure C++ — safe from any thread once
  `g_dom_instance`/`g_mission_service` are resolved.
- Lua READS and Lua-based `ExecuteCommand` must run on the **Lua/main thread**
  via a Lua hook (see "Full state egress" above), NOT via these detours.

## Live test workflow (planet)

1. **Build** (mingw): `x86_64-w64-mingw32-gcc -O2 -Wall -Wextra -Wl,--no-insert-timestamp -shared -o rbbridge.dll rbbridge.c`
   (or `bash scripts/build_rbbridge_tools.sh /tmp/rbtools-build`).
2. **Stage + restart:** `cp rbbridge.dll /opt/rbmods/rbtools/dev/rbbridge.dll &&
docker restart riftbreaker-dedicated` (per-env; prod: `/opt/rbmods/rbtools/prod/rbbridge.dll`).
   Injection runs at boot (~2-3 min). **Never** the top-level `/opt/rbmods/rbtools/rbbridge.dll` —
   that stale path was removed (per-env isolation, #409/#601).
3. **Readiness:** poll `curl -s http://127.0.0.1:9001/health` then
   `curl -s -X POST http://127.0.0.1:9001/get_state -d '{}'` until `ok:true`
   (boot takes ~2-3 min; `no_account` = world still loading).
4. **Logging flags:** `POST /exec {"command":"verbose_dom_manager 1"}` → DOM
   state transitions (`OnEnter*`/`OnExit*`) land in `exor_logs.txt`
   (`docker logs riftbreaker-dedicated`). `debug_dom_manager 1` only renders
   on-screen (`LogService:DebugText`), NOT the log — don't rely on it headless.
5. **Time series:** `tools/re/dom_sampler.sh` on planet (2 s poll of
   `get_state`, appends `<epoch> <json>` to `/tmp/dom_wave_sample.log`).
   Run with `nohup /tmp/dom_sampler.sh /tmp/dom_wave_sample.log 2 &`.
   Correlate against player-reported timestamps (wave timer visible / wave
   spawns) to confirm `wave`/`time_to_next` match the HUD.
6. **Commands:** `debug_dom_manager_spawn_wave_level N` spawns a wave (does NOT
   bump `currentDifficultyLevel`); difficulty increments on its own timer
   (`OnExitDifficultyIncrease`). `debug_dom_pause`/`debug_dom_resume` freeze
   the DOM (`SetSuspended`) — the HUD mission-flow timer is NOT frozen by these.

## Pitfalls

- **Editing `.c`/`.html` via edit_file/write_file can auto-format** (clang-format
  on `.c`, prettier on `.html`) IF the Zed language servers are on. If a diff
  shows unexpected reflow, turn OFF the Zed language servers, then
  `git checkout -- <file>` and re-apply via a Python `str.replace` script that
  does `io.open(path,"w",newline="\n").write(...)`. See `io-re-session-handoff.md`.
- `_G.dom_mananger` is the **class** (method table), not the instance — its
  fields are `nil`. The instance comes from method dispatch (`self`) or, from
  C++, via the vftable scan + `LuaGraphNode+0x20` luabind object.
- HUD "wave incoming" timer is a separate `MissionService:ActivateMissionFlow`
  (`time_max`), NOT the DOM timer — `SetSuspended` on `dom_mananger` does not
  freeze it. Read the DOM fields, not the HUD.
- Wine: `GetModuleHandleA("riftbreaker_dll_win_release.dll")` can return NULL
  (GLE=126) under Wine — module resolution must not depend on it alone.
- `resolve_console_service` / vftable-scan pattern: a QWORD == vftable address
  in committed readable memory is (essentially) a real instance; false positives
  are negligible. For Lua reads, still skip sentinel refs (`0xFFFFFFFF`/-2) and
  NULL `lua_State*`.
- **CONFIRMED CRASH (Issue #376):** reading Lua fields from any non-Lua thread
  (pipe thread OR worker thread) is NOT thread-safe. It corrupts the Lua stack
  and crashes the DedicatedServer (NULL+0x30/0x110 deref or a garbage-pointer
  page fault). The values can read _correctly_ for many samples first, so the
  bug is concurrency, not the offsets.
  **Key correction (#378):** `ConsoleService::Update` and `LuaGraphNode::Update`
  run on a WORKER thread (`TaskWorldExecutor`), NOT the game/main thread — so a
  C++ vftable detour on either is ALSO unsafe for `lua_*`. The only safe trigger
  is the Lua/main thread: PLAYERMOD wraps `dom_mananger:Update` and calls a
  DLL-registered C function. Do NOT do raw `lua_*` stack ops off the Lua thread.

## References

- `docs/research/dedicated-io-re-findings.md`, `io-write-poc.md`,
  `io-re-session-handoff.md`, `pdb-symbol-validation.md`
- `tools/re/disasm.py`, `tools/re/carbonium_case.py`
- Lua source (planet/lan): `/home/momo/rb-game/lua-src/lua/missions/v2/dom_manager.lua`
  and `event_manager.lua` (class chain + state machine + timer fields).
