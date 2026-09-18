-- ============================================================================
-- rbbattle_autoexec.lua — PLAYERMOD
-- PoC #631: Markt-Area mit 9 Wave-Buttons (WAVE 1..9), horizontal angeordnet.
-- NUR der Dedicated-Server spawnt die Markt-Area (serverseitige Autorität).
-- Client/Single-Player: kein Spawn (Client rendert nur die Server-Entities).
-- Jedes Gebäude: Space (InteractWithEntityRequest) -> Chat "-send waveN"
--   (-> pipe_bridge: try_spend + activate_mission_flow nach 5 min, #694/#698).
-- Preise = Test-C-Kurve (#670), Label „send WAVE N | <preis>“ kommt aus der .ent.
-- ============================================================================

local RBB = {}
-- Einheitliche Build-Identitaet (Issue #494): version == ref == SHA (dev) bzw.
-- Tag (prod). Der Platzhalter wird beim Packen (package.sh) durch den echten
-- Ref ersetzt. Keine hartcodierte Versionsnummer.
RBB.version = "RBB_BUILD_REF"
RBB.ref = "RBB_BUILD_REF"

local LOG_TAG = "[RBBATTLE:" .. RBB.version .. "]"

local function Log(fmt, ...)
    local okMsg, msg = pcall(string.format, fmt, ...)
    if not okMsg then msg = fmt end
    local service = LogService
    if service then pcall(service.Log, service, LOG_TAG .. " " .. msg) end
end

local function WriteConsole(fmt, ...)
    local okMsg, msg = pcall(string.format, fmt, ...)
    if not okMsg then msg = fmt end
    local service = ConsoleService
    if service then pcall(service.Write, service, LOG_TAG .. " " .. msg) end
end

function RBSendChat(text)
    if type(text) ~= "string" or text == "" then text = "hello world" end
    local player = PlayerService:GetLeadingPlayer()
    local mech = PlayerService:GetPlayerControlledEnt(player)
    if mech == nil then
        WriteConsole("rb_chat: keine Mech-Entity")
        return false
    end
    local ok, err = pcall(QueueEvent, "PlayerChatRequest", mech, text, 4)
    if not ok then
        Log("event=chat_send status=error err=%s", tostring(err))
        return false
    end
    Log("event=chat_send status=ok text=%s", text)
    WriteConsole("rb_chat: gesendet: " .. text)
    return true
end

-- Konsolen-Kommandos (Hotkey/Manual-Test), unabhängig vom Gebäude.
pcall(function()
    ConsoleService:RegisterCommand("rb_hello", function(args) RBSendChat("hello world") end)
    ConsoleService:RegisterCommand("rb_chat", function(args)
        local text = nil
        if type(args) == "table" and #args >= 1 then text = table.concat(args, " ") end
        RBSendChat(text)
    end)
end)

local INVALID = 4294967295

-- Blueprint je Button (Label kommt aus localization_id der jeweiligen .ent).
-- Preise = Test-C-Kurve (#670): 10/110/480/960/1590/2250/2800/3060/3110.
local BUTTONS = {
    { bp = "buildings/decorations/rbbattle_wave_1", msg = "-send wave1" },
    { bp = "buildings/decorations/rbbattle_wave_2", msg = "-send wave2" },
    { bp = "buildings/decorations/rbbattle_wave_3", msg = "-send wave3" },
    { bp = "buildings/decorations/rbbattle_wave_4", msg = "-send wave4" },
    { bp = "buildings/decorations/rbbattle_wave_5", msg = "-send wave5" },
    { bp = "buildings/decorations/rbbattle_wave_6", msg = "-send wave6" },
    { bp = "buildings/decorations/rbbattle_wave_7", msg = "-send wave7" },
    { bp = "buildings/decorations/rbbattle_wave_8", msg = "-send wave8" },
    { bp = "buildings/decorations/rbbattle_wave_9", msg = "-send wave9" },
}

local function IsDedicatedServer()
    local ok, mode = pcall(function() return ConsoleService:GetConfig("app_mode") end)
    return ok and type(mode) == "string" and mode == "server"
end

local function SpawnButton(bp, x, y, z, msg)
    local ok, ent = pcall(function()
        return EntityService:SpawnEntity(bp, x, y, z, "")
    end)
    if ok and ent ~= nil and ent ~= INVALID then
        _G.RBB_BUTTON_MESSAGES = _G.RBB_BUTTON_MESSAGES or {}
        _G.RBB_BUTTON_MESSAGES[ent] = msg
    end
    return ok, ent
end

pcall(function()
    RegisterGlobalEventHandler("PlayerControlledEntityChangeEvent", function()
        -- Nur der Dedicated-Server spawnt. Client rendert nur.
        if not IsDedicatedServer() then return end
        if _G.RBB_MARKET_SPAWNED then return end

        local mech = PlayerService:GetPlayerControlledEnt(PlayerService:GetLeadingPlayer())
        Log("event=controlled mech=%s", tostring(mech))
        if not mech or mech == INVALID then return end

        _G.RBB_MARKET_SPAWNED = true
        local pos = EntityService:GetPosition(mech)
        for i, b in ipairs(BUTTONS) do
            -- Eine horizontale Reihe (links->rechts): in Riftbreaker laeuft
            -- links/rechts ueber die Z-Achse (X ist Tiefe/oben-unten auf dem
            -- Screen). Deshalb X fix, Z variieren.
            local x = pos.x + 4
            local z = pos.z + 4 + (i - 1) * 4
            local ok, ent = SpawnButton(b.bp, x, pos.y, z, b.msg)
            Log("event=spawn_market i=%d msg=%s ok=%s ent=%s", i, b.msg, tostring(ok), tostring(ent))
        end
    end)
end)

Log("event=mod_load version=%s status=ok ref=%s mode=%s", RBB.version, RBB.ref,
    IsDedicatedServer() and "server" or "client")
