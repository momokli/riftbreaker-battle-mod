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
-- #213-Sampling: _G.__enemySeq[group] ist eine Liste von Groessen, die
-- FindEntitiesByGroup fuer GENAU diese Gruppe der Reihe nach liefert (ein
-- Wert pro Aufruf -- simuliert "vorher" dann "nachher" ohne echten
-- Spiel-State). Andere Gruppen/Typen liefern {} (kein Treffer).
_G.__enemySeq = {}
_G.__enemySeqIdx = {}
FindService = {
    FindEntitiesByType = function(self, t) return {} end,
    FindEntitiesByGroup = function(self, g)
        local seq = _G.__enemySeq[g]
        if seq == nil then return {} end
        local i = (_G.__enemySeqIdx[g] or 0) + 1
        _G.__enemySeqIdx[g] = i
        local n = seq[i] or seq[#seq]
        local arr = {}
        for k = 1, n do arr[k] = k end
        return arr
    end,
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
    maxDifficultyLevel = 9,
    currentDifficultyLevel = 4,
    OnEnterSpawn = function(self, state)
        _G.__waveStarts = _G.__waveStarts + 1
        self:SpawnWavesForDifficultyLevel(self.currentDifficultyLevel, true)
    end,
    SpawnWavesForDifficultyLevel = function(self, level, addToSpawned) end,
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

local function log_count(sub)
    local c = 0
    for _, m in ipairs(_G.__logs) do
        if string.find(m, sub, 1, true) then c = c + 1 end
    end
    return c
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

-- 6. #213-Sampling: Naturwelle spawnt (Gruppe "enemy" liefert 5 dann 13) ->
--    event=richtwert_sample mit level/before/after/delta. RBB.commenced wird
--    direkt gesetzt (das HQ-Auto-Detect ist hier nicht das Testziel).
RBB.commenced = true
_G.__enemySeq["enemy"] = { 5, 13 }
dom_mananger.OnEnterSpawn(dom_mananger, {})
check(log_has("event=richtwert_sample_source status=found kind=group name=enemy"),
    "6. Sampling-Quelle gefunden: Gruppe 'enemy'")
check(log_has("event=richtwert_sample level=4 before=5 after=13 delta=8"),
    "6. Sample: Level 4, 5 -> 13 Kreaturen (delta=8)")

-- 7. Zweite Naturwelle: die einmal gefundene Quelle wird WIEDERVERWENDET
--    (kein erneutes Duchprobieren aller Kandidaten, kein zweites
--    richtwert_sample_source-Log).
_G.__enemySeq["enemy"] = { 13, 20 }
_G.__enemySeqIdx["enemy"] = 0
dom_mananger.OnEnterSpawn(dom_mananger, {})
check(log_count("event=richtwert_sample_source") == 1,
    "7. Sampling-Quelle wird nur einmal geloggt (wiederverwendet)")
check(log_has("event=richtwert_sample level=4 before=13 after=20 delta=7"),
    "7. Zweites Sample nutzt dieselbe Quelle (13 -> 20, delta=7)")

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
