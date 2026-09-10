'use strict';

// Statische Verifikation des Lua-Mods (rbbattle_autoexec.lua) fuer Issue #27
// (Reveal-HUD / Poker): Vor dem Wellenstart sind beide Werte (Gegner-Built-
// Value + WAS kommt) verborgen; BEIM Wellenstart lockt der Mod den EIGENEN
// Built-Value + die eigene Send-Komposition (event=reveal), die Bridge
// injiziert die Gegner-Werte (rb_reveal); rb_hud liefert die HUD-Standard-
// felder (Runde, Countdown, Pool, HQ beider Teams), rb_round_start verbirgt
// den Reveal fuer die naechste Build-Phase. Wie send-queue.test.js:
// 1) luaparse-Syntax-Check (Lua 5.1), 2) fengari-Lua-VM mit Stub-Services +
// Assertions auf die [RBBATTLE]-Log-Zeilen (Vertragsflaeche Bridge/Server).
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

// Stub-Services + Mod + Assertions als EIN Lua-Chunk (eine Umgebung).
const STUBS = `
-- ==== Stub-Services (fengari, Muster send-queue.test.js) ====
_G.__logs = {}
_G.__handlers = {}
_G.__commands = {}
_G.__db = {}
_G.__waveStarts = 0
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

-- DOM-Klasse (Wellenstart-Hook #42/#25) + Timer-API (#23/#27-Countdown).
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
-- ==== Assertions (Issue #27: Reveal-HUD / Poker) ====
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

-- 1. Mod geladen, Reveal-/HUD-Commands registriert.
check(log_has("event=mod_load version=0.31.0"), "mod_load version=0.31.0")
check(_G.__commands["rb_reveal"] ~= nil, "rb_reveal registriert")
check(_G.__commands["rb_round_start"] ~= nil, "rb_round_start registriert")
check(_G.__commands["rb_hud"] ~= nil, "rb_hud registriert")

-- 2. VOR Wellenstart: beide Werte verborgen (reveal=hidden, Gegner-Felder hidden).
_G.__commands["rb_hud"]({})
check(log_has("event=hud round=0 countdown=300 pool=0 built_own=0 built_opp=hidden incoming=hidden hq_own=100 hq_opp=hidden reveal=hidden"),
    "rb_hud vor Wellenstart: reveal=hidden, Gegner-Werte verborgen")

-- 3. Economy: farmen + irreversibles convert + Kauf -> Built-Value 3000, Queue bereit.
local farmEvt = {}
function farmEvt:GetResourceName() return "carbonium" end
function farmEvt:GetAmount() return 5000 end
_G.__handlers["ResourceObtainedEvent"](farmEvt)
_G.__commands["rb_convert"]({ "carbonium", "2000" })
_G.__commands["rb_buy_wave"]({ "brabit", "2" })
check(log_has("event=buy_wave unit=brabit tier=t1 count=2 price=100 total=200 pool=1800 queue=2 status=ok"),
    "Kauf brabit x2 (Built-Value = 5000-2000 = 3000)")

-- 4. Vor Wellenstart (nach Farming): eigener Built-Value sichtbar, Gegner weiter verborgen.
_G.__commands["rb_hud"]({})
check(log_has("event=hud round=0 countdown=300 pool=1800 built_own=3000 built_opp=hidden incoming=hidden hq_own=100 hq_opp=hidden reveal=hidden"),
    "rb_hud vor Wellenstart: built_own=3000, Gegner weiter verborgen")

-- #158 Setup-Phase: HQ platziert -> Commence (Waves starten).
_G.__handlers["PlayerInitializedEvent"](nil)

-- 5. BEI Wellenstart: eigener Built-Value + eigene Send-Komposition werden gelockt.
dom_mananger.OnEnterSpawn(nil, {})
check(_G.__waveStarts == 1, "Original-OnEnterSpawn genau 1x (Naturwelle unangetastet)")
check(log_has("event=reveal round=1 status=revealed built_own=3000 built_opp=hidden send_own=units/ground/brabit:2 incoming=hidden"),
    "event=reveal bei Wellenstart (built_own=3000, send_own=brabit:2)")

-- 6. Nach Wellenstart, vor rb_reveal: Gegner-Werte weiter verborgen (reveal=revealed).
_G.__commands["rb_hud"]({})
check(log_has("event=hud round=1 countdown=300 pool=1800 built_own=3000 built_opp=hidden incoming=hidden hq_own=100 hq_opp=hidden reveal=revealed"),
    "rb_hud nach Wellenstart (revealed, Gegner noch hidden)")

-- 7. rb_reveal: Bridge injiziert Gegner-Werte -> Reveal beider Teams komplett.
_G.__commands["rb_reveal"]({ "6400", "80", "brabit:2" })
check(log_has("event=reveal_opp round=1 built_opp=6400 hq_opp=80 incoming=brabit:2 status=ok"),
    "rb_reveal injiziert built_opp/hq_opp/incoming")

-- 8. rb_hud: beide Teams sichtbar (Built beider Teams + WAS kommt + HQ beider).
_G.__commands["rb_hud"]({})
check(log_has("event=hud round=1 countdown=300 pool=1800 built_own=3000 built_opp=6400 incoming=brabit:2 hq_own=100 hq_opp=80 reveal=revealed"),
    "rb_hud nach rb_reveal: Built beider Teams + incoming + HQ beider")

-- 9. rb_round_start: neue Build-Phase -> Reveal wieder verborgen.
_G.__commands["rb_round_start"]({ "2" })
check(log_has("event=round_start round=2 status=build reveal=hidden"),
    "rb_round_start -> reveal=hidden")

-- 10. rb_hud nach rb_round_start: Gegner-Werte wieder verborgen.
_G.__commands["rb_hud"]({})
check(log_has("event=hud round=1 countdown=300 pool=1800 built_own=3000 built_opp=hidden incoming=hidden hq_own=100 hq_opp=hidden reveal=hidden"),
    "rb_hud nach rb_round_start: reveal=hidden")

-- 11. Zweiter Wellenstart: neuer Reveal (send_own leer, da Queue geflusht).
dom_mananger.OnEnterSpawn(nil, {})
check(log_has("event=reveal round=2 status=revealed built_own=3000 built_opp=hidden send_own=- incoming=hidden"),
    "zweiter Wellenstart: event=reveal round=2")

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

test('Issue #27: Reveal-HUD / Poker (fengari + Stub-Services)', () => {
    const failures = runLua(STUBS + modSource + '\n' + ASSERTIONS);
    assert.strictEqual(failures, 0, `Assertion-Fehler im Lua-Harness: ${failures}`);
});
