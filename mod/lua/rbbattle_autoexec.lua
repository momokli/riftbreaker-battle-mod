-- ============================================================================
-- rbbattle_autoexec.lua — PLAYERMOD (HUD/Player-Facing, GREEN FIELD)
--
-- SERVERMOD = rbbridge.dll (C++): State-Egress + WRITE liegen dort (#378).
-- PLAYERMOD = diese Lua: nur HUD/Display + Event-Emission.
--
-- Business-Logik lag bis Commit 3c4c5249696c17c0eb2f535802168b9f57c9e9a3 hier
-- und wurde entfernt (#378/#380). Wiederherstellbar via:
--   git show 3c4c524:mod/lua/rbbattle_autoexec.lua
-- ============================================================================

local RBB = {}
RBB.round = 0
RBB.mode = "sp"
RBB.commenced = false
RBB.hq = nil

-- Log-/Konsole-Helfer: Praefix [RBBATTLE] fuer externes Parsen.
local LOG_TAG = "[RBBATTLE]"
local function Log(fmt, ...)
    local okMsg, msg = pcall(string.format, fmt, ...)
    if not okMsg then msg = fmt end
    local service = LogService
    if service then
        pcall(service.Log, service, LOG_TAG .. " " .. msg)
    end
end

local function WriteConsole(fmt, ...)
    local okMsg, msg = pcall(string.format, fmt, ...)
    if not okMsg then msg = fmt end
    local service = ConsoleService
    if service then
        pcall(service.Write, service, LOG_TAG .. " " .. msg)
    end
end

-- ---------------------------------------------------------------------------
-- PoC (#379): "send 10 mythium" nur als Event emittieren. Die Subtraktion macht
-- das Backend, NICHT der Mod. (Transport Webhook/curl noch offen.)
-- ---------------------------------------------------------------------------
local function PocSend()
    Log("event=poc_send amount=10 resource=mythium")
    WriteConsole("poc_send: 10 mythium (Backend subtrahiert)")
end

pcall(function()
    ConsoleService:RegisterCommand("rb_poc_send", function(args)
        PocSend()
    end)
end)

-- ---------------------------------------------------------------------------
-- PoC (#379): klickbarer HUD-Button. `rb_poc_hud` oeffnet ein 1-Button-Popup
-- (GuiService:OpenPopup, Muster Baustein 02); der Klick auf "OK" feuert
-- GuiPopupResultEvent (result="button_ok") und emittiert denselben
-- poc_send-Event wie rb_poc_send. Kein Close-API -> schliessen nur per Button.
-- ---------------------------------------------------------------------------
RBB.pocHud = { open = false }

-- Klick-Handler (global, da autoexec kein self.entity hat). Reagiert nur,
-- wenn unser Popup offen ist (Guard gegen fremde GuiPopupResultEvents).
local function OnPocGuiResult(evt)
    if not RBB.pocHud.open then
        return
    end
    local ok, result = pcall(function()
        return evt:GetResult()
    end)
    if not ok then
        RBB.pocHud.open = false
        return
    end
    RBB.pocHud.open = false
    if tostring(result) == "button_ok" then
        PocSend()
        Log("event=poc_hud status=clicked result=button_ok action=poc_send")
        WriteConsole("poc_hud: OK geklickt -> poc_send emittiert")
    else
        Log("event=poc_hud status=closed result=%s action=none", tostring(result))
    end
end

pcall(function()
    RegisterGlobalEventHandler("GuiPopupResultEvent", function(evt)
        OnPocGuiResult(evt)
    end)
end)

-- Oeffnet das PoC-Popup (1 Button, "OK"). Best-effort wie Baustein 02.
local function OpenPocHud()
    if not (GuiService and GuiService.OpenPopup) then
        Log("event=poc_hud status=api_missing")
        WriteConsole("rb_poc_hud: GuiService:OpenPopup nicht verfuegbar (API MISSING)")
        return
    end
    if RBB.pocHud.open then
        Log("event=poc_hud status=already_open")
        WriteConsole("rb_poc_hud: Popup bereits offen (OK klicken zum Senden)")
        return
    end
    local ok, mech = pcall(function()
        return PlayerService:GetPlayerControlledEnt(0)
    end)
    if not ok or mech == nil or mech == INVALID_ID then
        Log("event=poc_hud status=no_player")
        WriteConsole("rb_poc_hud: kein Spieler-Mech gefunden")
        return
    end
    local text = '<style="header_35">RBBATTLE — PoC HUD</style>\r\n'
        .. 'Klick auf "OK" sendet:\r\n'
        .. '<style="big_red">event=poc_send amount=10 resource=mythium</style>'
    local ok2, err = pcall(function()
        return GuiService:OpenPopup(mech, "gui/popup/popup_template_1button", text)
    end)
    if not ok2 then
        Log("event=poc_hud status=error err=%s", tostring(err))
        WriteConsole("rb_poc_hud: OpenPopup Fehler")
        return
    end
    RBB.pocHud.open = true
    Log("event=poc_hud status=opened")
    WriteConsole("rb_poc_hud: Popup geoeffnet (OK klicken -> poc_send)")
end

pcall(function()
    ConsoleService:RegisterCommand("rb_poc_hud", function(args)
        OpenPocHud()
    end)
end)

Log("player_mod ok")
