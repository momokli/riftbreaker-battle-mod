-- ============================================================================
-- rbbattle_autoexec.lua  (Einzel-Mod rbbattle, v0.17.0)
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
--   #25 Send-Queue & Shop-HUD: Custom-UI-Shop mit Tier-Struktur
--       (Legion-TD-2-artig) + Boss-Tier; `rb_buy_wave <unit> [count]` kauft
--       Einheiten aus dem Shop in die Send-Queue (deduziert den Spar-Pool
--       sofort); der Wellenstart-Hook (dom_mananger:OnEnterSpawn) gibt die
--       Queue beim naechsten natuerlichen Wellenstart als Zusatz-Spawns an
--       den eigenen Rand-Spawnern (#26) aus — Tiered Units + Bosse boosten
--       so die NAEchste Welle. Senden jederzeit bis Wellenstart, unbegrenzt
--       oft (Deckel maxQueueCreatures). Preisliste v1 = Struktur/Platzhalter,
--       KEIN Balancing (Tuning #33/#12).
--   #28 Win-Condition (HQ-HP, Leak, Match-Ende): Leaks (feindliche Kreaturen,
--       die die Trigger-Zone ums HQ erreichen, EnteredTriggerEvent) senken den
--       HQ-HP (event=leak/event=hq_hp); HQ-Tod (HP<=0 oder RespawnFailedEvent
--       der HQ-Entity) -> event=hq_dead/match_end. Report laeuft als
--       [RBBATTLE]-Log-Zeile ueber die Bridge an den Tournament-Server
--       (POST /report hq_hp); Sieg-Zustand + Rematch sind dort implementiert.
--   #27 Reveal-HUD (Poker): Vor dem Wellenstart sind beide Werte (Gegner-
--       Built-Value + WAS kommt) verborgen; BEIM Wellenstart lockt der Mod
--       den EIGENEN Built-Value + die eigene Send-Komposition (event=reveal),
--       die Bridge injiziert die Gegner-Werte (rb_reveal). rb_hud liefert die
--       HUD-Standardfelder (Runde, Countdown, Pool, HQ beider Teams);
--       rb_round_start verbirgt den Reveal fuer die naechste Build-Phase.
--
-- Basis: rbbattle v0.2.0-single (feature/single-mod, PR #15) + Baustein 00/01.
-- Kein io/socket/http, keine Bindings, kein eigenes HUD-Framework (nur
-- Shop-Popup ueber GuiService:OpenPopup, Muster Baustein 02). Alle API-Aufrufe
-- sind pcall-gesichert (graceful no-op, Muster Spike).
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
--   event=mod_load version=0.17.0 status=ok mode=sp econ_source=.. econ_pool=.. hq_hp=.. hq_dead=..
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
--   event=round round=N status=start mode=.. pool=.. queue=..             (#42/#25)
--   event=buy_wave unit=.. tier=.. count=.. price=.. total=.. pool=.. queue=.. status=.. (#25)
--   event=shop status=listed|popup_opened|popup_skipped|api_missing tiers=.. (#25)
--   event=queue status=show count=.. value=.. pool=..                     (#25)
--   event=send_queue round=N status=done|empty|skip spawned=.. value=.. anchor=.. (#25)
--   event=status mode=.. round=.. pool=.. queue=..                         (#42/#25)
--   event=leak damage=.. hp_before=.. hp=..                                (#28)
--   event=hq_hp hp=.. dead=..                                              (#28)
--   event=hq_dead status=match_end hp=0                                    (#28)
--   event=match_end reason=hq_destroyed winner=opponent                    (#28)
--   event=hq_respawn status=unmatched entity=..                            (#28)
--   event=hq_zone status=.. entity=.. hp=.. dead=..                        (#28)
--   event=hq_status / hq_reset / hq_entity                                 (#28)
--   event=reveal round=.. status=revealed built_own=.. built_opp=.. send_own=.. incoming=.. (#27)
--   event=reveal_opp round=.. built_opp=.. hq_opp=.. incoming=.. status=ok  (#27)
--   event=round_start round=.. status=build reveal=hidden                   (#27)
--   event=hud round=.. countdown=.. pool=.. built_own=.. built_opp=.. incoming=.. hq_own=.. hq_opp=.. reveal=.. (#27)
-- ============================================================================

local RBB = {}
RBB.version = "0.17.0"

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

-- #25 Send-Queue & Shop-HUD: Preisliste v1 (Struktur/Platzhalter — KEIN
-- Balancing, Tuning in Issue #33/#12). Tier-Struktur Legion-TD-2-artig:
-- guenstige Tier-1-Einheiten bis teure Boss-Einheiten. Blueprints sind
-- Platzhalter aus dem bestehenden Wellen-/Boost-Pool; echte Boss-/Unit-Listen
-- aus den Spieldaten folgen in der Balance-Session (#33).
RBB.shopCfg = {
    tiers = {
        { id = "t1", name = "Tier 1", units = {
            { id = "brabit",   blueprint = "units/ground/brabit",   price = 100 },
            { id = "baxmoth",  blueprint = "units/ground/baxmoth",  price = 150 },
        } },
        { id = "t2", name = "Tier 2", units = {
            { id = "artigian", blueprint = "units/ground/artigian", price = 200 },
        } },
        { id = "t3", name = "Tier 3", units = {
            { id = "canceroth", blueprint = "units/ground/canceroth", price = 300 },
        } },
        { id = "boss", name = "Boss", units = {
            -- Platzhalter-Boss (Blueprint wie Tier 3; echte Boss-Liste folgt
            -- in der Balance-Session #33). boss=true markiert die Boss-Tier.
            { id = "boss", blueprint = "units/ground/canceroth", price = 800, boss = true },
        } },
    },
    maxQueueCreatures = 40, -- Schutz vor Endlos-Spam je Welle
}

-- #25: Send-Queue — gekaufte (noch nicht gesendete) Einheiten fuer die
-- naechste Naturwelle. Einheiten werden per `rb_buy_wave` aus dem Shop
-- gekauft (Pool wird sofort deduziert); beim naechsten Wellenstart gibt der
-- Hook die Queue als Zusatz-Spawns aus und leert sie. In-Memory (wird je
-- Welle verbraucht), der Spar-Pool persistiert weiterhin ueber Runden.
RBB.sendQueue = {
    units = {},   -- { blueprint=.., price=.., boss=.., tier=.. }
    count = 0,    -- Anzahl Einheiten in der Queue
    value = 0,    -- Summe der Kaufpreise (fuer Anzeige/Status)
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
-- #27 Reveal-HUD (Poker): Built-Value + Send-Komposition bei Wellenstart
--
-- GDD "Reveal"/"HUD": Vor dem Wellenstart sind beide Werte verborgen
-- (Gegner-Built-Value + WAS kommt); BEIM Wellenstart werden Built-Value
-- beider Teams + die Send-Komposition aufgedeckt. Der Mod ist die Lock- und
-- Empfaengerseite (kein I/O): er lockt den EIGENEN Built-Value + die eigene
-- Send-Komposition beim natuerlichen Wellenstart (event=reveal), nimmt die
-- Gegner-Werte per `rb_reveal` (Bridge -> exec) entgegen und liefert die
-- HUD-Standardfelder per `rb_hud`. `rb_round_start` verbirgt den Reveal
-- wieder (Start der naechsten Build-Phase, Bridge-Signal "round steigt").
-- ============================================================================

-- Reveal-Zustand (pro Runde): locked=false = vor Wellenstart (verborgen).
RBB.reveal = {
    round    = 0,       -- Runde, auf die sich der (letzte) Reveal bezieht
    locked   = false,   -- true: bei Wellenstart gelockt (Werte aufgedeckt)
    builtOwn = 0,       -- eigener Built-Value (Lock bei Wellenstart)
    builtOpp = 0,       -- Gegner-Built-Value (verborgen bis rb_reveal)
    sendOwn  = "-",     -- eigene Send-Komposition (WAS rausgeht)
    incoming = "-",     -- eingehende Send-Komposition (WAS kommt)
    hqOpp    = 0,       -- Gegner-HQ-HP (verborgen bis rb_reveal)
    oppKnown = false,   -- true: Gegner-Werte via rb_reveal injiziert
}

-- Kompakte Komposition einer Einheiten-Liste -> "bp:count,bp:count" (stabil).
local function RevealComposition(units)
    local counts, order = {}, {}
    for _, u in ipairs(units or {}) do
        local id = tostring(u.blueprint or "?")
        if counts[id] == nil then
            counts[id] = 0
            order[#order + 1] = id
        end
        counts[id] = counts[id] + 1
    end
    local parts = {}
    for _, id in ipairs(order) do
        parts[#parts + 1] = string.format("%s:%d", id, counts[id])
    end
    local s = table.concat(parts, ",")
    if s == "" then s = "-" end
    return s
end

-- Countdown bis zur naechsten Welle: DOM-Prepare-Zeit (gekappt), Fallback cap.
local function RevealCountdown()
    local t = RBB.waveIntervalCapS
    local dom = nil
    if type(_G) == "table" then dom = rawget(_G, "dom_mananger") end
    if type(dom) == "table" and type(dom.GetPrepareSpawnTime) == "function" then
        local ok, v = pcall(dom.GetPrepareSpawnTime, dom)
        if ok and type(v) == "number" then t = math.floor(v) end
    end
    return t
end

-- Vor Wellenstart: beide Werte verbergen (Start einer neuen Build-Phase).
local function RevealReset()
    local r = RBB.reveal
    r.locked = false
    r.builtOwn = 0
    r.builtOpp = 0
    r.sendOwn = "-"
    r.incoming = "-"
    r.hqOpp = 0
    r.oppKnown = false
end

-- Bei Wellenstart: eigenen Built-Value + Send-Komposition locken + loggen.
local function RevealLock()
    local r = RBB.reveal
    r.round = RBB.round
    r.locked = true
    r.builtOwn = EconomyBuiltValue()
    r.sendOwn = RevealComposition(RBB.sendQueue.units)
    local builtOpp = "hidden"
    local incoming = "hidden"
    if r.oppKnown then
        builtOpp = tostring(r.builtOpp)
        incoming = r.incoming
    end
    Log("event=reveal round=%d status=revealed built_own=%d built_opp=%s send_own=%s incoming=%s",
        r.round, r.builtOwn, builtOpp, r.sendOwn, incoming)
end

-- ============================================================================
-- #25 Send-Queue & Shop-HUD: Tiered Units + Bosse boosten die naechste Welle
--
-- Ersetzt den MVP-Self-Boost (#42): statt den Spar-Pool am Wellenstart
-- automatisch (greedy) zu verbrauchen, kauft der Spieler Einheiten EXPLIZIT
-- aus dem Shop (`rb_buy_wave <unit> [count]`) — der Pool wird sofort
-- deduziert, die Einheit landet in der Send-Queue. Der Wellenstart-Hook
-- (dom_mananger:OnEnterSpawn, Muster #36/#42) gibt die Queue beim naechsten
-- natuerlichen Wellenstart als Zusatz-Spawns an den eigenen Rand-Spawnern
-- (#26) aus — Tiered Units + Bosse boosten so die NAEchste Welle. Senden
-- jederzeit bis Wellenstart, unbegrenzt oft (maxQueueCreatures als Deckel).
--
-- Mod-Mode (`rb_mode sp|duel`, Default sp): im sp-Mode boostet die Queue die
-- EIGENE naechste Naturwelle (Sich-selber-senden). Im duel-Mode (Stub)
-- passiert noch nichts — das 1v1-Routing folgt spaeter (#27).
--
-- Custom-UI-Shop (`rb_shop`): oeffnet ein Popup (GuiService:OpenPopup, Muster
-- Baustein 02) mit der Tier-/Preis-Liste; der Kauf selbst laeuft als
-- Konsolen-Command `rb_buy_wave` (der Eingabe-Hook fuer die Send-Queue —
-- Bridge/Trainer fuehren ihn aus, wie rb_wave/rb_convert).
-- ============================================================================

-- Flache Suche ueber alle Tiers: unitId -> unit-Def + Tier. Nil = unbekannt.
local function FindShopUnit(unitId)
    for _, tier in ipairs(RBB.shopCfg.tiers) do
        for _, u in ipairs(tier.units) do
            if u.id == unitId then return u, tier end
        end
    end
    return nil, nil
end

-- Kauf: `rb_buy_wave <unitId> [count]`. Deduziert den Spar-Pool sofort und
-- haengt die Einheit(en) an die Send-Queue. Liefert nie einen Fehler nach
-- aussen; Validierung (unbekannt/zu teuer/Queue voll) vorab per Log.
local function BuyWave(rawUnitId, rawCount)
    local q = RBB.sendQueue
    local unitId = tostring(rawUnitId or ""):lower()

    if unitId == "" or unitId == "help" then
        WriteConsole("rb_buy_wave: Aufruf rb_buy_wave <unit> [count] — rb_shop zeigt Units/Preise")
        Log("event=buy_wave status=usage")
        return
    end

    local unit, tier = FindShopUnit(unitId)
    if unit == nil then
        WriteConsole("rb_buy_wave: unbekannte Unit '%s' — rb_shop zeigt die Liste", unitId)
        Log("event=buy_wave status=unknown_unit unit=%s", unitId)
        return
    end

    local count = math.floor(tonumber(rawCount) or 1)
    if count < 1 then count = 1 end
    if q.count + count > RBB.shopCfg.maxQueueCreatures then
        WriteConsole("rb_buy_wave: Queue voll (%d + %d > max %d)",
            q.count, count, RBB.shopCfg.maxQueueCreatures)
        Log("event=buy_wave status=queue_full unit=%s count=%d queue=%d",
            unitId, count, q.count)
        return
    end

    local total = unit.price * count
    if RBB.economy.pool < total then
        WriteConsole("rb_buy_wave: Pool reicht nicht (%d < %d) — erst rb_convert",
            RBB.economy.pool, total)
        Log("event=buy_wave status=insufficient unit=%s count=%d need=%d pool=%d",
            unitId, count, total, RBB.economy.pool)
        return
    end

    -- Abbuchung + Queue-Anhang (Kaufpreis sofort faellig, unwiderruflich).
    RBB.economy.pool = RBB.economy.pool - total
    for _ = 1, count do
        q.units[#q.units + 1] = {
            blueprint = unit.blueprint,
            price     = unit.price,
            boss      = unit.boss == true,
            tier      = tier.id,
        }
    end
    q.count = q.count + count
    q.value = q.value + total
    EconomySave()

    Log("event=buy_wave unit=%s tier=%s count=%d price=%d total=%d pool=%d queue=%d status=ok",
        unitId, tier.id, count, unit.price, total, RBB.economy.pool, q.count)
    WriteConsole("rb_buy_wave: +%d %s (%s, %d je) -> Queue %d, Pool %d",
        count, unitId, tier.name, unit.price, q.count, RBB.economy.pool)
end

-- Textliste des Shops (Tier-Struktur + Preise) fuer Konsole UND Popup.
local function ShopText()
    local lines = { "RBBATTLE — Shop (Send-Queue)" }
    for _, tier in ipairs(RBB.shopCfg.tiers) do
        lines[#lines + 1] = string.format("— %s —", tier.name)
        for _, u in ipairs(tier.units) do
            local boss = ""
            if u.boss == true then boss = " [BOSS]" end
            lines[#lines + 1] = string.format("  %s%s  %d", u.id, boss, u.price)
        end
    end
    return lines
end

-- Custom-UI-Popup (GuiService:OpenPopup, Muster Baustein 02). Best-effort:
-- ohne Spieler/API -> nur Konsolen-Liste (Log status=popup_skipped/api_missing).
local function OpenShopPopup()
    if not (GuiService and GuiService.OpenPopup) then
        Log("event=shop status=api_missing")
        return
    end
    local okM, mech = pcall(function()
        return PlayerService:GetPlayerControlledEnt(0)
    end)
    if not okM or mech == nil or mech == INVALID_ID then
        Log("event=shop status=popup_skipped reason=no_player")
        return
    end
    local text = '<style="header_35">RBBATTLE — Shop</style>\r\n'
        .. table.concat(ShopText(), "\r\n")
        .. '\r\n<style="big_red">Kauf:</style> rb_buy_wave <unit> [count]'
    local ok, err = pcall(function()
        return GuiService:OpenPopup(mech, "gui/popup/popup_template_1button", text)
    end)
    if not ok then
        Log("event=shop status=popup_skipped reason=error err=%s", tostring(err))
        return
    end
    Log("event=shop status=popup_opened tiers=%d", #RBB.shopCfg.tiers)
end

-- Konsolen-/Status-Anzeige des Shops (immer verfuegbar).
local function CmdShop(args)
    for _, line in ipairs(ShopText()) do
        WriteConsole("%s", line)
    end
    WriteConsole("rb_shop: Kauf mit rb_buy_wave <unit> [count] (Pool %d)", RBB.economy.pool)
    Log("event=shop status=listed tiers=%d pool=%d", #RBB.shopCfg.tiers, RBB.economy.pool)
    OpenShopPopup()
end

-- Send-Queue-Status: was ist gekauft und steht fuer die naechste Welle an?
local function CmdQueue(args)
    local q = RBB.sendQueue
    local parts = {}
    for _, u in ipairs(q.units) do
        parts[#parts + 1] = u.blueprint
    end
    local detail = table.concat(parts, ",")
    if detail == "" then detail = "-" end
    WriteConsole("rb_queue: %d Einheiten (Wert %d, Pool %d): %s",
        q.count, q.value, RBB.economy.pool, detail)
    Log("event=queue status=show count=%d value=%d pool=%d",
        q.count, q.value, RBB.economy.pool)
end

-- Gibt die Send-Queue am Wellenstart als Zusatz-Spawns aus (Boost der
-- naechsten Welle). Die Queue ist beim Kauf bereits bezahlt (Pool deduziert);
-- hier wird nur gespawnt und geleert — der Pool bleibt unangetastet.
local function FlushSendQueue()
    local q = RBB.sendQueue
    if q.count == 0 then
        Log("event=send_queue round=%d status=empty", RBB.round)
        return
    end

    local spawners = GetBorderSpawners()
    local anchorMode = "border"
    local mech, playerPos = nil, nil
    if #spawners == 0 then
        anchorMode = "fallback_mech"
        local okM, m = pcall(function() return PlayerService:GetPlayerControlledEnt(0) end)
        if not okM or m == nil or m == INVALID_ID then
            Log("event=send_queue round=%d status=skip reason=no_anchor queue=%d",
                RBB.round, q.count)
            return
        end
        local okP, p = pcall(function() return EntityService:GetPosition(m) end)
        if not okP or p == nil then
            Log("event=send_queue round=%d status=skip reason=no_anchor queue=%d",
                RBB.round, q.count)
            return
        end
        mech, playerPos = m, p
    end

    local totalSpawned = 0
    for _, u in ipairs(q.units) do
        if BlueprintExists(u.blueprint) then
            local okSpawn = false
            if anchorMode == "border" then
                okSpawn = SpawnCreatureAtBorderSpawner(u.blueprint, spawners)
            else
                okSpawn = SpawnCreatureAtRandomOffset(mech, u.blueprint, playerPos)
            end
            if okSpawn then totalSpawned = totalSpawned + 1 end
        end
    end

    local value = q.value
    q.units = {}
    q.count = 0
    q.value = 0
    Log("event=send_queue round=%d status=done spawned=%d value=%d anchor=%s",
        RBB.round, totalSpawned, value, anchorMode)
    WriteConsole("rb_send: Welle %d — Send-Queue ausgeliefert (+%d Kreaturen, Wert %d)",
        RBB.round, totalSpawned, value)
end

-- Kurztext fuer rb_status: was boostet die naechste Welle?
local function QueueSummary()
    if RBB.mode ~= "sp" then return "duel (kein send)" end
    local q = RBB.sendQueue
    if q.count == 0 then return "leer" end
    return string.format("%d units (wert %d)", q.count, q.value)
end

-- Wird aus dem OnEnterSpawn-Wrap aufgerufen: Runde zaehlen + Send-Queue
-- ausliefern (Boost der naechsten Naturwelle).
local function OnNaturalWaveStart()
    RBB.round = RBB.round + 1
    RevealReset()   -- #27: neue Runde beginnt verborgen
    Log("event=round round=%d status=start mode=%s pool=%d queue=%d",
        RBB.round, RBB.mode, RBB.economy.pool, RBB.sendQueue.count)
    RevealLock()    -- #27: eigenen Built-Value + Send-Komposition locken (vor Flush)
    if RBB.mode == "sp" then
        FlushSendQueue()
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
-- Commands: rb_buy_wave (Kauf-Hook), rb_shop (Custom-UI), rb_queue (Status),
-- rb_mode (sp|duel) + rb_status (Runde, Pool, Queue).
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
    local queue = QueueSummary()
    WriteConsole("rb_status: mode=%s runde=%d pool=%d queue=%s",
        RBB.mode, RBB.round, RBB.economy.pool, queue)
    Log("event=status mode=%s round=%d pool=%d queue=%s",
        RBB.mode, RBB.round, RBB.economy.pool, queue)
end

pcall(function()
    ConsoleService:RegisterCommand("rb_buy_wave", function(args)
        local unitId, count = nil, nil
        if args ~= nil and #args >= 1 then unitId = tostring(args[1]) end
        if args ~= nil and #args >= 2 then count = tostring(args[2]) end
        BuyWave(unitId, count)
    end)
    ConsoleService:RegisterCommand("rb_shop", function(args)
        CmdShop(args)
    end)
    ConsoleService:RegisterCommand("rb_queue", function(args)
        CmdQueue(args)
    end)
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

-- ============================================================================
-- #28 Win-Condition: HQ-HP, Leak-Erkennung (EnteredTriggerEvent), HQ-Tod
-- (RespawnFailedEvent) -> Match-Ende. GDD "Win-Condition": Leaks (feindliche
-- Kreaturen, die die Trigger-Zone ums HQ erreichen) senken den HQ-HP; HQ-Tod
-- beendet das Match (Sieg der Gegenseite).
--
-- Aufgabenteilung mit dem Tournament-Server (tournament/, Rust):
--   Server: HQ-HP-Buchung (POST /report hq_hp), Match-Ende bei HP<=0
--     (Phase finished + winner), Rematch (POST /rematch) — dort bereits
--     implementiert (Issues #29/#30/#44, Tests gruen). "Sieg-Screen" ist
--     Client-UI (Mod hat bewusst KEIN UI) und folgt spaeter; der Server
--     liefert dafuer bereits winner/finished + Feed-Event match_end, die
--     Bridge uebersetzt das in `match_over` (tournament/bridge/).
--   Mod: Spiel-Seite der Win-Condition — Leak -> HP sinkt, event=hq_hp als
--     Report-Flaeche (Bridge -> POST /report hq_hp), event=hq_dead/match_end
--     bei HQ-Tod. KEIN io/os/http (Mod hat keinen I/O-Kanal, docs/concept.md):
--     "Report" = [RBBATTLE]-Log-Zeile, die die Bridge an den Server reicht.
--
-- Verifikationsstatus (kein Live-Spiel durch den Agenten):
--   * HQ-HP-/Leak-/Match-Ende-Logik: reine Lua-Logik, statisch getestet
--     (fengari/Stub-Services, s. tests/lua-static/win-condition.test.js).
--   * EnteredTriggerEvent: Event-Name aus der Issue-Anforderung, im Repo
--     NICHT belegt (docs/research/api-deep-dive.md hat kein Trigger-Event) —
--     Handler pcall-gesichert registriert; feuert er nicht, degradiert der
--     Mod ohne Leak-Erkennung (Log event=hq_zone status=skip).
--   * Trigger-Zone selbst (Asset/Engine-API ums HQ): OFFEN — im Repo nicht
--     belegt; die Zone wird als Spiel-Asset/Blueprint ums HQ autorisiert
--     (oder per unverifizierter Engine-API), der Mod ist die Empfaengerseite.
--   * RespawnFailedEvent: verifiziert existent (dom_manager.lua lauscht
--     selbst darauf, s. docs/SEND_HOOK.md); Handler-Feuerung/Getter fuer den
--     Mod sind "wahrscheinlich" (wie EntityKilledEvent, api-deep-dive.md §2).
--   * HQ-Entity-Identifikation: OFFEN — kein verifizierter Blueprint/Lookup;
--     Operator kann die Entity per `rb_hq entity <id>` zuordnen; sonst greift
--     nur der HP<=0-Pfad (Leak) als Match-Ende.
-- ============================================================================

-- Konfiguration (Balance-Platzhalter; hqHpStart muss TOURNAMENT_HQ_HP des
-- Servers entsprechen, Default 100).
RBB.hqCfg = {
    hqHpStart = 100,     -- Start-HP des HQ (Server-Default TOURNAMENT_HQ_HP)
    leakDamage = 10,     -- HP-Verlust je Leak (Kreatur erreicht die HQ-Zone)
}

-- Laufzeit-Zustand der Win-Condition.
RBB.hq = {
    hp = 0,             -- aktueller HQ-HP (absolut, wie Server-Report)
    entity = nil,       -- getrackte HQ-Entity (nil = nicht zugeordnet)
    dead = false,       -- true nach HQ-Tod (Match-Ende gemeldet)
}

-- Report-Flaeche Richtung Tournament-Server (Bridge reicht die Zeile an
-- POST /report hq_hp weiter; der Mod hat keinen eigenen I/O-Kanal).
local function HqReportHp()
    Log("event=hq_hp hp=%d dead=%s", RBB.hq.hp, tostring(RBB.hq.dead))
end

-- HQ-Tod: Match-Ende melden (Sieg = Gegenseite; der Server setzt winner).
-- Idempotent.
local function HqOnDestroyed()
    if RBB.hq.dead then return end
    RBB.hq.dead = true
    RBB.hq.hp = 0
    Log("event=hq_dead status=match_end hp=0")
    Log("event=match_end reason=hq_destroyed winner=opponent")
    WriteConsole("HQ zerstoert — Match beendet (Sieg Gegenseite)")
end

-- Leak: eine Kreatur hat die HQ-Zone erreicht -> HQ-HP sinkt. Reine Logik
-- (keine Game-API, unit-testbar ueber die Event-Handler). Bei HP<=0 ->
-- HqOnDestroyed.
local function HqApplyLeak(damage)
    if RBB.hq.dead then return end
    local d = math.floor(tonumber(damage) or RBB.hqCfg.leakDamage)
    if d <= 0 then return end
    local before = RBB.hq.hp
    RBB.hq.hp = math.max(0, RBB.hq.hp - d)
    Log("event=leak damage=%d hp_before=%d hp=%d", d, before, RBB.hq.hp)
    HqReportHp()
    if RBB.hq.hp <= 0 then
        HqOnDestroyed()
    end
end

-- Event-Getter-Ladder: liefert die ausloesende Entity eines Events oder nil
-- (Getter pro Event sind unbekannt, s. api-deep-dive.md §2 — Konvention
-- evt:GetEntity(), wie bei PlayerCreatedEvent verifiziert).
local function HqEventEntity(evt)
    if evt == nil then return nil end
    local ok, ent = pcall(function() return evt:GetEntity() end)
    if ok and ent ~= nil and ent ~= INVALID_ID then return ent end
    return nil
end

-- #28 Leak-Erkennung (EnteredTriggerEvent): die Trigger-Zone ums HQ feuert,
-- sobald eine Kreatur sie betritt -> Leak. Event-Name unverifiziert (s. Kopf),
-- Handler pcall-gesichert; ohne Event bleibt die Leak-Erkennung inaktiv.
local function OnEnteredTrigger(evt)
    if RBB.hq.dead then return end
    HqApplyLeak(RBB.hqCfg.leakDamage)
end

-- #28 HQ-Tod-Kette (RespawnFailedEvent): feuert, wenn ein Gebaeude/Entity
-- nicht mehr respawnen kann (zerstoert). Nur Match-Ende, wenn es die
-- getrackte HQ-Entity trifft. Ohne getrackte Entity loggt der Handler einen
-- Hinweis statt faelschlich das Match zu beenden (kein Blindflug).
local function OnRespawnFailed(evt)
    if RBB.hq.dead then return end
    local ent = HqEventEntity(evt)
    if ent == nil then
        return -- Event nicht lesbar -> nicht auswertbar (kein Crash)
    end
    if RBB.hq.entity == nil then
        -- HQ-Entity nicht zugeordnet: nur Hinweis (einmalig), kein Match-Ende.
        if not RBB.hq.unmatchedLogged then
            RBB.hq.unmatchedLogged = true
            Log("event=hq_respawn status=unmatched entity=%s hint=rb_hq_entity",
                tostring(ent))
        end
        return
    end
    if RBB.hq.entity ~= ent then
        return -- Respawn-Fehler eines anderen Gebaeudes -> kein HQ-Tod
    end
    HqOnDestroyed()
end

-- Zone-/Entity-Status: best-effort-Aufklaerung, was fuer die Win-Condition
-- (noch) fehlt. Die Trigger-Zone selbst ist OFFEN (Asset/Engine-API).
local function HqZoneStatus()
    local zone = "pending"
    if not RBB.hqEnteredTriggerRegistered then
        zone = "skip reason=event_unregistered"
    elseif RBB.hq.entity == nil then
        zone = "pending reason=no_hq_entity"
    else
        zone = "armed"
    end
    Log("event=hq_zone status=%s entity=%s hp=%d dead=%s",
        zone, tostring(RBB.hq.entity), RBB.hq.hp, tostring(RBB.hq.dead))
end

-- Konsolen-Command rb_hq: Status / manueller Leak / Entity-Zuordnung / Reset
-- (Entwickler-/Operator-Werkzeug, Muster rb_economy).
local function CmdHq(args)
    local sub = nil
    if args ~= nil and #args >= 1 then sub = tostring(args[1]):lower() end
    if sub == "reset" then
        RBB.hq.hp = RBB.hqCfg.hqHpStart
        RBB.hq.entity = nil
        RBB.hq.dead = false
        RBB.hq.unmatchedLogged = false
        Log("event=hq_reset status=ok hp=%d", RBB.hq.hp)
        WriteConsole("rb_hq: Win-Condition zurueckgesetzt (hp=%d)", RBB.hq.hp)
        return
    end
    if sub == "entity" and args ~= nil and #args >= 2 then
        local id = tonumber(args[2])
        if id == nil then
            WriteConsole("rb_hq: rb_hq entity <id> (numerische Entity-Id)")
            Log("event=hq_entity status=usage")
            return
        end
        RBB.hq.entity = id
        Log("event=hq_entity status=ok entity=%s", tostring(id))
        WriteConsole("rb_hq: HQ-Entity %s zugeordnet", tostring(id))
        return
    end
    if sub == "leak" then
        local dmg = RBB.hqCfg.leakDamage
        if args ~= nil and #args >= 2 then dmg = tonumber(args[2]) or dmg end
        HqApplyLeak(dmg)
        WriteConsole("rb_hq: Leak angewendet (damage=%d, hp=%d)", dmg, RBB.hq.hp)
        return
    end
    -- Default: Status.
    WriteConsole("rb_hq: hp=%d dead=%s entity=%s (rb_hq leak|entity|reset)",
                 RBB.hq.hp, tostring(RBB.hq.dead), tostring(RBB.hq.entity))
    Log("event=hq_status hp=%d dead=%s entity=%s",
        RBB.hq.hp, tostring(RBB.hq.dead), tostring(RBB.hq.entity))
    HqZoneStatus()
end

pcall(function()
    ConsoleService:RegisterCommand("rb_hq", function(args)
        CmdHq(args)
    end)
end)

-- #28: Win-Condition-Event-Handler registrieren (pcall-gesichert; unbekannter
-- Event-Name laesst den Rest unangetastet). hqEnteredTriggerRegistered dient
-- der Status-Anzeige (rb_hq -> event=hq_zone).
RBB.hqEnteredTriggerRegistered = false
pcall(function()
    RegisterGlobalEventHandler("EnteredTriggerEvent", OnEnteredTrigger)
    RBB.hqEnteredTriggerRegistered = true
end)
pcall(function()
    RegisterGlobalEventHandler("RespawnFailedEvent", OnRespawnFailed)
end)

-- Init: HQ-HP auf Startwert setzen (Spielzustand im Speicher; der Server
-- fuehrt die autoritative Buchung).
RBB.hq.hp = RBB.hqCfg.hqHpStart

-- ============================================================================
-- #27 Reveal-HUD: Commands (rb_reveal = Gegner-Injektion, rb_round_start =
-- Build-Phase, rb_hud = HUD-Standardfelder).
-- ============================================================================

-- rb_reveal <built_opp> <hq_opp> [incoming]: die Bridge injiziert die vom
-- Server aufgedeckten Gegner-Werte (Built-Value, HQ-HP, eingehende Kompo-
-- sition). Erst damit ist der Reveal beider Teams komplett (rb_hud zeigt
-- beide Seiten).
local function CmdReveal(args)
    local r = RBB.reveal
    local builtOpp = 0
    local hqOpp = 0
    local incoming = "-"
    if args ~= nil and #args >= 1 then builtOpp = math.floor(tonumber(args[1]) or 0) end
    if args ~= nil and #args >= 2 then hqOpp = math.floor(tonumber(args[2]) or 0) end
    if args ~= nil and #args >= 3 then incoming = tostring(args[3]) end
    r.builtOpp = builtOpp
    r.hqOpp = hqOpp
    r.incoming = incoming
    r.oppKnown = true
    Log("event=reveal_opp round=%d built_opp=%d hq_opp=%d incoming=%s status=ok",
        r.round, builtOpp, hqOpp, incoming)
    WriteConsole("rb_reveal: Gegner built=%d hq=%d incoming=%s",
        builtOpp, hqOpp, incoming)
end

-- rb_round_start [n]: neue Build-Phase — Reveal wieder verbergen (Bridge-
-- Signal "round steigt", vgl. docs/TOURNAMENT_API.md). RBB.round bleibt der
-- Wellenstart-Zaehler des Mods; <n> ist nur informativ fuer die Anzeige.
local function CmdRoundStart(args)
    RevealReset()
    local n = RBB.round
    if args ~= nil and #args >= 1 then
        n = math.floor(tonumber(args[1]) or RBB.round)
    end
    Log("event=round_start round=%d status=build reveal=hidden", n)
    WriteConsole("rb_round_start: Runde %d — Build-Phase, Reveal verborgen", n)
end

-- rb_hud: HUD-Standardfelder (Runde, Countdown, eigener Pool, HQ beider
-- Teams) + Reveal-Zustand. Vor Wellenstart (locked=false) sind Gegner-Built,
-- incoming und Gegner-HQ verborgen ("hidden"); bei Wellenstart werden sie
-- aufgedeckt (Gegner-Werte erst nach rb_reveal).
local function CmdHud(args)
    local r = RBB.reveal
    local countdown = RevealCountdown()
    local builtOwn = r.locked and r.builtOwn or EconomyBuiltValue()
    local builtOpp = "hidden"
    local incoming = "hidden"
    local hqOpp = "hidden"
    local reveal = "hidden"
    if r.locked then
        reveal = "revealed"
        if r.oppKnown then
            builtOpp = tostring(r.builtOpp)
            incoming = r.incoming
            hqOpp = tostring(r.hqOpp)
        end
    end
    Log("event=hud round=%d countdown=%d pool=%d built_own=%d built_opp=%s incoming=%s hq_own=%d hq_opp=%s reveal=%s",
        RBB.round, countdown, RBB.economy.pool, builtOwn, builtOpp, incoming,
        RBB.hq.hp, hqOpp, reveal)
    WriteConsole("rb_hud: runde=%d countdown=%d pool=%d built_own=%d built_opp=%s incoming=%s hq_own=%d hq_opp=%s",
        RBB.round, countdown, RBB.economy.pool, builtOwn, builtOpp, incoming,
        RBB.hq.hp, hqOpp)
end

pcall(function()
    ConsoleService:RegisterCommand("rb_reveal", function(args)
        CmdReveal(args)
    end)
    ConsoleService:RegisterCommand("rb_round_start", function(args)
        CmdRoundStart(args)
    end)
    ConsoleService:RegisterCommand("rb_hud", function(args)
        CmdHud(args)
    end)
end)

-- Lebenszeichen-Log beim Laden (analog Baustein 01 / Spike).
Log("event=mod_load version=%s status=ok mode=%s anchor=border_spawner_groups timer_cap=%d econ_source=%s econ_pool=%d hq_hp=%d hq_dead=%s",
    RBB.version, RBB.mode, RBB.waveIntervalCapS, RBB.economy.source, RBB.economy.pool,
    RBB.hq.hp, tostring(RBB.hq.dead))
