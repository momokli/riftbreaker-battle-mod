'use strict';

// Statische Verifikation des Lua-Mods (rbbattle_autoexec.lua) ohne Spiel:
//   1) Syntax-Check via luaparse (Lua-5.1-Grammatik, wie CI-Pipeline),
//   2) Ausfuehrung in fengari-Lua-VM mit Stub-Services und Assertions auf die
//      [RBBATTLE]-Log-Zeilen (die Vertragsflaeche fuer Bridge/Server).
//
// Deckt Issue #281 ab: nach `match_end reason=hq_destroyed` geht die Runde
// deterministisch + idempotent auf 0 zurueck (Setup-/HQ-Placement-Phase,
// Economy-Pool 0, Runden-/Wave-Timer 0). Genau EIN Reset pro Niederlage, keine
// Restart-Schleife, saubere Session-Boundary (match_end -> reset -> commence
// pending/place_hq).
//
// Test-Split (Pflicht, #281):
//   * OHNE Player (dieser Test, automatisiert): HQ-Tod simulieren -> Reset
//     feuert genau einmal -> neuer Zustand (commence pending/place_hq,
//     Economy 0).
//   * NUR mit Player (OFFEN, Momo/Matheo): echtes HQ zerstoeren -> Runde
//     startet sichtbar neu. Nicht hier abgedeckt (braucht laufendes Spiel).
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
_G.__console = {}
_G.__handlers = {}
_G.__commands = {}
_G.__db = {}
INVALID_ID = -1

LogService = {
    Log = function(self, msg) _G.__logs[#_G.__logs + 1] = msg end,
}
ConsoleService = {
    Write = function(self, msg) _G.__console[#_G.__console + 1] = msg end,
    RegisterCommand = function(self, name, fn) _G.__commands[name] = fn end,
}
-- #144: _G.__findTypes[type] steuert, was FindEntitiesByType pro Typ
-- liefert (Test setzt das gezielt); Default = leere Liste (kein Treffer).
_G.__findTypes = {}
FindService = {
    FindEntitiesByType = function(self, t) return _G.__findTypes[t] or {} end,
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

local function console_has(sub)
    for _, m in ipairs(_G.__console) do
        if string.find(m, sub, 1, true) then return true end
    end
    return false
end

-- Index der ersten Log-Zeile mit <sub> (1-basiert) oder nil. Belegt die
-- Session-Boundary-Reihenfolge (match_end -> reset -> commence).
local function log_index(sub)
    for i, m in ipairs(_G.__logs) do
        if string.find(m, sub, 1, true) then return i end
    end
    return nil
end

-- Erster Log-Index >= from mit <sub> (die neue Session liegt NACH dem Reset).
local function log_index_after(sub, from)
    for i = from, #_G.__logs do
        if string.find(_G.__logs[i], sub, 1, true) then return i end
    end
    return nil
end

local function trigger_evt(entity, teamId)
    local evt = { entity = entity }
    function evt:GetEntity() return self.entity end
    if teamId ~= nil then
        function evt:GetTeamId() return teamId end
    end
    return evt
end

-- Simuliert einen echten HQ-Tod ueber 10 Leaks (10 x 10 = 100 -> hp 0).
local function kill_hq()
    _G.__commands["rb_hq"]({ "entity", "12345" })
    for _ = 1, 10 do _G.__handlers["EnteredTriggerEvent"](trigger_evt(9999)) end
end

-- ============================================================================
-- 1. Laden + Registrierung
-- ============================================================================
check(log_has("event=mod_load version=0.34.3"), "1. mod_load version=0.34.3")
check(_G.__commands["rb_reset"] ~= nil, "1. rb_reset registriert (#281)")
check(_G.__commands["rb_hq"] ~= nil, "1. rb_hq registriert")

-- ============================================================================
-- 2. OHNE Player: HQ-Tod simulieren -> Reset steht an, feuert noch NICHT
-- ============================================================================
-- Laufende Runde mit Restzustand simulieren (damit "auf 0" pruefbar ist).
_G.__commands["rb_hq"]({ "entity", "12345" })
RBB.round = 5
RBB.economy.pool = 777
RBB.economy.farmed = 321
RBB.sendQueue.count = 2
RBB.sendQueue.value = 300
RBB.sendQueue.units = { { blueprint = "units/ground/brabit" } }
RBB.boost.pct = 50
RBB.boost.buys = 2
RBB.reveal.locked = true
RBB.commenced = true
RBB.setupAnnounced = true
_G.__db["pool"] = 777

kill_hq()
check(log_has("event=hq_dead status=match_end hp=0"), "2. HQ-Tod: event=hq_dead status=match_end")
check(log_has("event=match_end reason=hq_destroyed"), "2. event=match_end reason=hq_destroyed")
check(RBB.reset.pending == true, "2. Reset ist nach Niederlage vorgemerkt (pending)")
check(RBB.reset.auto == true, "2. echte HQ-Zerstoerung -> Tick-Pfad darf auto-ausfuehren")
check(count_logs("event=reset round=0 status=ok") == 0, "2. Reset noch NICHT ausgefuehrt (kein Tick)")

-- ============================================================================
-- 3. Reset feuert genau einmal (HourEvent-Tick, in-game ohne externen Trigger)
-- ============================================================================
_G.__handlers["HourEvent"](nil)
check(log_has("event=reset round=0 status=ok reason=hour_tick count=1"),
    "3. Reset feuert genau einmal (reason=hour_tick count=1)")
check(count_logs("event=reset round=0 status=ok") == 1, "3. genau EIN Reset-Log")
check(RBB.reset.pending == false, "3. pending nach Reset geloescht")

-- Neuer Zustand: frische HQ-Placement-Phase, Economy 0, Wave-Timer 0.
check(RBB.round == 0, "3. Runde/Wave-Timer auf 0")
check(RBB.commenced == false, "3. Setup-Phase neu (commenced=false, Waves gehalten)")
check(RBB.hq.dead == false, "3. HQ nicht mehr tot")
check(RBB.hq.hp == RBB.hqCfg.hqHpStart, "3. HQ-HP zurueck auf Startwert")
check(RBB.hq.entity == nil, "3. HQ-Entity geloescht (Neuerkennung nach Platzierung)")
check(RBB.economy.pool == 0, "3. Economy-Pool auf 0")
check(RBB.economy.farmed == 0, "3. Economy farmed auf 0")
check(_G.__db["pool"] == 0, "3. Economy-Persistenz (DB) auf 0")
check(RBB.sendQueue.count == 0 and RBB.sendQueue.value == 0 and #RBB.sendQueue.units == 0,
    "3. Send-Queue geleert")
check(RBB.boost.pct == 0 and RBB.boost.buys == 0, "3. Boost zurueckgesetzt")
check(RBB.reveal.locked == false, "3. Reveal zurueckgesetzt")
check(log_has("event=economy_reset status=ok"), "3. Economy-Reset geloggt")

-- Session-Boundary: neue Session startet als commence pending/place_hq.
check(log_has("event=commence status=pending hint=place_hq"),
    "3. neue Session: event=commence status=pending hint=place_hq")
check(RBB.setupAnnounced == true, "3. Setup-Announce fuer die neue Session gesetzt")

-- ============================================================================
-- 4. Kein Restart-Loop / Idempotenz (viele weitere Ticks aendern nichts)
-- ============================================================================
for _ = 1, 20 do _G.__handlers["HourEvent"](nil) end
check(count_logs("event=reset round=0 status=ok") == 1, "4. keine weiteren Resets (Loop-Schutz)")
check(count_logs("event=match_end") == 1, "4. kein zweites match_end (Loop-Schutz)")
check(RBB.economy.pool == 0, "4. Economy bleibt 0")
check(RBB.round == 0, "4. Runde bleibt 0")

-- ============================================================================
-- 5. rb_reset ohne offenen Reset -> Skip (idempotent, KEIN Reset)
-- ============================================================================
_G.__commands["rb_reset"](nil)
check(log_has("event=reset status=skip reason=not_pending"),
    "5. rb_reset ohne pending -> status=skip")
check(count_logs("event=reset round=0 status=ok") == 1, "5. Skip erzeugt keinen Reset")
check(console_has("rb_reset: kein Reset offen"), "5. Konsolen-Hinweis bei Skip")

-- ============================================================================
-- 6. Zweite Niederlage -> genau EIN weiterer Reset, via rb_reset (IO-Kanal)
-- ============================================================================
_G.__commands["rb_hq"]({ "entity", "22222" })
RBB.round = 3
RBB.economy.pool = 500
_G.__commands["rb_hq"]({ "leak" }) -- Setup: nicht mehr toetend (hp 100 -> 90)
for _ = 1, 10 do _G.__handlers["EnteredTriggerEvent"](trigger_evt(9999)) end
check(count_logs("event=match_end") == 2, "6. zweite Niederlage -> zweites match_end")
check(RBB.reset.pending == true, "6. zweiter Reset vorgemerkt")
_G.__commands["rb_reset"]({ "hq_destroyed" })
check(log_has("event=reset round=0 status=ok reason=hq_destroyed count=2"),
    "6. rb_reset fuehrt den Reset aus (reason=hq_destroyed count=2)")
check(count_logs("event=reset round=0 status=ok") == 2, "6. jetzt genau ZWEI Resets")
check(RBB.round == 0 and RBB.economy.pool == 0, "6. neuer Zustand nach rb_reset")

-- Session-Boundary-Reihenfolge (erster Zyklus): match_end VOR reset VOR commence.
-- (commence pending gibt es auch beim Mod-Load; die neue Session muss NACH dem Reset liegen.)
local iEnd = log_index("event=match_end")
local iReset = log_index("event=reset round=0 status=ok")
local iCommence = iReset and log_index_after("event=commence status=pending hint=place_hq", iReset)
check(iEnd ~= nil and iReset ~= nil and iCommence ~= nil and iEnd < iReset and iReset < iCommence,
    "6. Session-Boundary: match_end -> reset -> commence (Reihenfolge)")

-- ============================================================================
-- 7. AFK-Ende wird NICHT autonom resettet (keine Schleife bei scharfem Zaehler)
-- ============================================================================
RBB.afkCfg.afkHourTicks = 1
_G.__commands["rb_hq"]({ "reset" })
RBB.commenced = false
RBB.hq.dead = false
local resetsBefore = count_logs("event=reset round=0 status=ok")
_G.__handlers["HourEvent"](nil) -- AFK-Schwelle=1 -> Match endet als AFK
check(log_has("event=afk_timeout status=match_end"), "7. AFK-Ende ausgeloest")
check(RBB.reset.pending == true and RBB.reset.auto == false,
    "7. AFK-Ende merkt Reset vor, aber NICHT auto")
_G.__handlers["HourEvent"](nil)
_G.__handlers["HourEvent"](nil)
check(count_logs("event=reset round=0 status=ok") == resetsBefore,
    "7. kein autonomer Reset nach AFK (keine Restart-Schleife)")

-- Operator kann den AFK-Fall explizit zuruecksetzen.
_G.__commands["rb_reset"]({ "afk_no_hq" })
check(count_logs("event=reset round=0 status=ok") == resetsBefore + 1,
    "7. expliziter rb_reset loest den AFK-Fall aus (genau einmal)")
check(RBB.commenced == false and RBB.round == 0, "7. AFK-Reset -> frische Setup-Phase")
RBB.afkCfg.afkHourTicks = 0

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

test('Issue #281: Round-Reset auf 0 nach Niederlage (fengari + Stub-Services)', () => {
    const failures = runLua(STUBS + modSource + '\n' + ASSERTIONS);
    assert.strictEqual(failures, 0, `Assertion-Fehler im Lua-Harness: ${failures}`);
});
