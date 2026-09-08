-- ============================================================================
-- rbbattle_02_customui_autoexec.lua  (Baustein 02: Custom-UI-Popup)
--
-- Abgeleitet aus mod/lua/rbbattle_autoexec.lua (Spike, PR #1) - Experiment B
-- (CUSTOM-UI), isoliert: registriert NUR den Console-Command rb_ui (Popup
-- togglen). Kein Wave-Spawn, keine Bindings.
--
-- Genutzte API (wie im Spike kommentiert):
--   GuiService:OpenPopup( entity, "gui/popup/popup_template_1button", text )
--     -> offizielles Wiki-Beispiel (day_cycle_machine:ShowEventPopup in
--        exorstudios/riftbreaker-wiki, docs/modding-files/lua-files/gui-popup.md)
--     -> Template gui/popup/popup_template_1button.gui in Spieldaten
--        verifiziert (OriginalPacksData/gui/popup/)
--   GuiPopupResultEvent global empfangen -> Button-Ergebnis ("button_ok")
--   PlayerService:GetPlayerControlledEnt(0) -> Spieler-Mech als Anker
--   ConsoleService:RegisterCommand(...)     -> rb_ui registrieren
--
-- Hinweis: Kein API-Close fuer Popups dokumentiert - Schliessen nur per
-- Button (Rueckmeldung via GuiPopupResultEvent).
-- ============================================================================

local RBB = {}
RBB.version = "0.1.0-baustein02"
RBB.uiOpen = false

-- Log-/Konsole-Helfer (Muster Spike): Praefix [RBBATTLE] fuer externes Parsen.
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

-- Popup-Ergebnis global empfangen (Autoexec hat kein self.entity; der
-- globale Weg ist der dokumentierte Ausweg neben RegisterHandler).
local function OnPopupResult(evt)
    local ok, result = pcall(function()
        return evt:GetResult()
    end)
    if ok then
        RBB.uiOpen = false
        Log("event=ui_popup status=closed result=%s", tostring(result))
        WriteConsole("UI-Popup geschlossen (result=%s)", tostring(result))
    end
end

pcall(function()
    RegisterGlobalEventHandler("GuiPopupResultEvent", function(evt)
        OnPopupResult(evt)
    end)
end)

-- Popup oeffnen (Kernfunktion Experiment B).
local function ToggleUi()
    if not (GuiService and GuiService.OpenPopup) then
        Log("event=ui_popup status=api_missing")
        WriteConsole("rb_ui: GuiService:OpenPopup nicht verfuegbar (API MISSING)")
        return
    end

    if RBB.uiOpen then
        Log("event=ui_popup status=already_open")
        WriteConsole("rb_ui: Popup ist bereits offen (bitte per Button schliessen)")
        return
    end

    local ok, mech = pcall(function()
        return PlayerService:GetPlayerControlledEnt(0)
    end)
    if not ok or mech == nil or mech == INVALID_ID then
        Log("event=ui_popup status=no_player")
        WriteConsole("rb_ui: kein Spieler-Mech gefunden")
        return
    end

    local text = '<style="header_35">RBBATTLE - Baustein 02 (Custom-UI)</style>\r\n'
        .. 'Custom-UI-Popup laeuft.\r\n'
        .. '<style="big_red">Popup-Test:</style> OK klicken zum Schliessen\r\n'
        .. 'Version: ' .. RBB.version
    local ok2, err = pcall(function()
        return GuiService:OpenPopup(mech, "gui/popup/popup_template_1button", text)
    end)
    if not ok2 then
        Log("event=ui_popup status=error err=%s", tostring(err))
        WriteConsole("rb_ui: OpenPopup Fehler (siehe exor_logs.txt)")
        return
    end
    RBB.uiOpen = true
    Log("event=ui_popup status=opened")
    WriteConsole("rb_ui: Popup geoeffnet (Button 'OK' schliesst)")
end

-- Command-Registrierung (Spike-Muster).
pcall(function()
    ConsoleService:RegisterCommand("rb_ui", function()
        ToggleUi()
    end)
end)

-- Lebenszeichen-Log beim Laden (analog Spike).
Log("event=mod_load version=%s status=ok", RBB.version)
