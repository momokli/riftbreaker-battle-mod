-- ============================================================================
-- rbbattle_autoexec.lua  (Einzel-Mod rbbattle, v0.7.0)
--
-- RIFT BATTLE Mod-Core (Foundation):
--   #26 Send-Spawn an den 16 natuerlichen Kartenrand-Spawnern
--       Anker der Send-Kreaturen = spawner-Entities der DOM-Gruppen
--       spawn_enemy_border_{south,north,east,west} statt Spieler-Mech.
--       DOM-Naturwellen bleiben unangetastet (Basis-Druck).
--   #12 rb_wave OHNE Spieler: Fehlen die Kartenrand-Spawner, faellt der
--       Anker auf die Missions-Spawnpunkte zurueck (FindService:
--       FindPlayerSpawnPoints + MapGenerator:GetInitialSpawnPoint) — beides
--       serverseitig verfuegbar, sobald die Welt gebootet ist. Erst wenn auch
--       die fehlen, wird der Spieler-Mech als letzter Fallback genutzt.
--   #23 Runden-Takt: DOM-Wellen-Vorbereitung auf max. 300 s gedeckelt
--       (prepareSpawnTime 420 -> 300, 5-Min-Wellen) + Setup-Log
--       (difficulty/map size/seed werden beim Server-Start gesetzt, s.
--       docs/DUEL_SETUP.md — der Mod loggt die aktiv wirksame Difficulty).
--   #24 Economy (Duell-Oekonomie): Alle gefarmten Ressourcen (Carbonium &
--       Co.) werden als Value getrackt (Ressourcen-Events, Getter-Ladder);
--       bewusste, IRREVERSIBLE Konvertierung in Send-Waehrung per
--       `rb_convert <menge>` (Calcium, #40) bzw. `rb_convert <resource>
--       <amount>`; Spar-Pool persistiert ueber Runden
--       (Global-Database). Built-Value (= nicht konvertierter Farmwert) wird
--       getrennt gefuehrt (Reveal-Basis fuer #27). Fallback: HourEvent-Tick,
--       falls die Ressourcen-Event-API fehlt (docs/research/api-deep-dive.md §1).
--   #42 MVP Single-Player Self-Send (Sich-selber-senden, Testing Mode):
--       Mod-Mode `rb_mode sp|duel` (Default sp). `rb_convert` nutzt Calcium
--       (= Carbonium, #40) als Send-Waehrung. Self-Boost am Wellenstart
--       (Function-Wrap dom_mananger:OnEnterSpawn, Hook-Muster #36): der Pool
--       wird als Zusatz-Spawns an den eigenen Rand-Spawnern (#26) ausgegeben;
--       der %-Staerke-Boost der naechsten Welle (#39) bleibt offen, weil die
--       dom_manager-Wave-Strength-API unverifiziert ist (docs/SEND_HOOK.md).
--       Status via `rb_status` (Runde, Pool, naechster Boost).
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
--   RegisterGlobalEventHandler("ResourceObtainedEvent"/"ResourceChangeEvent",
--     "HourEvent") -> Farm-Value-Quellen + Tick-Fallback       (Issue #24)
--   PlayerService:GetOrCreateGlobalDatabase("rbbattle_economy")
--     -> Persistenz (HasInt/GetIntOrDefault/SetInt/RemoveKey)  (Issue #24)
--
-- Log-Zeilen (externes Parsing, Praefix [RBBATTLE]):
--   event=mod_load version=0.7.0 status=ok mode=sp econ_source=.. econ_pool=.. ...
--   event=wave level=N status=start|done spawned=.. skipped=.. anchor=border|mission|mech
--   event=spawn ok|failed|skip ... anchor=<gruppe>/<id>            (je Kreatur)
--   event=wave_spawners count=N                                    (Pool-Groesse)
--   event=dom_timer patch status=ok|skip|no_class cap=300          (#23)
--   event=setup difficulty=<name> creatures_difficulty=<n>         (bei Map-Ready)
--   event=economy_db status=new|resume|unavailable pool=.. farmed=..      (#24)
--   event=economy_source source=resource_obtained|resource_change|tick    (#24)
--   event=economy_farm source=.. resource=.. amount=.. value=.. farmed=.. (#24)
--   event=convert resource=.. amount=.. value=.. pool=.. irreversible=1   (#24)
--   event=economy_show / economy_reset                                    (#24)
--   event=mode mode=sp|duel status=ok|usage|stub                          (#42)
--   event=wave_hook patch status=ok|skip|no_class reason=no_api           (#42)
--   event=round round=N status=start mode=.. pool=..                      (#42)
--   event=self_boost round=N pool_before=.. spent=.. spawned=.. pool_after=.. (#42)
--   event=status mode=.. round=.. pool=.. boost=..                         (#42)
-- ============================================================================

local RBB = {}
RBB.version = "0.7.0"

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

-- Fallback (v0.2.0-Verhalten), falls die Welt weder Rand-Spawner noch
-- Missions-Spawnpunkte hat: Ring um den Spieler-Mech. Abstaende wie v0.2.0.
-- Der Ring wird auch fuer Missions-Spawnpunkte (#12) genutzt.
RBB.spawnRingMin = 8.0
RBB.spawnRingMax = 20.0

-- Kleine Streuung um den Spawn-Anchor, damit Kreaturen nicht exakt uebereinander
-- stehen; Radius in Metern. (Terrain-Hoehe wird versucht, sonst Anchor-Hoehe.)
RBB.spawnJitterMax = 3.0

-- #23: Deckel fuer die DOM-Wellen-Vorbereitung in Sekunden (5-Min-Wellen).
-- Vanilla-Wert in normal/hard-Survival-Rules: 420 (Beleg:
-- lua/missions/survival/v2/dom_survival_*_rules_{normal,hard}.lua).
RBB.waveIntervalCapS = 300

-- #42: Mod-Mode (MVP). sp = Single-Player Self-Send (Sich-selber-senden),
-- duel = 1v1-Duell (folgt spaeter, hier nur Stub). Default sp.
RBB.mode = "sp"
RBB.round = 0   -- Runden-Zaehler (+1 bei jedem natuerlichen Wellenstart)

-- #42 Self-Boost: der Spar-Pool (Send-Waehrung) wird am Wellenstart als
-- Zusatz-Spawns an den eigenen Rand-Spawnern ausgegeben (#26-Pfad). Wert je
-- Kreatur = Balance-Platzhalter (Tuning #33). Der %-Staerke-Boost der
-- naechsten Welle (#39) bleibt offen, weil die dom_manager-Wave-Strength-API
-- unverifiziert ist (docs/SEND_HOOK.md) — #26 ist der dokumentierte Fallback.
RBB.boostCfg = {
    units = { -- teuerste zuerst gespawnt (greedy)
        { blueprint = "units/ground/brabit",    value = 100 },
        { blueprint = "units/ground/baxmoth",   value = 150 },
        { blueprint = "units/ground/artigian",  value = 200 },
        { blueprint = "units/ground/canceroth", value = 300 },
    },
    maxBoostCreatures = 20, -- Schutz vor Endlos-Spam je Welle
}

-- Vorwaertsdeklaration fuer den Wellenstart-Hook (#42): Definition folgt nach
-- dem Economy-Block (braucht EconomySave); aufgerufen wird er bereits in
-- OnPlayerInitialized / HandleWaveCommand / Mod-Load (Retry-Zeitpunkte,
-- Muster PatchDomTimer).
local PatchWaveStartHook

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

-- ---------------------------------------------------------------------------
-- #12: Missions-Spawnpunkte als Anker OHNE Spieler (Fallback, wenn die Welt
-- keine Kartenrand-Spawner hat). mission_base:SelectPlayerSpawnPoint nutzt
-- FindService:FindPlayerSpawnPoints() + MapGenerator:GetInitialSpawnPoint();
-- beide sind serverseitig verfuegbar, sobald die Welt gebootet ist — kein
-- Client/Player noetig (docs/ASSUMPTIONS.md Fakten 2/3).
-- ---------------------------------------------------------------------------

-- Liefert die Liste der Missions-/Player-Spawnpunkte (frisch pro Aufruf;
-- leer = keine Spawnpunkte auffindbar -> Mech-Fallback).
local function GetMissionSpawnPoints()
    local found = {}
    if type(FindService) == "table" then
        local ok, ents = pcall(function()
            return FindService:FindPlayerSpawnPoints()
        end)
        if ok and type(ents) == "table" then
            for _, ent in ipairs(ents) do
                if ent ~= nil and ent ~= INVALID_ID then
                    found[#found + 1] = ent
                end
            end
        end
    end
    if #found == 0
        and type(MapGenerator) == "table"
        and type(MapGenerator.GetInitialSpawnPoint) == "function" then
        local ok, sp = pcall(MapGenerator.GetInitialSpawnPoint, MapGenerator)
        if ok and sp ~= nil and sp ~= INVALID_ID then
            found[#found + 1] = sp
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

-- #12: Spawnt EINE Kreatur im Ring um einen zufaelligen Missions-Spawnpunkt
-- (Fallback-Anker OHNE Spieler). Liefert true/false (+ Log) - nie Fehler.
local function SpawnCreatureAtMissionSpawnPoint(blueprint, points)
    local anchor = points[math.random(#points)]
    local posOk, pos = pcall(function()
        return EntityService:GetPosition(anchor)
    end)
    if not posOk or pos == nil then
        Log("event=spawn failed blueprint=%s anchor=%s reason=no_position",
            blueprint, GetEntityNameOrId(anchor))
        return false
    end

    local angle = math.random() * 2.0 * math.pi
    local radius = RBB.spawnRingMin + math.random() * (RBB.spawnRingMax - RBB.spawnRingMin)
    local x = pos.x + math.cos(angle) * radius
    local z = pos.z + math.sin(angle) * radius

    local y = GetTerrainHeight(x, z)
    if y == nil then
        y = pos.y -- Fallback: Spawnpunkt-Hoehe
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

    -- Anker-Aufloesung (alle Stufen OHNE Spieler moeglich, vgl. Issue #12):
    --   1) border  = natuerliche Kartenrand-Spawner (#26)
    --   2) mission = Missions-Spawnpunkte (#12, serverseitig verfuegbar)
    --   3) mech    = Spieler-Mech-Ring (letzter Fallback, braucht Spieler)
    local spawners = GetBorderSpawners()
    local anchorMode = "border"
    local missionPoints = nil
    local anchorCount = #spawners
    if #spawners == 0 then
        missionPoints = GetMissionSpawnPoints()
        anchorCount = #missionPoints
        if #missionPoints > 0 then
            anchorMode = "mission"
            Log("event=wave level=%d status=no_border_spawners anchor=mission_spawn_point count=%d",
                level, #missionPoints)
        else
            anchorMode = "mech"
            anchorCount = 1
            Log("event=wave level=%d status=no_border_spawners warn=fallback_mech", level)
        end
    end

    -- Mech-Fallback: Spieler-Mech + Position (nur bei Bedarf laden).
    local mech = nil
    local playerPos = nil
    if anchorMode == "mech" then
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
                elseif anchorMode == "mission" then
                    okSpawn = SpawnCreatureAtMissionSpawnPoint(unitDef.blueprint, missionPoints)
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

    Log("event=wave level=%d status=done spawned=%d skipped=%d anchor=%s anchors=%d",
        level, spawned, skipped, anchorMode, anchorCount)
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
    PatchWaveStartHook()
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
    PatchWaveStartHook() -- weiterer Retry-Zeitpunkt (#42)
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

-- ============================================================================
-- #24 Economy: Farm-Value, irreversibles Convert in Send-Waehrung, Spar-Pool
--
-- Konzept (GDD "Economy" / "Poker-Kern"): Value entsteht durchs Farmen
-- (abgebaute Ressourcen), gesendet wird nur durch bewusste Konvertierung:
--   rb_convert <resource> <amount>   (IRREVERSIBEL — kein Ruecktausch)
-- Der Spar-Pool (Send-Waehrung) persistiert ueber Runden hinweg in einer
-- Global-Database (profilgebunden, docs/misc/database-class.md) und wird
-- spaeter vom Shop ausgegeben (Issue #25). Built-Value (= nicht konvertierter
-- Farmwert, GDD "was gebaut wurde = was NICHT gesendet wurde") wird getrennt
-- gefuehrt und bei Wellenstart aufgedeckt (Issue #27).
--
-- Wertquellen (Dual-Mode, Muster Baustein 05):
--   resource_obtained : RegisterGlobalEventHandler("ResourceObtainedEvent")
--   resource_change   : RegisterGlobalEventHandler("ResourceChangeEvent")
--     Die Getter-Details beider Event-Klassen sind NICHT dokumentiert
--     (docs/research/api-deep-dive.md §1: Felder Entity/Resource bzw.
--     ResourceBasket, Getter unbekannt) -> Getter-Ladder versucht mehrere
--     Konventionen; das ERSTE fehlerfrei lesbare Event sperrt die aktive
--     Quelle (kein Doppel-Zaehlen derselben Ernte ueber beide Events).
--   tick (Fallback)   : Schlaegt die Event-Quelle dauerhaft fehl (pcall-
--     Fehler), zahlt HourEvent pauschal Value (cfg.valuePerHourTick) —
--     dokumentierter Fallback, Mod laeuft dann ohne Erz-Events weiter.
--
-- KEIN io/os/http; alle Service-Zugriffe pcall-gesichert (graceful no-op).
-- ============================================================================

-- Konfiguration: zentrale Wert-Tabelle (Balance-Platzhalter, Tuning in
-- Issue #33 — dort auch echte Boss-/Unit-Listen aus den Spieldaten).
RBB.economyCfg = {
    -- Value-Faktor je Ressource (1 abgebaute Einheit = Faktor Value).
    -- Bekannte Spiel-Ressourcen (api-deep-dive.md §1); unbekannte -> defaultFactor.
    resourceFactors = {
        carbonium = 1,
        steel = 1,
        cobalt = 1,
        palladium = 2,
        titanium = 2,
        uranium_ore = 3,
        morphium = 3,
        flammable_gas = 1,
        geothermal = 1,
        mud = 1,
        magma = 1,
        sludge = 1,
        water = 1,
    },
    defaultFactor = 1,      -- Faktor fuer Ressourcen ohne Eintrag

    -- Fallback-Einkommen pro HourEvent (nur Modus tick/auto).
    valuePerHourTick = 5,

    -- Fehler-Obergrenze der Event-Quelle, bevor dauerhaft auf tick gewechselt
    -- wird (Handler-Fehler = API dieser Event-Klasse unbrauchbar).
    maxSourceErrors = 3,

    -- Obergrenze je Convert-Aufruf (Schutz vor Tippfehlern / Endlos-Args).
    maxConvertAmount = 100000,
}

-- Laufzeit-Zustand + Persistenz-Spiegel (Global-Database "rbbattle_economy").
RBB.economy = {
    source  = "none",        -- none | resource_obtained | resource_change | tick
    pool    = 0,             -- Send-Waehrung (persistiert, Spar-Pool)
    farmed  = 0,             -- Value aus Farmen, kumuliert (persistiert)
    converted = 0,           -- Value in Send-Waehrung gewandelt (persistiert)
    converts = 0,            -- Anzahl Convert-Vorgaenge (persistiert)
    resources = {},          -- Ressource -> gefarmte Menge (int, persistiert)
    sourceErrors = 0,        -- Fehler der aktiven/geprueften Event-Quelle
    eventLocked = false,     -- true: eine Event-Quelle ist aktiv gesperrt
    sourceTried = {},        -- Quelle -> true (bereits gescheitert)
    db = nil,                -- Global-Database (nil = nicht verfuegbar)
    dbOk = false,
}

-- Built-Value (getrennt gefuehrt): farmed - converted = behaltener
-- (nicht gesendeter) Wert. Bewusst abgeleitet statt separat gespeichert
-- (keine doppelte Buchfuehrung).
local function EconomyBuiltValue()
    return math.max(0, RBB.economy.farmed - RBB.economy.converted)
end

local function EconomyDbName() return "rbbattle_economy" end

local function EconomyDbKeyResource(name)
    return "res_" .. tostring(name)
end

local function EconomyLoad()
    local okDb, db = pcall(function()
        return PlayerService:GetOrCreateGlobalDatabase(EconomyDbName())
    end)
    if not okDb or db == nil then
        Log("event=economy_db status=unavailable reason=no_global_database")
        return
    end
    local e = RBB.economy
    e.db = db
    e.dbOk = true
    local okH, has = pcall(function() return db:HasInt("pool") end)
    if okH and has then
        pcall(function()
            e.pool = db:GetIntOrDefault("pool", 0)
            e.farmed = db:GetIntOrDefault("farmed", 0)
            e.converted = db:GetIntOrDefault("converted", 0)
            e.converts = db:GetIntOrDefault("converts", 0)
        end)
        Log("event=economy_db status=resume pool=%d farmed=%d converted=%d",
            e.pool, e.farmed, e.converted)
    else
        Log("event=economy_db status=new db=%s", EconomyDbName())
    end
end

local function EconomySave()
    local db = RBB.economy.db
    if db == nil then return end
    pcall(function()
        db:SetInt("pool", RBB.economy.pool)
        db:SetInt("farmed", RBB.economy.farmed)
        db:SetInt("converted", RBB.economy.converted)
        db:SetInt("converts", RBB.economy.converts)
    end)
end

local function EconomySaveResource(name)
    local db = RBB.economy.db
    if db == nil then return end
    pcall(function()
        db:SetInt(EconomyDbKeyResource(name), RBB.economy.resources[name] or 0)
    end)
end

local function EconomyLoadResources()
    -- Persistierte Ressourcen-Konten zuruecklesen. Ressourcen-Namen sind
    -- feste Spiel-Strings (lowercase, _), kein Injection-Vektor via DB.
    local db = RBB.economy.db
    if db == nil then return end
    local names = { "carbonium", "steel", "cobalt", "palladium", "titanium",
                    "uranium_ore", "morphium", "flammable_gas", "geothermal",
                    "mud", "magma", "sludge", "water" }
    pcall(function()
        for _, n in ipairs(names) do
            local okH, has = pcall(function() return db:HasInt(EconomyDbKeyResource(n)) end)
            if okH and has then
                RBB.economy.resources[n] = db:GetIntOrDefault(EconomyDbKeyResource(n), 0)
            end
        end
    end)
end

-- Getter-Ladder: liefert (resourceName, amount) aus einem Event-Objekt oder
-- nil. Kandidaten decken die unbekannte Event-API ab (Felder Resource bzw.
-- ResourceBasket laut api-deep-dive.md §1); jede Stufe pcall-gesichert.
local function TryEventAmount(evt)
    if evt == nil then return nil end

    -- Stufe 1: evt:GetResource() -> Objekt mit GetName()/GetAmount()
    local ok1, res = pcall(function() return evt:GetResource() end)
    if ok1 and res ~= nil then
        local okN, name = pcall(function() return res:GetName() end)
        if (not okN or name == nil) then
            okN, name = pcall(function() return res:GetResourceName() end)
        end
        if (not okN or name == nil) then
            okN, name = pcall(function() return res.name end)
        end
        local okA, amount = pcall(function() return res:GetAmount() end)
        if (not okA or amount == nil) then
            okA, amount = pcall(function() return res:GetCount() end)
        end
        if (not okA or amount == nil) then
            okA, amount = pcall(function() return res.amount end)
        end
        if okN and okA and name ~= nil and amount ~= nil then
            return tostring(name), amount
        end
    end

    -- Stufe 2: evt:GetResourceName() + evt:GetAmount()
    local ok2n, name2 = pcall(function() return evt:GetResourceName() end)
    local ok2a, amount2 = pcall(function() return evt:GetAmount() end)
    if ok2n and ok2a and name2 ~= nil and amount2 ~= nil then
        return tostring(name2), amount2
    end

    -- Stufe 3: evt:GetResourceBasket() -> Objekt mit GetResourceName/GetAmount
    local ok3, basket = pcall(function() return evt:GetResourceBasket() end)
    if ok3 and basket ~= nil then
        local ok3n, name3 = pcall(function() return basket:GetResourceName() end)
        if (not ok3n or name3 == nil) then
            ok3n, name3 = pcall(function() return basket:GetName() end)
        end
        local ok3a, amount3 = pcall(function() return basket:GetAmount() end)
        if ok3n and ok3a and name3 ~= nil and amount3 ~= nil then
            return tostring(name3), amount3
        end
    end

    return nil -- Event nicht lesbar (unbekannte API-Variante)
end

-- Farm-Buchung: Ressourcen-Konto + farmedValue (+Erst-Lock der Quelle).
local function EconomyBookFarm(source, name, rawAmount)
    local e = RBB.economy

    if e.eventLocked then
        if e.source ~= source then
            return -- andere Quelle ist aktiv: kein Doppel-Zaehlen
        end
    elseif source == "tick" then
        -- auto-Phase: Tick-Einkommen OHNE Sperre — das erste fehlerfrei
        -- gelesene Farm-Event sperrt die Quelle (Muster Baustein 05).
    else
        e.source = source
        e.eventLocked = true
        Log("event=economy_source source=%s status=active", source)
    end

    local amount = math.floor(tonumber(rawAmount) or 0)
    if amount <= 0 then return end

    local factor = RBB.economyCfg.resourceFactors[name]
    if factor == nil then factor = RBB.economyCfg.defaultFactor end
    local value = amount * factor

    local res = e.resources[name] or 0
    e.resources[name] = res + amount
    e.farmed = e.farmed + value

    EconomySave()
    EconomySaveResource(name)
    Log("event=economy_farm source=%s resource=%s amount=%d value=%d farmed=%d built=%d",
        source, name, amount, value, e.farmed, EconomyBuiltValue())
end

-- Event-Handler (je Quelle ein Handler; Fehler zaehlen -> Fallback tick).
local function HandleFarmEvent(evt, source)
    local okCall, err = pcall(function()
        local name, rawAmount = TryEventAmount(evt)
        if name == nil or rawAmount == nil then
            error("event_unreadable")
        end
        EconomyBookFarm(source, name, rawAmount)
    end)
    if not okCall then
        local e = RBB.economy
        -- Fehler der (potentiellen) Event-Quelle zaehlen; unabhaengig davon,
        -- ob sie schon gesperrt ist oder nicht. Ab Obergrenze: dauerhafter
        -- Fallback auf tick (dokumentierter Degradationspfad).
        if e.source ~= "tick" and (not e.eventLocked or e.source == source) then
            e.sourceErrors = e.sourceErrors + 1
            if e.sourceErrors >= RBB.economyCfg.maxSourceErrors then
                e.source = "tick"
                e.eventLocked = true
                Log("event=economy_source source=tick status=fallback reason=handler_errors err=%s",
                    tostring(err))
            end
        end
    end
end

local function OnResourceObtainedEvent(evt)
    HandleFarmEvent(evt, "resource_obtained")
end

local function OnResourceChangeEvent(evt)
    HandleFarmEvent(evt, "resource_change")
end

-- Fallback-Quelle: HourEvent zahlt pauschal, solange keine Event-Quelle
-- aktiv gesperrt ist (auto) oder die Quelle dauerhaft auf tick gefallen ist.
local function OnHourEventEconomy(evt)
    local e = RBB.economy
    if e.eventLocked then
        if e.source == "tick" then
            EconomyBookFarm("tick", "hour_tick", RBB.economyCfg.valuePerHourTick)
        end
        return
    end
    -- auto-Phase (noch kein lesbares Farm-Event): Tick-Einkommen ohne Sperre.
    EconomyBookFarm("tick", "hour_tick", RBB.economyCfg.valuePerHourTick)
end

-- ---------------------------------------------------------------------------
-- Convert (IRREVERSIBEL): bewusste Umwandlung von gefarmtem Wert einer
-- Ressource in Send-Waehrung (Spar-Pool). Es gibt bewusst KEINEN
-- Ruecktausch-Pfad (Pool -> Ressource) im Mod.
-- ---------------------------------------------------------------------------

-- #40/#42: "Calcium" ist die MVP-Send-Waehrung; im Spiel heisst die Basis-
-- Ressource "carbonium" (api-deep-dive.md §1). Alias aufloesen.
local function CanonicalResource(name)
    local n = tostring(name or ""):lower()
    if n == "calcium" then return "carbonium" end
    return n
end

local function ConvertToSendPool(rawResource, rawAmount)
    local e = RBB.economy
    local name = CanonicalResource(rawResource)
    local amount = math.floor(tonumber(rawAmount) or 0)

    if name == "" or amount <= 0 then
        WriteConsole("rb_convert: Aufruf: rb_convert <menge> (Calcium) oder rb_convert <resource> <menge> (z.B. rb_convert carbonium 100)")
        Log("event=convert status=usage")
        return
    end
    if amount > RBB.economyCfg.maxConvertAmount then
        WriteConsole("rb_convert: %d zu gross (max %d pro Aufruf)",
                     amount, RBB.economyCfg.maxConvertAmount)
        Log("event=convert status=amount_too_big resource=%s amount=%d",
            name, amount)
        return
    end

    local have = e.resources[name] or 0
    if have < amount then
        WriteConsole("rb_convert: %s nur %d gefarmt (benoetigt %d) — erst Farmen!",
                     name, have, amount)
        Log("event=convert status=insufficient resource=%s have=%d need=%d",
            name, have, amount)
        return
    end

    local factor = RBB.economyCfg.resourceFactors[name]
    if factor == nil then factor = RBB.economyCfg.defaultFactor end
    local value = amount * factor

    -- Abbuchung + irreversibler Transfer in den Spar-Pool.
    e.resources[name] = have - amount
    e.converted = e.converted + value
    e.converts = e.converts + 1
    e.pool = e.pool + value

    EconomySave()
    EconomySaveResource(name)
    Log("event=convert resource=%s amount=%d value=%d pool=%d status=ok irreversible=1",
        name, amount, value, e.pool)
    WriteConsole("rb_convert: %d %s -> %d Send-Waehrung (IRREVERSIBEL). Pool: %d",
                 amount, name, value, e.pool)
end

-- ---------------------------------------------------------------------------
-- Economy-Status: rb_economy (Anzeige/Reset) — rb_status (Baustein-Stil)
-- wird um die Economy-Felder ergaenzt (Konsolen-Fallback fuer HUD, #27).
-- ---------------------------------------------------------------------------
local function EconomyStatusLines()
    local e = RBB.economy
    WriteConsole("economy: source=%s pool=%d farmed=%d converted=%d built=%d converts=%d db_ok=%s",
                 e.source, e.pool, e.farmed, e.converted, EconomyBuiltValue(),
                 e.converts, tostring(e.dbOk))
    -- Ressourcen-Konten (kompakt, nur belegte).
    local parts = {}
    local sorted = {}
    for k, _ in pairs(e.resources) do
        if e.resources[k] > 0 then sorted[#sorted + 1] = k end
    end
    table.sort(sorted)
    for _, k in ipairs(sorted) do
        parts[#parts + 1] = string.format("%s:%d", k, e.resources[k])
    end
    local resLine = table.concat(parts, " ")
    if resLine == "" then resLine = "-" end
    WriteConsole("economy: resources %s", resLine)
    Log("event=economy_show source=%s pool=%d farmed=%d converted=%d built=%d converts=%d",
        e.source, e.pool, e.farmed, e.converted, EconomyBuiltValue(), e.converts)
end

local function ResetEconomy()
    local e = RBB.economy
    e.pool = 0
    e.farmed = 0
    e.converted = 0
    e.converts = 0
    e.resources = {}
    local db = e.db
    if db ~= nil then
        pcall(function()
            db:SetInt("pool", 0)
            db:SetInt("farmed", 0)
            db:SetInt("converted", 0)
            db:SetInt("converts", 0)
            for _, n in ipairs({ "carbonium", "steel", "cobalt", "palladium",
                                "titanium", "uranium_ore", "morphium",
                                "flammable_gas", "geothermal", "mud", "magma",
                                "sludge", "water" }) do
                db:RemoveKey(EconomyDbKeyResource(n))
            end
        end)
    end
    Log("event=economy_reset status=ok")
end

local function CmdEconomy(args)
    if args ~= nil and #args >= 1 and tostring(args[1]) == "reset" then
        ResetEconomy()
        WriteConsole("rb_economy: alle Konten + Spar-Pool + Ressourcen-Konten auf 0")
        return
    end
    EconomyStatusLines()
end

local function CmdConvert(args)
    -- #40/#42 MVP: `rb_convert <menge>` konvertiert Calcium (ein Argument);
    -- `rb_convert <resource> <menge>` bleibt abwaertskompatibel.
    if args ~= nil and #args == 1 then
        ConvertToSendPool("calcium", args[1])
        return
    end
    local res, amount = nil, nil
    if args ~= nil and #args >= 1 then res = tostring(args[1]) end
    if args ~= nil and #args >= 2 then amount = tostring(args[2]) end
    ConvertToSendPool(res, amount)
end

pcall(function()
    ConsoleService:RegisterCommand("rb_convert", function(args)
        CmdConvert(args)
    end)
    ConsoleService:RegisterCommand("rb_economy", function(args)
        CmdEconomy(args)
    end)
end)

-- ============================================================================
-- #42 MVP Single-Player Self-Send: Mod-Mode + Self-Boost + Status
--
-- Mod-Mode (`rb_mode sp|duel`, Default sp): im sp-Mode boostet der Spar-Pool
-- die EIGENE naechste Naturwelle (Sich-selber-senden). Im duel-Mode (Stub)
-- passiert noch nichts — das 1v1-Routing folgt spaeter (#25/#27).
--
-- Self-Boost (Wellenstart-Hook, Muster #36): der Function-Wrap an
-- dom_mananger:OnEnterSpawn laeuft im selben Call wie die Naturwelle. Der Pool
-- wird greedy (teuerste zuerst) in Zusatz-Spawns an den eigenen Rand-Spawnern
-- (#26) umgesetzt und verbraucht; Rest-Pool bleibt fuer die naechste Welle.
-- Der %-Staerke-Boost (#39) ist offen (unverifizierte Wave-Strength-API,
-- docs/SEND_HOOK.md) — #26 ist der dokumentierte Fallback.
-- ============================================================================

-- Berechnet (deterministisch) die Boost-Kreaturen aus einem Pool-Wert:
-- greedy vom teuersten zum billigsten Blueprint. Liefert spent (verbrauchter
-- Pool), spawned (Anzahl Kreaturen), counts (blueprint -> Anzahl) und order
-- (Reihenfolge der Blueprints). Pure Logik — ohne Game-API, unit-testbar.
local function ComputeBoost(pool)
    local budget = math.floor(tonumber(pool) or 0)
    if budget <= 0 then return 0, 0, {}, {} end

    -- Teuerste zuerst (Kopie, deterministische Sortierung).
    local units = {}
    for _, u in ipairs(RBB.boostCfg.units) do
        units[#units + 1] = { blueprint = u.blueprint, value = u.value }
    end
    table.sort(units, function(a, b) return a.value > b.value end)

    local spent = 0
    local spawned = 0
    local counts = {}
    local order = {}
    while spawned < RBB.boostCfg.maxBoostCreatures do
        local chosen = nil
        for _, u in ipairs(units) do
            if u.value <= (budget - spent) then chosen = u break end
        end
        if chosen == nil then break end
        spent = spent + chosen.value
        spawned = spawned + 1
        if counts[chosen.blueprint] == nil then order[#order + 1] = chosen.blueprint end
        counts[chosen.blueprint] = (counts[chosen.blueprint] or 0) + 1
    end
    return spent, spawned, counts, order
end

-- Wendet den Self-Boost an: Pool -> Zusatz-Spawns an eigenen Rand-Spawnern.
-- Verbraucht nur, was tatsaechlich gespawnt werden konnte; Rest bleibt im Pool.
local function ApplySelfBoost()
    if RBB.mode ~= "sp" then return end
    local e = RBB.economy
    if e.pool <= 0 then return end

    local poolBefore = e.pool
    local spent, spawned, counts, order = ComputeBoost(poolBefore)
    if spawned == 0 then
        Log("event=self_boost status=skip reason=pool_too_small pool=%d", poolBefore)
        return
    end

    local spawners = GetBorderSpawners()
    local anchorMode = "border"
    local mech, playerPos = nil, nil
    if #spawners == 0 then
        anchorMode = "fallback_mech"
        local okM, m = pcall(function() return PlayerService:GetPlayerControlledEnt(0) end)
        if not okM or m == nil or m == INVALID_ID then
            Log("event=self_boost status=skip reason=no_anchor pool=%d", poolBefore)
            return
        end
        local okP, p = pcall(function() return EntityService:GetPosition(m) end)
        if not okP or p == nil then
            Log("event=self_boost status=skip reason=no_anchor pool=%d", poolBefore)
            return
        end
        mech, playerPos = m, p
    end

    local totalSpawned = 0
    for _, bp in ipairs(order) do
        local count = counts[bp] or 0
        for _ = 1, count do
            if BlueprintExists(bp) then
                local okSpawn = false
                if anchorMode == "border" then
                    okSpawn = SpawnCreatureAtBorderSpawner(bp, spawners)
                else
                    okSpawn = SpawnCreatureAtRandomOffset(mech, bp, playerPos)
                end
                if okSpawn then totalSpawned = totalSpawned + 1 end
            end
        end
    end

    -- Nur den tatsaechlich verbrauchten Pool abbuchen (spent ist deterministisch).
    e.pool = poolBefore - spent
    EconomySave()
    Log("event=self_boost round=%d pool_before=%d spent=%d spawned=%d pool_after=%d anchor=%s",
        RBB.round, poolBefore, spent, totalSpawned, e.pool, anchorMode)
    WriteConsole("rb_boost: Welle %d — Pool %d -> +%d Boost-Kreaturen (Rest-Pool %d)",
        RBB.round, poolBefore, totalSpawned, e.pool)
end

-- Kurztext fuer rb_status: was boostet die naechste Welle?
local function BoostSummary()
    if RBB.mode ~= "sp" then return "duel (kein self-boost)" end
    local e = RBB.economy
    if e.pool <= 0 then return "keiner (pool leer)" end
    local _, spawned = ComputeBoost(e.pool)
    return string.format("naechste welle: +%d spawns (pool %d)", spawned, e.pool)
end

-- Wird aus dem OnEnterSpawn-Wrap aufgerufen: Runde zaehlen + Self-Boost.
local function OnNaturalWaveStart()
    RBB.round = RBB.round + 1
    Log("event=round round=%d status=start mode=%s pool=%d",
        RBB.round, RBB.mode, RBB.economy.pool)
    if RBB.mode == "sp" then
        ApplySelfBoost()
    end
end

-- Wellenstart-Hook (#36-Muster): wrap dom_mananger:OnEnterSpawn (Klasse,
-- nicht Instanz). Idempotent, lazy (Mod-Load/PlayerInitializedEvent/jeder
-- Send). Naturwelle bleibt unangetastet (Original zuerst).
RBB.waveStartPatched = false
RBB.waveStartOrig = nil

PatchWaveStartHook = function()
    if RBB.waveStartPatched then
        return RBB.waveStartOrig ~= nil
    end

    local dom = nil
    if type(_G) == "table" then
        dom = rawget(_G, "dom_mananger")
    end
    if type(dom) ~= "table" then
        return false -- Klasse (noch) nicht geladen; naechster Versuch spaeter
    end

    local orig = dom.OnEnterSpawn
    if type(orig) ~= "function" then
        Log("event=wave_hook patch status=skip reason=no_api")
        RBB.waveStartPatched = true
        return false
    end

    if RBB.waveStartOrig == nil then
        RBB.waveStartOrig = orig
        dom.OnEnterSpawn = function(self, state)
            pcall(RBB.waveStartOrig, self, state) -- Naturwelle UNANGETASTET
            OnNaturalWaveStart()
        end
        Log("event=wave_hook patch status=ok")
    end

    RBB.waveStartPatched = true
    return true
end

-- ---------------------------------------------------------------------------
-- Commands: rb_mode (sp|duel) + rb_status (Runde, Pool, naechster Boost).
-- ---------------------------------------------------------------------------
local function CmdMode(args)
    local m = nil
    if args ~= nil and #args >= 1 then m = tostring(args[1]):lower() end
    if m == "sp" then
        RBB.mode = "sp"
        Log("event=mode mode=sp status=ok")
        WriteConsole("rb_mode: sp (Single-Player Self-Send)")
    elseif m == "duel" then
        RBB.mode = "duel"
        Log("event=mode mode=duel status=stub")
        WriteConsole("rb_mode: duel (1v1) — noch nicht implementiert (MVP = sp). Weiter als sp testen.")
    else
        Log("event=mode status=usage mode=%s", RBB.mode)
        WriteConsole("rb_mode: Aufruf rb_mode sp|duel (aktuell: %s)", RBB.mode)
    end
end

local function CmdStatus(args)
    local boost = BoostSummary()
    WriteConsole("rb_status: mode=%s runde=%d pool=%d boost=%s",
        RBB.mode, RBB.round, RBB.economy.pool, boost)
    Log("event=status mode=%s round=%d pool=%d boost=%s",
        RBB.mode, RBB.round, RBB.economy.pool, boost)
end

pcall(function()
    ConsoleService:RegisterCommand("rb_mode", function(args)
        CmdMode(args)
    end)
    ConsoleService:RegisterCommand("rb_status", function(args)
        CmdStatus(args)
    end)
end)

-- Registrierung der Farm-/Fallback-Events (einzeln pcall-gesichert; ein
-- unbekannter Event-Name laesst die anderen unangetastet).
pcall(function()
    RegisterGlobalEventHandler("ResourceObtainedEvent", OnResourceObtainedEvent)
end)
pcall(function()
    RegisterGlobalEventHandler("ResourceChangeEvent", OnResourceChangeEvent)
end)
pcall(function()
    RegisterGlobalEventHandler("HourEvent", OnHourEventEconomy)
end)

-- Init: Persistenz zuruecklesen (Spar-Pool ueberlebt Runden + Neustart).
EconomyLoad()
EconomyLoadResources()

-- Wellenstart-Hook (#42) beim Laden versuchen (nachdem alle Definitionen
-- stehen; weitere Retry-Zeitpunkte: OnPlayerInitialized / jeder Send).
PatchWaveStartHook()

-- Lebenszeichen-Log beim Laden (analog Baustein 01 / Spike).
Log("event=mod_load version=%s status=ok mode=%s anchor=border_spawner_groups timer_cap=%d econ_source=%s econ_pool=%d",
    RBB.version, RBB.mode, RBB.waveIntervalCapS, RBB.economy.source, RBB.economy.pool)
