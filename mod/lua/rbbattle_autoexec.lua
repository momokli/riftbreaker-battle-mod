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
RBB.version = "0.34.3"
RBB.ref = "RBB_BUILD_REF"
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
pcall(function()
    ConsoleService:RegisterCommand("rb_poc_send", function(args)
        Log("event=poc_send amount=10 resource=mythium")
        WriteConsole("poc_send: 10 mythium (Backend subtrahiert)")
    end)
end)

-- ---------------------------------------------------------------------------
-- PoC (#518): Buy-Order-Queue. "rb_buy <item>" emittiert eine Buy-Order als
-- Log-Event; der Log-Sidecar (buy-order-bridge) meldet sie an den Referee, der
-- sie in eine Queue legt (POST/GET /buy_order(s)). Die autoritative Wirkung
-- (Ressource abziehen + Item gewaehren) macht NICHT der Mod (Lua kann das
-- Spieler-Konto nicht anfassen, api-deep-dive.md §1), sondern Backend/DLL.
-- ---------------------------------------------------------------------------
RBB.buyOrderSeq = 0

local function EmitBuyOrder(item, amount, resource)
    RBB.buyOrderSeq = RBB.buyOrderSeq + 1
    Log("event=buy_order world=A amount=%s resource=%s item=%s order_id=%d",
        tostring(amount or 10), tostring(resource or "carbonium"),
        tostring(item or "unknown"), RBB.buyOrderSeq)
    WriteConsole("buy_order: %s x%s (%s) emittiert",
        tostring(item or "unknown"), tostring(amount or 10),
        tostring(resource or "carbonium"))
end

pcall(function()
    ConsoleService:RegisterCommand("rb_buy", function(args)
        local item = "unknown"
        if args and args ~= "" then
            item = args:match("^%S+") or "unknown"
        end
        EmitBuyOrder(item, 10, "carbonium")
    end)
end)

-- Klickbarer HUD-Button (PoC #518): rb_buy_hud oeffnet ein 1-Button-Popup;
-- Klick auf "OK" emittiert dieselbe Buy-Order wie rb_buy. Muster #379.
RBB.buyHud = { open = false, item = "boss" }

local function OnBuyHudResult(evt)
    if not RBB.buyHud.open then
        return
    end
    local ok, result = pcall(function()
        return evt:GetResult()
    end)
    if not ok then
        RBB.buyHud.open = false
        return
    end
    RBB.buyHud.open = false
    if tostring(result) == "button_ok" then
        EmitBuyOrder(RBB.buyHud.item, 10, "carbonium")
        Log("event=buy_hud status=clicked item=%s", tostring(RBB.buyHud.item))
    else
        Log("event=buy_hud status=closed result=%s", tostring(result))
    end
end

pcall(function()
    RegisterGlobalEventHandler("GuiPopupResultEvent", function(evt)
        OnBuyHudResult(evt)
    end)
end)

local function OpenBuyHud(item)
    RBB.buyHud.item = item or "boss"
    if not (GuiService and GuiService.OpenPopup) then
        Log("event=buy_hud status=api_missing")
        WriteConsole("rb_buy_hud: GuiService:OpenPopup nicht verfuegbar")
        return
    end
    if RBB.buyHud.open then
        Log("event=buy_hud status=already_open")
        return
    end
    local ok, mech = pcall(function()
        return PlayerService:GetPlayerControlledEnt(0)
    end)
    if not ok or mech == nil or mech == INVALID_ID then
        Log("event=buy_hud status=no_player")
        return
    end
    local text = '<style="header_35">RBBATTLE — Buy</style>\r\n'
        .. 'Klick auf "OK" sendet eine Buy-Order:\r\n'
        .. '<style="big_red">10 carbonium -> ' .. tostring(RBB.buyHud.item) .. '</style>'
    local ok2, err = pcall(function()
        return GuiService:OpenPopup(mech, "gui/popup/popup_template_1button", text)
    end)
    if not ok2 then
        Log("event=buy_hud status=error err=%s", tostring(err))
        return
    end
    RBB.buyHud.open = true
    Log("event=buy_hud status=opened item=%s", tostring(RBB.buyHud.item))
end

pcall(function()
    ConsoleService:RegisterCommand("rb_buy_hud", function(args)
        local item = "boss"
        if args and args ~= "" then
            item = args:match("^%S+") or "boss"
        end
        OpenBuyHud(item)
    end)
end)

-- Lade-Marker fuer Boot-Test C1 + Deploy-Runtime-Check (kein Business-Logik,
-- nur das Lebenszeichen, das die Pipeline erwartet).
--
-- Build-Identitaet (Issue #499): `ref` wird beim BAUEN in den Mod gebacken.
-- `RBB.ref` ist hier nur der Platzhalter "RBB_BUILD_REF"; scripts/
-- package_bausteine.sh ersetzt ihn vor dem Zippen durch den echten
-- Commit/Tag (Env RBB_BUILD_REF, sonst `git rev-parse HEAD`). Grund: die
-- Riftbreaker-Lua-Sandbox liefert weder `os.getenv` noch `io.open` - ein
-- Laufzeit-Auslesen ergibt zuverlaessig "unknown". `env` ist eine
-- Deploy-Eigenschaft und wird ueber die uebrigen Identitaets-Surfaces
-- (Labels, Tournament, Server-Control, Sidecars) geliefert, NICHT ueber den
-- Mod - deshalb kein env-Feld mehr im mod_load-Marker. version/status bleiben
-- unveraendert, der mod_load-Marker bricht nie.
Log("event=mod_load version=%s status=ok ref=%s", RBB.version, RBB.ref)
