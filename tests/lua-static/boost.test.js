'use strict';

// Statische Verifikation des Lua-Mods (rbbattle_autoexec.lua) fuer Issue #39
// (Send-Boost: naechste Naturwelle prozentual verstaerken). Der Boost ist ein
// ZUSAETZLICHER Hebel neben der Shop-Composition aus #25: `rb_boost <stufe|pct>`
// kauft einen prozentualen Aufschlag aus dem Spar-Pool (irreversibel); beim
// naechsten natuerlichen Wellenstart wird er am Chokepoint
// dom_mananger:SpawnWavesForDifficultyLevel auf die Welle angewendet
// (difficultyLevel-Delta, diskreter Hebel) und danach zurueckgesetzt.
//
// Aufruf: `npm test` (= `node --test`) aus tests/lua-static/.
//
// Verifikationsstatus: reine Lua-Logik gegen Stub-Services (fengari). Der
// DOM-Hebel (difficultyLevel 1..9 indiziert GetWavePool/GetAttackCount) ist
// am lan-lua-src (Spiel 2.0.58485) VERIFIZIERT; die Boost-Zahlen (Stufen/
// Caps) sind dokumentierte Annahmen ohne Live-Test (#33).

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const luaparse = require('luaparse');
const { lua, lauxlib, lualib, to_luastring, to_jsstring } = require('fengari');

const MOD_PATH = path.join(__dirname, '..', '..', 'mod', 'lua', 'rbbattle_autoexec.lua');
const modSource = fs.readFileSync(MOD_PATH, 'utf8');

// Stub-Services + Mod + Assertions als EIN Lua-Chunk. Anders als send-queue/
// balance/click-hud hat dom_mananger hier auch SpawnWavesForDifficultyLevel
// (+ currentDifficultyLevel/maxDifficultyLevel), damit der Boost-Chokepoint-
// Hook (#39) greift. OnEnterSpawn (Stub) ruft — wie die echte DOM — beim
// natuerlichen Wellenstart SpawnWavesForDifficultyLevel(level, true) auf.
const STUBS = `
-- ==== Stub-Services (fengari, Muster send-queue.test.js) ====
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
DifficultyService = { GetCurrentDifficultyName = function(self) return "hard" end }
CampaignService = { GetCreaturesBaseDifficulty = function(self) return 5 end }
GuiService = {
    OpenPopup = function(self, ent, template, text) return true end,
}

-- DOM-Klasse: OnEnterSpawn (Wellenstart-Hook #42/#25) + SpawnWavesForDifficultyLevel
-- (Boost-Chokepoint #39) + Timer-API (#23). currentDifficultyLevel=2.
dom_mananger = {
    maxDifficultyLevel = 9,
    currentDifficultyLevel = 2,
    OnEnterSpawn = function(self, state)
        _G.__waveStarts = _G.__waveStarts + 1
        self:SpawnWavesForDifficultyLevel(self.currentDifficultyLevel, true)
    end,
    SpawnWavesForDifficultyLevel = function(self, level, addToSpawned)
        _G.__spawnedLevel = level
        _G.__spawnedAdd = addToSpawned
    end,
    GetPrepareSpawnTime = function(self) return 300 end,
}

function RegisterGlobalEventHandler(name, fn)
    _G.__handlers[name] = fn
end
`;

const ASSERTIONS = `
-- ==== Assertions (Issue #39: Send-Boost) ====
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

local function wave()
    dom_mananger.OnEnterSpawn(dom_mananger, {})
end

-- 1. Mod geladen, rb_boost registriert, Boost-Chokepoint-Hook aktiv.
check(log_has("event=mod_load version=0.24.1"), "mod_load version=0.24.1")
check(_G.__commands["rb_boost"] ~= nil, "rb_boost registriert")
check(log_has("event=boost patch status=ok"), "boost patch ok (SpawnWavesForDifficultyLevel-Hook aktiv)")

-- 2. rb_boost Guards (usage / unbekannte Stufe / ungueltiger pct / zu wenig Pool).
_G.__commands["rb_boost"]({})
check(log_has("event=boost status=usage"), "rb_boost ohne Args -> usage")
_G.__commands["rb_boost"]({ "xyz" })
check(log_has("event=boost status=unknown_stage stage=xyz"), "rb_boost unbekannte Stufe")
_G.__commands["rb_boost"]({ "0" })
check(log_has("event=boost status=invalid_pct pct=0"), "rb_boost pct=0 -> invalid")
_G.__commands["rb_boost"]({ "s1" })
check(log_has("event=boost status=insufficient pct=25 price=200 pool=0"), "rb_boost ohne Pool -> insufficient")

-- 3. Farm + Convert -> Spar-Pool 100000.
local farmEvt = {}
function farmEvt:GetResourceName() return "carbonium" end
function farmEvt:GetAmount() return 100000 end
_G.__handlers["ResourceObtainedEvent"](farmEvt)
_G.__commands["rb_convert"]({ "carbonium", "100000" })
check(log_has("event=convert resource=carbonium amount=100000 value=100000 pool=100000 status=ok irreversible=1"),
    "convert -> pool 100000")

-- 4. rb_boost s1 x2 -> total_pct=50, buys=2, Pool 99600 (200 je Stufe).
_G.__commands["rb_boost"]({ "s1" })
check(log_has("event=boost status=ok pct=25 total_pct=25 price=200 pool=99800 buys=1"),
    "rb_boost s1 -> pct 25 pool 99800 buys 1")
_G.__commands["rb_boost"]({ "s1" })
check(log_has("event=boost status=ok pct=25 total_pct=50 price=200 pool=99600 buys=2"),
    "rb_boost s1 -> total 50 pool 99600 buys 2")

-- 5. Debug-Trigger (addToSpawned=false) verbraucht den Boost NICHT.
dom_mananger.SpawnWavesForDifficultyLevel(dom_mananger, 2, false)
check(_G.__spawnedLevel == 2 and _G.__spawnedAdd == false, "Debug-Trigger: Level unangetastet (2)")
check(log_count("event=boost status=flush") == 0, "kein Boost-Flush durch Debug-Trigger")

-- 6. Natuerlicher Wellenstart -> Boost angewendet (50% @ Level 2 -> Delta 1 -> Level 3).
wave()
check(log_has("event=boost status=flush round=1 pct=50 level_from=2 level_to=3 delta=1 buys=2"),
    "Flush: 50% Boost Level 2 -> 3 (delta=1)")
check(_G.__spawnedLevel == 3 and _G.__spawnedAdd == true, "SpawnLevel auf 3 erhoeht (Naturwelle)")

-- 7. Boost zurueckgesetzt: zweite Welle ohne erneuten Kauf unbeeinflusst.
wave()
check(_G.__spawnedLevel == 2, "zweite Welle ohne Boost (Level 2)")
check(log_has("event=round round=2 status=start"), "round=2")
check(log_count("event=boost status=flush") == 1, "genau ein Boost-Flush bisher")

-- 8. Freie pct-Eingabe (rb_boost 30) -> linearer Preis (30*8=240).
_G.__commands["rb_boost"]({ "30" })
check(log_has("event=boost status=ok pct=30 total_pct=30 price=240 pool=99360 buys=1"),
    "rb_boost 30 -> price 240 total 30 pool 99360 buys 1")

-- 9. Boost-Cap (maxBoostPct=200): s3 (+100) ok, zweites s3 -> cap.
_G.__commands["rb_boost"]({ "s3" })
check(log_has("event=boost status=ok pct=100 total_pct=130 price=800 pool=98560 buys=2"),
    "rb_boost s3 -> total 130 pool 98560 buys 2")
_G.__commands["rb_boost"]({ "s3" })
check(log_has("event=boost status=cap pct=130 add=100 max=200"), "Boost-Cap: 130 + 100 > 200")

-- 10. Flush verbraucht 130% @ Level 2 -> Delta ceil(2*1.3)=3 -> Level 5.
wave()
check(log_has("event=boost status=flush round=3 pct=130 level_from=2 level_to=5 delta=3 buys=2"),
    "Flush: 130% Boost Level 2 -> 5 (delta=3)")

-- 11. maxBoostsPerWave=4: 4x s1 ok, 5. Kauf -> max_boosts.
_G.__commands["rb_boost"]({ "s1" })
_G.__commands["rb_boost"]({ "s1" })
_G.__commands["rb_boost"]({ "s1" })
_G.__commands["rb_boost"]({ "s1" })
check(log_has("event=boost status=ok pct=25 total_pct=100 price=200 pool=97760 buys=4"),
    "4x s1 -> buys 4 total 100 pool 97760")
_G.__commands["rb_boost"]({ "s1" })
check(log_has("event=boost status=max_boosts buys=4 pct=100"), "5. Boost -> max_boosts")

-- 12. duel-Modus: Boost-Flush NUR in sp (Boost bleibt liegen).
_G.__commands["rb_mode"]({ "duel" })
wave()
check(log_has("event=round round=4 status=start mode=duel"), "duel: round=4 mode=duel")
check(_G.__spawnedLevel == 2, "duel: keine Boost-Anwendung (Level 2)")
check(log_count("event=boost status=flush") == 2, "duel: kein zusaetzlicher Flush")
_G.__commands["rb_status"]({})
check(log_has("event=status mode=duel"), "rb_status mode=duel")

-- 13. Zurueck auf sp -> liegengebliebener Boost (100%) wird angewendet.
_G.__commands["rb_mode"]({ "sp" })
wave()
check(log_has("event=boost status=flush round=5 pct=100 level_from=2 level_to=4 delta=2 buys=4"),
    "sp: liegengebliebener 100% Boost Level 2 -> 4 (delta=2)")

-- 14. Boost-Zustand leer -> rb_status zeigt keinen Boost.
_G.__commands["rb_status"]({})
check(log_has("event=status mode=sp"), "rb_status mode=sp")

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

test('Issue #39: Send-Boost (fengari + Stub-Services)', () => {
    const failures = runLua(STUBS + modSource + '\n' + ASSERTIONS);
    assert.strictEqual(failures, 0, `Assertion-Fehler im Lua-Harness: ${failures}`);
});
