-- rbbattle_button.lua — Markt-Area-Gebäude.
-- Trigger 1 (Space/Interact): InteractWithEntityRequest (InteractiveComponent ACTIVATOR).
-- Trigger 2 (Radial-Menü): SpecialBuildingActionRequest (BuildingDesc.menu_action).
-- Message kommt aus _G.RBB_BUTTON_MESSAGES[entity] (vom Autoexec gesetzt).
local building = require("lua/buildings/building.lua")

class 'rbbattle_button' (building)

function rbbattle_button:__init()
    building.__init(self)
end

function rbbattle_button:OnInit()
    self:RegisterHandler(self.entity, "InteractWithEntityRequest", "OnInteractWithEntityRequest")
    self:RegisterHandler(self.entity, "SpecialBuildingActionRequest", "OnSpecialAction")
    self.data:SetInt("is_special_action_enabled", 1)
    LogService:Log("[RBBATTLE] rbbattle_button init")
end

function rbbattle_button:GetMessage()
    local msg = _G.RBB_BUTTON_MESSAGES and _G.RBB_BUTTON_MESSAGES[self.entity]
    if type(msg) ~= "string" or msg == "" then msg = "rbbattle button" end
    return msg
end

local BUTTON_COOLDOWN_SEC = 1.0

-- 1s-Click-Cooldown pro Button: liefert false, wenn noch im Cooldown. Nutzt
-- os.clock() (CPU-Zeit, monoton); ist die Sandbox ohne os.clock(), wird der
-- Cooldown uebersprungen (kein Fehler).
function rbbattle_button:TryCooldown()
    local now = 0
    local ok = pcall(function() now = os.clock() end)
    if not ok then
        return true
    end
    if (self.cooldown_until or 0) > now then
        return false
    end
    self.cooldown_until = now + BUTTON_COOLDOWN_SEC
    return true
end

-- Kurzer zufaelliger Buy-Request-Identifier (reicht zum Tracing).
local function GenerateBuyId()
    return string.format("%08x", math.floor(math.random(0, 0x7FFFFFFF)))
end

function rbbattle_button:SendChat()
    local text = self:GetMessage() .. " " .. GenerateBuyId()
    local player = PlayerService:GetLeadingPlayer()
    local mech = PlayerService:GetPlayerControlledEnt(player)
    if mech == nil or mech == 4294967295 then
        LogService:Log("[RBBATTLE] button_chat skipped: no mech")
        return false
    end
    local ok, err = pcall(QueueEvent, "PlayerChatRequest", mech, text, 4)
    if not ok then
        LogService:Log("[RBBATTLE] button_chat error: " .. tostring(err))
        return false
    end
    LogService:Log("[RBBATTLE] button_chat sent: " .. text)
    return true
end

function rbbattle_button:OnInteractWithEntityRequest(evt)
    if not self:TryCooldown() then
        LogService:Log("[RBBATTLE] button_interact: cooldown, ignoriert")
        return
    end
    LogService:Log("[RBBATTLE] button_interact (space)")
    self:SendChat()
end

function rbbattle_button:OnSpecialAction()
    if not self:TryCooldown() then
        LogService:Log("[RBBATTLE] button_special_action: cooldown, ignoriert")
        return
    end
    LogService:Log("[RBBATTLE] button_special_action (radial)")
    self:SendChat()
end

return rbbattle_button
