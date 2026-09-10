'use strict';

// Statische Verifikation des Lua-Mods (rbbattle_autoexec.lua) fuer Issue #213
// (Recherche: Wellen-Richtwert, Baseline fuer die %-Boost-Preisformel aus
// #205/#199 ECO-6). Deckt ab:
//   - WaveRichtwert(level): dokumentierte Annahme Richtwert = level * 100
//     (einzige dem Mod bekannte Staerke-Groesse ist difficultyLevel, s.
//     Kommentar im Mod-Quelltext -- die native Wellen-Pool-Tabelle ist ohne
//     Live-Spiel/RE-Zugriff nicht enumerierbar).
//   - BoostPctForCalcium(calcium, level): reine Vorschau-Formel (NICHT an
//     rb_boost/BuyBoost angeschlossen) -- zeigt, dass dieselbe Calcium-Menge
//     bei hoeherem Level einen kleineren %-Effekt hat.
//   - rb_richtwert (Konsolen-Command): Kurven-Log ohne Args, %-Vorschau mit
//     Calcium[+Level]-Args.
//   - rb_balance: loggt zusaetzlich die Richtwert-Konfiguration + -Kurve.
//
// Aufruf: `npm test` (= `node --test`) aus tests/lua-static/.
//
// Verifikationsstatus: reine Formel-Logik gegen Stub-Services (fengari).
// Die Konstanten (richtwertPerLevel/calciumPerRichtwert) sind dokumentierte
// Annahmen ohne Live-Test -- s. Ausfuehrung dazu im Mod-Quelltext und
// docs/GAME_DESIGN.md.

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const luaparse = require('luaparse');
const { lua, lauxlib, lualib, to_luastring, to_jsstring } = require('fengari');

const MOD_PATH = path.join(__dirname, '..', '..', 'mod', 'lua', 'rbbattle_autoexec.lua');
const modSource = fs.readFileSync(MOD_PATH, 'utf8');

// Stub-Services + Mod + Assertions als EIN Lua-Chunk (Muster balance.test.js).
const STUBS = `
-- ==== Stub-Services (fengari, Muster balance.test.js) ====
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
-- ==== Assertions (Issue #213: Wellen-Richtwert) ====
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

-- 1. Reine Formel: WaveRichtwert(level) = level * richtwertPerLevel (100).
check(type(WaveRichtwert) == "function", "WaveRichtwert ist eine globale Funktion")
check(WaveRichtwert(1) == 100, "WaveRichtwert(1) == 100")
check(WaveRichtwert(5) == 500, "WaveRichtwert(5) == 500")
check(WaveRichtwert(9) == 900, "WaveRichtwert(9) == 900")
check(WaveRichtwert(0) == 100, "WaveRichtwert(0) faellt auf Level 1 zurueck (min. 1)")

-- 2. BoostPctForCalcium: dieselbe Calcium-Menge wirkt bei hoeherem Level
--    schwaecher (Kernaussage aus ECO-6) -- rein rechnerisch, kein Pool-Abzug.
check(type(BoostPctForCalcium) == "function", "BoostPctForCalcium ist eine globale Funktion")
local pctLevel1 = BoostPctForCalcium(100, 1)   -- 100 Richtwert / 100 Richtwert = 100%
local pctLevel10 = BoostPctForCalcium(100, 10) -- 100 Richtwert / 1000 Richtwert = 10%
check(math.abs(pctLevel1 - 100.0) < 0.001, "100 Calcium bei Level 1 -> 100%")
check(math.abs(pctLevel10 - 10.0) < 0.001, "100 Calcium bei Level 10 -> 10%")
check(pctLevel10 < pctLevel1, "dieselbe Calcium-Menge ist bei hoeherem Level relativ schwaecher")
check(BoostPctForCalcium(0, 5) == 0, "0 Calcium -> 0% (keine Division durch 0 o.ae.)")
check(BoostPctForCalcium(-50, 5) == 0, "negatives Calcium wird auf 0 geklemmt -> 0%")

-- 3. rb_richtwert ohne Args: loggt die Kurve fuer Level 1..9.
check(_G.__commands["rb_richtwert"] ~= nil, "rb_richtwert registriert")
_G.__commands["rb_richtwert"]({})
check(log_has("event=richtwert level=1 value=100"), "Kurve: Level 1 -> 100")
check(log_has("event=richtwert level=9 value=900"), "Kurve: Level 9 -> 900")

-- 4. rb_richtwert mit Calcium+Level: %-Vorschau ohne Nebenwirkung auf Pool/Boost.
_G.__commands["rb_richtwert"]({ "200", "4" })
check(log_has("event=richtwert_preview calcium=200 level=4 wave_richtwert=400 pct=50.0"),
    "Vorschau: 200 Calcium bei Level 4 (Richtwert 400) -> 50.0%")
check(RBB.boost.pct == 0, "rb_richtwert veraendert RBB.boost NICHT (reine Vorschau)")
check(RBB.economy.pool == 0, "rb_richtwert veraendert den Pool NICHT (reine Vorschau)")

-- 5. rb_balance loggt zusaetzlich die Richtwert-Konfiguration + -Kurve (#213).
check(_G.__commands["rb_balance"] ~= nil, "rb_balance registriert")
_G.__commands["rb_balance"]({})
check(log_has("event=balance richtwert_cfg richtwert_per_level=100 calcium_per_richtwert=1"),
    "rb_balance: Richtwert-Konfiguration geloggt")
check(log_has("event=balance richtwert level=1 value=100"), "rb_balance: Richtwert-Kurve Level 1 -> 100")
check(log_has("event=balance richtwert level=9 value=900"), "rb_balance: Richtwert-Kurve Level 9 -> 900")

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

test('Issue #213: Wellen-Richtwert-Formel + rb_richtwert-Vorschau (fengari + Stub-Services)', () => {
    const failures = runLua(STUBS + modSource + '\n' + ASSERTIONS);
    assert.strictEqual(failures, 0, `Assertion-Fehler im Lua-Harness: ${failures}`);
});
