-- ============================================================================
-- rbbattle_autoexec.lua — PLAYERMOD
-- PoC #629: BUILDING-SPIKE. Spawnt ein klickbares Gebäude (rbbattle_button)
-- beim Mech. Klick -> OnActivate -> PlayerChatRequest -> Backend.
-- ============================================================================

local RBB = {}
RBB.version = "0.34.3"
RBB.ref = "RBB_BUILD_REF"
RBB.build = "20260917-173910"

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

local INVALID = 4294967295

pcall(function()
    RegisterGlobalEventHandler("PlayerControlledEntityChangeEvent", function()
        if _G.RBB_BUTTON_SPAWNED then return end
        local mech = PlayerService:GetPlayerControlledEnt(PlayerService:GetLeadingPlayer())
        Log("event=controlled mech=%s", tostring(mech))
        if mech and mech ~= INVALID then
            _G.RBB_BUTTON_SPAWNED = true
            local pos = EntityService:GetPosition(mech)
            local ok, ent = pcall(function()
                return EntityService:SpawnEntity("buildings/decorations/rbbattle_button", pos.x + 6, pos.y, pos.z, "")
            end)
            Log("event=spawn_button ok=%s ent=%s", tostring(ok), tostring(ent))
        end
    end)
end)

Log("event=mod_load version=%s status=ok ref=%s", RBB.version, RBB.ref)
