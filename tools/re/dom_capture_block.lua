-- ---------------------------------------------------------------------------
-- #376: Voll-State-Egress an die C++-Bridge (game thread). Der Mod baut hier
-- pro Frame einen JSON-Snapshot und ruft _G.rbbridge_capture_state(json) auf.
-- Die DLL darf Lua NICHT vom pipe thread anfassen (crasht, s. SKILL
-- riftbreaker-re); dieser Hook laeuft deshalb auf dem game thread.
-- ---------------------------------------------------------------------------
RBB.domCapturePatched = false
RBB.domCaptureOrig = nil

-- Minimal-JSON-String: unsere Werte sind Identifier (keine Quotes/Steuerzeichen).
local function rbJsonStr(s)
    if type(s) ~= "string" then return '""' end
    return '"' .. tostring(s) .. '"'
end

-- Restzeit eines State-Machine-States (GetDurationLimit - GetDuration).
local function rbStateRemaining(sm, name)
    local ok, s = pcall(sm.GetState, sm, name)
    if not ok or type(s) ~= "userdata" and type(s) ~= "table" then return nil end
    local okL, lim = pcall(s.GetDurationLimit, s)
    local okD, dur = pcall(s.GetDuration, s)
    if okL and okD and type(lim) == "number" and type(dur) == "number" then
        return lim - dur
    end
    return nil
end

-- State-abhaengiger Countdown (Spiegel von dom_mananger:Update-Debuglogik).
local function DomTimeToNext(self)
    local spawner = self.spawner
    if type(spawner) ~= "userdata" and type(spawner) ~= "table" then return 0 end
    local okState, state = pcall(spawner.GetCurrentState, spawner)
    if not okState or type(state) ~= "string" or state == "" then return 0 end
    if state == "cooldown_after_spawn" then
        return tonumber(self.cooldownTimer) or 0
    elseif state == "prepare_spawn" then
        return tonumber(self.waitForSpawnTimer) or 0
    elseif state == "idle" then
        return tonumber(self.idleTimer) or 0
    elseif state == "sleep" then
        return tonumber(self.sleepSafeTimer) or 0
    elseif state == "wait" then
        return rbStateRemaining(spawner, "wait") or 0
    end
    return 0
end

-- Baut den kompletten Snapshot als JSON-Objekt-String.
local function BuildStateJson(self)
    local f = {}
    local function numf(k, v) if type(v) == "number" then f[#f + 1] = '"' .. k .. '":' .. tostring(v) end end
    local function boolf(k, v) if v == true then f[#f + 1] = '"' .. k .. '":true' elseif v == false then f[#f + 1] = '"' ..
            k .. '":false' end end
    local function strf(k, v) if type(v) == "string" then f[#f + 1] = '"' .. k .. '":' .. rbJsonStr(v) end end
    local function svc(k, fn)
        local ok, v = pcall(fn)
        if ok then
            local t = type(v)
            if t == "number" then
                numf(k, v)
            elseif t == "boolean" then
                boolf(k, v)
            elseif t == "string" then
                strf(k, v)
            end
        end
    end

    -- DOM Schwierigkeit
    numf("wave", self.currentDifficultyLevel)
    numf("max_wave", self.maxDifficultyLevel)
    numf("frozen_wave", self.freezedDifficultyLevel)

    -- Spawner (Wellen-Zyklus)
    local spawnerState = ""
    pcall(function() spawnerState = self.spawner:GetCurrentState() or "" end)
    strf("dom_state", spawnerState)
    numf("time_to_next", math.ceil(DomTimeToNext(self)))
    numf("cooldown_timer", self.cooldownTimer)
    numf("idle_timer", self.idleTimer)
    numf("prepare_timer", self.waitForSpawnTimer)
    numf("sleep_timer", self.sleepSafeTimer)

    -- HQ
    local hqState = ""
    pcall(function() hqState = self.upgradeHQ:GetCurrentState() or "" end)
    strf("hq_state", hqState)
    numf("hq_attack_timer", self.hqAttackSafeTimer)

    -- Schwierigkeits-Progression
    local diffState = ""
    pcall(function() diffState = self.difficultyIncrease:GetCurrentState() or "" end)
    strf("difficulty_state", diffState)
    numf("time_to_next_difficulty", rbStateRemaining(self.difficultyIncrease, "difficulty_increase"))

    -- Flags
    boolf("pause_attacks", self.pauseAttacks)
    boolf("cancel_attack", self.cancelTheAttack)
    boolf("spawn_boss", self.spawnBoss)
    numf("extra_attacks", self.extraAttacks)

    -- Zaehler
    numf("spawned_attacks", (type(self.spawnedAttacks) == "table") and #self.spawnedAttacks or 0)
    numf("prepared_attacks", (type(self.preparedAttacks) == "table") and #self.preparedAttacks or 0)

    -- Event/Objective (event_manager)
    numf("event_level", self.currentEventLevel)
    numf("event_timer", self.eventManagerTimer)
    numf("active_objectives", (type(self.objectiveActiveList) == "table") and #self.objectiveActiveList or 0)
    if type(self.objectiveLastSpawnTime) == "number"
        and type(self.objectiveCurrentTimeBetweenNext) == "number"
        and type(self.eventManagerTimer) == "number" then
        numf("time_to_next_objective",
            self.objectiveLastSpawnTime + self.objectiveCurrentTimeBetweenNext - self.eventManagerTimer)
    end

    -- Services
    svc("players", function() return self:GetPlayersCounter() end)
    svc("creatures_difficulty", function() return CampaignService:GetCreaturesBaseDifficulty() end)
    svc("difficulty_name", function() return DifficultyService:GetCurrentDifficultyName() end)
    svc("wave_strength", function() return DifficultyService:GetWaveStrength() end)
    svc("waves_disabled", function() return DifficultyService:AreWavesDisabled() end)
    svc("mission_name", function() return MissionService:GetCurrentMissionName() end)
    svc("biome", function() return MissionService:GetCurrentBiomeName() end)
    svc("mission_duration", function() return DifficultyService:GetMissionDuration() end)
    svc("warmup_duration", function() return DifficultyService:GetWarmupDuration() end)
    svc("mission_infinite", function() return DifficultyService:IsMissionInfinite() end)

    -- Mod-eigener State
    numf("round", RBB.round)
    strf("mode", RBB.mode)
    boolf("commenced", RBB.commenced)
    if RBB.hq then
        numf("hq_hp", RBB.hq.hp)
        boolf("hq_dead", RBB.hq.dead)
    end

    return "{" .. table.concat(f, ",") .. "}"
end

local function PatchDomCapture()
    if RBB.domCapturePatched then
        return RBB.domCaptureOrig ~= nil
    end

    local dom = nil
    if type(_G) == "table" then
        dom = rawget(_G, "dom_mananger")
    end
    local dType = type(dom)
    if dType ~= "table" and dType ~= "userdata" then
        return false -- Klasse (noch) nicht geladen; naechster Versuch spaeter
    end

    local orig = dom.Update
    if type(orig) ~= "function" then
        Log("event=dom_capture patch status=skip reason=no_api")
        RBB.domCapturePatched = true
        return false
    end

    if RBB.domCaptureOrig == nil then
        RBB.domCaptureOrig = orig
        dom.Update = function(self, dt)
            local cap = rawget(_G, "rbbridge_capture_state")
            if type(cap) == "function" then
                pcall(cap, BuildStateJson(self))
            end
            return RBB.domCaptureOrig(self, dt)
        end
        Log("event=dom_capture patch status=ok")
    end

    RBB.domCapturePatched = true
    return true
end
