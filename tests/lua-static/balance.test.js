'use strict';

// Statische Verifikation des Lua-Mods (rbbattle_autoexec.lua) fuer Issue #33
// (Balance & Tuning v1): dokumentierte v1-Preisliste (Tiered Units + Bosse)
// und HQ-HP-Kurve ueber Runden. Wie send-queue.test.js:
// 1) luaparse-Syntax-Check (Lua 5.1), 2) fengari-Lua-VM mit Stub-Services +
// Assertions auf die [RBBATTLE]-Log-Zeilen. Kein Live-Spieltest (dafuer ist
// der Operator zustaendig); geprueft wird die reine Daten-/Formel-Logik:
//   - rb_balance legt Preisliste + HQ-HP-Kurve als Log-Flaeche offen.
//   - Preise sind strikt steigend ueber die Tiers (t1 < t2 < t3 < boss),
//     genau 1 Boss, eindeutige Unit-Ids, positive Integer-Preise.
//   - HQ-HP-Kurve folgt der dokumentierten Formel (start + per_round*min(r-1,cap))
//     und ist gedeckelt; der Wellenstart setzt den HQ-HP auf den Runden-Maxwert.
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
// Vollstaendiger Stub-Satz wie send-queue.test.js (Wellenstart-Hook + Shop),
// damit der Wellenstart-Flow (#25) und die HQ-Kurve (#33) gemeinsam laufen.
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
-- ==== Assertions (Issue #33: Balance & Tuning v1) ====
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

-- 1. Mod geladen (kein Version-Bump), rb_balance registriert.
check(log_has("event=mod_load version=0.27.0"), "mod_load version=0.27.0 (kein Version-Bump)")
check(_G.__commands["rb_balance"] ~= nil, "rb_balance registriert")

-- 2. rb_balance legt die v1-Preisliste offen (5 Units, 4 Tiers, 1 Boss).
_G.__commands["rb_balance"]({})
check(log_has("event=balance unit=brabit tier=t1 price=100 boss=false"), "Preis brabit=100 (t1)")
check(log_has("event=balance unit=baxmoth tier=t1 price=150 boss=false"), "Preis baxmoth=150 (t1)")
check(log_has("event=balance unit=artigian tier=t2 price=200 boss=false"), "Preis artigian=200 (t2)")
check(log_has("event=balance unit=canceroth tier=t3 price=300 boss=false"), "Preis canceroth=300 (t3)")
check(log_has("event=balance unit=boss tier=boss price=800 boss=true"), "Preis boss=800 (boss)")

-- 3. Preis-Invarianten (nicht trivial): 5 Units, 1 Boss, eindeutige Ids,
--    positive Integer-Preise, strikt steigend t1 < t2 < t3 < boss.
local units = {}
for _, m in ipairs(_G.__logs) do
    local id, tier, price, boss = string.match(m, "event=balance unit=([^ ]+) tier=([^ ]+) price=(%d+) boss=(%S+)")
    if id then
        units[#units + 1] = { id = id, tier = tier, price = tonumber(price), boss = boss }
    end
end
check(#units == 5, "genau 5 Units in der Preisliste")

local ids = {}
local tierMax = { t1 = 0, t2 = 0, t3 = 0, boss = 0 }
local bossCount = 0
local allPositiveInt = true
for _, u in ipairs(units) do
    ids[u.id] = (ids[u.id] or 0) + 1
    if u.boss == "true" then bossCount = bossCount + 1 end
    if u.price <= 0 or math.floor(u.price) ~= u.price then allPositiveInt = false end
    tierMax[u.tier] = math.max(tierMax[u.tier] or 0, u.price)
end
local nUnique = 0
for _ in pairs(ids) do nUnique = nUnique + 1 end
check(nUnique == 5, "5 eindeutige Unit-Ids")
check(bossCount == 1, "genau 1 Boss-Unit (boss=true)")
check(allPositiveInt, "alle Preise positive Integer")
check(tierMax.t1 < tierMax.t2 and tierMax.t2 < tierMax.t3 and tierMax.t3 < tierMax.boss,
    "Preise strikt steigend t1 < t2 < t3 < boss")

-- 4. HQ-HP-Kurve: Formel-Konstanten + Stichproben r1..r6 (inkl. Cap).
local start, per, cap, r1, r2, r3, r4, r5, r6
for _, m in ipairs(_G.__logs) do
    local a, b, c, d, e, f, g, h, i = string.match(m,
        "event=balance hq_curve start=(%d+) per_round=(%d+) cap=(%d+) r1=(%d+) r2=(%d+) r3=(%d+) r4=(%d+) r5=(%d+) r6=(%d+)")
    if a then
        start, per, cap = tonumber(a), tonumber(b), tonumber(c)
        r1, r2, r3, r4, r5, r6 = tonumber(d), tonumber(e), tonumber(f), tonumber(g), tonumber(h), tonumber(i)
    end
end
check(start == 100 and per == 20 and cap == 4, "Formel: start=100 per_round=20 cap=4")
check(r1 == 100 and r2 == 120 and r3 == 140 and r4 == 160,
    "Kurve r1..r4 = 100/120/140/160")
check(r5 == 180 and r6 == 180, "Kurve gedeckelt: r5 = r6 = 180")
check(r1 <= r2 and r2 <= r3 and r3 <= r4 and r4 <= r5 and r5 <= r6,
    "Kurve monoton nicht-fallend")

-- #158 Setup-Phase: HQ platziert -> Commence (Waves starten).
_G.__handlers["PlayerInitializedEvent"](nil)

-- 5. Wellenstart setzt den HQ-HP auf den Runden-Maxwert (Kurve wirkt).
--    Runde 1 -> 100, Runde 2 -> 120 (unabhaengig vom Leak-Zwischenstand).
dom_mananger.OnEnterSpawn(nil, {})
check(log_has("event=hq_curve round=1 maxhp=100 hp=100"), "Wellenstart Runde 1: HP=100")
dom_mananger.OnEnterSpawn(nil, {})
check(log_has("event=hq_curve round=2 maxhp=120 hp=120"), "Wellenstart Runde 2: HP=120")

-- 6. Kurve heilt zurueck aufs Runden-Max: nach Leaks in Runde 2 wird der
--    naechste Wellenstart (Runde 3) wieder auf 140 gesetzt.
_G.__handlers["EnteredTriggerEvent"](nil)   -- -10 -> 110
_G.__handlers["EnteredTriggerEvent"](nil)   -- -10 -> 100
dom_mananger.OnEnterSpawn(nil, {})           -- Runde 3 -> 140
check(log_has("event=hq_curve round=3 maxhp=140 hp=140"), "Wellenstart Runde 3: HP=140 (heilt aufs Max)")

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

test('Issue #33: Preisliste v1 + HQ-HP-Kurve (fengari + Stub-Services)', () => {
    const failures = runLua(STUBS + modSource + '\n' + ASSERTIONS);
    assert.strictEqual(failures, 0, `Assertion-Fehler im Lua-Harness: ${failures}`);
});
