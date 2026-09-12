'use strict';

// Statische Verifikation des Lua-Mods (rbbattle_autoexec.lua) fuer Issue #278
// (Wave-Interval-Diskrepanz: Setup loggt interval=480, effektiv ~300 s).
//
// Kern der Analyse: `RBB.waveIntervalCapS` (= Preset-`intervalS`, Preset A 480)
// ist ein DECKEL fuer `dom_mananger:GetPrepareSpawnTime` — die Klasse liefert
// den rules-Wert (normal/hard: 420, Beleg lua-src dom_survival_*_rules_*.lua)
// und der Mod senkt ihn nur nach UNTEN. Der alte Setup-Log schrieb deshalb
// `interval=<preset.intervalS>` (480) und behauptete damit einen Timer, den
// das Spiel nie benutzt. Der Fix: das Setup-Log trennt
//   interval_cfg = Preset-Ziel (Konfiguration)
//   interval_eff = tatsaechlich wirksamer DOM-Timer (nach Cap)
//
// Wie wave-presets.test.js: 1) luaparse-Syntax-Check (Lua 5.1), 2) fengari-VM
// mit Stub-Services. `dom_mananger:GetPrepareSpawnTime` wird je Szenario
// parametrisiert (420 < Cap, 600 > Cap, kein DOM, Preset B).
// Geprueft werden Log-Ausgabe UND Kappungs-Verhalten der gepatchten Methode.
// Das echte Wave-Timing im Spiel ist damit NICHT bewiesen — offener Punkt
// (Player-Test Momo/Matheo).

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const luaparse = require('luaparse');
const { lua, lauxlib, lualib, to_luastring, to_jsstring } = require('fengari');

const MOD_PATH = path.join(__dirname, '..', '..', 'mod', 'lua', 'rbbattle_autoexec.lua');
const modSource = fs.readFileSync(MOD_PATH, 'utf8');

// Stub-Services (Muster wave-presets.test.js). `domApi` steuert, ob die
// DOM-Klasse eine GetPrepareSpawnTime-Methode hat (Test des Fallbacks).
function stubs({ domApi = true, prepareTime = 420 } = {}) {
    const domTimer = domApi
        ? `    GetPrepareSpawnTime = function(self) return ${prepareTime} end,`
        : '';
    return `
-- ==== Stub-Services (fengari, Muster wave-presets.test.js) ====
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
DifficultyService = { GetCurrentDifficultyName = function(self) return "normal" end }
CampaignService = { GetCreaturesBaseDifficulty = function(self) return 5 end }
GuiService = { OpenPopup = function(self, ent, template, text) return true end }

-- DOM-Klasse mit optionaler Timer-API (#23). currentDifficultyLevel=4.
dom_mananger = {
    maxDifficultyLevel = 9,
    currentDifficultyLevel = 4,
    OnEnterSpawn = function(self, state) end,
    SpawnWavesForDifficultyLevel = function(self, level, addToSpawned) end,
${domTimer}
}

function RegisterGlobalEventHandler(name, fn)
    _G.__handlers[name] = fn
end
`;
}

// Assertions sind fuer alle Szenarien gleich aufgebaut; die erwarteten Werte
// kommen per Template-Interpolation aus JS.
function assertions({ cfg, eff, capped, checkCapped = true }) {
    const cappedCheck = checkCapped
        ? `
-- 3. Gepatchte Klasse kappt auf genau den effektiven Wert (bzw. gibt den
--    rules-Wert unveraendert zurueck, wenn er <= Cap ist).
check(dom_mananger.GetPrepareSpawnTime(dom_mananger) == ${capped},
    "GetPrepareSpawnTime liefert ${capped}")
`
        : `
-- 3. Ohne Timer-API existiert die Methode nicht -> nur Log-Fallback pruefen.
check(dom_mananger.GetPrepareSpawnTime == nil, "ohne API keine GetPrepareSpawnTime-Methode")
`;
    return `
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

-- Setup-Log (bei Map-Ready) ausloesen.
_G.__handlers["PlayerInitializedEvent"]()

-- 1. Setup-Log zeigt Ziel (cfg) und tatsaechlich wirksamen Timer (eff).
check(log_has("interval_cfg=${cfg} interval_eff=${eff}"),
    "setup: interval_cfg=${cfg} interval_eff=${eff}")

-- 2. Der alte, irrefuehrende Effektiv-Wert ist verschwunden (kein Feld
--    "interval=" mehr; nur noch interval_cfg/interval_eff).
check(not log_has("interval=${cfg} "), "setup: kein irrefuehrendes interval=${cfg}")
${cappedCheck}
print("FAILURES=" .. failures)
_G.__failures = failures
`;
}

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

function runScenario(opts) {
    return runLua(stubs(opts) + modSource + '\n' + assertions(opts));
}

test('Lua-Syntax (luaparse, Lua 5.1)', () => {
    assert.doesNotThrow(() => luaparse.parse(modSource),
        'mod/lua/rbbattle_autoexec.lua muss gültiges Lua 5.1 sein');
});

test('Issue #278: rules 420 < Preset-Cap 480 -> effektiv 420 (Preset A)', () => {
    const failures = runScenario({ domApi: true, prepareTime: 420, cfg: 480, eff: 420, capped: 420 });
    assert.strictEqual(failures, 0, `Assertion-Fehler im Lua-Harness: ${failures}`);
});

test('Issue #278: rules 600 > Preset-Cap 480 -> effektiv gedeckelt 480', () => {
    const failures = runScenario({ domApi: true, prepareTime: 600, cfg: 480, eff: 480, capped: 480 });
    assert.strictEqual(failures, 0, `Assertion-Fehler im Lua-Harness: ${failures}`);
});

test('Issue #278: rules 240 < Cap 480 -> effektiv 240 (kein Anheben)', () => {
    const failures = runScenario({ domApi: true, prepareTime: 240, cfg: 480, eff: 240, capped: 240 });
    assert.strictEqual(failures, 0, `Assertion-Fehler im Lua-Harness: ${failures}`);
});

test('Issue #278: DOM-API fehlt -> Fallback auf Cap (eff == cfg)', () => {
    const failures = runScenario({ domApi: false, prepareTime: 0, cfg: 480, eff: 480, capped: 480, checkCapped: false });
    assert.strictEqual(failures, 0, `Assertion-Fehler im Lua-Harness: ${failures}`);
});
