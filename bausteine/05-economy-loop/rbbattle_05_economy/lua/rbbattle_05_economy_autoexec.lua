-- ============================================================================
-- rbbattle_05_economy_autoexec.lua  (Baustein 05: Economy-Loop)
--
-- Punkte verdienen (Kills eigener Kampfwellen / Survival-Zeit-Tick) und
-- ausgeben (Kreaturen-Wellen gegen die eigene Basis kaufen). Bewusst KEINE
-- Spiel-Ressourcen (carbonium & Co.): es gibt keinen verifizierten
-- Lua-Zugriff aufs Spieler-Konto (s. docs/research/api-deep-dive.md §1) —
-- eigenes Punktesystem, gespiegelt in eine Global-Database.
--
-- Commands:
--   rb_points [reset]   Punkte anzeigen (reset = auf 0 zuruecksetzen)
--   rb_buy_wave <1..3>  Welle kaufen (Kosten 10/25/50) + gegen eigene Basis
--   rb_status           Details: Modus, Zaehler, DB, Config
--
-- Punktequellen (Dual-Mode, s. api-deep-dive.md §2):
--   kill : RegisterGlobalEventHandler("EntityKilledEvent", ...) — nur fuer
--          Kreaturen, die dieser Mod gespawnt hat (Entity-ID-Tracking).
--          Sicherheit: wahrscheinlich (Event existiert in der Reflection,
--          Global-Handler-Mechanik in-game verifiziert; Getter-Namen per
--          Konvention). Fehler im Handler (pcall) -> dauerhaft Fallback.
--   tick : RegisterGlobalEventHandler("HourEvent", ...) — globaler
--          Spielzeit-Takt (Feld Hour), sichere Fallback-Quelle.
--   auto : Startmodus. Tick-Einkommen laeuft, bis der ERSTE fehlerfreie
--          Kill die Kill-API zur Laufzeit verifiziert -> Umschaltung kill.
--
-- Genutzte API (Quellen = Dateien im exorstudios/riftbreaker-wiki, Stand
-- 06aa48f; verifiziert in-game per docs/findings.md #12-#16):
--   RegisterGlobalEventHandler( name, fn )
--     -> docs/modding-files/lua-files/autoexec.md ("listens for all events");
--        Event-Namen PascalCase, Dateien docs/game-reflection/events/
--   ConsoleService:RegisterCommand( name, cb ) / :Write( text )
--     -> docs/lua-services/console-service/; EXOR cheat.lua-Nutzung
--   LogService:Log( text )
--     -> docs/lua-services/log-service/ -> exor_logs.txt
--   PlayerService:GetOrCreateGlobalDatabase( name )
--     -> docs/misc/database-class.md; DB: HasInt/GetIntOrDefault/SetInt
--   PlayerService:GetPlayerControlledEnt( 0 )
--     -> Spieler-Mech als Spawn-Anker (verifiziert, Baustein 01)
--   EntityService:SpawnEntity( bp, x, y, z, team )
--     -> EntityService:SpawnEntity-Overload, Cheat-Command debug_spawn_entity
--        (Team "" = Blueprint-Standardteam, wie Baustein 01)
--   EntityService:GetPosition( ent ) / EnvironmentService:GetTerrainHeight(pos)
--   ResourceManager:GetBlueprint( bp ) -> Existenz-Check (docs/misc/resource-manager.md)
--
-- KEIN io.* / os.* (crasht die Lua-Sandbox hart, docs/findings.md #16).
-- Alle Service-Zugriffe pcall-gesichert; graceful no-op bei Fehlern.
-- ============================================================================

local RBB = {}
RBB.version = "0.1.0-baustein05"

-- Log-/Konsole-Helfer (Muster Baustein 01): Praefix [RBBATTLE] fuer externes
-- Parsen (bausteine/03-log-bridge/tail_events.py).
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

-- ============================================================================
-- Konfiguration
-- ============================================================================
RBB.cfg = {
    -- Kosten pro Welle (Level 1..3), s. README: grobe 40-50% Payback bei
    -- Voll-Clear -> Welle 1 kaufen, Kills, naechste Welle.
    waveCosts      = { 10, 25, 50 },

    -- Punkte je Kill, nach Blueprint (dickere Biester = mehr Punkte).
    killPoints     = {
        ["units/ground/brabit"]     = 1,
        ["units/ground/baxmoth"]    = 2,
        ["units/ground/artigian"]   = 3,
        ["units/ground/canceroth"]  = 5,
    },
    defaultPointsPerKill = 1,   -- Fallback fuer unbekannte Blueprints

    pointsPerHourTick = 1,      -- Fallback-Tick: +1 pro HourEvent (Spielzeit)

    -- Wellendefinitionen identisch zu Baustein 01 (in-game verifiziert).
    waves = {
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
    },
    spawnRingMin  = 8.0,   -- Spawn-Ring um den Spieler (wie Baustein 01,
    spawnRingMax  = 20.0,  -- verifiziert) — im Singleplayer-Test steht der
    spawnMaxPerUnit = 10,  -- Spieler an der EIGENEN Basis -> Welle greift sie an.
    maxTracked    = 500,   -- Obergrenze getrackte Spawn-IDs (Speicherschutz)
}

-- DB-Schluessel (Global-Database "rbbattle_05_economy").
RBB.dbName = "rbbattle_05_economy"
RBB.dbKeys = { points = "points", earned = "earned", spent = "spent",
               income = "income" }

-- ============================================================================
-- Zustand
-- ============================================================================
RBB.state = {
    points   = 0,   -- verfuegbares Guthaben
    earned   = 0,   -- Summe aller Einkuenfte
    spent    = 0,   -- Summe aller Ausgaben
    income   = 0,   -- Anzahl Einkunfts-Ereignisse (Kills + Ticks)
    mode     = "auto",   -- auto | kill | tick  (s. Kopfkommentar)
    killActive = true,   -- false nach Fehler im Kill-Handler (dauerhaft)
    dbOk     = false,    -- true wenn Global-Database erreichbar
}
RBB.tracked = {}       -- id -> blueprint (dieser Mod gespawnte Kreaturen)
RBB.trackedOrder = {}  -- Einfuege-Reihenfolge fuer Prune (aelteste zuerst)
RBB.trackedCount = 0

-- ============================================================================
-- Persistenz-Spiegel (Global-Database; docs/misc/database-class.md).
-- Lese beim Mod-Load zurueck, schreibe bei jeder Aenderung. DB-Ausfaelle
-- sind toleriert (Mod laeuft dann rein im Speicher).
-- ============================================================================
local function LoadState()
    local okDb, db = pcall(function()
        return PlayerService:GetOrCreateGlobalDatabase(RBB.dbName)
    end)
    if not okDb or db == nil then
        Log("event=db_load status=unavailable reason=no_global_database")
        return
    end
    local okHas, has = pcall(function() return db:HasInt(RBB.dbKeys.points) end)
    if not okHas or not has then
        Log("event=db_load status=new db=%s", RBB.dbName)
    else
        local okGet, v = pcall(function()
            return db:GetIntOrDefault(RBB.dbKeys.points, 0)
        end)
        if okGet and v then RBB.state.points = v end
        local okE, e = pcall(function()
            return db:GetIntOrDefault(RBB.dbKeys.earned, 0)
        end)
        if okE and e then RBB.state.earned = e end
        local okS, s = pcall(function()
            return db:GetIntOrDefault(RBB.dbKeys.spent, 0)
        end)
        if okS and s then RBB.state.spent = s end
        Log("event=db_load status=resume points=%d earned=%d spent=%d",
            RBB.state.points, RBB.state.earned, RBB.state.spent)
    end
    RBB.db = db
    RBB.state.dbOk = true
end

local function SaveState()
    local db = RBB.db
    if db == nil then return end
    pcall(function()
        db:SetInt(RBB.dbKeys.points, RBB.state.points)
        db:SetInt(RBB.dbKeys.earned, RBB.state.earned)
        db:SetInt(RBB.dbKeys.spent, RBB.state.spent)
    end)
end

local function ResetState()
    RBB.state.points = 0
    RBB.state.earned = 0
    RBB.state.spent = 0
    RBB.state.income = 0
    local db = RBB.db
    if db ~= nil then
        pcall(function()
            db:RemoveKey(RBB.dbKeys.points)
            db:RemoveKey(RBB.dbKeys.earned)
            db:RemoveKey(RBB.dbKeys.spent)
        end)
    end
end

-- ============================================================================
-- Spawn-Tracking: nur Kreaturen, die rb_buy_wave gespawnt hat, bringen
-- Punkte (kein Punktefarmen an ambienten Wildtieren).
-- ============================================================================
local function TrackEntity(id, blueprint)
    if id == nil or id == INVALID_ID then return end
    if RBB.tracked[id] ~= nil then return end
    RBB.tracked[id] = blueprint
    RBB.trackedCount = RBB.trackedCount + 1
    table.insert(RBB.trackedOrder, id)
    if RBB.trackedCount > RBB.cfg.maxTracked then
        local oldest = table.remove(RBB.trackedOrder, 1)
        if oldest ~= nil and RBB.tracked[oldest] ~= nil then
            RBB.tracked[oldest] = nil
            RBB.trackedCount = RBB.trackedCount - 1
            Log("event=track prune entity=%s reason=max_tracked", tostring(oldest))
        end
    end
end

local function UntrackEntity(id)
    if id ~= nil and RBB.tracked[id] ~= nil then
        RBB.tracked[id] = nil
        RBB.trackedCount = RBB.trackedCount - 1
        return true
    end
    return false
end

-- ============================================================================
-- Punkte verbuchen
-- ============================================================================
local function PointsForBlueprint(blueprint)
    local pts = RBB.cfg.killPoints[blueprint]
    if pts == nil then pts = RBB.cfg.defaultPointsPerKill end
    return pts
end

local function Award(pts, source, blueprint)
    RBB.state.points = RBB.state.points + pts
    RBB.state.earned = RBB.state.earned + pts
    RBB.state.income = RBB.state.income + 1
    Log("event=points_add source=%s blueprint=%s pts=%d points=%d",
        source, blueprint or "-", pts, RBB.state.points)
    SaveState()
end

-- ============================================================================
-- Kill-Quelle: EntityKilledEvent (docs/game-reflection/events/
-- entity_killed_event.md; Felder u.a. Entity, Blueprint, Killer, KillerPlayer).
-- Getter evt:GetEntity()/evt:GetBlueprint() per Event-Class-Konvention
-- (docs/misc/event-class.md) — Sicherheit: wahrscheinlich, siehe Deep-Dive §2.
-- JEDER Fehler im Handler schaltet dauerhaft auf Tick-Fallback um.
-- ============================================================================
local function HandleKillEvent(evt)
    local entity = evt:GetEntity()
    if entity == nil or entity == INVALID_ID then return end
    local blueprint = RBB.tracked[entity]
    if blueprint == nil then return end -- nicht unsere Kreatur -> keine Punkte
    if not UntrackEntity(entity) then return end

    if RBB.state.mode == "auto" then
        -- Erster fehlerfreier Kill verifiziert die Kill-API zur Laufzeit.
        RBB.state.mode = "kill"
        Log("event=points mode=kill status=kill_event_verified reason=erster_kill_erfolgreich")
    end
    if RBB.state.mode ~= "kill" then return end -- tick-Modus: keine Kill-Punkte

    Award(PointsForBlueprint(blueprint), "kill", blueprint)
end

local function OnEntityKilledEvent(evt)
    if not RBB.state.killActive then return end
    local ok, err = pcall(HandleKillEvent, evt)
    if not ok then
        RBB.state.killActive = false
        RBB.state.mode = "tick"
        Log("event=points mode=fallback_tick reason=kill_handler_error err=%s",
            tostring(err))
        WriteConsole("rb_economy: Kill-Event-API fehlerhaft -> Fallback-Tick aktiv (siehe exor_logs.txt)")
    end
end

-- ============================================================================
-- Tick-Quelle (Fallback): HourEvent = globaler Spielzeit-Takt
-- (docs/game-reflection/events/hour_event.md, Feld Hour). Aktiv in den
-- Modi auto (bis Kill verifiziert) und tick (nach Kill-Handler-Fehler).
-- ============================================================================
local function OnHourEvent(evt)
    if RBB.state.mode == "kill" then return end
    local ok, err = pcall(function()
        Award(RBB.cfg.pointsPerHourTick, "hour_tick", nil)
    end)
    if not ok then
        Log("event=points_add source=hour_tick status=error err=%s", tostring(err))
    end
end

-- ============================================================================
-- Wave-Spawn (Kernlogik aus Baustein 01, unveraendert uebernommen:
-- Spawn-Ring 8-20 m um den Spieler, Team "" = Blueprint-Standardteam,
-- Existenz-Check vor Spawn).
-- ============================================================================
local function BlueprintExists(blueprint)
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

-- Spawnt eine Einheit; liefert entity (oder nil) — nie einen Fehler nach
-- aussen. Erfolgreiche Spawns werden fuer die Kill-Punkte getrackt.
local function SpawnCreatureAtRandomOffset(mech, blueprint, playerPos)
    local angle = math.random() * 2.0 * math.pi
    local radius = RBB.cfg.spawnRingMin + math.random() *
                   (RBB.cfg.spawnRingMax - RBB.cfg.spawnRingMin)
    local x = playerPos.x + math.cos(angle) * radius
    local z = playerPos.z + math.sin(angle) * radius

    local y = GetTerrainHeight(x, z)
    if y == nil then y = playerPos.y end -- Fallback: Spieler-Hoehe

    local ok, ent = pcall(function()
        return EntityService:SpawnEntity(blueprint, x, y, z, "")
    end)
    if not ok then
        Log("event=spawn api_error blueprint=%s err=%s", blueprint, tostring(ent))
        return nil
    end
    if ent == nil or ent == INVALID_ID then
        Log("event=spawn failed blueprint=%s", blueprint)
        return nil
    end
    Log("event=spawn ok blueprint=%s entity=%s", blueprint, tostring(ent))
    TrackEntity(ent, blueprint)
    return ent
end

-- Liefert (spawned, skipped). Braucht aktiven Spieler-Mech als Anker.
local function SpawnWave(level)
    local playerOk, mech = pcall(function()
        return PlayerService:GetPlayerControlledEnt(0)
    end)
    if not playerOk or mech == nil or mech == INVALID_ID then
        Log("event=wave level=%d status=no_player", level)
        return 0, 0
    end

    local posOk, playerPos = pcall(function()
        return EntityService:GetPosition(mech)
    end)
    if not posOk or playerPos == nil then
        Log("event=wave level=%d status=no_position", level)
        return 0, 0
    end

    local waveDef = RBB.cfg.waves[level]
    local spawned = 0
    local skipped = 0
    for _, unitDef in ipairs(waveDef) do
        local maxCount = math.min(unitDef.count or 0, RBB.cfg.spawnMaxPerUnit)
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
    return spawned, skipped
end

-- ============================================================================
-- Command rb_buy_wave <1..3>: Punkte abziehen, Welle spawnen.
-- Kein aktiver Mech / kein einziger Spawn -> Punkte werden erstattet.
-- ============================================================================
local function BuyWave(rawLevel)
    local level = math.floor(tonumber(rawLevel) or 0)
    local maxLevel = #RBB.cfg.waves
    if level < 1 or level > maxLevel then
        WriteConsole("rb_buy_wave: level %s ungueltig (1..%d) — Aufruf: rb_buy_wave <1..3>, Kosten 10/25/50",
                     tostring(rawLevel), maxLevel)
        Log("event=buy_wave level=%s status=invalid_level", tostring(rawLevel))
        return
    end

    local cost = RBB.cfg.waveCosts[level] or 0
    if RBB.state.points < cost then
        WriteConsole("rb_buy_wave: nur %d Punkte, Welle %d kostet %d (erst Kills sammeln!)",
                     RBB.state.points, level, cost)
        Log("event=buy_wave level=%d cost=%d points=%d status=insufficient_points",
            level, cost, RBB.state.points)
        return
    end

    RBB.state.points = RBB.state.points - cost
    RBB.state.spent = RBB.state.spent + cost
    SaveState()
    Log("event=buy_wave level=%d cost=%d points=%d status=payed", level, cost, RBB.state.points)

    local spawned, skipped = SpawnWave(level)
    if spawned == 0 then
        -- Nichts gespawnt (z. B. kein Spieler-Mech): Erstattung, ehrlicher Loop.
        RBB.state.points = RBB.state.points + cost
        RBB.state.spent = RBB.state.spent - cost
        SaveState()
        Log("event=buy_wave level=%d cost=%d status=refunded reason=no_spawn skipped=%d",
            level, cost, skipped)
        WriteConsole("rb_buy_wave: Welle %d konnte nicht gespawnt werden -> %d Punkte erstattet",
                     level, cost)
        return
    end

    Log("event=buy_wave level=%d cost=%d spawned=%d skipped=%d points=%d status=done",
        level, cost, spawned, skipped, RBB.state.points)
    WriteConsole("rb_buy_wave: Welle %d gekauft (-%d Punkte), %d Kreaturen unterwegs. Punkte: %d",
                 level, cost, spawned, RBB.state.points)
end

-- ============================================================================
-- Commands rb_points [reset] und rb_status
-- ============================================================================
local function CmdPoints(args)
    local s = RBB.state
    if args ~= nil and #args >= 1 and tostring(args[1]) == "reset" then
        ResetState()
        Log("event=points_reset points=0 status=ok")
        WriteConsole("rb_points: auf 0 zurueckgesetzt (db_ok=%s)", tostring(s.dbOk))
        return
    end
    WriteConsole("rb_points: %d (source=%s earned=%d spent=%d income=%d db_ok=%s)",
                 s.points, s.mode, s.earned, s.spent, s.income, tostring(s.dbOk))
    Log("event=points_show points=%d source=%s earned=%d spent=%d income=%d db_ok=%s",
        s.points, s.mode, s.earned, s.spent, s.income, tostring(s.dbOk))
end

local function CmdStatus()
    local s = RBB.state
    WriteConsole("rb_status: version=%s mode=%s kill_active=%s", RBB.version, s.mode,
                 tostring(s.killActive))
    WriteConsole("rb_status: points=%d earned=%d spent=%d income=%d tracked=%d db_ok=%s",
                 s.points, s.earned, s.spent, s.income, RBB.trackedCount, tostring(s.dbOk))
    WriteConsole("rb_status: wave_costs=1:%d 2:%d 3:%d | kill_pts=brabit:%d baxmoth:%d artigian:%d canceroth:%d | hour_tick:%d",
                 RBB.cfg.waveCosts[1], RBB.cfg.waveCosts[2], RBB.cfg.waveCosts[3],
                 RBB.cfg.killPoints["units/ground/brabit"],
                 RBB.cfg.killPoints["units/ground/baxmoth"],
                 RBB.cfg.killPoints["units/ground/artigian"],
                 RBB.cfg.killPoints["units/ground/canceroth"],
                 RBB.cfg.pointsPerHourTick)
    Log("event=status_show version=%s mode=%s points=%d tracked=%d db_ok=%s",
        RBB.version, s.mode, s.points, RBB.trackedCount, tostring(s.dbOk))
end

-- ============================================================================
-- Registrierung (Muster Baustein 01 / EXOR cheat.lua)
-- ============================================================================
pcall(function()
    ConsoleService:RegisterCommand("rb_points", CmdPoints)
    ConsoleService:RegisterCommand("rb_buy_wave", function(args)
        -- Ohne Argument KEIN Default-Kauf (Geld!): Usage-Warnung statt Welle 1.
        local level = nil
        if args ~= nil and #args >= 1 and args[1] ~= nil then
            level = tostring(args[1])
        end
        BuyWave(level)
    end)
    ConsoleService:RegisterCommand("rb_status", CmdStatus)
end)

-- Globaler Event-Handler: Kill-Erkennung + Zeit-Tick-Fallback.
-- RegisterGlobalEventHandler( name, fn ) — docs/modding-files/lua-files/
-- autoexec.md; Namenskonvention PascalCase (Deep-Dive §2).
pcall(function()
    RegisterGlobalEventHandler("EntityKilledEvent", OnEntityKilledEvent)
    RegisterGlobalEventHandler("HourEvent", OnHourEvent)
end)

-- ============================================================================
-- Init
-- ============================================================================
LoadState()
Log("event=mod_load version=%s mode=%s points=%d status=ok",
    RBB.version, RBB.state.mode, RBB.state.points)
