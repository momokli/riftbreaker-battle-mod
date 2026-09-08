-- ============================================================================
-- rbbattle_03_logbridge_autoexec.lua  (Baustein 03: Log-Bridge)
--
-- Abgeleitet aus mod/lua/rbbattle_autoexec.lua (Spike, PR #1) - Experiment C
-- (Log-Bridge): [RBBATTLE]-Zeilen via LogService:Log ins Spiel-Log
-- (Documents/The Riftbreaker/exor_logs.txt). Isoliert mit eigenem
-- Test-Command rb_bridge_test [n]; kein Wave-Spawn, kein UI.
--
-- Genutzte API:
--   LogService:Log(...)                -> exor_logs.txt (6 rotierende Dateien)
--   ConsoleService:RegisterCommand(...) -> rb_bridge_test registrieren
--   (beides im Spike verifiziert, siehe docs/findings.md Punkte 4/7)
--
-- Zeilenformat: "[RBBATTLE] key=value key=value ..." - extern parsebar,
-- z.B. mit bausteine/03-log-bridge/tail_events.py.
-- ============================================================================

local RBB = {}
RBB.version = "0.1.0-baustein03"
RBB.run = 0 -- Durchlauf-Zaehler fuer rb_bridge_test

-- Log-Helfer (Muster Spike): Praefix [RBBATTLE] + formatierte Meldung.
local LOG_TAG = "[RBBATTLE]"
local function Log(fmt, ...)
    local okMsg, msg = pcall(string.format, fmt, ...)
    if not okMsg then msg = fmt end
    local service = LogService
    if service then
        pcall(service.Log, service, LOG_TAG .. " " .. msg)
    end
end

-- Test-Command: schreibt <count> Durchlaeufe a zwei key=value-Zeilen.
-- (Bewusst ohne ConsoleService:Write - dieser Baustein testet NUR den
-- LogService:Log-Pfad nach exor_logs.txt.)
local function BridgeTest(count)
    count = math.floor(tonumber(count) or 1)
    if count < 1 then count = 1 end
    if count > 10 then count = 10 end -- Schutz vor Endlos-Args
    for _ = 1, count do
        RBB.run = RBB.run + 1
        local run = RBB.run
        Log("event=bridge_test run=%d status=start", run)
        Log("event=bridge_test run=%d status=done", run)
    end
end

pcall(function()
    ConsoleService:RegisterCommand("rb_bridge_test", function(args)
        local count = 1
        if args and #args >= 1 then
            count = tonumber(args[1]) or 1
        end
        BridgeTest(count)
    end)
end)

-- Lebenszeichen-Log beim Laden (analog Spike).
Log("event=mod_load version=%s status=ok", RBB.version)
