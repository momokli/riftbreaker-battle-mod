-- rbbattle_button.lua — klickbares Objekt: Klick (Selektion) sendet Chat.
-- Basis ist das GLOBALE `LuaEntityObject` (kein require von Game-Dateien —
-- Mods haben keinen Zugriff auf die internen Game-Lua-Dateien via require).
class 'rbbattle_button' (LuaEntityObject)

function rbbattle_button:__init()
    LuaEntityObject.__init(self, self)
end

function rbbattle_button:init()
    self:RegisterHandler(self.entity, "EntitySelectedEvent", "OnEntitySelected")
    LogService:Log("[RBBATTLE] rbbattle_button init")
end

function rbbattle_button:OnEntitySelected(evt)
    LogService:Log("[RBBATTLE] building_selected")
    local player = PlayerService:GetLeadingPlayer()
    local mech = PlayerService:GetPlayerControlledEnt(player)
    if mech == nil or mech == 4294967295 then
        LogService:Log("[RBBATTLE] building_chat skipped: no mech")
        return
    end
    local ok, err = pcall(QueueEvent, "PlayerChatRequest", mech, "building button click", 4)
    if not ok then
        LogService:Log("[RBBATTLE] building_chat error: " .. tostring(err))
        return
    end
    LogService:Log("[RBBATTLE] building_chat sent: building button click")
end

return rbbattle_button
