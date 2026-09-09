-- ============================================================================
-- rbbattle_autoexec.lua  (Einzel-Mod rbbattle, v0.3.0)
--
-- RIFT BATTLE Mod-Core (Foundation):
--   #26 Send-Spawn an den 16 natuerlichen Kartenrand-Spawnern
--       Anker der Send-Kreaturen = spawner-Entities der DOM-Gruppen
--       spawn_enemy_border_{south,north,east,west} statt Spieler-Mech.
--       DOM-Naturwellen bleiben unangetastet (Basis-Druck).
--   #23 Runden-Takt: DOM-Wellen-Vorbereitung auf max. 300 s gedeckelt
--       (prepareSpawnTime 420 -> 300, 5-Min-Wellen) + Setup-Log
--       (difficulty/map size/seed werden beim Server-Start gesetzt, s.
--       docs/DUEL_SETUP.md — der Mod loggt die aktiv wirksame Difficulty).
--
-- Basis: rbbattle v0.2.0-single (feature/single-mod, PR #15) + Baustein 00/01.
-- Kein io/socket/http, keine Bindings, kein UI-Zusatz. Alle API-Aufrufe sind
-- pcall-gesichert (graceful no-op, Muster Spike).
--
-- Genutzte API:
--   EntityService:SpawnEntity( blueprint, x, y, z, team )        (wie v0.2.0)
--   FindService:FindEntitiesByGroup( group )  -> Rand-Spawner-Entities
--     (gruppiert von mission_base:SelectWaveSpawnPoints aus Blueprint
--      "logic/spawn_enemy"; dieselben Anker, die dom_manager per
--      RandomizeSpawnPoint fuer Naturwellen nutzt; Beleg dom_manager.lua
--      + mission_base.lua, Spiel 2.0.58485)
--   RegisterGlobalEventHandler("PlayerInitializedEvent", fn)     (findings #14)
--   DifficultyService:GetCurrentDifficultyName() / CampaignService:
--   GetCreaturesBaseDifficulty()                                  (dom_manager v2)
--   ConsoleService:RegisterCommand(...)                           (wie v0.2.0)
--
-- Log-Zeilen (externes Parsing, Praefix [RBBATTLE]):
--   event=mod_load version=0.3.0 status=ok ...
--   event=wave level=N status=start|done spawned=.. skipped=.. anchor=border|fallback_mech
--   event=spawn ok|failed|skip ... anchor=<gruppe>/<id>            (je Kreatur)
--   event=wave_spawners count=N                                    (Pool-Groesse)
--   event=dom_timer patch status=ok|skip|no_class cap=300          (#23)
--   event=setup difficulty=<name> creatures_difficulty=<n>         (bei Map-Ready)
-- ============================================================================

local RBB = {}
RBB.version = "0.3.0"

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

-- ---------------------------------------------------------------------------
-- Baustein 00 (Mod-Skeleton): Lebenszeichen beim Laden.
-- ---------------------------------------------------------------------------
Log("skeleton ok")
WriteConsole("skeleton ok")

-- ---------------------------------------------------------------------------
-- Konfiguration
-- ---------------------------------------------------------------------------

-- Send-Wellen-Definitionen (unveraendert aus Baustein 01 / Spike Experiment A).
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
RBB.spawnMaxPerUnit = 10 -- Schutz vor Tippfehlern / Endlos-Args

-- #26: Natuerliche Kartenrand-Spawner (DOM-Gruppen, 4 Himmelsrichtungen;
-- je Karte/Generator N Entities pro Gruppe, Issue-Erwartung 4x4 = 16).
RBB.borderSpawnGroups = {
    "spawn_enemy_border_south",
    "spawn_enemy_border_north",
    "spawn_enemy_border_east",
    "spawn_enemy_border_west",
}

-- Fallback (v0.2.0-Verhalten), falls die Welt keine Rand-Spawner hat:
-- Ring um den Spieler-Mech. Abstaende wie v0.2.0.
RBB.spawnRingMin = 8.0
RBB.spawnRingMax = 20.0

-- Kleine Streuung um den Spawn-Anchor, damit Kreaturen nicht exakt uebereinander
-- stehen; Radius in Metern. (Terrain-Hoehe wird versucht, sonst Anchor-Hoehe.)
RBB.spawnJitterMax = 3.0

-- #23: Deckel fuer die DOM-Wellen-Vorbereitung in Sekunden (5-Min-Wellen).
-- Vanilla-Wert in normal/hard-Survival-Rules: 420 (Beleg:
-- lua/missions/survival/v2/dom_survival_*_rules_{normal,hard}.lua).
RBB.waveIntervalCapS = 300

-- ---------------------------------------------------------------------------
-- Kleine Helfer
-- ---------------------------------------------------------------------------

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

-- ---------------------------------------------------------------------------
-- #26: Rand-Spawner finden (Gruppen spawn_enemy_border_*)
-- ---------------------------------------------------------------------------

-- Liefert die Liste der natuerlichen Kartenrand-Spawner-Entities (frisch pro
-- Aufruf; leer = Welt ohne DOM-Rand-Spawner -> Fallback-Pfad).
local function GetBorderSpawners()
    local found = {}
    if type(FindService) ~= "table" then
        Log("event=spawn skip reason=find_service_missing")
        return found
    end
    for _, group in ipairs(RBB.borderSpawnGroups) do
        local ok, ents = pcall(function()
            return FindService:FindEntitiesByGroup(group)
        end)
        if ok and type(ents) == "table" then
            for _, ent in ipairs(ents) do
                if ent ~= nil and ent ~= INVALID_ID then
                    found[#found + 1] = ent
                end
            end
        end
    end
    return found
end

local function GetEntityNameOrId(ent)
    if type(EntityService) ~= "table" then return tostring(ent) end
    local ok, name = pcall(function()
        return EntityService:GetName(ent)
    end)
    if ok and name ~= nil and name ~= "" then return name end
    return tostring(ent)
end

-- Spawnt EINE Kreatur an einem zufaelligen natuerlichen Rand-Spawner.
-- Liefert true/false (+ Log) - nie einen Fehler nach aussen.
local function SpawnCreatureAtBorderSpawner(blueprint, spawners)
    local anchor = spawners[math.random(#spawners)]
    local posOk, pos = pcall(function()
        return EntityService:GetPosition(anchor)
    end)
    if not posOk or pos == nil then
        Log("event=spawn failed blueprint=%s anchor=%s reason=no_position",
            blueprint, GetEntityNameOrId(anchor))
        return false
    end

    -- Kleine Streuung um den Anchor (Kartenrand-Verband).
    local angle = math.random() * 2.0 * math.pi
    local radius = math.random() * RBB.spawnJitterMax
    local x = pos.x + math.cos(angle) * radius
    local z = pos.z + math.sin(angle) * radius

    local y = GetTerrainHeight(x, z)
    if y == nil then
        y = pos.y -- Fallback: Anchor-Hoehe (Nicht-Spielbar-Streifen)
    end

    local ok, ent = pcall(function()
        return EntityService:SpawnEntity(blueprint, x, y, z, "")
    end)
    if not ok then
        Log("event=spawn api_error blueprint=%s anchor=%s err=%s",
            blueprint, GetEntityNameOrId(anchor), tostring(ent))
        return false
    end
    if ent == nil or ent == INVALID_ID then
        Log("event=spawn failed blueprint=%s anchor=%s",
            blueprint, GetEntityNameOrId(anchor))
        return false
    end
    Log("event=spawn ok blueprint=%s entity=%s anchor=%s",
        blueprint, tostring(ent), GetEntityNameOrId(anchor))
    return true
end

-- Spawnt eine Einheit eines Blueprints an zufaelliger Position im Ring um
-- den Spieler (v0.2.0-Fallback, nur wenn keine Rand-Spawner existieren).
local function SpawnCreatureAtRandomOffset(mech, blueprint, playerPos)
    local angle = math.random() * 2.0 * math.pi
    local radius = RBB.spawnRingMin + math.random() * (RBB.spawnRingMax - RBB.spawnRingMin)
    local x = playerPos.x + math.cos(angle) * radius
    local z = playerPos.z + math.sin(angle) * radius

    local y = GetTerrainHeight(x, z)
    if y == nil then
        y = playerPos.y -- Fallback: Spieler-Hoehe
    end

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

-- ---------------------------------------------------------------------------
-- Send-Welle der Stufe <level> spawnen (Kernfunktion Baustein 01, umgebaut
-- fuer #26): Anker = natuerliche Kartenrand-Spawner, Fallback = Mech-Ring.
-- ---------------------------------------------------------------------------
local function SpawnWave(level)
    level = math.floor(tonumber(level) or 1)
    local requested = level          -- original angefragtes Level (fuer Warnung)
    local isFallback = false         -- true, wenn level auf Welle 1 zurueckfiel
    if level < 1 then level = 1 end
    if level > RBB.maxWaveLevel then
        WriteConsole("rb_wave: level %d ungueltig (1..%d), nutze 1", level, RBB.maxWaveLevel)
        Log("event=wave level=%d status=invalid_level", level)
        isFallback = true
        level = 1
    end

    Log("event=wave level=%d status=start", level)

    local waveDef = RBB.waves[level]
    local spawned = 0
    local skipped = 0

    -- #26: Kartenrand-Spawner ermitteln (Bevorzugt; braucht KEINEN Spieler,
    -- funktioniert damit auch auf leeren/unpausierten Servern, vgl. Issue #12).
    local spawners = GetBorderSpawners()
    local anchorMode = "border"
    if #spawners == 0 then
        anchorMode = "fallback_mech"
        Log("event=wave level=%d status=no_border_spawners warn=fallback_mech", level)
    end

    -- Fallback-Pfad: Spieler-Mech + Position (nur bei Bedarf laden).
    local mech = nil
    local playerPos = nil
    if anchorMode == "fallback_mech" then
        local playerOk, m = pcall(function()
            return PlayerService:GetPlayerControlledEnt(0)
        end)
        if not playerOk or m == nil or m == INVALID_ID then
            Log("event=wave level=%d status=no_player", level)
            WriteConsole("rb_wave: kein Spieler-Mech gefunden (Karte geladen?)")
            if isFallback then
                Log("event=wave level=%d requested=%d warn=no_player_skip_fallback msg=fallback_uebersprungen_kein_aktiver_mech", level, requested)
                WriteConsole("rb_wave: Fallback auf Welle %d (angefragt: %d) wegen fehlendem aktivem Mech uebersprungen", level, requested)
            end
            return false
        end
        mech = m
        local posOk, p = pcall(function()
            return EntityService:GetPosition(mech)
        end)
        if not posOk or p == nil then
            Log("event=wave level=%d status=no_position", level)
            return false
        end
        playerPos = p
    end

    if #spawners > 0 then
        -- Nur loggen, wenn erstmals genutzt (kein Spam pro Welle).
        if not RBB.spawnerCountLogged then
            RBB.spawnerCountLogged = true
            Log("event=wave_spawners count=%d groups=%d",
                #spawners, #RBB.borderSpawnGroups)
        end
    end

    for _, unitDef in ipairs(waveDef) do
        local maxCount = math.min(unitDef.count or 0, RBB.spawnMaxPerUnit)
        for _ = 1, maxCount do
            if BlueprintExists(unitDef.blueprint) then
                local okSpawn = false
                if anchorMode == "border" then
                    okSpawn = SpawnCreatureAtBorderSpawner(unitDef.blueprint, spawners)
                else
                    okSpawn = SpawnCreatureAtRandomOffset(mech, unitDef.blueprint, playerPos)
                end
                if okSpawn then
                    spawned = spawned + 1
                end
            else
                skipped = skipped + 1
                Log("event=spawn skip blueprint=%s reason=not_found", unitDef.blueprint)
            end
        end
    end

    Log("event=wave level=%d status=done spawned=%d skipped=%d anchor=%s spawners=%d",
        level, spawned, skipped, anchorMode, #spawners)
    WriteConsole("rb_wave level %d: %d Kreaturen gespawnt (%d uebersprungen), Anker: %s",
                 level, spawned, skipped, anchorMode)
    return spawned > 0
end

-- ---------------------------------------------------------------------------
-- #23: DOM-Wellen-Timer auf 300 s deckeln (Function-Wrap, vgl. docs/SEND_HOOK.md)
--
-- Wrappt dom_mananger:GetPrepareSpawnTime (Klasse, nicht Instanz). Die Klasse
-- existiert erst, nachdem MissionService:AddGameRule die dom_manager.lua
-- geladen hat -> Patch-Versuche: Mod-Load, PlayerInitializedEvent und jeder
-- Send-Aufruf. Idempotent. NIE per require nachladen (Re-Execution wuerde die
-- Klassen-Tabelle ersetzen und bestehende DOM-Instanzen entkoppeln!).
-- ---------------------------------------------------------------------------
RBB.domTimerPatched = false
RBB.domTimerOrig = nil

local function PatchDomTimer()
    if RBB.domTimerPatched then
        return RBB.domTimerOrig ~= nil
    end

    local dom = nil
    if type(_G) == "table" then
        dom = rawget(_G, "dom_mananger")
    end
    if type(dom) ~= "table" then
        return false -- Klasse (noch) nicht geladen; naechster Versuch spaeter
    end

    local orig = dom.GetPrepareSpawnTime
    if type(orig) ~= "function" then
        Log("event=dom_timer patch status=skip reason=no_api")
        RBB.domTimerPatched = true
        return false
    end

    if RBB.domTimerOrig == nil then
        RBB.domTimerOrig = orig
        dom.GetPrepareSpawnTime = function(self)
            local okT, t = pcall(RBB.domTimerOrig, self)
            if not okT or type(t) ~= "number" then
                t = RBB.waveIntervalCapS
            end
            if t > RBB.waveIntervalCapS then
                t = RBB.waveIntervalCapS
            end
            return t
        end
        Log("event=dom_timer patch status=ok cap=%d", RBB.waveIntervalCapS)
    end

    RBB.domTimerPatched = true
    return true
end

-- Setup-/Difficulty-Log (bei Map-Ready): Beleg fuer #23-Teil "Schwierigkeit
-- hard" aus Sicht des laufenden Servers. Gesetzt wird die Difficulty beim
-- Server-/Welt-Start (C++/GameServerOptions, s. docs/DUEL_SETUP.md).
RBB.setupLogged = false
local function LogMapSetupInfo()
    if RBB.setupLogged then return end
    RBB.setupLogged = true

    local difficulty = "?"
    if type(DifficultyService) == "table"
        and type(DifficultyService.GetCurrentDifficultyName) == "function" then
        local okD, name = pcall(DifficultyService.GetCurrentDifficultyName,
                                DifficultyService)
        if okD and name ~= nil then difficulty = tostring(name) end
    end

    local creatureDifficulty = "?"
    if type(CampaignService) == "table"
        and type(CampaignService.GetCreaturesBaseDifficulty) == "function" then
        local okC, cd = pcall(CampaignService.GetCreaturesBaseDifficulty,
                              CampaignService)
        if okC and cd ~= nil then creatureDifficulty = tostring(cd) end
    end

    Log("event=setup difficulty=%s creatures_difficulty=%s timer_cap=%d",
        difficulty, creatureDifficulty, RBB.waveIntervalCapS)
end

local function OnPlayerInitialized()
    -- Welt ist fertig aufgesetzt: DOM-Klasse jetzt sicher verfuegbar.
    PatchDomTimer()
    LogMapSetupInfo()
end

-- ---------------------------------------------------------------------------
-- Command-Registrierungen: rb_wave (Bestand, Bridge-Pfad Issue #18) + Alias
-- rb_send (klarer Send-Name fuer spaetere Shop-/Queue-Integration #25).
-- ---------------------------------------------------------------------------
local function HandleWaveCommand(args, commandName)
    local level = 1
    if args and #args >= 1 then
        level = tonumber(args[1]) or 1
    end
    PatchDomTimer() -- weiterer Retry-Zeitpunkt (billig, idempotent)
    SpawnWave(level)
end

pcall(function()
    ConsoleService:RegisterCommand("rb_wave", function(args)
        HandleWaveCommand(args, "rb_wave")
    end)
end)

pcall(function()
    ConsoleService:RegisterCommand("rb_send", function(args)
        HandleWaveCommand(args, "rb_send")
    end)
end)

-- PlayerInitializedEvent: Patch + Setup-Log, sobald die Welt steht
-- (findings #14: RegisterGlobalEventHandler laeuft verifiziert in autoexec).
local evtApiOk, evtApiErr = pcall(function()
    RegisterGlobalEventHandler("PlayerInitializedEvent", OnPlayerInitialized)
end)
if not evtApiOk then
    Log("event=map_ready status=skip reason=event_api_missing")
end

-- Erster Patch-Versuch direkt beim Laden (falls die DOM-Klasse schon existiert)
-- und Setup-Log, falls kein Event-API verfuegbar ist.
PatchDomTimer()
if not evtApiOk then
    LogMapSetupInfo()
end

-- Lebenszeichen-Log beim Laden (analog Baustein 01 / Spike).
Log("event=mod_load version=%s status=ok anchor=border_spawner_groups timer_cap=%d",
    RBB.version, RBB.waveIntervalCapS)
