'use strict';

// Statische Verifikation des Lua-Mods (rbbattle_autoexec.lua) ohne Spiel:
//   1) Syntax-Check via luaparse (Lua-5.1-Grammatik, wie CI-Pipeline),
//   2) Ausfuehrung in fengari-Lua-VM mit Stub-Services und Assertions auf die
//      [RBBATTLE]-Log-Zeilen (die Vertragsflaeche fuer Bridge/Server).
//
// Deckt Issue #28 (Win-Condition) ab: Leak-Erkennung (EnteredTriggerEvent),
// HQ-HP-Tracking, HQ-Tod (RespawnFailedEvent) -> Match-Ende. Reine Logik —
// nicht live-verifizierbare Teile (Trigger-Zone-Asset, HQ-Entity-Lookup,
// Sieg-Screen-UI) sind in mod/lua/rbbattle_autoexec.lua + mod/README.md als
// OFFEN dokumentiert.
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
-- #144: _G.__findGroups[group] steuert, was FindEntitiesByGroup pro Gruppe
-- liefert (Test setzt das gezielt); Default = leere Liste (kein Treffer).
_G.__findGroups = {}
FindService = {
    FindEntitiesByGroup = function(self, g) return _G.__findGroups[g] or {} end,
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

-- Hilfs-Event: eintretende Entity (evt:GetEntity()) + optionale Team-Id
-- (evt:GetTeamId()); simuliert EnteredTriggerEvent im Stub.
local function trigger_evt(entity, teamId)
    local evt = { entity = entity }
    function evt:GetEntity() return self.entity end
    if teamId ~= nil then
        function evt:GetTeamId() return teamId end
    end
    return evt
end

-- 1. Mod geladen, Version 0.27.0, HQ initialisiert.
check(log_has("event=mod_load version=0.27.0"), "mod_load version=0.27.0")
check(log_has("hq_hp=100 hq_dead=false"), "mod_load enthaelt hq_hp=100 hq_dead=false")
check(_G.__handlers["EnteredTriggerEvent"] ~= nil, "EnteredTriggerEvent registriert")
check(_G.__handlers["RespawnFailedEvent"] ~= nil, "RespawnFailedEvent registriert")
check(_G.__commands["rb_hq"] ~= nil, "rb_hq registriert")

-- 2. Issue #143: OHNE gebundene HQ-Entity bleibt EnteredTriggerEvent inaktiv
--    (kein False-Positive-Sieg durch fremde/generische Map-Trigger).
_G.__handlers["EnteredTriggerEvent"](nil)
check(log_has("event=hq_leak status=skip reason=no_hq_entity"), "2. Leak OHNE HQ-Entity -> uebersprungen")
check(not log_has("event=leak damage=10 hp_before=100"), "kein Leak-Log ohne gebundene Entity")
check(log_has("hq_hp=100 hq_dead=false"), "HQ-HP unveraendert (100) nach uebersprungenem Leak")

-- 3. HQ-Entity zuordnen (Issue #144); danach greift die Leak-Erkennung NUR
--    fuer feindliche Kreaturen (Zone-/Team-Filter #152).
_G.__commands["rb_hq"]({ "entity", "12345" })
check(log_has("event=hq_entity status=ok entity=12345"), "rb_hq entity")

-- 3a. Trigger OHNE lesbare Entity -> uebersprungen (kein False-Positive).
_G.__handlers["EnteredTriggerEvent"](nil)
check(log_has("event=hq_leak status=skip reason=no_trigger_entity"),
    "3a. Trigger ohne Entity -> skip no_trigger_entity")
check(not log_has("event=leak damage=10 hp_before=100"), "kein Leak ohne Trigger-Entity")

-- 3b. Eigene HQ-Entity als Trigger-Entity -> uebersprungen.
_G.__commands["rb_hq"]({ "reset" })
_G.__commands["rb_hq"]({ "entity", "12345" })
_G.__handlers["EnteredTriggerEvent"](trigger_evt(12345))
check(log_has("event=hq_leak status=skip reason=own_hq"),
    "3b. eigene HQ-Entity -> skip own_hq")

-- 3c. Eigener Mech (GetPlayerControlledEnt=1) -> uebersprungen.
_G.__commands["rb_hq"]({ "reset" })
_G.__commands["rb_hq"]({ "entity", "12345" })
_G.__handlers["EnteredTriggerEvent"](trigger_evt(1))
check(log_has("event=hq_leak status=skip reason=own_team"),
    "3c. eigener Mech -> skip own_team")

-- 3d. Eigene Team-Id (GetTeamId==1) -> uebersprungen.
_G.__commands["rb_hq"]({ "reset" })
_G.__commands["rb_hq"]({ "entity", "12345" })
_G.__handlers["EnteredTriggerEvent"](trigger_evt(9999, 1))
check(log_has("event=hq_leak status=skip reason=own_team"),
    "3d. eigene Team-Id 1 -> skip own_team")

-- 3e. Feindliche Kreatur (fremde Entity, keine eigene Team-Id) -> Leak greift.
_G.__commands["rb_hq"]({ "reset" })
_G.__commands["rb_hq"]({ "entity", "12345" })
_G.__handlers["EnteredTriggerEvent"](trigger_evt(9999))
check(log_has("event=leak damage=10 hp_before=100 hp=90"), "3e. feindliche Kreatur -> hp 100->90")
check(log_has("event=hq_hp hp=90 dead=false"), "Report event=hq_hp hp=90")

-- 4. HQ-Tod durch Leaks (10x10 = 100 -> hp 0 -> Match-Ende).
_G.__commands["rb_hq"]({ "reset" })
_G.__commands["rb_hq"]({ "entity", "12345" })
for _ = 1, 10 do _G.__handlers["EnteredTriggerEvent"](trigger_evt(9999)) end
check(log_has("event=leak damage=10 hp_before=10 hp=0"), "10. Leak -> hp 10->0")
check(log_has("event=hq_dead status=match_end hp=0"), "event=hq_dead bei HP<=0")
check(log_has("event=match_end reason=hq_destroyed winner=opponent"), "event=match_end")
check(count_logs("event=match_end") == 1, "match_end genau einmal")

-- 5. Idempotenz: weiterer Leak nach Tod aendert nichts.
_G.__handlers["EnteredTriggerEvent"](trigger_evt(9999))
check(count_logs("event=match_end") == 1, "kein zweites match_end (idempotent)")

-- 6. RespawnFailedEvent-Pfad (HQ-Tod-Kette) mit zugeordneter Entity.
_G.__commands["rb_hq"]({ "reset" })
check(log_has("event=hq_reset status=ok hp=100"), "rb_hq reset")
_G.__commands["rb_hq"]({ "entity", "12345" })
local hqEvt = { entity = 12345 }
function hqEvt:GetEntity() return self.entity end
_G.__handlers["RespawnFailedEvent"](hqEvt)
check(count_logs("event=match_end") == 2, "RespawnFailedEvent(HQ) -> match_end")

-- 7. RespawnFailedEvent eines ANDEREN Gebaeudes -> kein Match-Ende.
_G.__commands["rb_hq"]({ "reset" })
_G.__commands["rb_hq"]({ "entity", "12345" })
local otherEvt = { entity = 99999 }
function otherEvt:GetEntity() return self.entity end
_G.__handlers["RespawnFailedEvent"](otherEvt)
check(count_logs("event=match_end") == 2, "RespawnFailedEvent(andere Entity) -> KEIN match_end")

-- 8. RespawnFailedEvent ohne zugeordnete Entity -> nur Hinweis, kein Ende.
_G.__commands["rb_hq"]({ "reset" })
_G.__handlers["RespawnFailedEvent"](hqEvt)
check(count_logs("event=match_end") == 2, "RespawnFailedEvent ohne Entity-Zuordnung -> KEIN match_end")
check(log_has("event=hq_respawn status=unmatched"), "Hinweis event=hq_respawn status=unmatched")

-- 9. Reset setzt auch den Leak-Skip-Hinweis zurueck (erneut ohne Entity -> skip).
_G.__commands["rb_hq"]({ "reset" })
_G.__handlers["EnteredTriggerEvent"](nil)
check(count_logs("event=hq_leak status=skip reason=no_hq_entity") == 2, "Leak-Skip-Hinweis nach Reset erneut geloggt")

-- 10. Issue #144: HqAutoDetectEntity -- automatische HQ-Entity-Bindung.
check(_G.__handlers["PlayerInitializedEvent"] ~= nil, "PlayerInitializedEvent registriert")

-- 10a. Gruppe "headquarters" liefert 0 Treffer -> not_found, entity bleibt nil.
_G.__handlers["PlayerInitializedEvent"](nil)
check(log_has("event=hq_autodetect status=not_found candidates=headquarters"),
    "10a. Autodetect ohne Treffer -> status=not_found")
_G.__commands["rb_hq"]({ "leak" })
check(log_has("event=hq_leak status=skip reason=no_hq_entity"),
    "10a. HQ weiterhin ungebunden -> Leak bleibt uebersprungen")

-- 10b. Erneuter Versuch (z.B. rb_wave) ohne Treffer -> KEIN zweites Log
--      (Spam-Guard, Reset setzt ihn zurueck).
_G.__commands["rb_wave"]({ "1" })
check(count_logs("event=hq_autodetect status=not_found") == 1,
    "10b. Wiederholter Fehlversuch loggt nicht erneut (Guard)")

-- 10c. Mehrdeutiger Treffer (2 Entities in der Gruppe) -> kein Rateschuss,
--      Entity bleibt ungebunden.
_G.__commands["rb_hq"]({ "reset" })
_G.__findGroups["headquarters"] = { 501, 502 }
_G.__handlers["PlayerInitializedEvent"](nil)
check(not log_has("event=hq_autodetect status=ok group=headquarters entity=501")
    and not log_has("event=hq_autodetect status=ok group=headquarters entity=502"),
    "10c. Mehrdeutiger Treffer (2 Entities) bindet KEINE der beiden Entities")
_G.__commands["rb_hq"]({ "leak" })
check(log_has("event=hq_leak status=skip reason=no_hq_entity"),
    "10c. Mehrdeutiger Treffer (2 Entities) bindet NICHT -> Leak weiterhin uebersprungen")

-- 10d. Genau EIN Treffer -> automatische Bindung, Leak greift ab sofort.
_G.__commands["rb_hq"]({ "reset" })
_G.__findGroups["headquarters"] = { 777 }
_G.__handlers["PlayerInitializedEvent"](nil)
check(log_has("event=hq_autodetect status=ok group=headquarters entity=777"),
    "10d. Genau ein Treffer -> automatische Bindung")
_G.__handlers["EnteredTriggerEvent"](trigger_evt(9999))
check(log_has("event=leak damage=10 hp_before=100 hp=90"),
    "10d. Leak greift nach automatischer Bindung sofort (kein rb_hq entity noetig)")

-- 10e. Bereits gebundene Entity wird NICHT ueberschrieben (auch bei erneutem Aufruf).
_G.__findGroups["headquarters"] = { 999 }
_G.__handlers["PlayerInitializedEvent"](nil)
check(not log_has("event=hq_autodetect status=ok group=headquarters entity=999"),
    "10e. Bereits gebundene Entity wird nicht durch einen zweiten Treffer ersetzt")

_G.__findGroups["headquarters"] = nil -- aufraeumen fuer nachfolgende Tests

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

test('Win-Condition: Leak, HQ-HP, HQ-Tod (fengari + Stub-Services)', () => {
    const failures = runLua(STUBS + modSource + '\n' + ASSERTIONS);
    assert.strictEqual(failures, 0, `Assertion-Fehler im Lua-Harness: ${failures}`);
});
