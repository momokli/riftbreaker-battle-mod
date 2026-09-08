-- ============================================================================
-- rbbattle_autoexec.lua
-- The Rift Breaker Battle Mod - Spike (feature/spike-mod-skeleton)
--
-- WAS: Autoexec-Dateien ("*_autoexec.lua" unter lua/) werden von EXOR bei
--      jeder Kartenerstellung ausgefuehrt und haben Zugriff auf alle Services,
--      Reflection und RegisterGlobalEventHandler (Quelle: exorstudios/riftbreaker-wiki,
--      docs/modding-files/lua-files/autoexec.md).
--
-- INHALT (3 Experimente, bewusst klein, kein Feature-Creep):
--   Skeleton    : Registriert zwei Custom Console Commands (rb_wave, rb_ui)
--                 und Bindings (F7/F8/F9) - laedt ohne Seiteneffekte.
--   Experiment A: WAVE-SPAWN  -> rb_wave <level> spawnt eine kleine
--                 Kreaturen-Welle um den Spieler.
--   Experiment B: CUSTOM-UI   -> rb_ui togglet ein Popup-Panel mit Button
--                 (GuiService:OpenPopup + GuiPopupResultEvent).
--   Experiment C: BRUECKE     -> LogService:Log mit "[RBBATTLE] event=..." -
--                 Praefix (Log-Datei: Documents/The Riftbreaker/exor_logs.txt)
--                 + Konsolen-Command rb_wave als Bridge-Test.
--
-- Alle API-Aufrufe sind pcall-gesichert: Fehlt eine Funktion (anderer
-- Game-Stand), loggen wir "API MISSING" und tun sonst nichts (graceful no-op).
-- ============================================================================

local RBB = {}
RBB.version = "0.1.0-spike"

-- Log-Praefix laut Experiment C (1): einzeilige Events zum externen Parsen.
-- Beispiel: [RBBATTLE] event=wave level=3 count=14
local LOG_TAG = "[RBBATTLE]"
local function Log(fmt, ...)
    -- fmt + varargs oben formatieren (5.1: '...' nur in vararg-Funktion direkt
    -- nutzbar, nicht in verschachtelten non-vararg-Funktionen).
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
-- Experiment A: WAVE-SPAWN
-- ---------------------------------------------------------------------------
-- Genutzte API (im Code kommentiert, wie beauftragt):
--   EntityService:SpawnEntity( blueprint, x, y, z, team )
--     -> offizielle Service-API (exorstudios/riftbreaker-wiki, lua-services)
--     -> Identisch implementiert im Spiel-eigenen Cheat-Command
--        "debug_spawn_entity" (OriginalPacksData/lua/commands/cheat.lua):
--        EntityService:SpawnEntity( args[1], position.x, position.y,
--                                    position.z, "" )
--   EnvironmentService:GetTerrainHeight( pos ) -> y auf Geländehöhe legen
--     (Muster aus echten Workshop-Mods, z.B. lilly1987/Riftbreaker-mods)
--   PlayerService:GetPlayerControlledEnt(0) -> Spieler-Mech als Spawn-Anker
--
-- Blueprint-Namen sind echte Entity-Pfade aus den Original-Spieldaten
-- (Packs/00_win_data.zip -> entities/units/ground/*.ent; verifiziert ueber
-- PonomarevDmitry/RiftbreakersMods OriginalPacksData):
--   brabit (klein, frueh), baxmoth (mittel), artigian, canceroth (groesser)
--
-- Team-Parameter "": exakt das, was EXORs eigener debug_spawn_entity nutzt;
-- Einheiten behalten ihr Blueprint-Standard-Team (Feinde = hostil).
RBB.waves = {
    { -- level 1: kleine Welle
        { blueprint = "units/ground/brabit",  count = 5 },
    },
    { -- level 2
        { blueprint = "units/ground/brabit",  count = 5 },
        { blueprint = "units/ground/baxmoth", count = 3 },
    },
    { -- level 3
        { blueprint = "units/ground/baxmoth", count = 5 },
        { blueprint = "units/ground/artigian", count = 2 },
        { blueprint = "units/ground/canceroth", count = 1 },
    },
}
RBB.maxWaveLevel = #RBB.waves
RBB.spawnRingMin = 8.0   -- Abstand vom Spieler (kleiner Ring, sichtbar)
RBB.spawnRingMax = 20.0
RBB.spawnMaxPerUnit = 10 -- Schutz vor Tippfehlern / Endlos-Args

local function BlueprintExists(blueprint)
    -- Existenz-Check vor dem Spawn: ResourceManager:GetBlueprint (Muster aus
    -- lilly1987/Riftbreaker-mods). Unbekannte Blueprints -> skip + Log, statt
    -- riskantem Spawn-Versuch.
    local ok, bp = pcall(function()
        return ResourceManager:GetBlueprint(blueprint)
    end)
    return ok and bp ~= nil and bp ~= false
end

local function GetTerrainHeight(x, z)
    local ok, y = pcall(function()
        local h = EnvironmentService:GetTerrainHeight({ x = x, y = 0, z = z })
        if h == nil then error("no height") end
        return h
    end)
    if not ok then return nil end
    return y
end

-- Spawnt eine Einheit eines Blueprints an zufaelliger Position im Ring um den
-- Spieler. Liefert true/false (+ Log) - nie einen Fehler nach aussen.
local function SpawnCreatureAtRandomOffset(mech, blueprint, playerPos)
    local angle = math.random() * 2.0 * math.pi
    local radius = RBB.spawnRingMin + math.random() * (RBB.spawnRingMax - RBB.spawnRingMin)
    local x = playerPos.x + math.cos(angle) * radius
    local z = playerPos.z + math.sin(angle) * radius

    local y = GetTerrainHeight(x, z)
    if y == nil then
        -- GetTerrainHeight nicht verfuegbar: Spieler-Hoehe als Fallback.
        y = playerPos.y
    end

    -- >>> Experiment A - eigentlicher API-Aufruf <<<
    -- EntityService:SpawnEntity( blueprint, x, y, z, team )
    local ok, ent = pcall(function()
        return EntityService:SpawnEntity(blueprint, x, y, z, "")
    end)
    if not ok then
        Log("event=spawn api_error blueprint=%s err=%s", blueprint, tostring(ent))
        return false
    end
    if ent == nil or ent == INVALID_ID then
        Log("event=spawn failed blueprint=%s", blueprint)
        return false
    end
    Log("event=spawn ok blueprint=%s entity=%s", blueprint, tostring(ent))
    return true
end

-- Hauptfunktion Experiment A + C (2): Welle der Stufe <level> spawnen.
local function SpawnWave(level)
    level = math.floor(tonumber(level) or 1)
    if level < 1 then level = 1 end
    if level > RBB.maxWaveLevel then
        WriteConsole("rb_wave: level %d ungueltig (1..%d), nutze 1", level, RBB.maxWaveLevel)
        Log("event=wave level=%d status=invalid_level", level)
        level = 1
    end

    -- >>> Experiment C (1): Bridge-Logzeile <<<
    -- Format: [RBBATTLE] event=wave level=<n> ... (extern parsebar)
    Log("event=wave level=%d status=start", level)

    local waveDef = RBB.waves[level]
    local spawned = 0
    local skipped = 0

    local playerOk, mech = pcall(function()
        return PlayerService:GetPlayerControlledEnt(0)
    end)
    if not playerOk or mech == nil or mech == INVALID_ID then
        -- Spieler existiert (noch) nicht -> graceful no-op + Log.
        Log("event=wave level=%d status=no_player", level)
        WriteConsole("rb_wave: kein Spieler-Mech gefunden (Karte geladen?)")
        return false
    end

    local posOk, playerPos = pcall(function()
        return EntityService:GetPosition(mech)
    end)
    if not posOk or playerPos == nil then
        Log("event=wave level=%d status=no_position", level)
        return false
    end

    for _, unitDef in ipairs(waveDef) do
        local maxCount = math.min(unitDef.count or 0, RBB.spawnMaxPerUnit)
        for _ = 1, maxCount do
            if BlueprintExists(unitDef.blueprint) then
                if SpawnCreatureAtRandomOffset(mech, unitDef.blueprint, playerPos) then
                    spawned = spawned + 1
                end
            else
                skipped = skipped + 1
                Log("event=spawn skip blueprint=%s reason=not_found", unitDef.blueprint)
            end
        end
    end

    Log("event=wave level=%d status=done spawned=%d skipped=%d", level, spawned, skipped)
    WriteConsole("rb_wave level %d: %d Kreaturen gespawnt (%d uebersprungen)",
                 level, spawned, skipped)
    return spawned > 0
end

-- ---------------------------------------------------------------------------
-- Experiment B: CUSTOM-UI (minimales HUD/Panel, per Hotkey/Command togglen)
-- ---------------------------------------------------------------------------
-- Genutzte API:
--   GuiService:OpenPopup( entity, "gui/popup/popup_template_1button", text )
--     -> offizielles Wiki-Beispiel (day_cycle_machine:ShowEventPopup in
--        exorstudios/riftbreaker-wiki, docs/modding-files/lua-files/gui-popup.md)
--     -> Template gui/popup/popup_template_1button.gui in Spieldaten
--        verifiziert (OriginalPacksData/gui/popup/)
--   GuiPopupResultEvent global empfangen -> Button-Ergebnis ("button_ok")
--   ConsoleService:Write -> In-Game-Konsole (als zusaetzliches UI-Feedback)
RBB.uiOpen = false

local function OnPopupResult(evt)
    -- Wird von RegisterGlobalEventHandler("GuiPopupResultEvent", ...) gerufen.
    local ok, result = pcall(function()
        return evt:GetResult()
    end)
    if ok then
        RBB.uiOpen = false
        Log("event=ui_popup status=closed result=%s", tostring(result))
        WriteConsole("UI-Popup geschlossen (result=%s)", tostring(result))
    end
end

local function ToggleUi()
    -- >>> Experiment B - eigentlicher API-Aufruf <<<
    if not (GuiService and GuiService.OpenPopup) then
        Log("event=ui_popup status=api_missing")
        WriteConsole("rb_ui: GuiService:OpenPopup nicht verfuegbar (API MISSING)")
        return
    end

    if RBB.uiOpen then
        -- Kein API-Close fuer Popups dokumentiert; Hinweis + no-op.
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

    local text = '<style="header_35">RBBATTLE - Battle Mod (Spike)</style>\r\n'
        .. 'Mod geladen. Experiment B (Custom-UI) laeuft.\r\n'
        .. '<style="big_red">Welle spawnen:</style> rb_wave 1..3 oder F7/F8\r\n'
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

-- ---------------------------------------------------------------------------
-- Skeleton: Registrierung (Custom Console Commands + Hotkeys)
-- ---------------------------------------------------------------------------
-- Genutzte API:
--   ConsoleService:RegisterCommand( name, function(args) )  -> Custom Command
--   ConsoleService:ExecuteCommand('bind f7 "rb_wave 1"')    -> Hotkey-Bindung
--   Beides offiziell dokumentiert:
--     - exorstudios/riftbreaker-wiki, accessing-keyboard-hotkeys.md
--     - exorstudios/riftbreaker-wiki, console-service (Signatur-Dump Fandom)
--     - Von EXOR selbst genutzt: OriginalPacksData/lua/commands/cheat.lua
--       registriert debug_spawn_entity etc. exakt so.
local function RegisterRbCommands()
    if not (ConsoleService and ConsoleService.RegisterCommand) then
        Log("event=init status=api_missing service=ConsoleService")
        return
    end

    -- Experiment A + C (2): rb_wave <level>
    pcall(function()
        ConsoleService:RegisterCommand("rb_wave", function(args)
            local level = 1
            if args and #args >= 1 then
                level = tonumber(args[1]) or 1
            end
            SpawnWave(level)
        end)
    end)

    -- Experiment B: rb_ui (Panel togglen)
    pcall(function()
        ConsoleService:RegisterCommand("rb_ui", function()
            ToggleUi()
        end)
    end)

    Log("event=init status=commands_registered")

    -- Hotkeys: F7 = Welle 1, F8 = Welle 3, F9 = UI-Panel.
    -- (Bind-Syntax aus exorstudios/riftbreaker-wiki, accessing-keyboard-hotkeys.md)
    pcall(function()
        ConsoleService:ExecuteCommand('bind f7 "rb_wave 1"')
        ConsoleService:ExecuteCommand('bind f8 "rb_wave 3"')
        ConsoleService:ExecuteCommand('bind f9 "rb_ui"')
    end)
    Log("event=init status=binds_registered")
end

-- Popup-Ergebnisse global empfangen (Autoexec hat kein self.entity; der
-- globale Weg ist hier der dokumentierte Ausweg neben RegisterHandler).
pcall(function()
    RegisterGlobalEventHandler("GuiPopupResultEvent", function(evt)
        OnPopupResult(evt)
    end)
end)

-- Sichtbarer Lebenszeichen-Log beim Laden des Mods (Experiment C (1) Bridge):
Log("event=mod_load version=%s status=ok", RBB.version)

-- Commands registrieren (Autoexec laeuft bei Map-Erstellung; Registrierung
-- ist zu diesem Zeitpunkt unkritisch und folgt dem Spiel-eigenen Muster).
RegisterRbCommands()

-- Kein return: Autoexec-Dateien sind keine Modul-Requires (Muster der
-- Spiel-eigenen lua/commands/*.lua und der Workshop-Mods von lilly1987).
