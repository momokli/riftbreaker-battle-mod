-- ============================================================================
-- rbbattle_autoexec.lua  (Einzel-Mod rbbattle, v0.26.0)
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
--   #23 Runden-Takt: DOM-Wellen-Vorbereitung wird auf den Wellen-Takt des
--       aktiven Presets gedeckelt (RBB.wavePresets, #41; prepareSpawnTime
--       420 -> Preset-Intervall) + Setup-Log
--       (difficulty/map size/seed werden beim Server-Start gesetzt, s.
--       docs/DUEL_SETUP.md — der Mod loggt die aktiv wirksame Difficulty).
--   #24 Economy (Duell-Oekonomie): Alle gefarmten Ressourcen (Carbonium &
--       Co.) werden als Value getrackt (Ressourcen-Events, Getter-Ladder);
--       bewusste, IRREVERSIBLE Konvertierung in Send-Waehrung per
--       `rb_convert <menge>` (Calcium, #40) bzw. `rb_convert calcium
--       <amount>`; Spar-Pool persistiert ueber Runden
--       (Global-Database). Built-Value (= nicht konvertierter Farmwert) wird
--       getrennt gefuehrt (Reveal-Basis fuer #27). Fallback: HourEvent-Tick,
--       falls die Ressourcen-Event-API fehlt (docs/research/api-deep-dive.md §1).
--   #42 MVP Single-Player Self-Send (Sich-selber-senden, Testing Mode):
--       Mod-Mode `rb_mode sp|duel` (Default sp). `rb_convert` nutzt NUR
--       Calcium (= Carbonium, #40) als Send-Waehrung. Self-Boost am Wellenstart
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
--   #39 Send-Boost: naechste Naturwelle prozentual verstaerken (zusatzlicher
--       Hebel NEBEN der Shop-Composition aus #25). `rb_boost <stufe|pct>`
--       kauft einen prozentualen Aufschlag aus dem Spar-Pool (irreversibel);
--       beim naechsten natuerlichen Wellenstart wird er auf die Welle
--       angewendet (Chokepoint dom_mananger:SpawnWavesForDifficultyLevel,
--       Beleg docs/SEND_HOOK.md) und danach zurueckgesetzt — genau eine
--       Welle, nicht kumulativ. Stufen/Caps in RBB.boostCfg (Annahme, #33).
--       duel-Modus: Boost-Flush nur in sp (Stub, Muster FlushSendQueue).
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
--   #99 Click-HUD (Senden per Klick statt Tippen): toggle-bares HUD-Overlay
--       (GuiService:OpenPopup, 2-Button-Template) + die wichtigste Send-
--       Aktion (Einheit kaufen -> Send-Queue) per Klick (GuiPopupResultEvent,
--       button_yes -> BuyWave). rb_hud_ui oeffnet/schliesst das Overlay,
--       rb_quick [unit [count]] ruestet die Quick-Send-Einheit (Default
--       brabit). Convert bleibt bewusst Konsolen-/Bridge-Aktion (braucht
--       Menge). Kein Version-Bump (Release/Tag macht der Loop).
--   #33 Balance & Tuning v1 (Preisliste + HQ-HP-Kurve, KEIN Live-Test):
--       shopCfg ist die zentrale, dokumentierte v1-Preisliste (Tiered Units +
--       Bosse); die HQ-HP-Kurve ueber Runden ist als Formel dokumentiert
--       (hqCfg.hqHpPerRound/hqHpRoundCap -> HqMaxHp) und setzt den HQ-HP bei
--       jedem Wellenstart auf den Runden-Maxwert. Der Wellen-Takt bleibt
--       waveIntervalCapS (nur dokumentiert, nicht fest verdrahtet). Alle
--       Werte sind eine Balance-Annahme und brauchen Live-Test (#33).
--   #41 Wellen-Takt + Grundschwierigkeit (Test-Varianten): explizite Presets
--       A = alle 8 Min in voller Groesse, B = alle 4 Min in halber Groesse
--       (RBB.wavePresets); Grundschwierigkeit "normal" (leichter als hard,
--       weil Sends die Schwierigkeit zusaetzlich erhoehen). Kein Version-Bump.
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
--   event=mod_load version=0.26.0 status=ok mode=sp econ_source=.. econ_pool=.. hq_hp=.. hq_dead=..
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
--   event=economy_checkpoint round=.. pool=.. status=ok|skip reason=no_db  (#65)
--   event=mode mode=sp|duel status=ok|usage|stub                          (#42)
--   event=wave_hook patch status=ok|skip|no_class reason=no_api           (#42)
--   event=round round=N status=start mode=.. pool=.. queue=..             (#42/#25)
--   event=commence status=pending|held|ok [hint=place_hq] [reason=no_hq]   (#158)
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
--   event=hq_curve round=.. maxhp=.. hp=..                                (#33)
--   event=balance unit=.. tier=.. price=.. boss=.. / hq_curve ..           (#33)
--   event=reveal round=.. status=revealed built_own=.. built_opp=.. send_own=.. incoming=.. (#27)
--   event=reveal_opp round=.. built_opp=.. hq_opp=.. incoming=.. status=ok  (#27)
--   event=round_start round=.. status=build reveal=hidden                   (#27)
--   event=hud round=.. countdown=.. pool=.. built_own=.. built_opp=.. incoming=.. hq_own=.. hq_opp=.. reveal=.. (#27)
--   event=hud_ui status=opened|already_open|closed|no_player|api_missing|error result=.. action=.. quick=.. count=.. (#99)
--   event=quick_send status=armed|usage|unknown_unit unit=.. count=.. price=.. (#99)
--   event=boost status=usage|unknown_stage|invalid_pct|max_boosts|cap|insufficient|ok pct=.. total_pct=.. price=.. pool=.. buys=.. (#39)
--   event=boost status=flush round=.. pct=.. level_from=.. level_to=.. delta=.. buys=.. (#39)
--   event=boost patch status=ok|skip reason=no_api                                    (#39)
-- ============================================================================

local RBB = {}
RBB.version = "0.26.0"

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

-- #41: Wellen-Takt + Grundschwierigkeit als explizite, dokumentierte
-- Konfigurations-Presets (zu testen, Issue #41). Zwei Varianten:
--   A = alle 8 Min (480 s) in VOLLER Groesse (strengthPct 100)
--   B = alle 4 Min (240 s) in HALBER Groesse (strengthPct 50)
-- Grundschwierigkeit: "normal" (leichter als der bisherige Default "hard"),
-- weil Sends die Schwierigkeit zusaetzlich erhoehen (Basis-Druck niedriger).
-- Der Wellen-Takt ist bewusst NICHT fest verdrahtet: waveIntervalCapS wird
-- aus dem aktiven Preset abgeleitet (docs/GAME_DESIGN.md §Wellen-Takt).
RBB.wavePresets = {
    baseDifficulty = "normal",  -- Grundschwierigkeit (Server-Seite, docs/DUEL_SETUP.md)
    active = "A",               -- aktives Preset (A|B), aenderbar vor Map-Start
    variants = {
        A = { id = "A", intervalS = 480, strengthPct = 100 },  -- 8 Min, volle Groesse
        B = { id = "B", intervalS = 240, strengthPct = 50 },   -- 4 Min, halbe Groesse
    },
}

-- Aktives Wellen-Preset (ein Point-of-Switch vor dem Map-Start; unbekannte
-- aktive ID faellt auf Variante A zurueck).
local function ActiveWavePreset()
    return RBB.wavePresets.variants[RBB.wavePresets.active]
        or RBB.wavePresets.variants.A
end

-- #23: Deckel fuer die DOM-Wellen-Vorbereitung in Sekunden — abgeleitet aus
-- dem aktiven Wellen-Preset (#41). Vanilla-Wert in normal/hard-Survival-Rules:
-- 420 (Beleg lua/missions/survival/v2/dom_survival_*_rules_{normal,hard}.lua).
RBB.waveIntervalCapS = ActiveWavePreset().intervalS

-- #42: Mod-Mode (MVP). sp = Single-Player Self-Send (Sich-selber-senden),
-- duel = 1v1-Duell (folgt spaeter, hier nur Stub). Default sp.
RBB.mode = "sp"
RBB.round = 0   -- Runden-Zaehler (+1 bei jedem natuerlichen Wellenstart)

-- #158 Setup-Phase / Commence-Flow: Spielstart OHNE Auto-HQ. Solange der
-- Spieler das HQ nicht selbst ueber das Build-Menue platziert hat, haelt der
-- Wave-PROGRESS an (kein Spawn, kein Runden-Zaehler) — das Spiel selbst
-- laeuft frei weiter (kein debug_dom_pause). Sobald das HQ ueber
-- FindService:FindEntitiesByGroup("headquarters") erkannt wird (#144/#151),
-- starten die Wellen (commenced=true) + Commence-Announce.
RBB.commenced = false        -- Setup-Phase aktiv, bis HQ erkannt
RBB.commenceHeldLogged = false -- Spam-Guard fuer den "held"-Log im Wellenstart-Hook

-- #33 Balance & Tuning v1: PREISLISTE v1 (Tiered Units + Bosse) als zentrale
-- Datenbasis fuer den Shop. Preise in Send-Waehrung (= 1 Carbonium-Value,
-- Faktor 1, vgl. economyCfg). Die Preise sind eine dokumentierte erste
-- Balance-Annahme OHNE Live-Test (braucht Test-Duell, #33):
--
--   Tier 1  brabit       100   (billigster Basis-Send)
--   Tier 1  baxmoth      150
--   Tier 2  artigian     200
--   Tier 3  canceroth    300
--   Boss    boss         800   (8x billigster Tier-1, Single-Slot im Tier)
--
-- Prinzip: monoton steigende Preise ueber die Tiers, Boss als teuerste Einheit.
-- Blueprints sind weiterhin Platzhalter aus dem bestehenden Wellen-/Boost-Pool
-- (echte Boss-/Unit-Listen aus den Spieldaten folgen separat); die PREIS-
-- Relationen sind die hier festgelegte Balance-Groesse. Kein eigenes Framework:
-- der Shop bleibt die bestehende shopCfg-Struktur (#25).
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

-- #39 Send-Boost: prozentualer Aufschlag auf die NAEchste Naturwelle (zusatz-
-- licher Hebel neben der Shop-Composition aus #25). Preise in Send-Waehrung
-- (= 1 Carbonium-Value, Faktor 1, vgl. economyCfg). Alle Werte sind eine
-- dokumentierte Balance-Annahme OHNE Live-Test (braucht Test-Duell, #33):
--
--   Stufe  s1  +25%   200
--   Stufe  s2  +50%   400
--   Stufe  s3  +100%  800   (freie pct-Eingabe: 8 Waehrung je Prozentpunkt)
--
-- Prinzip: linearer Preis (pricePerPct), monotone Stufen. maxBoostPct deckelt
-- die kumulierte Boost-Summe je Welle, maxBoostsPerWave die Anzahl Kaeufe je
-- Welle (Schutz vor Endlos-Spam, analog shopCfg.maxQueueCreatures).
RBB.boostCfg = {
    stages = {
        { id = "s1", pct = 25,  price = 200 },
        { id = "s2", pct = 50,  price = 400 },
        { id = "s3", pct = 100, price = 800 },
    },
    pricePerPct = 8,       -- linearer Preis fuer freie pct-Eingabe (Annahme)
    maxBoostPct = 200,     -- Cap der kumulierten Boost-Summe je Welle
    maxBoostsPerWave = 4,  -- max. Boost-Kaeufe je Welle (Schutz vor Spam)
}

-- #39: Laufzeit-Zustand des Send-Boost (in-memory, je Welle verbraucht).
RBB.boost = {
    pct  = 0,   -- akkumulierter %-Boost fuer die naechste Naturwelle
    buys = 0,   -- Anzahl Boost-Kaeufe in der aktuellen Welle
}

-- Vorwaertsdeklaration fuer den Wellenstart-Hook (#42): Definition folgt nach
-- dem Economy-Block (braucht EconomySave); aufgerufen wird er bereits in
-- OnPlayerInitialized / HandleWaveCommand / Mod-Load (Retry-Zeitpunkte,
-- Muster PatchDomTimer).
local PatchWaveStartHook
local PatchSpawnWavesHook
local HqAutoDetectEntity
local BoostSummary
local CommenceGame
local AnnounceSetupPhase

-- Vorwaertsdeklaration fuer die HQ-HP-Kurve (#33): Definition folgt im
-- Win-Condition-Block (braucht RBB.hqCfg); aufgerufen wird sie in
-- OnNaturalWaveStart (Wellenstart-Hook) — Muster PatchWaveStartHook.
local HqMaxHp

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
-- #23: DOM-Wellen-Timer auf den aktiven Wellen-Takt deckeln (RBB.waveIntervalCapS,
-- Function-Wrap, vgl. docs/SEND_HOOK.md)
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

    local preset = ActiveWavePreset()
    Log("event=setup difficulty=%s creatures_difficulty=%s timer_cap=%d preset=%s interval=%d strength_pct=%d base_difficulty=%s",
        difficulty, creatureDifficulty, RBB.waveIntervalCapS,
        preset.id, preset.intervalS, preset.strengthPct, RBB.wavePresets.baseDifficulty)
end

local function OnPlayerInitialized()
    -- Welt ist fertig aufgesetzt: DOM-Klasse jetzt sicher verfuegbar.
    AnnounceSetupPhase() -- #158: Start-Announce (Setup-Phase, HQ noch offen)
    PatchDomTimer()
    PatchWaveStartHook()
    PatchSpawnWavesHook()
    HqAutoDetectEntity() -- #144: HQ-Entity ist jetzt (falls vorhanden) im Level
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
    PatchSpawnWavesHook() -- weiterer Retry-Zeitpunkt (#39)
    HqAutoDetectEntity() -- weiterer Retry-Zeitpunkt (#144)
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

    -- #40 Send-Waehrung (zentrales Mapping): MVP = NUR Calcium (Masse). Im
    -- Spiel heisst Calcium "carbonium" (api-deep-dive.md §1); der Alias
    -- "calcium" wird in CanonicalResource aufgeloest. Nur hier gelistete
    -- Ressourcen sind per rb_convert konvertierbar; der spaetere Ironium-
    -- Qualitaets-Split (Ironium -> Bosse) ergaenzt hier ein zweites Mapping.
    -- Achtung: getrennt von resourceFactors — jenes zaehlt den Farm-Value
    -- ALLER Ressourcen (#24), dieses bestimmt die Konvertierbarkeit (#40).
    sendCurrency = {
        carbonium = 1,   -- Calcium -> Send-Waehrung, Faktor 1
    },

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

-- #65: Defensiver Persistenz-Checkpoint an der Rundengrenze (Wellenstart =
-- Ende der vorherigen Build-/Send-Phase). Der Pool wird transaktional bei
-- jeder Aenderung gespiegelt (EconomySave); dieser Checkpoint schreibt ihn
-- ZUSAETZLICH an der Rundengrenze in die Global-DB und zieht dabei einen beim
-- Boot nicht aufloesbaren DB-Handle nach (Retry-Muster wie PatchWaveStartHook/
-- PatchSpawnWavesHook). So ueberlebt der Spar-Pool einen Map-/Session-Reload
-- auch dann, wenn der letzte transaktionale Save verlorenging oder die
-- Global-DB erst nach dem Mod-Load verfuegbar war. AC1 (In-Game-Bestaetigung)
-- bleibt offen: die Global-DB-Persistenz ueber Reload ist statisch nicht
-- beweisbar (docs/research/api-deep-dive.md §3).
local function EconomyCheckpoint()
    local e = RBB.economy
    if not e.dbOk then
        local okDb, db = pcall(function()
            return PlayerService:GetOrCreateGlobalDatabase(EconomyDbName())
        end)
        if okDb and db ~= nil then
            e.db = db
            e.dbOk = true
        end
    end
    if e.dbOk then
        EconomySave()
        Log("event=economy_checkpoint round=%d pool=%d farmed=%d converted=%d status=ok",
            RBB.round, e.pool, e.farmed, e.converted)
    else
        Log("event=economy_checkpoint round=%d pool=%d status=skip reason=no_db",
            RBB.round, e.pool)
    end
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
        WriteConsole("rb_convert: Aufruf: rb_convert <menge> (Calcium) bzw. rb_convert carbonium <menge>")
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

    -- #40 Calcium-only: nur die zentrale Send-Waehrung ist konvertierbar. Der
    -- spaetere Ironium-Qualitaets-Split ergaenzt RBB.economyCfg.sendCurrency um
    -- ein zweites Mapping (Ironium -> Bosse); bis dahin wird jede andere
    -- Ressource abgelehnt — vor der Mengen-Pruefung (Waehrung entscheidet zuerst).
    local factor = RBB.economyCfg.sendCurrency[name]
    if factor == nil then
        WriteConsole("rb_convert: '%s' ist (noch) keine Send-Waehrung — MVP ist Calcium (carbonium), #40",
                     name)
        Log("event=convert status=not_send_currency resource=%s", name)
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
    -- #40 Calcium-only: `rb_convert <menge>` konvertiert Calcium (ein Argument);
    -- `rb_convert <resource> <menge>` akzeptiert nur Calcium (Alias "calcium"
    -- ≡ "carbonium"), jede andere Ressource -> not_send_currency.
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
    WriteConsole("rb_shop: Boost rb_boost <stufe|pct> — naechste Welle: %s", BoostSummary())
    Log("event=shop status=listed tiers=%d pool=%d boost=%s", #RBB.shopCfg.tiers, RBB.economy.pool, BoostSummary())
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
    EconomyCheckpoint()   -- #65: Spar-Pool an der Rundengrenze persistieren
    -- #33: HQ-HP-Kurve ueber Runden — bei Wellenstart HP auf Runden-Max setzen
    -- (solange das HQ nicht zerstoert ist).
    if not RBB.hq.dead then
        RBB.hq.hp = HqMaxHp(RBB.round)
        Log("event=hq_curve round=%d maxhp=%d hp=%d",
            RBB.round, HqMaxHp(RBB.round), RBB.hq.hp)
    end
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
            if not RBB.commenced then
                -- #158 Setup-Phase: Waves pausiert bis HQ platziert. NUR der
                -- Wave-PROGRESS haelt an (kein Naturwellen-Spawn, kein
                -- Runden-Zaehler, keine Send-Queue/Reveal); das Spiel selbst
                -- laeuft weiter (kein debug_dom_pause).
                if not RBB.commenceHeldLogged then
                    RBB.commenceHeldLogged = true
                    Log("event=commence status=held reason=no_hq")
                end
                return
            end
            pcall(RBB.waveStartOrig, self, state) -- Naturwelle UNANGETASTET
            OnNaturalWaveStart()
        end
        Log("event=wave_hook patch status=ok")
    end

    RBB.waveStartPatched = true
    return true
end

-- ============================================================================
-- #39 Send-Boost: naechste Naturwelle prozentual verstaerken (statt bzw.
-- zusaetzlich zur Shop-Composition aus #25). `rb_boost <stufe|pct>` kauft
-- einen prozentualen Aufschlag aus dem Spar-Pool (sofort irreversibel
-- deduziert, Muster rb_buy_wave); beim naechsten natuerlichen Wellenstart
-- wird der akkumulierte Boost auf die Welle angewendet und zurueckgesetzt.
--
-- Technischer Hebel (VERIFIZIERT am lan-lua-src, Spiel 2.0.58485):
--   dom_mananger:SpawnWavesForDifficultyLevel(difficultyLevel, addToSpawned)
--   indiziert GetWavePool(difficultyLevel) -> rules.waves[group][difficultyLevel]
--   und GetAttackCount(difficultyLevel) -> rules.maxAttackCountPerDifficulty
--   [difficultyLevel]. Die Naturwellen-Staerke ist DISKRET ueber
--   difficultyLevel (1..maxDifficultyLevel=9) indiziert. Ein prozentualer
--   Aufschlag wird daher als Level-Delta approximiert:
--       delta = ceil(level * pct/100), min. 1, gedeckelt auf maxDifficultyLevel.
--   addToSpawned==true = Naturwelle (OnEnterSpawn); false = Debug-Trigger
--   (debug_dom_manager_spawn_wave_level) und bleibt unangetastet.
--   Alle Boost-Zahlen sind dokumentierte Annahmen (brauchen Live-Test, #33).
--   Patch idempotent + pcall, Fehlschlag = harmlos (Vanilla), Muster
--   PatchWaveStartHook. duel-Modus: Boost-Flush nur in sp (Stub).
-- ============================================================================

-- Stufen-Lookup (id -> Stufen-Def); nil = unbekannt.
local function FindBoostStage(stageId)
    for _, s in ipairs(RBB.boostCfg.stages) do
        if s.id == stageId then return s end
    end
    return nil
end

-- Linearer Preis fuer freie pct-Eingabe (Annahme, pricePerPct je Prozent).
local function BoostPriceForPct(pct)
    return math.max(1, math.ceil(pct * RBB.boostCfg.pricePerPct))
end

-- Kauf: `rb_boost <stufe|pct>`. Deduziert den Spar-Pool sofort und akkumuliert
-- den Boost fuer die naechste Welle. Guards: usage / unbekannte Stufe / pct
-- ungueltig / max Boosts je Welle / Boost-Cap / zu wenig Pool. Liefert nie
-- einen Fehler nach aussen.
local function BuyBoost(rawArg)
    local b = RBB.boost
    local arg = tostring(rawArg or ""):lower()

    if arg == "" or arg == "help" then
        WriteConsole("rb_boost: Aufruf rb_boost <stufe|pct> — Stufen: s1=+25%% (%d), s2=+50%% (%d), s3=+100%% (%d); oder pct (z.B. rb_boost 30)",
            RBB.boostCfg.stages[1].price, RBB.boostCfg.stages[2].price, RBB.boostCfg.stages[3].price)
        Log("event=boost status=usage")
        return
    end

    local pct, price
    local stage = FindBoostStage(arg)
    if stage ~= nil then
        pct, price = stage.pct, stage.price
    else
        local n = tonumber(arg)
        if n == nil then
            WriteConsole("rb_boost: unbekannte Stufe '%s' — rb_boost s1|s2|s3 oder pct", arg)
            Log("event=boost status=unknown_stage stage=%s", arg)
            return
        end
        pct = math.floor(n)
        price = BoostPriceForPct(pct)
    end

    if pct <= 0 then
        WriteConsole("rb_boost: pct muss > 0 sein (bekam %d)", pct)
        Log("event=boost status=invalid_pct pct=%d", pct)
        return
    end

    if b.buys >= RBB.boostCfg.maxBoostsPerWave then
        WriteConsole("rb_boost: max %d Boosts pro Welle erreicht", RBB.boostCfg.maxBoostsPerWave)
        Log("event=boost status=max_boosts buys=%d pct=%d", b.buys, b.pct)
        return
    end

    if b.pct + pct > RBB.boostCfg.maxBoostPct then
        WriteConsole("rb_boost: Boost-Cap erreicht (%d%% + %d%% > max %d%%)",
            b.pct, pct, RBB.boostCfg.maxBoostPct)
        Log("event=boost status=cap pct=%d add=%d max=%d", b.pct, pct, RBB.boostCfg.maxBoostPct)
        return
    end

    if RBB.economy.pool < price then
        WriteConsole("rb_boost: Pool reicht nicht (%d < %d) — erst rb_convert",
            RBB.economy.pool, price)
        Log("event=boost status=insufficient pct=%d price=%d pool=%d",
            pct, price, RBB.economy.pool)
        return
    end

    RBB.economy.pool = RBB.economy.pool - price
    b.pct = b.pct + pct
    b.buys = b.buys + 1
    EconomySave()

    Log("event=boost status=ok pct=%d total_pct=%d price=%d pool=%d buys=%d",
        pct, b.pct, price, RBB.economy.pool, b.buys)
    WriteConsole("rb_boost: +%d%% naechste Welle (%d) -> total %d%%, Pool %d",
        pct, price, b.pct, RBB.economy.pool)
end

-- Kurztext fuer rb_status/rb_shop: was boostet die naechste Welle?
BoostSummary = function()
    if RBB.mode ~= "sp" then return "duel (kein send)" end
    local b = RBB.boost
    if b.pct == 0 then return "kein boost" end
    return string.format("+%d%% (buys=%d)", b.pct, b.buys)
end

-- %-Boost -> Level-Delta (diskreter Hebel, s. Block-Kommentar oben).
local function BoostLevelDelta(level, pct)
    local delta = math.ceil(level * pct / 100.0)
    if delta < 1 then delta = 1 end
    return delta
end

-- #41: Wellen-Staerke-Skalierung (diskreter Hebel ueber difficultyLevel, wie
-- der Boost #39). Preset B = "halbe Groesse" -> floor(level * pct/100), min. 1;
-- Preset A = "volle Groesse" (strengthPct 100) -> Level unveraendert.
-- Approximation (diskrete Level statt Kreaturen-Anzahl), braucht Live-Test (#41).
local function ScaleWaveLevel(level, strengthPct)
    local l = math.floor(tonumber(level) or 1)
    if l < 1 then l = 1 end
    local pct = math.floor(tonumber(strengthPct) or 100)
    if pct >= 100 then return l end
    if pct <= 0 then pct = 1 end
    local scaled = math.floor(l * pct / 100)
    if scaled < 1 then scaled = 1 end
    return scaled
end

-- Wendet den akkumulierten Boost auf die naechste Naturwelle an (nur sp) und
-- setzt ihn danach zurueck. Liefert das ggf. erhoehte difficultyLevel. Bei
-- duel bleibt der Boost liegen (Stub, analog FlushSendQueue).
local function ApplyPendingBoost(self, difficultyLevel)
    local b = RBB.boost
    if RBB.mode ~= "sp" or b.pct <= 0 then
        return difficultyLevel
    end

    local level = math.floor(tonumber(difficultyLevel) or 1)
    if level < 1 then level = 1 end

    local maxLevel = level
    if type(self) == "table" and type(self.maxDifficultyLevel) == "number"
        and self.maxDifficultyLevel > 0 then
        maxLevel = self.maxDifficultyLevel
    end

    local delta = BoostLevelDelta(level, b.pct)
    local newLevel = level + delta
    if newLevel > maxLevel then newLevel = maxLevel end

    local pct = b.pct
    local buys = b.buys
    b.pct = 0
    b.buys = 0

    Log("event=boost status=flush round=%d pct=%d level_from=%d level_to=%d delta=%d buys=%d",
        RBB.round + 1, pct, level, newLevel, newLevel - level, buys)
    WriteConsole("rb_boost: Welle %d — +%d%% Boost angewendet (Level %d -> %d)",
        RBB.round + 1, pct, level, newLevel)
    return newLevel
end

-- Wellenstart-Chokepoint-Hook (#39): wrap dom_mananger:SpawnWavesForDifficultyLevel
-- (Klasse, nicht Instanz). Naturwelle (addToSpawned=true) wird um den Boost
-- erhoeht; Debug-Trigger (false) bleibt unangetastet. Idempotent, lazy
-- (Mod-Load/PlayerInitializedEvent/jeder Send), pcall-gesichert.
RBB.spawnWavesPatched = false
RBB.spawnWavesOrig = nil

PatchSpawnWavesHook = function()
    if RBB.spawnWavesPatched then
        return RBB.spawnWavesOrig ~= nil
    end

    local dom = nil
    if type(_G) == "table" then
        dom = rawget(_G, "dom_mananger")
    end
    if type(dom) ~= "table" then
        return false -- Klasse (noch) nicht geladen; naechster Versuch spaeter
    end

    local orig = dom.SpawnWavesForDifficultyLevel
    if type(orig) ~= "function" then
        Log("event=boost patch status=skip reason=no_api")
        RBB.spawnWavesPatched = true
        return false
    end

    if RBB.spawnWavesOrig == nil then
        RBB.spawnWavesOrig = orig
        dom.SpawnWavesForDifficultyLevel = function(self, difficultyLevel, shouldAddtoSpawnedAttacks)
            local newLevel = difficultyLevel
            if shouldAddtoSpawnedAttacks == true then
                -- Naturwelle (OnEnterSpawn, addToSpawned=true); Debug-Trigger
                -- (false) bleibt unangetastet. Grundschwierigkeits-Skalierung
                -- (#41) vor dem Send-Boost (#39).
                local preset = ActiveWavePreset()
                newLevel = ScaleWaveLevel(difficultyLevel, preset.strengthPct)
                newLevel = ApplyPendingBoost(self, newLevel)
            end
            return RBB.spawnWavesOrig(self, newLevel, shouldAddtoSpawnedAttacks)
        end
        Log("event=boost patch status=ok")
    end

    RBB.spawnWavesPatched = true
    return true
end

pcall(function()
    ConsoleService:RegisterCommand("rb_boost", function(args)
        local arg = nil
        if args ~= nil and #args >= 1 then arg = tostring(args[1]) end
        BuyBoost(arg)
    end)
end)

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
    local boost = BoostSummary()
    WriteConsole("rb_status: mode=%s runde=%d pool=%d queue=%s boost=%s",
        RBB.mode, RBB.round, RBB.economy.pool, queue, boost)
    Log("event=status mode=%s round=%d pool=%d queue=%s boost=%s",
        RBB.mode, RBB.round, RBB.economy.pool, queue, boost)
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
-- #158: HourEvent-Tick (globaler Spielzeit-Takt) dient auch als Retry-Takt fuer
-- die HQ-Erkennung: der Spieler platziert das HQ waehrend der Setup-Phase ueber
-- das Build-Menue, ohne ein rb_wave/rb_send aufzurufen — der Tick erkennt es
-- und stoesst den Commence an (Muster Retry-Punkte PatchDomTimer).
local function OnHourEvent(evt)
    OnHourEventEconomy(evt)
    HqAutoDetectEntity()
end
pcall(function()
    RegisterGlobalEventHandler("HourEvent", OnHourEvent)
end)

-- Init: Persistenz zuruecklesen (Spar-Pool ueberlebt Runden + Neustart).
EconomyLoad()
EconomyLoadResources()

-- Wellenstart-Hook (#42) beim Laden versuchen (nachdem alle Definitionen
-- stehen; weitere Retry-Zeitpunkte: OnPlayerInitialized / jeder Send).
PatchWaveStartHook()

-- Send-Boost-Hook (#39) beim Laden versuchen (gleiche Retry-Zeitpunkte).
PatchSpawnWavesHook()

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
--   * HQ-Entity-Identifikation: TEILWEISE (Issue #144) — HqAutoDetectEntity()
--     versucht bei PlayerInitializedEvent/jedem rb_wave-Aufruf automatisch
--     ueber FindService:FindEntitiesByGroup("headquarters") zu binden (Gruppen-
--     Name aus docs/GAME_DESIGN.md-Tabelle "HQ | Entity `headquarters`,
--     `HealthService`, `ReportHeadquaterDamage`" -- dort selbst UNVERIFIZIERT,
--     in docs/research/ nicht belegt). Nur EIN eindeutiger Treffer wird
--     gebunden (event=hq_autodetect status=ok); bei 0 oder >1 Treffern bleibt
--     `rb_hq entity <id>` der verlaessliche manuelle Fallback (kein Rateschuss
--     bei Mehrdeutigkeit). Braucht In-Game-Bestaetigung, ob "headquarters" der
--     richtige Gruppen-Name ist.
-- ============================================================================

-- Konfiguration (hqHpStart muss TOURNAMENT_HQ_HP des Servers entsprechen,
-- Default 100). #33: HQ-HP-Kurve ueber Runden als dokumentierte Formel —
--   maxHp(r) = hqHpStart + hqHpPerRound * min(r-1, hqHpRoundCap)   (r >= 1)
-- Tabelle (braucht Live-Test, #33):
--   Runde  1    2    3    4    5+
--   maxHP  100  120  140  160  180
-- Bei jedem Wellenstart (OnNaturalWaveStart) wird der HQ-HP auf diesen
-- Runden-Maxwert gesetzt, solange das HQ nicht zerstoert ist.
RBB.hqCfg = {
    hqHpStart = 100,      -- Start-HP des HQ (Server-Default TOURNAMENT_HQ_HP)
    leakDamage = 10,      -- HP-Verlust je Leak (Kreatur erreicht die HQ-Zone)
    hqHpPerRound = 20,    -- #33: +HP je abgeschlossener Runde (ab Runde 2)
    hqHpRoundCap = 4,     -- #33: Kurve nach N Runden gedeckelt (max +20*4=80)
}

-- #33: HQ-HP-Kurve ueber Runden — reine Formel (unit-testbar). Deckelt die
-- Wachstums-Runden auf hqHpRoundCap; Ergebnis nie unter hqHpStart.
HqMaxHp = function(round)
    local r = math.floor(tonumber(round) or 1)
    if r < 1 then r = 1 end
    local growth = math.min(math.max(r - 1, 0), RBB.hqCfg.hqHpRoundCap)
    return RBB.hqCfg.hqHpStart + RBB.hqCfg.hqHpPerRound * growth
end

-- Laufzeit-Zustand der Win-Condition.
RBB.hq = {
    hp = 0,             -- aktueller HQ-HP (absolut, wie Server-Report)
    entity = nil,       -- getrackte HQ-Entity (nil = nicht zugeordnet)
    dead = false,       -- true nach HQ-Tod (Match-Ende gemeldet)
}

-- ============================================================================
-- #158 Setup-Phase / Commence-Flow: Spielstart OHNE Auto-HQ. Bis der Spieler
-- das HQ selbst ueber das Build-Menue platziert (erkannt via FindEntitiesByGroup
-- "headquarters", #144/#151), haelt der Wave-PROGRESS an. Es wird NUR der
-- Wave-PROGRESS pausiert, NICHT das Spiel (kein debug_dom_pause). Kein
-- HQ-Auto-Place. Announce auf drei Kanaelen (u.a. Telegram Topic 312 ueber die
-- Bridge, Web-Konsole ggf. nur vorbereitet):
--   in-game     = WriteConsole (In-Game-Konsole)
--   Telegram    = [RBBATTLE] event=commence ... (Bridge -> Topic 312)
--   Web-Konsole = dieselbe Log-Zeile (dev-log/solo.html, nur vorbereitet)
-- ============================================================================

-- Start-Announce (Setup-Phase): einmalig, idempotent.
RBB.setupAnnounced = false
AnnounceSetupPhase = function()
    if RBB.setupAnnounced then return end
    RBB.setupAnnounced = true
    Log("event=commence status=pending hint=place_hq")
    WriteConsole("To commence the game, place the headquarter")
end

-- Commence: HQ erkannt -> Waves starten + Commence-Announce. Idempotent.
CommenceGame = function()
    if RBB.commenced then return end
    RBB.commenced = true
    Log("event=commence status=ok")
    WriteConsole("Headquarter placed — waves commencing")
end

-- #144: Kandidaten-Gruppennamen fuer die automatische HQ-Entity-Erkennung.
-- "headquarters" ist der einzige im Repo dokumentierte Name (docs/GAME_DESIGN.md,
-- dort selbst UNVERIFIZIERT) -- weitere Kandidaten hier ergaenzen, sobald ein
-- In-Game-Test den tatsaechlichen Gruppen-/Blueprint-Namen bestaetigt/widerlegt.
RBB.hqEntityGroupCandidates = { "headquarters" }

-- #144: versucht RBB.hq.entity automatisch zu binden (Muster #26 Rand-Spawner:
-- FindService:FindEntitiesByGroup). Nur bei GENAU einem Treffer wird gebunden,
-- um keinen Rateschuss bei Mehrdeutigkeit zu riskieren; bei 0/>1 Treffern
-- bleibt der manuelle `rb_hq entity <id>`-Fallback die verlaessliche Option.
-- Bereits gebundene Entity (manuell oder frueherer Versuch) wird nicht
-- ueberschrieben. Idempotent, pcall-gesichert, mehrfach aufrufbar (Retry-
-- Muster wie PatchWaveStartHook: Mod-Load/PlayerInitializedEvent/jeder Send).
-- Nicht-ok-Ausgang (0/>1 Treffer je Kandidat, API fehlt) wird NUR einmal
-- geloggt (Guard wie unmatchedLogged/unarmedLeakLogged) -- HqAutoDetectEntity
-- wird bei jedem rb_wave/rb_send aufgerufen (Retry-Muster), das soll nicht
-- pro Aufruf spammen. Ein Erfolg wird immer geloggt und bindet sofort.
HqAutoDetectEntity = function()
    if RBB.hq.entity ~= nil then return end
    if FindService and FindService.FindEntitiesByGroup then
        for _, group in ipairs(RBB.hqEntityGroupCandidates) do
            local ok, list = pcall(function()
                return FindService:FindEntitiesByGroup(group)
            end)
            if ok and type(list) == "table" and #list == 1 then
                RBB.hq.entity = list[1]
                Log("event=hq_autodetect status=ok group=%s entity=%s",
                    group, tostring(list[1]))
                CommenceGame() -- #158: HQ erkannt -> Waves starten
                return
            end
        end
    end
    if not RBB.hq.autodetectFailLogged then
        RBB.hq.autodetectFailLogged = true
        Log("event=hq_autodetect status=not_found candidates=%s hint=rb_hq_entity",
            table.concat(RBB.hqEntityGroupCandidates, ","))
    end
end

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
-- sobald eine Kreatur sie betritt -> Leak. Event-Name UND Zone sind
-- unverifiziert (s. Kopf) -- EnteredTriggerEvent koennte jeder beliebige
-- Map-Trigger sein, nicht zwingend die HQ-Zone (Issue #143: sofortige
-- Niederlage durch fremde Trigger). Deshalb bleibt der Leak-Handler defensiv
-- inaktiv, bis eine HQ-Entity zugeordnet ist (rb_hq entity <id>, Issue #144)
-- -- Muster wie OnRespawnFailed weiter unten.
local function OnEnteredTrigger(evt)
    if RBB.hq.dead then return end
    if RBB.hq.entity == nil then
        if not RBB.hq.unarmedLeakLogged then
            RBB.hq.unarmedLeakLogged = true
            Log("event=hq_leak status=skip reason=no_hq_entity hint=rb_hq_entity")
        end
        return
    end
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
        RBB.hq.unarmedLeakLogged = false
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
        CommenceGame() -- #158: manuelle HQ-Zuordnung -> Waves starten
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

-- #33: rb_balance — zentrale Balance-Daten (Preisliste + HQ-HP-Kurve) als
-- Log-Flaeche. Read-only; testbare Vertragsflaeche fuer die Tuning-Werte.
local function CmdBalance(args)
    -- Preisliste (Tier-Struktur aus shopCfg, #25).
    for _, tier in ipairs(RBB.shopCfg.tiers) do
        for _, u in ipairs(tier.units) do
            Log("event=balance unit=%s tier=%s price=%d boss=%s",
                u.id, tier.id, u.price, tostring(u.boss == true))
        end
    end
    -- HQ-HP-Kurve (#33): Konstanten + Stichproben r=1..6 (inkl. Cap).
    Log("event=balance hq_curve start=%d per_round=%d cap=%d r1=%d r2=%d r3=%d r4=%d r5=%d r6=%d",
        RBB.hqCfg.hqHpStart, RBB.hqCfg.hqHpPerRound, RBB.hqCfg.hqHpRoundCap,
        HqMaxHp(1), HqMaxHp(2), HqMaxHp(3), HqMaxHp(4), HqMaxHp(5), HqMaxHp(6))
    -- Send-Boost (#39): Stufen + Caps (Annahme, braucht Live-Test).
    for _, s in ipairs(RBB.boostCfg.stages) do
        Log("event=balance boost_stage id=%s pct=%d price=%d", s.id, s.pct, s.price)
    end
    Log("event=balance boost_cfg price_per_pct=%d max_boost_pct=%d max_boosts_per_wave=%d",
        RBB.boostCfg.pricePerPct, RBB.boostCfg.maxBoostPct, RBB.boostCfg.maxBoostsPerWave)
    -- Wellen-Presets (#41): Variante A/B (Takt + Groesse) als Dokumentation.
    for _, v in pairs(RBB.wavePresets.variants) do
        Log("event=balance wave_preset id=%s interval=%d strength_pct=%d",
            v.id, v.intervalS, v.strengthPct)
    end
    Log("event=balance wave_preset_cfg active=%s base_difficulty=%s timer_cap=%d",
        RBB.wavePresets.active, RBB.wavePresets.baseDifficulty, RBB.waveIntervalCapS)
    WriteConsole("rb_balance: Preisliste + HQ-HP-Kurve + Boost-Stufen + Wellen-Presets geloggt (braucht Live-Test, #33/#39/#41)")
end

pcall(function()
    ConsoleService:RegisterCommand("rb_balance", function(args)
        CmdBalance(args)
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

-- #158: Start-Announce der Setup-Phase (Fallback fuer Mod-Load ohne
-- PlayerInitializedEvent; idempotent neben OnPlayerInitialized).
AnnounceSetupPhase()

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

-- ============================================================================
-- #99 Click-HUD (Senden per Klick statt Tippen): toggle-bares HUD-Overlay +
-- wichtigste Send-Aktion per Klick. Overlay = GuiService:OpenPopup mit dem
-- 2-Button-Template (Muster Baustein 02 / rb_shop); Klick = GuiPopupResultEvent
-- (button_yes = senden, button_no = schliessen). Reicht die bestehende
-- Befehls-/Event-Schicht durch: Klick ruft BuyWave (= rb_buy_wave-Logik) auf,
-- der Wellenstart-Hook (#25) liefert die Queue weiterhin bei der naechsten
-- Naturwelle aus. MVP-Umfang: Convert bleibt Konsolen-/Bridge-Aktion (braucht
-- Menge). Kein Version-Bump (Release/Tag macht der Loop).
-- ============================================================================

-- Laufzeit-Zustand des Click-HUD.
RBB.clickHud = {
    open       = false,     -- Overlay (Popup) aktuell offen
    quickUnit  = "brabit",  -- MVP-Quick-Send-Einheit (billigste Tier-1)
    quickCount = 1,         -- Einheiten je Klick
}

-- Overlay-Text: Rundenzustand + was ein Klick auf "Ja" senden wuerde.
local function ClickHudText()
    local q = RBB.sendQueue
    local countdown = RevealCountdown()
    local unit, _ = FindShopUnit(RBB.clickHud.quickUnit)
    local price = (unit and unit.price or 0) * RBB.clickHud.quickCount
    local lines = {
        '<style="header_35">RBBATTLE — Send-HUD</style>',
        string.format("Runde: %d", RBB.round),
        string.format("Countdown: %d s", countdown),
        string.format("Pool: %d", RBB.economy.pool),
        string.format("Queue: %d Einheiten", q.count),
    }
    lines[#lines + 1] = ""
    if unit ~= nil then
        lines[#lines + 1] = string.format(
            '<style="big_red">Senden:</style> %s x%d (%d) in die Queue',
            RBB.clickHud.quickUnit, RBB.clickHud.quickCount, price)
    else
        lines[#lines + 1] = string.format(
            '<style="big_red">Senden:</style> unbekannte Unit \'%s\' — rb_quick <unit>',
            RBB.clickHud.quickUnit)
    end
    return table.concat(lines, "\r\n")
end

-- Oeffnet das Click-HUD-Overlay (2-Button-Popup: Ja = senden, Nein = zu).
-- Best-effort wie rb_shop: ohne Spieler/API -> nur Log (status=no_player/..).
local function OpenClickHud()
    if not (GuiService and GuiService.OpenPopup) then
        Log("event=hud_ui status=api_missing")
        return
    end
    local okM, mech = pcall(function()
        return PlayerService:GetPlayerControlledEnt(0)
    end)
    if not okM or mech == nil or mech == INVALID_ID then
        Log("event=hud_ui status=no_player")
        return
    end
    local ok, err = pcall(function()
        return GuiService:OpenPopup(mech, "gui/popup/popup_ingame_2buttons", ClickHudText())
    end)
    if not ok then
        Log("event=hud_ui status=error err=%s", tostring(err))
        return
    end
    RBB.clickHud.open = true
    Log("event=hud_ui status=opened quick=%s count=%d round=%d countdown=%d pool=%d queue=%d",
        RBB.clickHud.quickUnit, RBB.clickHud.quickCount, RBB.round,
        RevealCountdown(), RBB.economy.pool, RBB.sendQueue.count)
end

-- GuiPopupResultEvent (Klick-Handler): "Ja" -> Quick-Send (BuyWave), "Nein"
-- -> schliessen ohne Aktion. Reagiert nur, wenn unser Overlay offen ist
-- (Guarded, damit z. B. das Shop-Popup hier nicht faelschlich ausloest).
local function OnGuiPopupResult(evt)
    if not RBB.clickHud.open then
        return -- nicht unser Popup (z. B. rb_shop) -> ignorieren
    end
    local okR, result = pcall(function() return evt:GetResult() end)
    if not okR or result == nil then
        -- Event nicht lesbar -> Zustand freigeben, kein Crash.
        RBB.clickHud.open = false
        return
    end
    if result == "button_yes" then
        -- Wichtigste Send-Aktion per Klick: Einheit(en) in die Queue kaufen.
        -- Nutzt die bestehende Befehls-Schicht (BuyWave = rb_buy_wave-Logik).
        BuyWave(RBB.clickHud.quickUnit, RBB.clickHud.quickCount)
        Log("event=hud_ui status=closed result=button_yes action=quick_send unit=%s count=%d",
            RBB.clickHud.quickUnit, RBB.clickHud.quickCount)
    else
        Log("event=hud_ui status=closed result=%s action=none",
            tostring(result))
    end
    RBB.clickHud.open = false
end

-- rb_hud_ui: Overlay ein-/ausblenden (Toggle).
local function CmdHudUi(args)
    if RBB.clickHud.open then
        Log("event=hud_ui status=already_open")
        WriteConsole("rb_hud_ui: Send-HUD ist bereits offen (Ja = senden, Nein = schliessen)")
        return
    end
    OpenClickHud()
end

-- rb_quick [<unit> [count]]: Quick-Send-Einheit ruesten (Default brabit).
local function CmdQuick(args)
    local unitId = nil
    local count = nil
    if args ~= nil and #args >= 1 then unitId = tostring(args[1]):lower() end
    if args ~= nil and #args >= 2 then count = tostring(args[2]) end

    if unitId == nil or unitId == "" or unitId == "help" then
        WriteConsole("rb_quick: Aufruf rb_quick [<unit> [count]] — aktuell: %s x%d (rb_shop zeigt Units)",
            RBB.clickHud.quickUnit, RBB.clickHud.quickCount)
        Log("event=quick_send status=usage unit=%s count=%d",
            RBB.clickHud.quickUnit, RBB.clickHud.quickCount)
        return
    end

    local unit, _ = FindShopUnit(unitId)
    if unit == nil then
        WriteConsole("rb_quick: unbekannte Unit '%s' — rb_shop zeigt die Liste", unitId)
        Log("event=quick_send status=unknown_unit unit=%s", unitId)
        return
    end

    local n = math.floor(tonumber(count) or 1)
    if n < 1 then n = 1 end
    if n > RBB.shopCfg.maxQueueCreatures then
        n = RBB.shopCfg.maxQueueCreatures
    end
    RBB.clickHud.quickUnit = unitId
    RBB.clickHud.quickCount = n
    Log("event=quick_send status=armed unit=%s count=%d price=%d",
        unitId, n, unit.price)
    WriteConsole("rb_quick: Quick-Send = %s x%d (%d je) — rb_hud_ui oeffnet das Send-HUD",
        unitId, n, unit.price)
end

-- rb_quick_step <+N|-N|xN>: Send-Menge relativ anpassen (Clicker-Stil, #147
-- MVP: +1/-1 fuer Feinjustierung, x10/x100/x1000 fuer Grobjustierung -- ohne
-- eine exakte Zahl tippen zu muessen). Wirkt auf dieselbe Quick-Send-Menge
-- wie rb_quick/rb_hud_ui (#99), kein eigener Zustand. Ein echtes klickbares
-- Overlay dafuer ist mangels verifizierter Multi-Button-/Freiform-GUI-API
-- noch offen (s. Issue #147); dieser Command liefert schon die Logik dafuer,
-- vorerst per Konsole statt per Klick.
local function CmdQuickStep(args)
    local opArg = nil
    if args ~= nil and #args >= 1 then opArg = tostring(args[1]):lower() end
    if opArg == nil or opArg == "" or opArg == "help" then
        WriteConsole("rb_quick_step: Aufruf rb_quick_step <+N|-N|xN> (z.B. +1, -1, x10, x100, x1000) -- aktuell: %s x%d",
            RBB.clickHud.quickUnit, RBB.clickHud.quickCount)
        Log("event=quick_step status=usage unit=%s count=%d",
            RBB.clickHud.quickUnit, RBB.clickHud.quickCount)
        return
    end

    local kind = opArg:sub(1, 1)
    local n = tonumber(opArg:sub(2))
    if (kind ~= "+" and kind ~= "-" and kind ~= "x") or n == nil or n <= 0 then
        WriteConsole("rb_quick_step: ungueltig '%s' -- Format +N/-N/xN (z.B. +1, x10)", opArg)
        Log("event=quick_step status=bad_op op=%s", opArg)
        return
    end
    n = math.floor(n)

    local before = RBB.clickHud.quickCount
    local after = before
    if kind == "+" then
        after = before + n
    elseif kind == "-" then
        after = before - n
    else
        after = before * n
    end
    if after < 1 then after = 1 end
    if after > RBB.shopCfg.maxQueueCreatures then after = RBB.shopCfg.maxQueueCreatures end

    RBB.clickHud.quickCount = after
    Log("event=quick_step status=ok op=%s unit=%s before=%d after=%d",
        opArg, RBB.clickHud.quickUnit, before, after)
    WriteConsole("rb_quick_step: %s -> %s x%d (rb_hud_ui zeigt/sendet)",
        opArg, RBB.clickHud.quickUnit, after)
end

pcall(function()
    ConsoleService:RegisterCommand("rb_hud_ui", function(args)
        CmdHudUi(args)
    end)
    ConsoleService:RegisterCommand("rb_quick", function(args)
        CmdQuick(args)
    end)
    ConsoleService:RegisterCommand("rb_quick_step", function(args)
        CmdQuickStep(args)
    end)
end)

-- Klick-Event registrieren (pcall-gesichert; unbekannter Event-Name laesst
-- den Rest unangetastet). Muster Baustein 02 (GuiPopupResultEvent).
pcall(function()
    RegisterGlobalEventHandler("GuiPopupResultEvent", OnGuiPopupResult)
end)

-- Lebenszeichen-Log beim Laden (analog Baustein 01 / Spike).
Log("event=mod_load version=%s status=ok mode=%s anchor=border_spawner_groups timer_cap=%d preset=%s econ_source=%s econ_pool=%d hq_hp=%d hq_dead=%s",
    RBB.version, RBB.mode, RBB.waveIntervalCapS, RBB.wavePresets.active,
    RBB.economy.source, RBB.economy.pool, RBB.hq.hp, tostring(RBB.hq.dead))
