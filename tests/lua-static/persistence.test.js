'use strict';

// Statische Verifikation des Lua-Mods (rbbattle_autoexec.lua) fuer Issue #65
// (RE-Verifikation: Persistenz des Spar-Pools ueber Session-/Map-Reload).
//
// AC1 (In-Game-Test) ist hier NICHT abbildbar — es gibt keinen Spiel-Zugriff.
// Stattdessen wird der codebare Teil statisch verifiziert: (a) der defensive
// Fallback (#65, AC3) schreibt den Spar-Pool an der Rundengrenze (Wellenstart)
// ZUSAETZLICH in die Global-DB (`event=economy_checkpoint`) und (b) beim Boot
// liest `EconomyLoad` den Pool aus derselben DB zurueck (`status=resume`) — das
// bildet einen Map-/Session-Reload ab, indem der Mod ein zweites Mal in eine
// frische Lua-VM mit vorbefuellter DB geladen wird. Kein Balancing/Preis-Tuning.

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const luaparse = require('luaparse');
const { lua, lauxlib, lualib, to_luastring, to_jsstring } = require('fengari');

const MOD_PATH = path.join(__dirname, '..', '..', 'mod', 'lua', 'rbbattle_autoexec.lua');
const modSource = fs.readFileSync(MOD_PATH, 'utf8');

// Stub-Services (Muster send-currency.test.js). `_G.__db` ist die in-memory
// Global-Database; ueber `_G.__seed` kann sie vor dem Mod-Load vorbelegt
// werden (simuliert eine ueber den Reload hinweg persistierte DB).
const STUBS = `
-- ==== Stub-Services (fengari, Muster send-currency.test.js) ====
_G.__logs = {}
_G.__handlers = {}
_G.__commands = {}
_G.__db = {}
_G.__waveStarts = 0
_G.__popup = nil
if type(_G.__seed) == "table" then
    for k, v in pairs(_G.__seed) do _G.__db[k] = v end
end
INVALID_ID = -1

LogService = {
    Log = function(self, msg) _G.__logs[#_G.__logs + 1] = msg end,
}
ConsoleService = {
    Write = function(self, msg) end,
    RegisterCommand = function(self, name, fn) _G.__commands[name] = fn end,
}
FindService = {
    FindEntitiesByType = function(self, t) return { 100 } end,
    FindEntitiesByGroup = function(self, g) return { 100 } end,
    FindPlayerSpawnPoints = function(self) return {} end,
}
MapGenerator = { GetInitialSpawnPoint = function(self) return nil end }
ResourceManager = { GetBlueprint = function(self, bp) return true end }
EnvironmentService = { GetTerrainHeight = function(self, pos) return 0 end }
EntityService = {
    GetName = function(self, e) return "" end,
    GetPosition = function(self, e) return { x = 0, y = 0, z = 0 } end,
    SpawnEntity = function(self, ...) return 1 end,
}
PlayerService = {
    GetPlayerControlledEnt = function(self, i) return 1 end,
    GetOrCreateGlobalDatabase = function(self, name)
        local db = {}
        db.HasInt = function(s, k) return _G.__db[k] ~= nil end
        db.GetIntOrDefault = function(s, k, d)
            local v = _G.__db[k]
            if v ~= nil then return v end
            return d
        end
        db.SetInt = function(s, k, v) _G.__db[k] = v end
        db.RemoveKey = function(s, k) _G.__db[k] = nil end
        return db
    end,
}
DifficultyService = { GetCurrentDifficultyName = function(self) return "hard" end }
CampaignService = { GetCreaturesBaseDifficulty = function(self) return 5 end }
GuiService = {
    OpenPopup = function(self, ent, template, text) return true end,
}

-- DOM-Klasse (Wellenstart-Hook #42/#25) + Timer-API (#23).
dom_mananger = {
    OnEnterSpawn = function(self, state)
        _G.__waveStarts = _G.__waveStarts + 1
    end,
    GetPrepareSpawnTime = function(self) return 300 end,
}

function RegisterGlobalEventHandler(name, fn)
    _G.__handlers[name] = fn
end
`;

// Phase 1: frischer Run — Farm -> Convert -> Wellenstart -> Checkpoint in die DB.
const PHASE1_ASSERTIONS = `
local failures = 0
local function check(cond, msg)
    if cond then
        print("PASS: " .. msg)
    else
        print("FAIL: " .. msg)
        failures = failures + 1
    end
end

local function log_has(sub)
    for _, m in ipairs(_G.__logs) do
        if string.find(m, sub, 1, true) then return true end
    end
    return false
end

check(log_has("event=mod_load version=0.28.2"), "mod_load version=0.28.2")
check(log_has("econ_pool=0"), "frischer Run: mod_load econ_pool=0")

-- Farm 2000 carbonium -> Convert 1500 -> Pool 1500.
local farmC = {}
function farmC:GetResourceName() return "carbonium" end
function farmC:GetAmount() return 2000 end
_G.__handlers["ResourceObtainedEvent"](farmC)
_G.__commands["rb_convert"]({ "1500" })
check(log_has("event=convert resource=carbonium amount=1500 value=1500 pool=1500 status=ok irreversible=1"),
    "convert 1500 -> pool 1500")

-- #158 Setup-Phase: HQ platziert -> Commence (Waves starten).
_G.__handlers["PlayerInitializedEvent"](nil)

-- Rundengrenze (Wellenstart) -> defensiver Checkpoint in die Global-DB.
dom_mananger.OnEnterSpawn(nil, {})
check(log_has("event=round round=1 status=start mode=sp pool=1500 queue=0"),
    "round=1 status=start pool=1500")
check(log_has("event=economy_checkpoint round=1 pool=1500 farmed=2000 converted=1500 status=ok"),
    "economy_checkpoint an Rundengrenze (status=ok)")
check(_G.__db["pool"] == 1500, "Global-DB hat pool=1500 nach Checkpoint")
check(_G.__db["farmed"] == 2000, "Global-DB hat farmed=2000 nach Checkpoint")

print("FAILURES=" .. failures)
_G.__failures = failures
`;

// Phase 2: Reload — vorbefuellte DB (simuliert persistierte Global-DB) ->
// EconomyLoad resumes den Pool.
const PHASE2_ASSERTIONS = `
local failures = 0
local function check(cond, msg)
    if cond then
        print("PASS: " .. msg)
    else
        print("FAIL: " .. msg)
        failures = failures + 1
    end
end

local function log_has(sub)
    for _, m in ipairs(_G.__logs) do
        if string.find(m, sub, 1, true) then return true end
    end
    return false
end

check(log_has("event=economy_db status=resume pool=1500 farmed=2000 converted=1500"),
    "boot-load resume pool=1500 (Spar-Pool ueberlebt Reload)")
check(log_has("econ_pool=1500"), "mod_load zeigt econ_pool=1500 nach Reload")

print("FAILURES=" .. failures)
_G.__failures = failures
`;

function runLua(seed, assertions) {
    const L = lauxlib.luaL_newstate();
    lualib.luaL_openlibs(L);
    const seedChunk = seed
        ? '_G.__seed = { ' + Object.entries(seed).map(([k, v]) => k + ' = ' + v).join(', ') + ' }\n'
        : '';
    const code = seedChunk + STUBS + modSource + '\n' + assertions;
    const loadStatus = lauxlib.luaL_loadstring(L, to_luastring(code));
    if (loadStatus !== lua.LUA_OK) {
        const err = to_jsstring(lua.lua_tostring(L, -1));
        lua.lua_close(L);
        throw new Error('Lua-Loadfehler: ' + err);
    }
    const status = lua.lua_pcall(L, 0, 0, 0);
    if (status !== lua.LUA_OK) {
        const err = to_jsstring(lua.lua_tostring(L, -1));
        lua.lua_close(L);
        throw new Error('Lua-Laufzeitfehler: ' + err);
    }
    lua.lua_getglobal(L, to_luastring('__failures'));
    const failures = lua.lua_tonumber(L, -1) || 0;
    lua.lua_close(L);
    return failures;
}

test('Lua-Syntax (luaparse, Lua 5.1)', () => {
    assert.doesNotThrow(() => luaparse.parse(modSource),
        'mod/lua/rbbattle_autoexec.lua muss gültiges Lua 5.1 sein');
});

test('Issue #65: Spar-Pool defensiv an der Rundengrenze persistieren + beim Boot laden', () => {
    const failures1 = runLua(null, PHASE1_ASSERTIONS);
    assert.strictEqual(failures1, 0, `Phase 1 (Checkpoint): ${failures1} Assertion-Fehler`);

    const failures2 = runLua(
        { pool: 1500, farmed: 2000, converted: 1500, converts: 1 },
        PHASE2_ASSERTIONS
    );
    assert.strictEqual(failures2, 0, `Phase 2 (Reload/Resume): ${failures2} Assertion-Fehler`);
});
