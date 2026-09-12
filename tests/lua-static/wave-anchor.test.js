'use strict';

// Anti-Regressions-Gate fuer den Kern-Spawnpfad (Issue #288), OHNE Spieler.
//
// Hintergrund (Issue #288): `POST /wave` meldete `exec_result.ok=true`, aber im
// Spiel entstanden 0 Kreaturen. `ok:true` belegt nur, dass
// `ConsoleService::ExecuteCommand` lief — NICHT, dass gespawnt wurde. Bis hier
// stubbte JEDER Harness im Repo `FindService` als vorhandenes `table`
// (`FindEntitiesByGroup -> {100}` / `{}`); der `FindService == nil`-Fall und der
// `no_player`-Pfad waren nie gefahren, und "status=done impliziert spawned>0"
// war nirgends geprueft. Dieser Test faehrt genau diese Faelle.
//
// Anker-Aufloesung im Mod (SpawnWave): border -> mission -> mech (letzter
// Fallback, braucht einen Spieler). Geprueft wird verhaltensbasiert auf die
// `[RBBATTLE]`-Log-Zeilen + die tatsaechlichen `SpawnEntity`-Aufrufe:
//
//   A) kein Anker, kein Spieler  -> status=no_player, KEIN status=done, 0 Spawns
//   B) Rand-Spawner, kein Spieler-> status=done spawned=8 anchor=border
//                                   (der Beleg: die Kernfunktion spawnt OHNE Player)
//   C) nur Mech                  -> status=done spawned=8 anchor=mech
//   D) Anker da, Spawn schlaegt fehl -> status=no_spawns spawned=0, KEIN status=done
//
// OHNE Player beweisbar. Was NICHT hierher gehoert (Player-Test Momo/Matheo,
// OFFEN): dass die Welle im headless Dedicated-Server sichtbar spawnt.

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const luaparse = require('luaparse');
const { lua, lauxlib, lualib, to_luastring, to_jsstring } = require('fengari');

const MOD_PATH = path.join(__dirname, '..', '..', 'mod', 'lua', 'rbbattle_autoexec.lua');
const modSource = fs.readFileSync(MOD_PATH, 'utf8');

// ---------------------------------------------------------------------------
// Lua-Harness: Stubs + Mod + Assertions in EINEM Chunk (Muster wave-presets).
// ---------------------------------------------------------------------------

// optionale Bausteine ---------------------------------------------------------
function findServiceStub(groups, points) {
  // groups: Lua-Ausdruck fuer FindEntitiesByGroup; points: fuer FindPlayerSpawnPoints
  return `
FindService = {
    FindEntitiesByGroup = function(self, g) return ${groups} end,
    FindPlayerSpawnPoints = function(self) return ${points} end,
}
`;
}

function runLua(code) {
  const L = lauxlib.luaL_newstate();
  lualib.luaL_openlibs(L);
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
  const t = lua.lua_tostring(L, -1);
  const report = t ? to_jsstring(t) : '';
  lua.lua_close(L);
  return { failures, report };
}

// Gemeinsame Stub-Services (Muster wave-presets/vollkette). `opts` steuert die
// variablen Teile: FindService, MapGenerator, Mech und das Spawn-Ergebnis.
function buildStubs(opts) {
  const o = opts || {};
  const mech = 'mech' in o ? o.mech : 'nil';           // GetPlayerControlledEnt(0)
  const spawn = 'spawn' in o ? o.spawn : '1';          // Rueckgabe von SpawnEntity
  const find = 'find' in o ? o.find : '';
  const mapgen = 'mapgen' in o ? o.mapgen : 'MapGenerator = nil';
  return `
-- ==== Stub-Services (fengari) ====
_G.__logs = {}
_G.__handlers = {}
_G.__commands = {}
_G.__db = {}
_G.__spawnCalls = 0
INVALID_ID = -1

LogService = {
    Log = function(self, msg) _G.__logs[#_G.__logs + 1] = msg end,
}
ConsoleService = {
    Write = function(self, msg) end,
    RegisterCommand = function(self, name, fn) _G.__commands[name] = fn end,
}
${find}
${mapgen}
ResourceManager = { GetBlueprint = function(self, bp) return true end }
EnvironmentService = { GetTerrainHeight = function(self, pos) return 0 end }
EntityService = {
    GetName = function(self, e) return "" end,
    GetPosition = function(self, e) return { x = 0, y = 0, z = 0 } end,
    SpawnEntity = function(self, ...)
        _G.__spawnCalls = _G.__spawnCalls + 1
        return ${spawn}
    end,
}
PlayerService = {
    GetPlayerControlledEnt = function(self, i) return ${mech} end,
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
}

// Assertion-Footer: check() sammelt PASS/FAIL in __report, zaehlt __failures.
function header() {
  return `
-- ==== Assertions ====
local __lines = {}
local failures = 0
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
check(_G.__commands["rb_wave"] ~= nil, "rb_wave registriert")
_G.__commands["rb_wave"]({ "3" })
`;
}

function footer() {
  return `
_G.__failures = failures
_G.__report = table.concat(__lines, "\\n")
`;
}

// ---------------------------------------------------------------------------
// Statisch: die Falsch-Gruen-Semantik ist im Code verankert
// ---------------------------------------------------------------------------

test('Syntax: mod/lua/rbbattle_autoexec.lua ist Lua 5.1 (luaparse)', () => {
  luaparse.parse(modSource, { luaVersion: '5.1' });
});

test('Fix (#288): status=done nur bei spawned>0, sonst no_spawns', () => {
  // Der Spawn-Wellen-Abschluss darf "status=done" ohne Spawns nicht mehr
  // emittieren (genau das war die Falsch-Gruen-Zeile im Log).
  const idxDone = modSource.indexOf('event=wave level=%d status=done spawned=%d');
  const idxNo = modSource.indexOf('event=wave level=%d status=no_spawns spawned=0');
  const idxGuard = modSource.indexOf('if spawned > 0 then');
  assert.ok(idxGuard > 0, 'Guard "if spawned > 0 then" um die done-Zeile vorhanden');
  assert.ok(idxDone > idxGuard, 'status=done-Zeile liegt im spawned>0-Zweig');
  assert.ok(idxNo > idxGuard, 'status=no_spawns-Zeile liegt im else-Zweig');
  // Dokumentation der Semantik im Kopfkommentar.
  assert.ok(modSource.includes('#288'), 'Kopfkommentar referenziert #288');
});

// ---------------------------------------------------------------------------
// A) Kein Anker, kein Spieler (der Live-Fall) -> ehrlich status=no_player
// ---------------------------------------------------------------------------

test('A) kein Anker + kein Spieler -> status=no_player, kein status=done, 0 Spawns', () => {
  const stubs = buildStubs({
    mech: 'nil',
    find: '',                       // FindService fehlt (nil)
    mapgen: 'MapGenerator = nil',
  });
  const assertions = `
check(log_has("event=spawn skip reason=find_service_missing"), "find_service_missing geloggt")
check(log_has("event=wave level=3 status=no_border_spawners warn=fallback_mech"),
    "kein Rand-Spawner -> fallback_mech angekuendigt")
check(log_has("event=wave level=3 status=no_player"), "status=no_player")
check(not log_has("event=wave level=3 status=done"), "KEIN status=done ohne Spawns")
check(not log_has("status=no_spawns"), "kein no_spawns (frueher Return im Mech-Fallback)")
check(_G.__spawnCalls == 0, "kein einziger SpawnEntity-Aufruf (spawnCalls=" .. tostring(_G.__spawnCalls) .. ")")
`;
  const { failures, report } = runLua(stubs + modSource + header() + assertions + footer());
  assert.strictEqual(failures, 0, 'Harness-Fehler:\n' + report);
});

// ---------------------------------------------------------------------------
// B) Rand-Spawner vorhanden, KEIN Spieler -> Kernfunktion spawnt trotzdem
// ---------------------------------------------------------------------------

test('B) Rand-Spawner ohne Spieler -> status=done spawned=8 anchor=border', () => {
  const stubs = buildStubs({
    mech: 'nil',                       // kein Spieler
    find: findServiceStub('{ 100 }', '{}'),
    mapgen: 'MapGenerator = nil',
  });
  const assertions = `
check(log_has("event=wave level=3 status=start"), "status=start")
check(log_has("event=wave level=3 status=done spawned=8 skipped=0 anchor=border"),
    "status=done spawned=8 skipped=0 anchor=border")
check(not log_has("status=no_player"), "kein no_player (Rand-Anker reicht)")
check(_G.__spawnCalls == 8, "8 SpawnEntity-Aufrufe (spawnCalls=" .. tostring(_G.__spawnCalls) .. ")")
check(log_count("event=spawn ok blueprint=units/ground/baxmoth") == 5, "5x baxmoth")
check(log_count("event=spawn ok blueprint=units/ground/artigian") == 2, "2x artigian")
check(log_count("event=spawn ok blueprint=units/ground/canceroth") == 1, "1x canceroth")
`;
  const { failures, report } = runLua(stubs + modSource + header() + assertions + footer());
  assert.strictEqual(failures, 0, 'Harness-Fehler:\n' + report);
});

// ---------------------------------------------------------------------------
// C) Nur Mech-Anker (kein Rand-/Missions-Spawner) -> letzter Fallback greift
// ---------------------------------------------------------------------------

test('C) nur Mech -> status=done spawned=8 anchor=mech', () => {
  const stubs = buildStubs({
    mech: '1',                         // Spieler-Mech vorhanden
    find: findServiceStub('{}', '{}'), // leere Gruppen + keine Missionspunkte
    mapgen: 'MapGenerator = nil',
  });
  const assertions = `
check(log_has("event=wave level=3 status=done spawned=8 skipped=0 anchor=mech"),
    "status=done spawned=8 anchor=mech")
check(_G.__spawnCalls == 8, "8 SpawnEntity-Aufrufe (spawnCalls=" .. tostring(_G.__spawnCalls) .. ")")
`;
  const { failures, report } = runLua(stubs + modSource + header() + assertions + footer());
  assert.strictEqual(failures, 0, 'Harness-Fehler:\n' + report);
});

// ---------------------------------------------------------------------------
// D) Anker vorhanden, aber Spawn schlaegt fehl -> status=no_spawns (kein done)
// ---------------------------------------------------------------------------

test('D) Anker da, SpawnEntity liefert nil -> status=no_spawns spawned=0', () => {
  const stubs = buildStubs({
    mech: 'nil',
    find: findServiceStub('{ 100 }', '{}'),
    mapgen: 'MapGenerator = nil',
    spawn: 'nil',                      // SpawnEntity schlaegt fuer jede Kreatur fehl
  });
  const assertions = `
check(log_has("event=wave level=3 status=no_spawns spawned=0"), "status=no_spawns spawned=0")
check(not log_has("event=wave level=3 status=done"), "KEIN status=done, obwohl Anker da waren")
check(_G.__spawnCalls == 8, "8 Spawn-Versuche (spawnCalls=" .. tostring(_G.__spawnCalls) .. ")")
check(log_count("event=spawn failed blueprint=") == 8, "8x event=spawn failed")
`;
  const { failures, report } = runLua(stubs + modSource + header() + assertions + footer());
  assert.strictEqual(failures, 0, 'Harness-Fehler:\n' + report);
});
