'use strict';

// Statische Verifikation des Lua-Mods (rbbattle_autoexec.lua) fuer Issue #145
// (Zwei Solo-Modi): "Solo Normal" (sp) bleibt das bisherige, unveraenderte
// Verhalten (Default); "Solo OP" (rb_mode sp_op) ist ein Test-/Debug-Modus mit
// klar anderen Startwerten (hoher Startpool + schneller Rundentakt) und ist in
// rb_status sichtbar. Der Cheat-Zustand liegt in RBB.soloOp; RBB.mode bleibt
// "sp", damit Self-Send/Boost-Flush unangetastet greifen.
//
// Wie balance.test.js: 1) luaparse-Syntax-Check (Lua 5.1), 2) fengari-Lua-VM
// mit Stub-Services + Assertions auf [RBBATTLE]-Log-Zeilen. Geprueft werden:
//   - Default = sp ohne Cheats (kein impliziter OP-Zustand)
//   - rb_mode sp_op: Pool + Rundentakt auf OP-Werte, sichtbar in rb_status
//   - rb_mode sp: exakter Rueckweg (Normalwerte wiederhergestellt)
//   - rb_mode duel bleibt Stub; OP wird dabei verlassen
//   - unbekanntes Argument -> usage, Modus unveraendert
//
// Bewusst NICHT live-verifizierbar: die tatsaechliche Wirkung des dynamischen
// waveIntervalCapS am DOM-Timer im Spiel (Operator, E2E auf :6321).

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const luaparse = require('luaparse');
const { lua, lauxlib, lualib, to_luastring, to_jsstring } = require('fengari');

const MOD_PATH = path.join(__dirname, '..', '..', 'mod', 'lua', 'rbbattle_autoexec.lua');
const modSource = fs.readFileSync(MOD_PATH, 'utf8');

// Erwartete Mod-Version dynamisch aus der Mod-Quelle lesen (RBB.version),
// damit Version-Bumps auf main die Assertions nicht stale machen (#150).
const MOD_VERSION = (modSource.match(/RBB\.version\s*=\s*"([^"]+)"/) || [])[1];
if (!MOD_VERSION) {
    throw new Error('RBB.version nicht in mod/lua/rbbattle_autoexec.lua gefunden');
}

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
const ASSERTIONS = `
-- ==== Assertions (Issue #145: Solo Normal vs. Solo OP) ====
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

local function log_count(sub)
    local n = 0
    for _, m in ipairs(_G.__logs) do
        if string.find(m, sub, 1, true) then n = n + 1 end
    end
    return n
end

-- 1. Default = Solo Normal (sp), KEIN Cheat-Zustand.
check(log_has("event=mod_load version=0.34.0 status=ok mode=sp"),
    "mod_load: mode=sp (Default = Solo Normal)")
check(RBB.soloOp == false, "RBB.soloOp ist per Default false (kein impliziter OP)")
check(RBB.economy.pool == 0, "Default-Pool = 0 (kein Cheat)")
check(RBB.waveIntervalCapS == RBB.wavePresets.variants[RBB.wavePresets.active].intervalS,
    "Default-Rundentakt = aktives Wellen-Preset (unveraendert)")
check(RBB.opCfg.startPool > 0 and RBB.opCfg.waveIntervalS > 0,
    "opCfg dokumentiert OP-Startwerte (startPool/waveIntervalS)")

-- 2. rb_status im Default: mode=sp, op=false, Pool 0 sichtbar.
_G.__commands["rb_status"]({})
check(log_has("event=status mode=sp round=0 pool=0"), "rb_status: mode=sp round=0 pool=0")
check(log_has("op=false"), "rb_status zeigt op=false (kein Cheat aktiv)")

-- 3. Umschalten auf Solo OP (sp_op): Startwerte + Label.
_G.__commands["rb_mode"]({ "sp_op" })
check(log_has("event=mode mode=sp_op status=ok"), "rb_mode sp_op -> event=mode mode=sp_op status=ok")
check(log_has("event=solo_op status=on"), "event=solo_op status=on geloggt")
check(RBB.soloOp == true, "RBB.soloOp true nach sp_op")
check(RBB.mode == "sp", "RBB.mode bleibt sp (Self-Send greift weiter)")
check(RBB.economy.pool == RBB.opCfg.startPool,
    "Solo OP: Pool = opCfg.startPool (" .. RBB.opCfg.startPool .. ")")
check(RBB.waveIntervalCapS == RBB.opCfg.waveIntervalS,
    "Solo OP: Rundentakt = opCfg.waveIntervalS (" .. RBB.opCfg.waveIntervalS .. "s)")

-- 4. rb_status zeigt jetzt sp_op + op=true + Cheat-Pool.
_G.__commands["rb_status"]({})
check(log_has("event=status mode=sp_op round=0 pool=" .. RBB.opCfg.startPool) and log_has("op=true"),
    "rb_status: mode=sp_op + Cheat-Pool + op=true sichtbar")

-- 5. Self-Send funktioniert im OP-Modus (Queue-Summary bleibt "sp", nicht "duel").
check(log_has("queue="), "rb_status zeigt Queue-Feld (sp, nicht duel-stub)")

-- 6. Rueckweg: rb_mode sp stellt die Normalwerte exakt wieder her.
local poolBeforeOp = 0
local capBeforeOp = RBB.wavePresets.variants[RBB.wavePresets.active].intervalS
_G.__commands["rb_mode"]({ "sp" })
check(log_has("event=mode mode=sp status=ok"), "rb_mode sp -> event=mode mode=sp status=ok")
check(log_has("event=solo_op status=off"), "event=solo_op status=off geloggt")
check(RBB.soloOp == false, "RBB.soloOp false nach Rueckweg")
check(RBB.economy.pool == poolBeforeOp, "Pool exakt zurueck auf Normalwert (0)")
check(RBB.waveIntervalCapS == capBeforeOp, "Rundentakt exakt zurueck auf Preset-Wert")

-- 7. Wechsel von Normal auf OP und zurueck aendert nichts an modusfremden Werten
--    (Pool-Rueckweg ist verlustfrei, wenn schon Pool existierte).
RBB.economy.pool = 700
_G.__commands["rb_mode"]({ "sp_op" })
check(RBB.economy.pool == RBB.opCfg.startPool, "sp_op ueberschreibt vorhandenen Pool mit Startpool")
_G.__commands["rb_mode"]({ "sp" })
check(RBB.economy.pool == 700, "sp stellt den vorherigen Pool (700) exakt wieder her")

-- 8. duel bleibt Stub und verlaesst OP.
_G.__commands["rb_mode"]({ "sp_op" })
_G.__commands["rb_mode"]({ "duel" })
check(log_has("event=mode mode=duel status=stub"), "rb_mode duel -> status=stub (unveraendert)")
check(RBB.soloOp == false, "duel deaktiviert Solo-OP (Cheat aus)")
check(RBB.mode == "duel", "RBB.mode == duel")
_G.__commands["rb_status"]({})
check(log_has("event=status mode=duel"), "rb_status zeigt mode=duel")

-- 9. Unbekanntes Argument -> usage, Modus unveraendert.
_G.__commands["rb_mode"]({ "quark" })
check(log_has("event=mode status=usage mode=duel"), "unbekanntes Arg -> usage mit aktuellem Modus (duel)")

-- 10. Idempotenz: zweimal sp_op setzt die Cheat-Werte nur einmal (kein Doppel-Apply).
_G.__commands["rb_mode"]({ "sp" })
local onBefore = log_count("event=solo_op status=on")
_G.__commands["rb_mode"]({ "sp_op" })
_G.__commands["rb_mode"]({ "sp_op" })
check(RBB.economy.pool == RBB.opCfg.startPool, "doppelt sp_op: Pool bleibt Startpool")
check(log_count("event=solo_op status=on") == onBefore + 1,
    "solo_op status=on nur einmal zusaetzlich geloggt (idempotent)")

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

test('Mod-Version ist lesbar (#150)', () => {
    assert.match(MOD_VERSION, /^\d+\.\d+\.\d+$/, 'RBB.version muss semver-artig sein');
});

test('Issue #145: Solo Normal (sp, Default) + Solo OP (sp_op, Test/Cheats) (fengari + Stub-Services)', () => {
    const failures = runLua(STUBS + modSource + '\n' + ASSERTIONS);
    assert.strictEqual(failures, 0, `Assertion-Fehler im Lua-Harness: ${failures}`);
});
