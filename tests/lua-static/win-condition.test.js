'use strict';

// Statische Verifikation des Lua-Mods (rbbattle_autoexec.lua) ohne Spiel:
//   1) Syntax-Check via luaparse (Lua-5.1-Grammatik, wie CI-Pipeline),
//   2) Ausfuehrung in fengari-Lua-VM mit Stub-Services und Assertions auf die
//      [RBBATTLE]-Log-Zeilen (die Vertragsflaeche fuer Bridge/Server).
//
// Deckt Issue #28 (Win-Condition) ab: Leak-Erkennung (EnteredTriggerEvent),
// HQ-HP-Tracking, HQ-Tod (RespawnFailedEvent) -> Match-Ende. Reine Logik —
// nicht live-verifizierbare Teile (Trigger-Zone-Asset, HQ-Entity-Lookup,
// Sieg-Screen-UI) sind in mod/lua/rbbattle_autoexec.lua + mod/README.md als
// OFFEN dokumentiert.
//
// Aufruf: `npm test` (= `node --test`) aus diesem Verzeichnis.

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const luaparse = require('luaparse');
const { lua, lauxlib, lualib, to_luastring, to_jsstring } = require('fengari');

const MOD_PATH = path.join(__dirname, '..', '..', 'mod', 'lua', 'rbbattle_autoexec.lua');
const modSource = fs.readFileSync(MOD_PATH, 'utf8');

// Stub-Services + Mod + Assertions als EIN Lua-Chunk (eine Umgebung).
const STUBS = `
-- ==== Stub-Services (fengari/Stub-Services, Muster mod/README.md) ====
_G.__logs = {}
_G.__handlers = {}
_G.__commands = {}
_G.__db = {}
INVALID_ID = -1

LogService = {
    Log = function(self, msg) _G.__logs[#_G.__logs + 1] = msg end,
}
ConsoleService = {
    Write = function(self, msg) end,
    RegisterCommand = function(self, name, fn) _G.__commands[name] = fn end,
}
FindService = {
    FindEntitiesByGroup = function(self, g) return {} end,
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
        db.Clear = function(s) _G.__db = {} end
        return db
    end,
}
DifficultyService = { GetCurrentDifficultyName = function(self) return "hard" end }
CampaignService = { GetCreaturesBaseDifficulty = function(self) return 5 end }

function RegisterGlobalEventHandler(name, fn)
    _G.__handlers[name] = fn
end
`;

const ASSERTIONS = `
-- ==== Assertions ====
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

local function count_logs(sub)
    local n = 0
    for _, m in ipairs(_G.__logs) do
        if string.find(m, sub, 1, true) then n = n + 1 end
    end
    return n
end

-- 1. Mod geladen, Version 0.24.2, HQ initialisiert.
check(log_has("event=mod_load version=0.24.2"), "mod_load version=0.24.2")
check(log_has("hq_hp=100 hq_dead=false"), "mod_load enthaelt hq_hp=100 hq_dead=false")
check(_G.__handlers["EnteredTriggerEvent"] ~= nil, "EnteredTriggerEvent registriert")
check(_G.__handlers["RespawnFailedEvent"] ~= nil, "RespawnFailedEvent registriert")
check(_G.__commands["rb_hq"] ~= nil, "rb_hq registriert")

-- 2. Leak-Flow: ein Leak senkt den HQ-HP.
_G.__handlers["EnteredTriggerEvent"](nil)
check(log_has("event=leak damage=10 hp_before=100 hp=90"), "1. Leak -> hp 100->90")
check(log_has("event=hq_hp hp=90 dead=false"), "Report event=hq_hp hp=90")

-- 3. HQ-Tod durch Leaks (10x10 = 100 -> hp 0 -> Match-Ende).
for _ = 1, 9 do _G.__handlers["EnteredTriggerEvent"](nil) end
check(log_has("event=leak damage=10 hp_before=10 hp=0"), "10. Leak -> hp 10->0")
check(log_has("event=hq_dead status=match_end hp=0"), "event=hq_dead bei HP<=0")
check(log_has("event=match_end reason=hq_destroyed winner=opponent"), "event=match_end")
check(count_logs("event=match_end") == 1, "match_end genau einmal")

-- 4. Idempotenz: weiterer Leak nach Tod aendert nichts.
_G.__handlers["EnteredTriggerEvent"](nil)
check(count_logs("event=match_end") == 1, "kein zweites match_end (idempotent)")

-- 5. RespawnFailedEvent-Pfad (HQ-Tod-Kette) mit zugeordneter Entity.
_G.__commands["rb_hq"]({ "reset" })
check(log_has("event=hq_reset status=ok hp=100"), "rb_hq reset")
_G.__commands["rb_hq"]({ "entity", "12345" })
check(log_has("event=hq_entity status=ok entity=12345"), "rb_hq entity")
local hqEvt = { entity = 12345 }
function hqEvt:GetEntity() return self.entity end
_G.__handlers["RespawnFailedEvent"](hqEvt)
check(count_logs("event=match_end") == 2, "RespawnFailedEvent(HQ) -> match_end")

-- 6. RespawnFailedEvent eines ANDEREN Gebaeudes -> kein Match-Ende.
_G.__commands["rb_hq"]({ "reset" })
_G.__commands["rb_hq"]({ "entity", "12345" })
local otherEvt = { entity = 99999 }
function otherEvt:GetEntity() return self.entity end
_G.__handlers["RespawnFailedEvent"](otherEvt)
check(count_logs("event=match_end") == 2, "RespawnFailedEvent(andere Entity) -> KEIN match_end")

-- 7. RespawnFailedEvent ohne zugeordnete Entity -> nur Hinweis, kein Ende.
_G.__commands["rb_hq"]({ "reset" })
_G.__handlers["RespawnFailedEvent"](hqEvt)
check(count_logs("event=match_end") == 2, "RespawnFailedEvent ohne Entity-Zuordnung -> KEIN match_end")
check(log_has("event=hq_respawn status=unmatched"), "Hinweis event=hq_respawn status=unmatched")

print("FAILURES=" .. failures)
_G.__failures = failures
`;

function runLua(code) {
    const L = lauxlib.luaL_newstate();
    lualib.luaL_openlibs(L);
    const luacode = to_luastring(code);
    const loadStatus = lauxlib.luaL_loadstring(L, luacode);
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
    assert.doesNotThrow(() => luaparse.parse(modSource), 'mod/lua/rbbattle_autoexec.lua muss gültiges Lua 5.1 sein');
});

test('Win-Condition: Leak, HQ-HP, HQ-Tod (fengari + Stub-Services)', () => {
    const failures = runLua(STUBS + modSource + '\n' + ASSERTIONS);
    assert.strictEqual(failures, 0, `Assertion-Fehler im Lua-Harness: ${failures}`);
});
