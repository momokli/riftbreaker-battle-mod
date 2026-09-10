'use strict';

// Statische Verifikation des Lua-Mods (rbbattle_autoexec.lua) fuer Issue #41
// (Grundschwierigkeit leichter + Wellen-Takt testen). Der Mod bekommt zwei
// explizite, dokumentierte Wellen-Presets (RBB.wavePresets):
//   A = alle 8 Min (480 s) in VOLLER Groesse (strengthPct 100)
//   B = alle 4 Min (240 s) in HALBER Groesse (strengthPct 50)
// Grundschwierigkeit: "normal" (leichter als der bisherige Default "hard").
//
// Wie boost.test.js: 1) luaparse-Syntax-Check (Lua 5.1), 2) fengari-Lua-VM mit
// Stub-Services + Assertions auf [RBBATTLE]-Log-Zeilen und direkte Zugriffe
// auf die (im selben Lua-Chunk sichtbaren) Preset-/Skalierungs-Funktionen.
// Geprueft werden:
//   - Preset-Konfiguration (intervalS/strengthPct/baseDifficulty, kein Version-Bump)
//   - Wellen-Staerke-Skalierung (ScaleWaveLevel) + Integration am
//     SpawnWavesForDifficultyLevel-Chokepoint (A volle, B halbe Groesse)
//   - Debug-Trigger (addToSpawned=false) bleibt unangetastet.
// Die tatsaechliche Wrap-Wirksamkeit im Autoexec-Environment ist live
// verifizierbar (Operator, Test-Duell Momo vs. Matheo).

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

// Stub-Services + Mod + Assertions als EIN Lua-Chunk. dom_mananger hat hier —
// wie boost.test.js — SpawnWavesForDifficultyLevel (+ maxDifficultyLevel),
// damit der Skalierungs-Chokepoint (#41) greift. currentDifficultyLevel=4,
// damit "halbe Groesse" (floor(4/2)=2) eindeutig testbar ist.
const STUBS = `
-- ==== Stub-Services (fengari, Muster boost.test.js) ====
_G.__logs = {}
_G.__handlers = {}
_G.__commands = {}
_G.__db = {}
_G.__waveStarts = 0
_G.__spawnedLevel = -1
_G.__spawnedAdd = nil
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
DifficultyService = { GetCurrentDifficultyName = function(self) return "normal" end }
CampaignService = { GetCreaturesBaseDifficulty = function(self) return 5 end }
GuiService = {
    OpenPopup = function(self, ent, template, text) return true end,
}

-- DOM-Klasse: OnEnterSpawn (Wellenstart-Hook #42/#25) + SpawnWavesForDifficultyLevel
-- (Skalierungs-/Boost-Chokepoint #41/#39) + Timer-API (#23). currentDifficultyLevel=4.
dom_mananger = {
    maxDifficultyLevel = 9,
    currentDifficultyLevel = 4,
    OnEnterSpawn = function(self, state)
        _G.__waveStarts = _G.__waveStarts + 1
        self:SpawnWavesForDifficultyLevel(self.currentDifficultyLevel, true)
    end,
    SpawnWavesForDifficultyLevel = function(self, level, addToSpawned)
        _G.__spawnedLevel = level
        _G.__spawnedAdd = addToSpawned
    end,
    GetPrepareSpawnTime = function(self) return 480 end,
}

function RegisterGlobalEventHandler(name, fn)
    _G.__handlers[name] = fn
end
`;

const ASSERTIONS = `
-- ==== Assertions (Issue #41: Wellen-Takt + Grundschwierigkeit) ====
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

-- 1. Mod geladen (kein Version-Bump), Presets als Konfiguration vorhanden.
check(log_has("event=mod_load version=${MOD_VERSION}"), "mod_load version=${MOD_VERSION} (kein Version-Bump)")
check(log_has("event=mod_load version=${MOD_VERSION} status=ok mode=sp anchor=border_spawner_groups timer_cap=480 preset=A"),
    "mod_load: timer_cap=480 preset=A")

-- 2. Preset-Konfiguration (direkt, RBB im selben Chunk sichtbar).
check(RBB.waveIntervalCapS == 480, "waveIntervalCapS == 480 (Preset A)")
check(RBB.wavePresets.baseDifficulty == "normal", "baseDifficulty == normal")
check(RBB.wavePresets.active == "A", "aktives Preset == A (Default)")
check(RBB.wavePresets.variants.A.id == "A", "Variante A id=A")
check(RBB.wavePresets.variants.A.intervalS == 480, "Variante A intervalS=480 (8 Min)")
check(RBB.wavePresets.variants.A.strengthPct == 100, "Variante A strengthPct=100 (volle Groesse)")
check(RBB.wavePresets.variants.B.id == "B", "Variante B id=B")
check(RBB.wavePresets.variants.B.intervalS == 240, "Variante B intervalS=240 (4 Min)")
check(RBB.wavePresets.variants.B.strengthPct == 50, "Variante B strengthPct=50 (halbe Groesse)")

-- 3. Aktives Preset (Point-of-Switch): unbekannte ID faellt auf A zurueck,
--    aktives B liefert intervalS 240.
check(ActiveWavePreset().id == "A", "ActiveWavePreset() default A")
RBB.wavePresets.active = "B"
check(ActiveWavePreset().id == "B", "ActiveWavePreset() nach Switch B")
check(ActiveWavePreset().intervalS == 240, "Preset B intervalS 240 (4 Min)")
RBB.wavePresets.active = "A"
check(ActiveWavePreset().id == "A", "ActiveWavePreset() zurueck auf A")

-- 4. Wellen-Staerke-Skalierung (reine Funktion, ScaleWaveLevel).
check(ScaleWaveLevel(4, 100) == 4, "Scale 100%%: Level 4 -> 4")
check(ScaleWaveLevel(4, 50) == 2, "Scale 50%%: Level 4 -> 2")
check(ScaleWaveLevel(3, 50) == 1, "Scale 50%%: Level 3 -> 1 (floor)")
check(ScaleWaveLevel(1, 50) == 1, "Scale 50%%: Level 1 -> 1 (min)")
check(ScaleWaveLevel(0, 50) == 1, "Scale 50%%: Level 0 -> 1 (min)")

-- 5. Integration am Chokepoint: Variante A (volle Groesse) -> Level unveraendert.
dom_mananger.SpawnWavesForDifficultyLevel(dom_mananger, 4, true)
check(_G.__spawnedLevel == 4 and _G.__spawnedAdd == true, "A: Naturwelle Level 4 -> 4 (voll)")

-- 6. Debug-Trigger (addToSpawned=false) bleibt unangetastet.
dom_mananger.SpawnWavesForDifficultyLevel(dom_mananger, 4, false)
check(_G.__spawnedLevel == 4 and _G.__spawnedAdd == false, "Debug-Trigger: Level unangetastet (4)")

-- 7. Variante B (halbe Groesse) -> Level halbiert (floor, min 1).
RBB.wavePresets.active = "B"
dom_mananger.SpawnWavesForDifficultyLevel(dom_mananger, 4, true)
check(_G.__spawnedLevel == 2, "B: Naturwelle Level 4 -> 2 (halbe Groesse)")
dom_mananger.SpawnWavesForDifficultyLevel(dom_mananger, 3, true)
check(_G.__spawnedLevel == 1, "B: Naturwelle Level 3 -> 1 (floor(3/2))")
dom_mananger.SpawnWavesForDifficultyLevel(dom_mananger, 1, true)
check(_G.__spawnedLevel == 1, "B: Naturwelle Level 1 -> 1 (min)")
RBB.wavePresets.active = "A"

-- 8. rb_balance legt die Presets als dokumentierte Log-Flaeche offen.
_G.__commands["rb_balance"]({})
check(log_has("event=balance wave_preset id=A interval=480 strength_pct=100"),
    "rb_balance: wave_preset A (480/100)")
check(log_has("event=balance wave_preset id=B interval=240 strength_pct=50"),
    "rb_balance: wave_preset B (240/50)")
check(log_has("event=balance wave_preset_cfg active=A base_difficulty=normal timer_cap=480"),
    "rb_balance: wave_preset_cfg active=A base_difficulty=normal timer_cap=480")

-- 9. Setup-Log (bei Map-Ready) fuehrt Preset + Grundschwierigkeit mit.
_G.__handlers["PlayerInitializedEvent"]()
check(log_has("event=setup difficulty=normal creatures_difficulty=5 timer_cap=480 preset=A interval=480 strength_pct=100 base_difficulty=normal"),
    "event=setup: preset A + base_difficulty normal")

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

test('Issue #41: Wellen-Presets A/B + Grundschwierigkeit (fengari + Stub-Services)', () => {
    const failures = runLua(STUBS + modSource + '\n' + ASSERTIONS);
    assert.strictEqual(failures, 0, `Assertion-Fehler im Lua-Harness: ${failures}`);
});
