'use strict';

// Statische Verifikation der Konto-Farm-Quelle (Issue #242).
//
// Hintergrund: Die Resource-Event-API ist live als unlesbar bestaetigt —
// `ResourceObtainedEvent` traegt Entity + Resource-NAME, aber ueberhaupt keinen
// Betrag, deshalb faellt der Mod reproduzierbar auf pauschales Tick-Einkommen
// zurueck (`event=economy_source source=tick status=fallback
// reason=handler_errors`). Der Kontostand ist dagegen direkt lesbar
// (`PlayerService:GetResourceAmount(playerId, name)`), also bucht der Mod jetzt
// im HourEvent-Tick die DIFFERENZ zum letzten Snapshot.
//
// Gefahren wird das Verhalten (Log-Zeilen + gebuchte Werte), nicht die
// Implementierung: Seed-Tick bucht nichts, positives Delta bucht mit Faktor,
// Verbrauch bucht nicht, fehlende API degradiert auf den heutigen Stand.
//
// NICHT hier abbildbar (Player-Test, Issue #242 „Offener Punkt"): dass
// `GetResourceAmount` im echten Mod-/Duel-Kontext den erwarteten Wert liefert.
// Das ist RE-seitig belegt, aber nicht ausgefuehrt — hier wird der Getter
// gestubbt.

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const luaparse = require('luaparse');
const { lua, lauxlib, lualib, to_luastring, to_jsstring } = require('fengari');

const MOD_PATH = path.join(__dirname, '..', '..', 'mod', 'lua', 'rbbattle_autoexec.lua');
const modSource = fs.readFileSync(MOD_PATH, 'utf8');

// Stub-Services (Muster persistence.test.js). Der Kontostand kommt aus
// `_G.__account`; `_G.__accountApi = false` simuliert eine Engine ohne
// `GetResourceAmount` (der Live-Zustand vor diesem Fix).
const STUBS = `
_G.__logs = {}
_G.__handlers = {}
_G.__commands = {}
_G.__db = {}
_G.__accountReads = 0
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
-- #242: der eigentliche Prueflings-Getter. Fehlt er (accountApi = false),
-- laeuft der Mod in den pcall-Fehler und muss auf tick zurueckfallen.
if _G.__accountApi ~= false then
    PlayerService.GetResourceAmount = function(self, playerId, name)
        _G.__accountReads = _G.__accountReads + 1
        _G.__lastPlayerId = playerId
        local acc = _G.__account or {}
        return acc[name]
    end
end
DifficultyService = { GetCurrentDifficultyName = function(self) return "hard" end }
CampaignService = { GetCreaturesBaseDifficulty = function(self) return 5 end }
GuiService = { OpenPopup = function(self, ent, template, text) return true end }

dom_mananger = {
    OnEnterSpawn = function(self, state) end,
    GetPrepareSpawnTime = function(self) return 300 end,
}

function RegisterGlobalEventHandler(name, fn)
    _G.__handlers[name] = fn
end
`;

const HELPERS = `
local failures = 0
local __lines = {}
local function check(cond, msg)
    if cond then
        __lines[#__lines + 1] = "PASS: " .. msg
    else
        __lines[#__lines + 1] = "FAIL: " .. msg
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
-- Ein HourEvent-Tick mit dem angegebenen Kontostand.
local function tick(account)
    if account ~= nil then _G.__account = account end
    _G.__handlers["HourEvent"](nil)
end
`;

const FOOTER = `
_G.__failures = failures
_G.__report = table.concat(__lines, "\\n")
`;

function runLua(prelude, assertions) {
    const L = lauxlib.luaL_newstate();
    lualib.luaL_openlibs(L);
    const code = (prelude || '') + STUBS + modSource + '\n' + HELPERS + assertions + FOOTER;
    const loadStatus = lauxlib.luaL_loadstring(L, to_luastring(code));
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
    lua.lua_pop(L, 1);
    lua.lua_getglobal(L, to_luastring('__report'));
    const raw = lua.lua_tostring(L, -1);
    const report = raw ? to_jsstring(raw) : '';
    lua.lua_close(L);
    return { failures, report };
}

function runCase(prelude, assertions) {
    const { failures, report } = runLua(prelude, assertions);
    assert.strictEqual(failures, 0, 'Assertion-Fehler im Lua-Harness:\n' + report);
}

test('Lua-Syntax (luaparse, Lua 5.1)', () => {
    assert.doesNotThrow(() => luaparse.parse(modSource),
        'mod/lua/rbbattle_autoexec.lua muss gültiges Lua 5.1 sein');
});

// ---------------------------------------------------------------------------
// 1) Seed: der erste Tick liest nur ein und bucht bewusst nichts.
// ---------------------------------------------------------------------------

test('#242 (1): erster Tick seedet den Snapshot und bucht NICHTS', () => {
    runCase(null, `
tick({ carbonium = 5000, steel = 800 })
check(log_has("event=economy_source source=account status=seed"),
    "status=seed beim ersten Tick")
check(log_has("event=economy_source source=account status=active"),
    "Konto-Quelle wird aktiv gesperrt")
check(not log_has("event=economy_farm source=account"),
    "KEINE Farm-Buchung beim Seed (Startbestand ist nicht gefarmt)")
check(RBB.economy.farmed == 0, "farmed bleibt 0 (ist " .. tostring(RBB.economy.farmed) .. ")")
check(not log_has("event=economy_farm source=tick"),
    "kein pauschales Tick-Einkommen mehr, sobald das Konto lesbar ist")
check(_G.__lastPlayerId == 0, "gelesen wird mit playerId=0 wie im Rest der Mod")
`);
});

// ---------------------------------------------------------------------------
// 2) Positives Delta -> gebucht, mit dem konfigurierten Faktor.
// ---------------------------------------------------------------------------

test('#242 (2): Zuwachs wird als Delta gebucht, Faktor greift', () => {
    runCase(null, `
tick({ carbonium = 5000, palladium = 100 })
-- +250 carbonium (Faktor 1) und +10 palladium (Faktor 2) -> value 250 + 20.
tick({ carbonium = 5250, palladium = 110 })
check(log_has("event=economy_farm source=account resource=carbonium amount=250 value=250"),
    "carbonium: amount=250 value=250 (Faktor 1)")
check(log_has("event=economy_farm source=account resource=palladium amount=10 value=20"),
    "palladium: amount=10 value=20 (Faktor 2)")
check(RBB.economy.farmed == 270,
    "farmed = 250 + 20 = 270 (ist " .. tostring(RBB.economy.farmed) .. ")")
check(RBB.economy.resources["carbonium"] == 250, "Ressourcen-Konto carbonium=250")

-- Dritter Tick ohne Aenderung darf NICHTS nachbuchen (Delta 0).
tick({ carbonium = 5250, palladium = 110 })
check(log_count("event=economy_farm source=account") == 2,
    "unveraenderter Kontostand bucht nicht nach (Buchungen: "
    .. tostring(log_count("event=economy_farm source=account")) .. ")")
check(RBB.economy.farmed == 270, "farmed unveraendert bei 270")
`);
});

// ---------------------------------------------------------------------------
// 3) Verbrauch (sinkender Kontostand) ist kein negativer Farm-Ertrag.
// ---------------------------------------------------------------------------

test('#242 (3): Verbrauch bucht nicht, zieht den Snapshot aber nach', () => {
    runCase(null, `
tick({ carbonium = 1000 })
tick({ carbonium = 400 })   -- 600 verbaut
check(not log_has("event=economy_farm source=account"),
    "Bauen erzeugt keine Farm-Buchung")
check(RBB.economy.farmed == 0, "farmed bleibt 0 nach reinem Verbrauch")

-- Entscheidend: der Snapshot ist nachgezogen. Wiederaufbauen auf 1000 darf
-- nur die 600 NEU gefarmten zaehlen, nicht die Differenz zum alten Hoechststand.
tick({ carbonium = 1000 })
check(log_has("event=economy_farm source=account resource=carbonium amount=600 value=600"),
    "nach Verbrauch zaehlt nur der echte Zuwachs (600), nicht mehr")
check(RBB.economy.farmed == 600, "farmed = 600 (ist " .. tostring(RBB.economy.farmed) .. ")")
`);
});

// ---------------------------------------------------------------------------
// 4) Fehlende API -> heutiges Verhalten (Tick-Fallback), kein Fehler.
// ---------------------------------------------------------------------------

test('#242 (4): ohne GetResourceAmount bleibt es beim Tick-Einkommen', () => {
    runCase('_G.__accountApi = false\n', `
tick(nil)
tick(nil)
tick(nil)
check(log_has("event=economy_source source=account status=unavailable reason=no_readable_resource"),
    "Konto-Quelle wird nach maxAccountErrors aufgegeben")
check(not log_has("event=economy_farm source=account"), "keine Konto-Buchung ohne API")
check(log_has("event=economy_farm source=tick resource=hour_tick"),
    "Tick-Einkommen laeuft weiter (heutiges Verhalten, kein Rueckschritt)")
check(RBB.economy.farmed > 0, "Economy steht nicht still (farmed="
    .. tostring(RBB.economy.farmed) .. ")")

-- Aufgegeben heisst aufgegeben: kein pcall-Geknatter bei jedem weiteren Tick.
local readsBefore = _G.__accountReads
tick(nil)
check(_G.__accountReads == readsBefore,
    "nach dem Aufgeben wird das Konto nicht weiter abgefragt")
`);
});

// ---------------------------------------------------------------------------
// 5) Faellt das Konto NACH dem Sperren aus, darf die Economy nicht stillstehen.
// ---------------------------------------------------------------------------

test('#242 (5): Konto-Ausfall nach dem Sperren faellt auf tick zurueck', () => {
    runCase(null, `
tick({ carbonium = 100 })          -- Seed, sperrt auf source=account
check(RBB.economy.source == "account", "Quelle ist account")

-- Ab jetzt liefert die API nichts mehr (leeres Konto = kein lesbarer Wert).
tick({})
tick({})
tick({})
check(log_has("event=economy_source source=account status=unavailable"),
    "Konto-Quelle als unavailable markiert")
check(log_has("event=economy_source source=tick status=fallback reason=account_lost"),
    "Sperre wird auf tick umgelegt (sonst stuende die Economy still)")
check(RBB.economy.source == "tick", "Quelle ist wieder tick (ist "
    .. tostring(RBB.economy.source) .. ")")

local farmedBefore = RBB.economy.farmed
tick({})
check(RBB.economy.farmed > farmedBefore,
    "Tick-Einkommen laeuft wieder (farmed waechst)")
`);
});

// ---------------------------------------------------------------------------
// 6) Kein Doppelzaehlen gegen die bestehenden Event-Quellen.
// ---------------------------------------------------------------------------

test('#242 (6): eine bereits gesperrte Event-Quelle bleibt unangetastet', () => {
    runCase(null, `
-- Ein lesbares Farm-Event sperrt wie bisher auf resource_obtained.
local evt = {}
function evt:GetResourceName() return "carbonium" end
function evt:GetAmount() return 300 end
_G.__handlers["ResourceObtainedEvent"](evt)
check(RBB.economy.source == "resource_obtained", "Event-Quelle ist gesperrt")

-- Der Konto-Tick darf daneben NICHT zusaetzlich buchen.
tick({ carbonium = 9999 })
tick({ carbonium = 99999 })
check(not log_has("event=economy_source source=account status=seed"),
    "Konto-Quelle wird bei gesperrter Event-Quelle gar nicht erst angefasst")
check(not log_has("event=economy_farm source=account"), "kein Doppel-Zaehlen")
check(RBB.economy.farmed == 300, "farmed bleibt bei 300 (ist "
    .. tostring(RBB.economy.farmed) .. ")")
`);
});

// ---------------------------------------------------------------------------
// 7) Der Event-Fallback von #242 bleibt, wie er live bestaetigt wurde.
// ---------------------------------------------------------------------------

test('#242 (7): unlesbare Events fallen weiterhin auf tick zurueck', () => {
    runCase('_G.__accountApi = false\n', `
-- Genau der Live-Fall: das Event feuert, traegt aber keinen Betrag.
local evt = { Resource = "carbonium" }
_G.__handlers["ResourceObtainedEvent"](evt)
_G.__handlers["ResourceObtainedEvent"](evt)
_G.__handlers["ResourceObtainedEvent"](evt)
check(log_has("event=economy_source source=tick status=fallback reason=handler_errors"),
    "handler_errors-Fallback unveraendert (live bestaetigtes Verhalten)")
check(log_has("event_unreadable"), "err=event_unreadable unveraendert")
`);
});

// ---------------------------------------------------------------------------
// 8) Die Live-Reihenfolge aus #242: erst der handler_errors-Fallback auf tick,
//    DANN der erste HourEvent. Genau hier waere der Fix sonst wirkungslos.
// ---------------------------------------------------------------------------

test('#242 (8): Konto-Quelle greift auch nach dem handler_errors-Fallback', () => {
    runCase(null, `
-- Reihenfolge wie im Live-Log: die Resource-Events feuern zuerst und sind
-- unlesbar -> Sperre auf tick, BEVOR das Konto je gelesen wurde.
local evt = { Resource = "carbonium" }
_G.__handlers["ResourceObtainedEvent"](evt)
_G.__handlers["ResourceObtainedEvent"](evt)
_G.__handlers["ResourceObtainedEvent"](evt)
check(RBB.economy.source == "tick", "Vorbedingung: auf tick gesperrt (ist "
    .. tostring(RBB.economy.source) .. ")")

-- Jetzt erst der erste HourEvent. Der Tick-Platzhalter darf das echte
-- Konto-Tracking NICHT dauerhaft aussperren.
tick({ carbonium = 2000 })
check(log_has("event=economy_source source=account status=seed"),
    "Konto-Quelle wird trotz tick-Sperre angelaufen")
check(RBB.economy.source == "account",
    "echtes Tracking loest den Platzhalter ab (ist " .. tostring(RBB.economy.source) .. ")")

tick({ carbonium = 2400 })
check(log_has("event=economy_farm source=account resource=carbonium amount=400 value=400"),
    "Zuwachs wird gebucht")
check(not log_has("event=economy_farm source=tick"),
    "kein pauschales Tick-Einkommen mehr neben dem echten Tracking")
`);
});
