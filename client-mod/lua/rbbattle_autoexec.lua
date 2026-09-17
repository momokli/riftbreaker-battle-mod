-- ============================================================================
-- rbbattle_autoexec.lua — PLAYERMOD
-- PoC #631: Markt-Area mit 7 Gebäuden (UNIT LOW/MID/HIGH, BOSS LOW/MID/HIGH/RANDOM).
-- NUR der Dedicated-Server spawnt die Markt-Area (serverseitige Autorität).
-- Client/Single-Player: kein Spawn (Client rendert nur die Server-Entities).
-- Jedes Gebäude: Space (InteractWithEntityRequest) + Radial (SpecialBuildingActionRequest) -> Chat.
-- ============================================================================

local RBB = {}
RBB.version = "0.34.3"
RBB.ref = "RBB_BUILD_REF"
RBB.build = "20260917-203600"

local LOG_TAG = "[RBBATTLE:" .. RBB.build .. "]"

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
local BUTTONS = {
    { bp = "buildings/decorations/rbbattle_button_unit_low",    msg = "unit low" },
    { bp = "buildings/decorations/rbbattle_button_unit_mid",    msg = "unit mid" },
    { bp = "buildings/decorations/rbbattle_button_unit_high",   msg = "unit high" },
    { bp = "buildings/decorations/rbbattle_button_boss_low",    msg = "boss low" },
    { bp = "buildings/decorations/rbbattle_button_boss_mid",    msg = "boss mid" },
    { bp = "buildings/decorations/rbbattle_button_boss_high",   msg = "boss high" },
    { bp = "buildings/decorations/rbbattle_button_boss_random", msg = "boss random" },
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
            -- 2 Reihen: UNIT (1-3) vorne, BOSS (4-7) dahinter.
            local row = (i <= 3) and 0 or 1
            local col = (i <= 3) and (i - 1) or (i - 4)
            local x = pos.x + 4 + col * 4
            local z = pos.z + 4 + row * 5
            local ok, ent = SpawnButton(b.bp, x, pos.y, z, b.msg)
            Log("event=spawn_market i=%d msg=%s ok=%s ent=%s", i, b.msg, tostring(ok), tostring(ent))
        end
    end)
end)

Log("event=mod_load version=%s status=ok ref=%s mode=%s", RBB.version, RBB.ref,
    IsDedicatedServer() and "server" or "client")
