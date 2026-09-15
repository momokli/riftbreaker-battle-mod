#!/usr/bin/env python3
"""Issue #376 v4: full-state egress via game-thread capture.

- C side: _G.rbbridge_capture_state(json) (string, spinlock-guarded buffer),
  get_state echoes it as "state":{...}.
- Mod side: full snapshot (DOM + event/objective + services + mod) built as a
  JSON object on the game thread.
Both files edited via str.replace (KEIN Formatter).
"""

import io
import sys

# ============================================================================
# C side
# ============================================================================

C_PATH = "bausteine/rbbridge/dll/rbbridge.c"

C_HELPER = r"""/* ====================================================================== */
/* DOM/Voll-State-Egress (Issue #376): thread-sichere Variante via Game-       */
/* Thread-Hook.                                                               */
/*                                                                          */
/* Erkenntnis aus dem ersten Entwurf: Lua NICHT vom pipe thread anfassen -   */
/* das crasht den Server (LUA CRASH "attempt to call a nil value", winedbg). */
/* Deshalb: der Mod (rbbattle_autoexec.lua) wrappt dom_mananger:Update auf   */
/* dem GAME thread, baut dort den kompletten State als JSON-String und ruft  */
/* die hier registrierte C-Funktion _G.rbbridge_capture_state(json) auf.     */
/* Diese cached den String (spinlock-guarded); dispatch_get_state (pipe      */
/* thread) liest nur den Cache - kein Lua-Zugriff mehr vom pipe thread.      */
/*                                                                          */
/* RE (Build 2.0.58485, verifiziert, s. .agents/skills/riftbreaker-re):      */
/*   World::GetSystem<LuaSystem>()   RVA 0x194EDA0  (this=World*)            */
/*   LuaSystem + 0x200 = Exor::Lua* ; Lua + 0x10 = lua_State*                 */
/*   lua_pushcclosure 0x290CBE0   lua_setfield  0x290D260                      */
/*   lua_tolstring    0x290D6D0   LUA_GLOBALSINDEX = -10002 (0xFFFFD8EE)      */
/* ====================================================================== */

typedef int (__fastcall *rbbridge_lua_cfunction)(void *L);
typedef void (__fastcall *rbbridge_lua_pushcclosure_fn)(void *L,
                                                        rbbridge_lua_cfunction fn,
                                                        int n);
typedef void (__fastcall *rbbridge_lua_setfield_fn)(void *L, int idx,
                                                    const char *k);
typedef const char *(__fastcall *rbbridge_lua_tolstring_fn)(void *L, int idx,
                                                            size_t *len);

#define RBBRIDGE_LUA_PUSHCCLOSURE_RVA 0x290CBE0u
#define RBBRIDGE_LUA_SETFIELD_RVA     0x290D260u
#define RBBRIDGE_LUA_TOLSTRING_RVA    0x290D6D0u
#define RBBRIDGE_LUA_GLOBALSINDEX     (-10002)

/* DOM-Snapshot (game thread schreibt, pipe thread liest). Spinlock schuetzt
 * den String-Puffer gegen zerrissene Reads. */
static const unsigned char *g_dom_base = NULL;
static char g_dom_state_buf[RESP_BUF_SIZE];
static volatile LONG g_dom_state_lock = 0;
static volatile LONG g_dom_capture_registered = 0;

/* Vom Mod (game thread) pro Frame aufgerufen: rbbridge_capture_state(json). */
static int rbbridge_capture_state(void *L)
{
    if (!g_dom_base)
        return 0;
    rbbridge_lua_tolstring_fn tolstring =
        (rbbridge_lua_tolstring_fn)(uintptr_t)(g_dom_base +
                                               RBBRIDGE_LUA_TOLSTRING_RVA);
    size_t len = 0;
    const char *s = tolstring(L, 1, &len);
    if (!s || len == 0)
        return 0;
    if (len > RESP_BUF_SIZE - 1)
        len = RESP_BUF_SIZE - 1;

    while (InterlockedExchange(&g_dom_state_lock, 1) != 0)
        ;
    memcpy(g_dom_state_buf, s, len);
    g_dom_state_buf[len] = '\0';
    InterlockedExchange(&g_dom_state_lock, 0);
    return 0;
}

/* lua_State* aus World -> LuaSystem -> Lua (RE #376). */
static void *resolve_lua_state(const unsigned char *base, void *world)
{
    if (!world)
        return NULL;
    void *(*get_luasystem)(void *) =
        (void *(*)(void *))(uintptr_t)(base + 0x194EDA0);
    void *luasys = get_luasystem(world);
    if (!luasys)
        return NULL;
    uint64_t lua_obj = 0;
    if (!safe_read_u64((const unsigned char *)luasys + 0x200, &lua_obj) ||
        !lua_obj)
        return NULL;
    uint64_t L = 0;
    if (!safe_read_u64((const unsigned char *)(uintptr_t)lua_obj + 0x10, &L) ||
        !L)
        return NULL;
    return (void *)(uintptr_t)L;
}

/* Registriert _G.rbbridge_capture_state einmalig (lazy, retry bis ok). */
static void register_dom_capture(const unsigned char *base, void *world)
{
    if (g_dom_capture_registered)
        return;
    void *L = resolve_lua_state(base, world);
    if (!L)
        return;
    rbbridge_lua_pushcclosure_fn pushcclosure =
        (rbbridge_lua_pushcclosure_fn)(uintptr_t)(base +
                                                  RBBRIDGE_LUA_PUSHCCLOSURE_RVA);
    rbbridge_lua_setfield_fn setfield =
        (rbbridge_lua_setfield_fn)(uintptr_t)(base +
                                              RBBRIDGE_LUA_SETFIELD_RVA);
    g_dom_base = base;
    pushcclosure(L, rbbridge_capture_state, 0);
    setfield(L, RBBRIDGE_LUA_GLOBALSINDEX, "rbbridge_capture_state");
    g_dom_capture_registered = 1;
    dbg("register_dom_capture: _G.rbbridge_capture_state registriert (L=%p)", L);
}

"""

C_ANCHOR_FN = "static void dispatch_get_state(HANDLE hPipe)\n"

C_OLD_WORLD = r"""    void *(*gpa)(void *, unsigned int) =
        (void *(*)(void *, unsigned int))(uintptr_t)(base + 0xC60050);
    void *account = gpa((void *)(uintptr_t)world, 0);"""

C_NEW_WORLD = r"""    /* #376: DOM-Capture (game thread -> Cache) einmalig registrieren. */
    register_dom_capture(base, (void *)(uintptr_t)world);

    void *(*gpa)(void *, unsigned int) =
        (void *(*)(void *, unsigned int))(uintptr_t)(base + 0xC60050);
    void *account = gpa((void *)(uintptr_t)world, 0);"""

C_OLD_TAIL = r"""    int64_t carbonium_max = read_resource_max(base, account, 0x659cc791);

    send_line(hPipe,
              "{\"event\":\"get_state_result\",\"ok\":true,"
              "\"carbonium\":%llu,\"carbonium_max\":%lld,\"resources\":%s}",
              (unsigned long long)carbonium, (long long)carbonium_max,
              resources);
}"""

C_NEW_TAIL = r"""    int64_t carbonium_max = read_resource_max(base, account, 0x659cc791);

    /* DOM/Voll-State aus dem Cache (game thread schreibt; spinlock-guarded). */
    char state_json[RESP_BUF_SIZE];
    while (InterlockedExchange(&g_dom_state_lock, 1) != 0)
        ;
    memcpy(state_json, g_dom_state_buf, sizeof(state_json));
    InterlockedExchange(&g_dom_state_lock, 0);
    state_json[sizeof(state_json) - 1] = '\0';
    if (state_json[0] == '\0')
        strcpy(state_json, "null");

    send_line(hPipe,
              "{\"event\":\"get_state_result\",\"ok\":true,"
              "\"carbonium\":%llu,\"carbonium_max\":%lld,\"resources\":%s,"
              "\"state\":%s}",
              (unsigned long long)carbonium, (long long)carbonium_max,
              resources, state_json);
}"""


def apply_c():
    with io.open(C_PATH, "r", encoding="utf-8") as f:
        src = f.read()
    if "rbbridge_capture_state" in src:
        print("C: rbbridge_capture_state bereits vorhanden - skip.")
        return 0
    for name, a in (("fn", C_ANCHOR_FN), ("world", C_OLD_WORLD), ("tail", C_OLD_TAIL)):
        if a not in src:
            print("C: Anker '%s' fehlt." % name, file=sys.stderr)
            return 1
    src = src.replace(C_ANCHOR_FN, C_HELPER + C_ANCHOR_FN, 1)
    src = src.replace(C_OLD_WORLD, C_NEW_WORLD, 1)
    src = src.replace(C_OLD_TAIL, C_NEW_TAIL, 1)
    with io.open(C_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(src)
    print("C: OK.")
    return 0


# ============================================================================
# Mod side
# ============================================================================

M_PATH = "mod/lua/rbbattle_autoexec.lua"

M_INSERT_AFTER = """    RBB.domTimerPatched = true
    return true
end

"""

M_BLOCK = """-- ---------------------------------------------------------------------------
-- #376: Voll-State-Egress an die C++-Bridge (game thread). Der Mod baut hier
-- pro Frame einen JSON-Snapshot und ruft _G.rbbridge_capture_state(json) auf.
-- Die DLL darf Lua NICHT vom pipe thread anfassen (crasht, s. SKILL
-- riftbreaker-re); dieser Hook laeuft deshalb auf dem game thread.
-- ---------------------------------------------------------------------------
RBB.domCapturePatched = false
RBB.domCaptureOrig = nil

-- Minimal-JSON-Escape fuer Strings.
local function rbJsonStr(s)
    if type(s) ~= "string" then return "\\"\\"" end
    return '"' .. tostring(s):gsub('[%z\\1-\\31\\\\"]', function(c)
        if c == '\\\\' then return '\\\\\\\\' end
        if c == '"' then return '\\\\"' end
        if c == '\\n' then return '\\\\n' end
        if c == '\\r' then return '\\\\r' end
        if c == '\\t' then return '\\\\t' end
        return string.format('\\\\u%04x', c:byte())
    end) .. '"'
end

-- Restzeit eines State-Machine-States (GetDurationLimit - GetDuration).
local function rbStateRemaining(sm, name)
    local ok, s = pcall(sm.GetState, sm, name)
    if not ok or type(s) ~= "userdata" and type(s) ~= "table" then return nil end
    local okL, lim = pcall(s.GetDurationLimit, s)
    local okD, dur = pcall(s.GetDuration, s)
    if okL and okD and type(lim) == "number" and type(dur) == "number" then
        return lim - dur
    end
    return nil
end

-- State-abhaengiger Countdown (Spiegel von dom_mananger:Update-Debuglogik).
local function DomTimeToNext(self)
    local spawner = self.spawner
    if type(spawner) ~= "userdata" and type(spawner) ~= "table" then return 0 end
    local okState, state = pcall(spawner.GetCurrentState, spawner)
    if not okState or type(state) ~= "string" or state == "" then return 0 end
    if state == "cooldown_after_spawn" then return tonumber(self.cooldownTimer) or 0
    elseif state == "prepare_spawn" then return tonumber(self.waitForSpawnTimer) or 0
    elseif state == "idle" then return tonumber(self.idleTimer) or 0
    elseif state == "sleep" then return tonumber(self.sleepSafeTimer) or 0
    elseif state == "wait" then return rbStateRemaining(spawner, "wait") or 0
    end
    return 0
end

-- Baut den kompletten Snapshot als JSON-Objekt-String.
local function BuildStateJson(self)
    local f = {}
    local function numf(k, v) if type(v) == "number" then f[#f + 1] = '"' .. k .. '":' .. tostring(v) end end
    local function boolf(k, v) if v == true then f[#f + 1] = '"' .. k .. '":true' elseif v == false then f[#f + 1] = '"' .. k .. '":false' end end
    local function strf(k, v) if type(v) == "string" then f[#f + 1] = '"' .. k .. '":' .. rbJsonStr(v) end end
    local function svc(k, fn)
        local ok, v = pcall(fn)
        if ok then
            local t = type(v)
            if t == "number" then numf(k, v)
            elseif t == "boolean" then boolf(k, v)
            elseif t == "string" then strf(k, v) end
        end
    end

    -- DOM Schwierigkeit
    numf("wave", self.currentDifficultyLevel)
    numf("max_wave", self.maxDifficultyLevel)
    numf("frozen_wave", self.freezedDifficultyLevel)

    -- Spawner (Wellen-Zyklus)
    local spawnerState = ""
    pcall(function() spawnerState = self.spawner:GetCurrentState() or "" end)
    strf("dom_state", spawnerState)
    numf("time_to_next", math.ceil(DomTimeToNext(self)))
    numf("cooldown_timer", self.cooldownTimer)
    numf("idle_timer", self.idleTimer)
    numf("prepare_timer", self.waitForSpawnTimer)
    numf("sleep_timer", self.sleepSafeTimer)

    -- HQ
    local hqState = ""
    pcall(function() hqState = self.upgradeHQ:GetCurrentState() or "" end)
    strf("hq_state", hqState)
    numf("hq_attack_timer", self.hqAttackSafeTimer)

    -- Schwierigkeits-Progression
    local diffState = ""
    pcall(function() diffState = self.difficultyIncrease:GetCurrentState() or "" end)
    strf("difficulty_state", diffState)
    numf("time_to_next_difficulty", rbStateRemaining(self.difficultyIncrease, "difficulty_increase"))

    -- Flags
    boolf("pause_attacks", self.pauseAttacks)
    boolf("cancel_attack", self.cancelTheAttack)
    boolf("spawn_boss", self.spawnBoss)
    numf("extra_attacks", self.extraAttacks)

    -- Zaehler
    numf("spawned_attacks", (type(self.spawnedAttacks) == "table") and #self.spawnedAttacks or 0)
    numf("prepared_attacks", (type(self.preparedAttacks) == "table") and #self.preparedAttacks or 0)

    -- Event/Objective (event_manager)
    numf("event_level", self.currentEventLevel)
    numf("event_timer", self.eventManagerTimer)
    numf("active_objectives", (type(self.objectiveActiveList) == "table") and #self.objectiveActiveList or 0)
    if type(self.objectiveLastSpawnTime) == "number"
       and type(self.objectiveCurrentTimeBetweenNext) == "number"
       and type(self.eventManagerTimer) == "number" then
        numf("time_to_next_objective",
             self.objectiveLastSpawnTime + self.objectiveCurrentTimeBetweenNext - self.eventManagerTimer)
    end

    -- Services
    svc("players", function() return self:GetPlayersCounter() end)
    svc("creatures_difficulty", function() return CampaignService:GetCreaturesBaseDifficulty() end)
    svc("difficulty_name", function() return DifficultyService:GetCurrentDifficultyName() end)
    svc("wave_strength", function() return DifficultyService:GetWaveStrength() end)
    svc("waves_disabled", function() return DifficultyService:AreWavesDisabled() end)
    svc("mission_name", function() return MissionService:GetCurrentMissionName() end)
    svc("biome", function() return MissionService:GetCurrentBiomeName() end)
    svc("mission_duration", function() return DifficultyService:GetMissionDuration() end)
    svc("warmup_duration", function() return DifficultyService:GetWarmupDuration() end)
    svc("mission_infinite", function() return DifficultyService:IsMissionInfinite() end)

    -- Mod-eigener State
    numf("round", RBB.round)
    strf("mode", RBB.mode)
    boolf("commenced", RBB.commenced)
    if RBB.hq then
        numf("hq_hp", RBB.hq.hp)
        boolf("hq_dead", RBB.hq.dead)
    end

    return "{" .. table.concat(f, ",") .. "}"
end

local function PatchDomCapture()
    if RBB.domCapturePatched then
        return RBB.domCaptureOrig ~= nil
    end

    local dom = nil
    if type(_G) == "table" then
        dom = rawget(_G, "dom_mananger")
    end
    local dType = type(dom)
    if dType ~= "table" and dType ~= "userdata" then
        return false -- Klasse (noch) nicht geladen; naechster Versuch spaeter
    end

    local orig = dom.Update
    if type(orig) ~= "function" then
        Log("event=dom_capture patch status=skip reason=no_api")
        RBB.domCapturePatched = true
        return false
    end

    if RBB.domCaptureOrig == nil then
        RBB.domCaptureOrig = orig
        dom.Update = function(self, dt)
            local cap = rawget(_G, "rbbridge_capture_state")
            if type(cap) == "function" then
                pcall(cap, BuildStateJson(self))
            end
            return RBB.domCaptureOrig(self, dt)
        end
        Log("event=dom_capture patch status=ok")
    end

    RBB.domCapturePatched = true
    return true
end

"""

M_EDITS = [
    (
        "    AnnounceSetupPhase() -- #158: Start-Announce (Setup-Phase, HQ noch offen)\n    PatchDomTimer()\n",
        "    AnnounceSetupPhase() -- #158: Start-Announce (Setup-Phase, HQ noch offen)\n    PatchDomTimer()\n    PatchDomCapture() -- #376: Voll-State an die Bridge\n",
    ),
    (
        "    PatchDomTimer() -- weiterer Retry-Zeitpunkt (billig, idempotent)\n    PatchWaveStartHook() -- weiterer Retry-Zeitpunkt (#42)\n",
        "    PatchDomTimer() -- weiterer Retry-Zeitpunkt (billig, idempotent)\n    PatchDomCapture() -- #376: weiterer Retry-Zeitpunkt\n    PatchWaveStartHook() -- weiterer Retry-Zeitpunkt (#42)\n",
    ),
    (
        "PatchDomTimer()\nif not evtApiOk then\n",
        "PatchDomTimer()\nPatchDomCapture() -- #376: erster Versuch direkt beim Laden\nif not evtApiOk then\n",
    ),
]


def apply_mod():
    with io.open(M_PATH, "r", encoding="utf-8") as f:
        src = f.read()
    if "PatchDomCapture" in src:
        print("Mod: PatchDomCapture bereits vorhanden - skip.")
        return 0
    if M_INSERT_AFTER not in src:
        print("Mod: Insert-Anker fehlt.", file=sys.stderr)
        return 1
    src = src.replace(M_INSERT_AFTER, M_INSERT_AFTER + M_BLOCK, 1)
    for a, r in M_EDITS:
        if a not in src:
            print("Mod: Anker fehlt: %r" % a, file=sys.stderr)
            return 1
        src = src.replace(a, r, 1)
    with io.open(M_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(src)
    print("Mod: OK.")
    return 0


def main():
    rc = apply_c()
    if rc == 0:
        rc = apply_mod()
    sys.exit(rc)


if __name__ == "__main__":
    main()
