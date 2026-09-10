'use strict';

// Statische Verifikation des Lua-Mods (rbbattle_autoexec.lua) fuer Issue #40
// (Send-Waehrung: Ressourcen-Mapping): MVP = Calcium-only. Geprueft wird, dass
// `rb_convert` NUR Calcium (Spiel-Ressource "carbonium", Alias "calcium") als
// Send-Waehrung akzeptiert und jede andere Ressource mit `not_send_currency`
// ablehnt — unabhaengig davon, ob sie gefarmt wurde (Waehrung entscheidet vor
// der Mengen-Pruefung). Das zentrale Mapping liegt in `RBB.economyCfg.sendCurrency`;
// der spaetere Ironium-Qualitaets-Split haengt dort ein zweites Mapping an.
//
// Wie send-queue.test.js: 1) luaparse-Syntax-Check (Lua 5.1), 2) fengari-Lua-VM
// mit Stub-Services + Assertions auf die [RBBATTLE]-Log-Zeilen.

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const luaparse = require('luaparse');
const { lua, lauxlib, lualib, to_luastring, to_jsstring } = require('fengari');

const MOD_PATH = path.join(__dirname, '..', '..', 'mod', 'lua', 'rbbattle_autoexec.lua');
const modSource = fs.readFileSync(MOD_PATH, 'utf8');

// Stub-Services + Mod + Assertions als EIN Lua-Chunk (eine Umgebung).
// Vollstaendiger Stub-Satz wie send-queue.test.js (inkl. PlayerService-DB
// fuer die Persistenz + dom_mananger fuer den Wellenstart-Hook).
const STUBS = `
-- ==== Stub-Services (fengari, Muster send-queue.test.js) ====
_G.__logs = {}
_G.__handlers = {}
_G.__commands = {}
_G.__db = {}
_G.__waveStarts = 0
_G.__popup = nil
INVALID_ID = -1

LogService = {
    Log = function(self, msg) _G.__logs[#_G.__logs + 1] = msg end,
}
ConsoleService = {
    Write = function(self, msg) end,
    RegisterCommand = function(self, name, fn) _G.__commands[name] = fn end,
}
FindService = {
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

const ASSERTIONS = `
-- ==== Assertions (Issue #40: Send-Waehrung Calcium-only) ====
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

-- 1. Mod geladen (kein Version-Bump), rb_convert/rb_economy registriert.
check(log_has("event=mod_load version=0.29.0"), "mod_load version=0.29.0 (kein Version-Bump)")
check(_G.__commands["rb_convert"] ~= nil, "rb_convert registriert")
check(_G.__commands["rb_economy"] ~= nil, "rb_economy registriert")

-- 2. Ein-Argument-Form: rb_convert <menge> konvertiert Calcium (carbonium).
local farmC = {}
function farmC:GetResourceName() return "carbonium" end
function farmC:GetAmount() return 2000 end
_G.__handlers["ResourceObtainedEvent"](farmC)
check(log_has("event=economy_farm source=resource_obtained resource=carbonium amount=2000 value=2000"),
    "farm 2000 carbonium")
_G.__commands["rb_convert"]({ "1000" })
check(log_has("event=convert resource=carbonium amount=1000 value=1000 pool=1000 status=ok irreversible=1"),
    "rb_convert 1000 (ein Arg) -> Calcium -> pool 1000")

-- 3. Zwei-Argument-Form mit Alias: rb_convert calcium <menge> ≡ carbonium.
_G.__commands["rb_convert"]({ "calcium", "500" })
check(log_has("event=convert resource=carbonium amount=500 value=500 pool=1500 status=ok irreversible=1"),
    "rb_convert calcium 500 (Alias) -> pool 1500")

-- 4. Nicht-Calcium wird abgelehnt, auch wenn es gefarmt wurde (Waehrung zuerst).
local farmS = {}
function farmS:GetResourceName() return "steel" end
function farmS:GetAmount() return 300 end
_G.__handlers["ResourceObtainedEvent"](farmS)
_G.__commands["rb_convert"]({ "steel", "100" })
check(log_has("event=convert status=not_send_currency resource=steel"),
    "rb_convert steel 100 -> not_send_currency")
check(not log_has("event=convert resource=steel amount=100 value="),
    "kein Convert-Vorgang fuer steel")

-- 5. Waehrung entscheidet VOR der Mengen-Pruefung: Ironium ohne Farm-Value
--    ergibt not_send_currency (nicht insufficient).
_G.__commands["rb_convert"]({ "ironium", "999" })
check(log_has("event=convert status=not_send_currency resource=ironium"),
    "rb_convert ironium 999 (ungefarmt) -> not_send_currency, nicht insufficient")
check(not log_has("event=convert status=insufficient resource=ironium"),
    "kein insufficient fuer ironium (Waehrung prueft zuerst)")

-- 6. Pool unveraendert durch die Ablehnungen (1500 aus Schritt 3).
_G.__commands["rb_economy"]({})
check(log_has("event=economy_show source=resource_obtained pool=1500 farmed=2300 converted=1500 built=800 converts=2"),
    "economy_show pool=1500 (Ablehnungen kosten nichts)")

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
    assert.doesNotThrow(() => luaparse.parse(modSource),
        'mod/lua/rbbattle_autoexec.lua muss gültiges Lua 5.1 sein');
});

test('Issue #40: Send-Währung Calcium-only (fengari + Stub-Services)', () => {
    const failures = runLua(STUBS + modSource + '\n' + ASSERTIONS);
    assert.strictEqual(failures, 0, `Assertion-Fehler im Lua-Harness: ${failures}`);
});
