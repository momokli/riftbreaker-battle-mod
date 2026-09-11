'use strict';

// Statische Verifikation des Lua-Mods (rbbattle_autoexec.lua) fuer Issue #158
// (game-loop Setup-Phase / Commence-Flow): Spielstart OHNE Auto-HQ — Waves
// pausiert bis der Spieler das HQ selbst ueber das Build-Menue platziert.
// Geprueft wird (fengari + Stub-Services, wie win-condition.test.js):
//   1) Start-Announce "To commence the game, place the headquarter" (pending)
//      auf In-Game-Konsole + als [RBBATTLE]-Log-Zeile (Bridge -> Telegram 312 /
//      Web-Konsole).
//   2) Ohne HQ haelt der Wellenstart (dom_mananger.OnEnterSpawn) AN: kein
//      Spawn, kein Runden-Zaehler — aber kein debug_dom_pause (Spiel laeuft).
//   3) Held-Hinweis (event=commence status=held reason=no_hq) genau einmal.
//   4) HQ platziert -> FindEntitiesByType("headquarters") erkennt es (via
//      HourEvent-Tick, periodische Erkennung) -> event=commence status=ok.
//   5) Nach Commence laeuft der Wellenstart normal (Spawn + round=1).
//   6) Commence idempotent; manueller Fallback rb_hq entity commencet ebenfalls.
//
// Nicht live-verifizierbar (bleibt Operator-Lauf auf :6321): die tatsaechliche
// Feuerung des OnEnterSpawn-Wraps im DOM-State-Machine-Kontext und die echte
// HQ-Platzierung ueber das Build-Menue — s. Mod-Kopf + PR-Body.
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

// Stub-Services (Muster win-condition.test.js). FindEntitiesByType ist ueber
// _G.__findTypes steuerbar (0 Treffer = kein HQ -> Setup-Phase; 1 Treffer =
// HQ platziert -> Commence). WriteConsole wird nach _G.__console gespiegelt,
// damit die In-Game-Announce-Texte assertiert werden koennen.
const STUBS = `
-- ==== Stub-Services (fengari, Muster win-condition.test.js) ====
_G.__logs = {}
_G.__console = {}
_G.__handlers = {}
_G.__commands = {}
_G.__db = {}
_G.__waveStarts = 0
_G.__findTypes = {}
INVALID_ID = -1

LogService = {
    Log = function(self, msg) _G.__logs[#_G.__logs + 1] = msg end,
}
ConsoleService = {
    Write = function(self, msg) _G.__console[#_G.__console + 1] = msg end,
    RegisterCommand = function(self, name, fn) _G.__commands[name] = fn end,
}
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
        return db
    end,
}
DifficultyService = { GetCurrentDifficultyName = function(self) return "hard" end }
CampaignService = { GetCreaturesBaseDifficulty = function(self) return 5 end }
GuiService = {
    OpenPopup = function(self, ent, template, text) return true end,
}

-- DOM-Klasse: OnEnterSpawn (Wellenstart-Hook) + Timer-API (#23). Der Stub
-- zaehlt Aufrufe, damit "gehalten" (kein Original-Aufruf) messbar ist.
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
-- ==== Assertions (Issue #158: Setup-Phase / Commence-Flow) ====
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

local function console_has(sub)
    for _, m in ipairs(_G.__console) do
        if string.find(m, sub, 1, true) then return true end
    end
    return false
end

-- 1. Mod geladen, Setup-Phase aktiv (commenced=false) + Start-Announce.
check(log_has("event=mod_load version=0.34.2"), "mod_load version=0.34.2")
check(log_has("event=commence status=pending hint=place_hq"),
    "Start-Announce als Log: event=commence status=pending hint=place_hq")
check(console_has("To commence the game, place the headquarter"),
    "Start-Announce in-game: 'To commence the game, place the headquarter'")
check(_G.__handlers["HourEvent"] ~= nil, "HourEvent registriert (periodische HQ-Erkennung)")

-- 2. OHNE HQ haelt der Wellenstart AN (kein Spawn, kein Runden-Zaehler);
--    das Spiel selbst laeuft weiter (kein debug_dom_pause -> kein Pause-Pfad).
dom_mananger.OnEnterSpawn(dom_mananger, {})
check(_G.__waveStarts == 0, "Wellenstart OHNE HQ: Original-OnEnterSpawn NICHT aufgerufen")
check(not log_has("event=round round=1 status=start"), "kein Runden-Fortschritt ohne HQ")
check(log_has("event=commence status=held reason=no_hq"), "Held-Hinweis: commence status=held reason=no_hq")

-- 3. Held-Hinweis ist einmalig (Spam-Guard) bei weiterem Wellenstart.
dom_mananger.OnEnterSpawn(dom_mananger, {})
check(log_count("event=commence status=held reason=no_hq") == 1, "Held-Log genau einmal (Spam-Guard)")
check(_G.__waveStarts == 0, "weiterhin kein Wellenstart ohne HQ")

-- 4. HQ platziert -> periodische Erkennung ueber den HourEvent-Tick:
--    FindEntitiesByType("headquarters") = 1 Treffer -> Commence.
_G.__findTypes["headquarters"] = { 777 }
_G.__handlers["HourEvent"](nil)
check(log_has("event=hq_autodetect status=ok type=headquarters entity=777"),
    "HQ auto-erkannt (FindEntitiesByType headquarters)")
check(log_has("event=commence status=ok"), "Commence-Announce: event=commence status=ok")
check(console_has("Headquarter placed"),
    "Commence-Announce in-game: 'Headquarter placed — waves commencing'")

-- 5. Nach Commence laeuft der Wellenstart normal (Spawn + Runden-Zaehler).
dom_mananger.OnEnterSpawn(dom_mananger, {})
check(_G.__waveStarts == 1, "Wellenstart NACH Commence: Original genau 1x aufgerufen")
check(log_has("event=round round=1 status=start"), "round=1 status=start nach Commence")

-- 6. Commence idempotent: weiterer Tick bindet die Entity nicht neu und
--    loggt kein zweites status=ok.
_G.__findTypes["headquarters"] = { 888 }
_G.__handlers["HourEvent"](nil)
check(log_count("event=commence status=ok") == 1, "Commence idempotent (kein zweites status=ok)")
check(not log_has("event=hq_autodetect status=ok type=headquarters entity=888"),
    "bereits gebundene HQ-Entity wird nicht ueberschrieben")

print("FAILURES=" .. failures)
_G.__failures = failures
`;

const MANUAL_ASSERTIONS = `
-- ==== Assertions (Issue #158: manueller rb_hq entity-Fallback) ====
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

-- Manueller Fallback (rb_hq entity <id>, falls die Gruppe "headquarters" in
-- einer Spielversion anders heisst, s. #144): bindet die HQ-Entity UND
-- commencet damit ebenfalls (Waves starten).
check(log_has("event=commence status=pending hint=place_hq"), "pending vor manueller Zuordnung")
_G.__commands["rb_hq"]({ "entity", "12345" })
check(log_has("event=hq_entity status=ok entity=12345"), "rb_hq entity -> gebunden")
check(log_has("event=commence status=ok"), "manuelle HQ-Zuordnung -> commence status=ok")

dom_mananger.OnEnterSpawn(dom_mananger, {})
check(_G.__waveStarts == 1, "Wellenstart nach manueller Zuordnung: Original aufgerufen")
check(log_has("event=round round=1 status=start"), "round=1 nach manueller Zuordnung")

print("FAILURES=" .. failures)
_G.__failures = failures
`;

function runLua(assertions) {
    const L = lauxlib.luaL_newstate();
    lualib.luaL_openlibs(L);
    const luacode = to_luastring(STUBS + modSource + '\n' + assertions);
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

test('Issue #158: Setup-Phase — Waves pausiert bis HQ platziert (fengari + Stub-Services)', () => {
    const failures = runLua(ASSERTIONS);
    assert.strictEqual(failures, 0, `Assertion-Fehler im Lua-Harness: ${failures}`);
});

test('Issue #158: manueller rb_hq entity-Fallback commencet ebenfalls', () => {
    const failures = runLua(MANUAL_ASSERTIONS);
    assert.strictEqual(failures, 0, `Assertion-Fehler im Lua-Harness: ${failures}`);
});
