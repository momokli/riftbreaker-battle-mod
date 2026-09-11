'use strict';

// Statische Verifikation des Lua-Mods (rbbattle_autoexec.lua) fuer Issue #217:
// im Autoexec-Environment ist `dom_mananger` ein **userdata**-Objekt (Spiel-
// Klasse), KEIN Lua-`table`. Die drei Hook-Patches (PatchDomTimer,
// PatchWaveStartHook, PatchSpawnWavesHook) pruefen seit dem Fix auf
// `table` ODER `userdata`; dieser Test baut `dom_mananger` als ECHTES
// fengari-userdata (JS `lua_newuserdata` + Metatable mit __index/__newindex)
// und weist nach, dass alle drei Load-Time-Hooks greifen und funktional
// wirken (Timer-Cap + `event=round`). Ohne den Fix war hier alles tot.
//
// Aufruf: `npm test` (= `node --test`) aus tests/lua-static/.

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const luaparse = require('luaparse');
const { lua, lauxlib, lualib, to_luastring, to_jsstring } = require('fengari');

const MOD_PATH = path.join(__dirname, '..', '..', 'mod', 'lua', 'rbbattle_autoexec.lua');
const modSource = fs.readFileSync(MOD_PATH, 'utf8');

// Stub-Services (fengari, Muster boost.test.js). ABER: `dom_mananger` wird hier
// NICHT als Lua-Table definiert — stattdessen Methoden/Daten in __dom_methods
// und die Metatable __dom_mt, damit der Harness daraus ein echtes userdata
// baut (Meta __index liest erst __dom_store (Hooks), dann __dom_methods).
// GetPrepareSpawnTime liefert bewusst 600 (> cap 480), damit die Kappung durch
// den Timer-Wrap sichtbar wird.
const STUBS = `
-- ==== Stub-Services (fengari, Muster boost.test.js) ====
_G.__logs = {}
_G.__handlers = {}
_G.__commands = {}
_G.__db = {}
_G.__waveStarts = 0
_G.__spawnedLevel = -1
_G.__spawnedAdd = nil
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
GuiService = { OpenPopup = function(self, ent, template, text) return true end }

-- dom_mananger-Backing-Store fuer das (vom Harness gebaute) userdata:
-- __dom_store nimmt die Hook-Wrapper auf, __dom_methods die Original-API.
_G.__dom_store = {}
_G.__dom_methods = {
    maxDifficultyLevel = 9,
    currentDifficultyLevel = 2,
    GetPrepareSpawnTime = function(self) return 600 end,
    OnEnterSpawn = function(self, state)
        _G.__waveStarts = _G.__waveStarts + 1
        self:SpawnWavesForDifficultyLevel(self.currentDifficultyLevel, true)
    end,
    SpawnWavesForDifficultyLevel = function(self, level, addToSpawned)
        _G.__spawnedLevel = level
        _G.__spawnedAdd = addToSpawned
    end,
}
_G.__dom_mt = {}
__dom_mt.__index = function(t, k)
    local v = __dom_store[k]
    if v ~= nil then return v end
    return __dom_methods[k]
end
__dom_mt.__newindex = function(t, k, v) __dom_store[k] = v end

function RegisterGlobalEventHandler(name, fn)
    _G.__handlers[name] = fn
end
`;

const ASSERTIONS = `
-- ==== Assertions (Issue #217: userdata dom_mananger) ====
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

-- 1. Sanity: dom_mananger ist userdata (kein Lua-table) — der Kern von #217.
check(type(dom_mananger) == "userdata", "dom_mananger ist userdata")

-- 2. Alle drei Load-Time-Hooks greifen trotz userdata -> status=ok.
check(log_has("event=dom_timer patch status=ok cap=480"), "dom_timer patch ok cap=480")
check(log_has("event=wave_hook patch status=ok"), "wave_hook patch ok")
check(log_has("event=boost patch status=ok"), "boost patch ok")

-- 3. Funktional: Timer-Wrap kappt die (userdata-)Methode 600 auf cap 480.
check(dom_mananger.GetPrepareSpawnTime(dom_mananger) == 480,
    "GetPrepareSpawnTime 600 -> cap 480")

-- 4. Commence (HQ via FindEntitiesByType erkannt) ueber PlayerInitializedEvent.
_G.__handlers["PlayerInitializedEvent"](nil)
check(log_has("event=commence status=ok"), "commence ok nach HQ-Erkennung")

-- 5. Naturwelle -> Wrapper laeuft: Original echt gefeuert + Runden-Zaehler.
dom_mananger.OnEnterSpawn(dom_mananger, {})
check(_G.__waveStarts == 1, "Original OnEnterSpawn lief genau einmal")
check(log_has("event=round round=1 status=start"), "event=round round=1 status=start")

-- 6. Chokepoint-Hook (Preset A strengthPct=100): Naturalwelle bleibt Level 2.
check(_G.__spawnedLevel == 2 and _G.__spawnedAdd == true,
    "Naturwelle Level 2 addToSpawned=true")

print("FAILURES=" .. failures)
_G.__failures = failures
`;

function newState() {
    const L = lauxlib.luaL_newstate();
    lualib.luaL_openlibs(L);
    return L;
}

function runChunk(L, code, label) {
    const loadStatus = lauxlib.luaL_loadstring(L, to_luastring(code));
    if (loadStatus !== lua.LUA_OK) {
        const err = to_jsstring(lua.lua_tostring(L, -1));
        lua.lua_close(L);
        throw new Error(`Lua-Loadfehler (${label}): ${err}`);
    }
    const status = lua.lua_pcall(L, 0, 0, 0);
    if (status !== lua.LUA_OK) {
        const err = to_jsstring(lua.lua_tostring(L, -1));
        lua.lua_close(L);
        throw new Error(`Lua-Laufzeitfehler (${label}): ${err}`);
    }
}

// Baut dom_mananger als ECHTES fengari-userdata mit Metatable __dom_mt.
function installUserdataDom(L) {
    lua.lua_newuserdata(L, 16);                            // userdata auf Stack
    lua.lua_getglobal(L, to_luastring('__dom_mt'));        // Metatable
    lua.lua_setmetatable(L, -2);                           // an userdata binden
    lua.lua_setglobal(L, to_luastring('dom_mananger'));    // als Global
}

function runLua() {
    const L = newState();
    runChunk(L, STUBS, 'STUBS');
    installUserdataDom(L);
    runChunk(L, modSource + '\n' + ASSERTIONS, 'mod+ASSERTIONS');
    lua.lua_getglobal(L, to_luastring('__failures'));
    const failures = lua.lua_tonumber(L, -1) || 0;
    lua.lua_close(L);
    return failures;
}

test('Lua-Syntax (luaparse, Lua 5.1)', () => {
    assert.doesNotThrow(() => luaparse.parse(modSource),
        'mod/lua/rbbattle_autoexec.lua muss gültiges Lua 5.1 sein');
});

test('Issue #217: userdata dom_mananger -> Wave-Hooks greifen (fengari)', () => {
    const failures = runLua();
    assert.strictEqual(failures, 0, `Assertion-Fehler im Lua-Harness: ${failures}`);
});
