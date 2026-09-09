-- ============================================================================
-- rbbattle_autoexec.lua  (Einzel-Mod rbbattle, v0.3.0)
--
-- RIFT BATTLE Mod-Core (Foundation):
--   #26 Send-Spawn an den 16 natuerlichen Kartenrand-Spawnern
--       Anker der Send-Kreaturen = spawner-Entities der DOM-Gruppen
--       spawn_enemy_border_{south,north,east,west} statt Spieler-Mech.
--       DOM-Naturwellen bleiben unangetastet (Basis-Druck).

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
--   ConsoleService:RegisterCommand(...)                           (wie v0.2.0)
--
-- Log-Zeilen (externes Parsing, Praefix [RBBATTLE]):
--   event=mod_load version=0.3.0 status=ok ...
--   event=wave level=N status=start|done spawned=.. skipped=.. anchor=border|fallback_mech
--   event=spawn ok|failed|skip ... anchor=<gruppe>/<id>            (je Kreatur)
--   event=wave_spawners count=N                                    (Pool-Groesse)
-- ============================================================================

local RBB = {}
RBB.version = "0.3.0" -- #26 border-send; #23 folgt (separater Commit)

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
-- Command-Registrierungen: rb_wave (Bestand, Bridge-Pfad Issue #18) + Alias
-- rb_send (klarer Send-Name fuer spaetere Shop-/Queue-Integration #25).
-- ---------------------------------------------------------------------------
local function HandleWaveCommand(args, commandName)
    local level = 1
    if args and #args >= 1 then
        level = tonumber(args[1]) or 1
    end
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

-- Lebenszeichen-Log beim Laden (analog Baustein 01 / Spike).
Log("event=mod_load version=%s status=ok anchor=border_spawner_groups",
    RBB.version)
