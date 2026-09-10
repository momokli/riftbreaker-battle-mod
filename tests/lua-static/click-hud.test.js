'use strict';

// Statische Verifikation des Lua-Mods (rbbattle_autoexec.lua) fuer Issue #99
// (Click-HUD: Senden per Klick statt Tippen): toggle-bares HUD-Overlay
// (GuiService:OpenPopup, 2-Button-Template) + wichtigste Send-Aktion
// (Einheit kaufen -> Send-Queue) per Klick (GuiPopupResultEvent, button_yes).
// Deckt zusaetzlich Issue #147 (Send-Amount-HUD) MVP ab: rb_quick_step
// passt die Quick-Send-Menge relativ an (+N/-N/xN), ohne eine exakte Zahl
// tippen zu muessen -- die Logik fuer ein spaeteres klickbares Stepper-UI.
// Wie send-queue.test.js: 1) luaparse-Syntax-Check (Lua 5.1),
// 2) fengari-Lua-VM mit Stub-Services + Assertions auf die [RBBATTLE]-Log-Zeilen
// (Vertragsflaeche Bridge/Server).
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
// Ergaenzt gegenueber send-queue.test.js um die Klick-Event-Erfassung:
// GuiService.OpenPopup speichert Template/Text (Popup-Behauptung), und der
// GuiPopupResultEvent-Handler wird ueber RegisterGlobalEventHandler gespeichert,
// damit der Test die Button-Klicks simuliert.
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
    OpenPopup = function(self, ent, template, text)
        _G.__popup = { ent = ent, template = template, text = text }
        return true
    end,
}

-- DOM-Klasse (Wellenstart-Hook #42/#25) + Timer-API (#23/#27-Countdown).
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
-- ==== Assertions (Issue #99: Click-HUD / Senden per Klick) ====
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

-- 1. Mod geladen, Click-HUD-Commands + Klick-Event registriert.
check(log_has("event=mod_load version=0.24.2"), "mod_load version=0.24.2")
check(_G.__commands["rb_hud_ui"] ~= nil, "rb_hud_ui registriert")
check(_G.__commands["rb_quick"] ~= nil, "rb_quick registriert")
check(_G.__handlers["GuiPopupResultEvent"] ~= nil, "GuiPopupResultEvent registriert")

-- 2. rb_quick ohne Args -> usage (Default brabit x1).
_G.__commands["rb_quick"]({})
check(log_has("event=quick_send status=usage unit=brabit count=1"),
    "rb_quick ohne Args -> usage (Default brabit x1)")

-- 3. rb_quick brabit 2 -> armen (Preis 100 je).
_G.__commands["rb_quick"]({ "brabit", "2" })
check(log_has("event=quick_send status=armed unit=brabit count=2 price=100"),
    "rb_quick brabit 2 -> armed")

-- 4. rb_quick unbekannt -> unknown_unit.
_G.__commands["rb_quick"]({ "unbekannt" })
check(log_has("event=quick_send status=unknown_unit unit=unbekannt"),
    "rb_quick unbekannte Unit -> unknown_unit")

-- 5. Economy vorbereiten: farmen + convert -> Pool 2000 (fuer den Klick-Kauf).
local farmEvt = {}
function farmEvt:GetResourceName() return "carbonium" end
function farmEvt:GetAmount() return 2000 end
_G.__handlers["ResourceObtainedEvent"](farmEvt)
_G.__commands["rb_convert"]({ "carbonium", "2000" })
check(log_has("event=convert resource=carbonium amount=2000 value=2000 pool=2000 status=ok irreversible=1"),
    "convert -> pool 2000")

-- 6. rb_hud_ui oeffnet das Overlay (2-Button-Template) mit Rundenzustand.
_G.__commands["rb_hud_ui"]({})
check(log_has("event=hud_ui status=opened quick=brabit count=2 round=0 countdown=300 pool=2000 queue=0"),
    "rb_hud_ui -> event=hud_ui status=opened")
check(_G.__popup ~= nil and _G.__popup.template == "gui/popup/popup_ingame_2buttons",
    "Overlay-Popup mit Template popup_ingame_2buttons")

-- 7. rb_hud_ui erneut (noch offen) -> already_open.
_G.__commands["rb_hud_ui"]({})
check(log_has("event=hud_ui status=already_open"),
    "rb_hud_ui erneut -> already_open")

-- 8. Klick "Ja" (button_yes) -> Quick-Send kauft brabit x2 in die Queue.
local yesEvt = {}
function yesEvt:GetResult() return "button_yes" end
_G.__handlers["GuiPopupResultEvent"](yesEvt)
check(log_has("event=buy_wave unit=brabit tier=t1 count=2 price=100 total=200 pool=1800 queue=2 status=ok"),
    "button_yes -> BuyWave brabit x2 (queue 2, pool 1800)")
check(log_has("event=hud_ui status=closed result=button_yes action=quick_send unit=brabit count=2"),
    "button_yes -> event=hud_ui status=closed action=quick_send")

-- 9. Klick "Nein" (button_no) -> schliessen ohne Aktion (Queue bleibt 2).
_G.__commands["rb_hud_ui"]({})
check(log_has("event=hud_ui status=opened quick=brabit count=2 round=0 countdown=300 pool=1800 queue=2"),
    "rb_hud_ui erneut oeffnen (queue=2)")
local noEvt = {}
function noEvt:GetResult() return "button_no" end
_G.__handlers["GuiPopupResultEvent"](noEvt)
check(log_has("event=hud_ui status=closed result=button_no action=none"),
    "button_no -> event=hud_ui status=closed action=none")
_G.__commands["rb_queue"]({})
check(log_has("event=queue status=show count=2 value=200 pool=1800"),
    "button_no kauft nichts (queue bleibt 2)")

-- 10. Fremdes Popup (open=false) loest keinen Kauf aus (Guard):
--     rb_shop oeffnet das Shop-Popup; ein button_yes-Klick darf dann KEIN
--     BuyWave ausloesen (der Guard ignoriert Popups, die nicht unser HUD sind).
_G.__commands["rb_shop"]({})
check(_G.__popup ~= nil and _G.__popup.template == "gui/popup/popup_template_1button",
    "rb_shop oeffnet Shop-Popup (1-Button)")
local buysBefore = log_count("event=buy_wave ")
_G.__handlers["GuiPopupResultEvent"](yesEvt)
local buysAfter = log_count("event=buy_wave ")
check(buysAfter == buysBefore, "Fremdes Popup (open=false) wird ignoriert (kein Kauf)")
_G.__commands["rb_queue"]({})
check(log_has("event=queue status=show count=2 value=200 pool=1800"),
    "Queue unveraendert nach fremdem Klick (count=2)")

-- 11. Issue #147 MVP: rb_quick_step passt die Quick-Send-Menge relativ an
--     (+N/-N Feinjustierung, xN Grobjustierung), ohne eine exakte Zahl
--     tippen zu muessen. quickUnit ist nach Schritt 8 noch "brabit" (count=2).
_G.__commands["rb_quick_step"]({})
check(log_has("event=quick_step status=usage unit=brabit count=2"),
    "rb_quick_step ohne Args -> usage")

_G.__commands["rb_quick_step"]({ "+1" })
check(log_has("event=quick_step status=ok op=+1 unit=brabit before=2 after=3"),
    "rb_quick_step +1 -> 2->3")

_G.__commands["rb_quick_step"]({ "x10" })
check(log_has("event=quick_step status=ok op=x10 unit=brabit before=3 after=30"),
    "rb_quick_step x10 -> 3->30")

_G.__commands["rb_quick_step"]({ "x1000" })
check(log_has("event=quick_step status=ok op=x1000 unit=brabit before=30 after=40"),
    "rb_quick_step x1000 -> gedeckelt auf maxQueueCreatures=40")

_G.__commands["rb_quick_step"]({ "-100" })
check(log_has("event=quick_step status=ok op=-100 unit=brabit before=40 after=1"),
    "rb_quick_step -100 -> nie unter 1")

_G.__commands["rb_quick_step"]({ "y5" })
check(log_has("event=quick_step status=bad_op op=y5"),
    "rb_quick_step ungueltiges Op-Prefix -> bad_op")

_G.__commands["rb_quick_step"]({ "+0" })
check(log_has("event=quick_step status=bad_op op=+0"),
    "rb_quick_step +0 -> bad_op (N muss > 0 sein)")

-- rb_hud_ui zeigt danach die per Step angepasste Menge (count=1 nach -100).
_G.__commands["rb_hud_ui"]({})
check(log_has("event=hud_ui status=opened quick=brabit count=1 round=0 countdown=300 pool=1800 queue=2"),
    "rb_hud_ui zeigt die per rb_quick_step angepasste Menge (count=1)")
_G.__handlers["GuiPopupResultEvent"](noEvt) -- Overlay wieder schliessen (Testzustand aufraeumen)

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

test('Issue #99: Click-HUD / Senden per Klick (fengari + Stub-Services)', () => {
    const failures = runLua(STUBS + modSource + '\n' + ASSERTIONS);
    assert.strictEqual(failures, 0, `Assertion-Fehler im Lua-Harness: ${failures}`);
});
