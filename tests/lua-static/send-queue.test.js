'use strict';

// Statische Verifikation des Lua-Mods (rbbattle_autoexec.lua) fuer Issue #25
// (Send-Queue & Shop-HUD): Custom-UI-Shop mit Tier-Struktur + Boss-Tier,
// rb_buy_wave (Kauf-Hook), Send-Queue und Flush am Wellenstart (Boost der
// naechsten Naturwelle). Wie win-condition.test.js: 1) luaparse-Syntax-Check
// (Lua 5.1), 2) fengari-Lua-VM mit Stub-Services + Assertions auf die
// [RBBATTLE]-Log-Zeilen (Vertragsflaeche fuer Bridge/Server).
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
// Ergaenzt gegenueber win-condition.test.js um dom_mananger (Wellenstart-Hook)
// und GuiService (Custom-UI-Popup). FindService liefert EINEN Rand-Spawner,
// damit der Send-Queue-Flush ueber den border-Pfad (#26) laeuft.
const STUBS = `
-- ==== Stub-Services (fengari, Muster win-condition.test.js) ====
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
    OpenPopup = function(self, ent, template, text)
        _G.__popup = { ent = ent, template = template, text = text }
        return true
    end,
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
-- ==== Assertions (Issue #25: Send-Queue & Shop-HUD) ====
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

-- 1. Mod geladen, Version 0.16.0, Shop-/Queue-Commands + Wellenstart-Hook aktiv.
check(log_has("event=mod_load version=0.16.0"), "mod_load version=0.16.0")
check(_G.__commands["rb_buy_wave"] ~= nil, "rb_buy_wave registriert")
check(_G.__commands["rb_shop"] ~= nil, "rb_shop registriert")
check(_G.__commands["rb_queue"] ~= nil, "rb_queue registriert")
check(log_has("event=wave_hook patch status=ok"), "wave_hook patch ok (Wellenstart-Hook aktiv)")

-- 2. Shop (rb_shop): Konsolen-Liste + Custom-UI-Popup (4 Tiers inkl. Boss).
_G.__commands["rb_shop"]({})
check(log_has("event=shop status=listed tiers=4 pool=0"), "rb_shop listed tiers=4")
check(log_has("event=shop status=popup_opened tiers=4"), "rb_shop popup_opened")
check(_G.__popup ~= nil and _G.__popup.template == "gui/popup/popup_template_1button",
    "Shop-Popup mit Template popup_template_1button")

-- 3. rb_buy_wave Guards (usage / unbekannte Unit / zu wenig Pool).
_G.__commands["rb_buy_wave"]({})
check(log_has("event=buy_wave status=usage"), "rb_buy_wave ohne Args -> usage")
_G.__commands["rb_buy_wave"]({ "unbekannt" })
check(log_has("event=buy_wave status=unknown_unit unit=unbekannt"), "rb_buy_wave unbekannte Unit")
_G.__commands["rb_buy_wave"]({ "brabit" })
check(log_has("event=buy_wave status=insufficient unit=brabit count=1 need=100 pool=0"),
    "rb_buy_wave ohne Pool -> insufficient")

-- 4. Economy: farmen + irreversibles convert -> Spar-Pool.
local farmEvt = {}
function farmEvt:GetResourceName() return "carbonium" end
function farmEvt:GetAmount() return 2000 end
_G.__handlers["ResourceObtainedEvent"](farmEvt)
check(log_has("event=economy_farm source=resource_obtained resource=carbonium amount=2000 value=2000"),
    "farm 2000 carbonium")
_G.__commands["rb_convert"]({ "carbonium", "2000" })
check(log_has("event=convert resource=carbonium amount=2000 value=2000 pool=2000 status=ok irreversible=1"),
    "convert -> pool 2000")

-- 5. Kauf Tier-1 (brabit x2) -> Queue 2, Pool 1800.
_G.__commands["rb_buy_wave"]({ "brabit", "2" })
check(log_has("event=buy_wave unit=brabit tier=t1 count=2 price=100 total=200 pool=1800 queue=2 status=ok"),
    "buy brabit x2 -> queue 2 pool 1800")

-- 6. Kauf Boss (Tier boss) -> Queue 3, Pool 1000.
_G.__commands["rb_buy_wave"]({ "boss" })
check(log_has("event=buy_wave unit=boss tier=boss count=1 price=800 total=800 pool=1000 queue=3 status=ok"),
    "buy boss -> queue 3 pool 1000")

-- 7. Send-Queue-Status.
_G.__commands["rb_queue"]({})
check(log_has("event=queue status=show count=3 value=1000 pool=1000"),
    "rb_queue count=3 value=1000 pool=1000")

-- 8. Wellenstart -> Send-Queue ausliefern (Boost der naechsten Naturwelle).
dom_mananger.OnEnterSpawn(nil, {})
check(_G.__waveStarts == 1, "Original-OnEnterSpawn genau 1x (Naturwelle unangetastet)")
check(log_has("event=round round=1 status=start mode=sp pool=1000 queue=3"),
    "round=1 status=start queue=3")
check(log_has("event=send_queue round=1 status=done spawned=3 value=1000 anchor=border"),
    "send_queue done spawned=3 value=1000 anchor=border")

-- 9. Queue nach Flush leer, Pool unangetastet (Kauf war bereits bezahlt).
_G.__commands["rb_queue"]({})
check(log_has("event=queue status=show count=0 value=0 pool=1000"),
    "queue nach Flush leer, pool bleibt 1000")

-- 10. Senden jederzeit/beliebig: erneuter Kauf + zweiter Wellenstart.
_G.__commands["rb_buy_wave"]({ "artigian" })
check(log_has("event=buy_wave unit=artigian tier=t2 count=1 price=200 total=200 pool=800 queue=1 status=ok"),
    "erneuter Kauf artigian -> queue 1 pool 800")
dom_mananger.OnEnterSpawn(nil, {})
check(log_has("event=round round=2 status=start mode=sp pool=800 queue=1"),
    "round=2 status=start queue=1")
check(log_has("event=send_queue round=2 status=done spawned=1 value=200 anchor=border"),
    "send_queue round=2 done spawned=1 value=200")

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

test('Issue #25: Send-Queue & Shop-HUD (fengari + Stub-Services)', () => {
    const failures = runLua(STUBS + modSource + '\n' + ASSERTIONS);
    assert.strictEqual(failures, 0, `Assertion-Fehler im Lua-Harness: ${failures}`);
});
