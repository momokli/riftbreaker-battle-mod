-- ============================================================================
-- rbbattle_autoexec.lua  (Einzel-Mod rbbattle, v0.2.0-single)
--
-- Fusion von Baustein 00 (Mod-Skeleton) + Baustein 01 (Wave-Spawn) zu EINEM
-- Mod-Paket "rbbattle" - ersetzt die zwei Einzel-Mods rbbattle_00_skeleton
-- und rbbattle_01_wavespawn (Client-Download + Server-Mod-Liste).
-- Quellen: bausteine/00-mod-skeleton/ + bausteine/01-wave-spawn/ (Repo
-- momokli/riftbreaker-battle-mod), Inhalt 1:1 kombiniert, nichts Neues.
--
-- Zweck: validiert den Mod-Load-Pfad (Skeleton-Lebenszeichen) UND registriert
-- den Console-Command rb_wave <level> (Wellen-Spawning). Kein UI, keine
-- Bindings, kein Bridge-Zusatz.
--
-- Erwartung: GENAU diese zwei Zeilen im Spiel-Log (Documents/The Riftbreaker/
-- exor_logs.txt) bei jeder Kartenerstellung:
--   [RBBATTLE] skeleton ok
--   [RBBATTLE] event=mod_load version=0.2.0-single status=ok
--
-- Genutzte API (wie im Baustein 01 kommentiert):
--   EntityService:SpawnEntity( blueprint, x, y, z, team )
--     -> offizielle Service-API (exorstudios/riftbreaker-wiki, lua-services);
--        identisch implementiert im Spiel-eigenen Cheat-Command
--        "debug_spawn_entity" (OriginalPacksData/lua/commands/cheat.lua):
--        EntityService:SpawnEntity( args[1], position.x, position.y,
--                                    position.z, "" )
--   EnvironmentService:GetTerrainHeight( pos ) -> y auf Gelaendehoehe
--   PlayerService:GetPlayerControlledEnt(0)     -> Spieler-Mech als Anker
--   ResourceManager:GetBlueprint(...)           -> Existenz-Check vor Spawn
--   ConsoleService:RegisterCommand(...)         -> rb_wave registrieren
--
-- Blueprint-Namen: echte Entity-Pfade aus den Original-Spieldaten
-- (Packs/00_win_data.zip -> entities/units/ground/*.ent, verifiziert).
-- Team "": wie EXORs debug_spawn_entity - Einheiten behalten ihr
-- Blueprint-Standard-Team (Feinde = hostil).
--
-- Alle API-Aufrufe sind pcall-gesichert (graceful no-op, Muster Spike).
-- ============================================================================

local RBB = {}
RBB.version = "0.2.0-single"

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
-- Baustein 00 (Mod-Skeleton): Lebenszeichen beim Laden. Zusaetzlich in die
-- In-Game-Konsole schreiben (falls offen), damit der Erfolg auch ohne
-- Log-Datei sichtbar ist.
-- ---------------------------------------------------------------------------
Log("skeleton ok")
WriteConsole("skeleton ok")

-- ---------------------------------------------------------------------------
-- Baustein 01 (Wave-Spawn): rb_wave <level>
-- ---------------------------------------------------------------------------

-- Wellendefinitionen (unveraendert aus Baustein 01 / Spike Experiment A).
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

-- Spawnt eine Einheit eines Blueprints an zufaelliger Position im Ring um
-- den Spieler. Liefert true/false (+ Log) - nie einen Fehler nach aussen.
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

-- Welle der Stufe <level> spawnen (Kernfunktion Baustein 01).
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

    local playerOk, mech = pcall(function()
        return PlayerService:GetPlayerControlledEnt(0)
    end)
    if not playerOk or mech == nil or mech == INVALID_ID then
        Log("event=wave level=%d status=no_player", level)
        WriteConsole("rb_wave: kein Spieler-Mech gefunden (Karte geladen?)")
        -- Fallback-Pfad (z. B. rb_wave 99 -> Welle 1): Skip NICHT still lassen,
        -- sondern explizit warnen, dass der Fallback ohne aktiven Mech
        -- uebersprungen wurde. Verhalten (Skip) bleibt unveraendert.
        if isFallback then
            Log("event=wave level=%d requested=%d warn=no_player_skip_fallback msg=fallback_uebersprungen_kein_aktiver_mech", level, requested)
            WriteConsole("rb_wave: Fallback auf Welle %d (angefragt: %d) wegen fehlendem aktivem Mech uebersprungen", level, requested)
        end
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

-- Command-Registrierung (Spike-Muster; EXOR nutzt dasselbe in
-- OriginalPacksData/lua/commands/cheat.lua).
pcall(function()
    ConsoleService:RegisterCommand("rb_wave", function(args)
        local level = 1
        if args and #args >= 1 then
            level = tonumber(args[1]) or 1
        end
        SpawnWave(level)
    end)
end)

-- Lebenszeichen-Log beim Laden (analog Baustein 01 / Spike).
Log("event=mod_load version=%s status=ok", RBB.version)
