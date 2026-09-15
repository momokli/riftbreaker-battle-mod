-- ============================================================================
-- rbbattle_autoexec.lua — PLAYERMOD (HUD/Player-Facing, GREEN FIELD)
--
-- SERVERMOD = rbbridge.dll (C++): State-Egress + WRITE liegen dort (#378).
-- PLAYERMOD = diese Lua: nur HUD/Display + Event-Emission.
--
-- Business-Logik lag bis Commit 3c4c5249696c17c0eb2f535802168b9f57c9e9a3 hier
-- und wurde entfernt (#378/#380). Wiederherstellbar via:
--   git show 3c4c524:mod/lua/rbbattle_autoexec.lua
-- ============================================================================

local RBB = {}
RBB.version = "0.34.3"
RBB.round = 0
RBB.mode = "sp"
RBB.commenced = false
RBB.hq = nil

-- Log-/Konsole-Helfer: Praefix [RBBATTLE] fuer externes Parsen.
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
-- PoC (#379): "send 10 mythium" nur als Event emittieren. Die Subtraktion macht
-- das Backend, NICHT der Mod. (Transport Webhook/curl noch offen.)
-- ---------------------------------------------------------------------------
pcall(function()
    ConsoleService:RegisterCommand("rb_poc_send", function(args)
        Log("event=poc_send amount=10 resource=mythium")
        WriteConsole("poc_send: 10 mythium (Backend subtrahiert)")
    end)
end)

-- Lade-Marker für Boot-Test C1 + Deploy-Runtime-Check (kein Business-Logik,
-- nur das Lebenszeichen, das die Pipeline erwartet).
--
-- Deploy-Identitaet (Issue #483, US4): env/ref aus dem Container-Env
-- (RBB_ENV/RBB_REF, gesetzt vom Compose-Template). Bewusst defensiv: fehlt
-- `os.getenv` in der Sandbox oder wirft der Aufruf, faellt der Wert auf
-- "unknown" zurueck und die BESTEHENDEN Felder (version/status) bleiben
-- unveraendert — der mod_load-Marker wird nie gebrochen.
--
-- BEST-EFFORT, im Live-Lauf bisher NICHT wirksam (Review-F2): im Boot-Test
-- stand trotz gesetztem RBB_ENV=test/RBB_REF=<sha> weiterhin
-- `event=mod_load ... env=unknown ref=unknown` — die Riftbreaker-Lua-Sandbox
-- liefert `os.getenv` offenbar nicht (nil/Exception), der Fallback "unknown"
-- greift. Diese Surface ist also nur VORBEREITET, nicht als erledigt zu
-- fuehren. Verlaesslicher Kanal (Config/Datei statt Lua-getenv) ist Follow-up;
-- die uebrigen Identitaets-Surfaces (Labels, Tournament, Server-Control,
-- Sidecars) liefern env/ref unabhaengig davon korrekt.
local function DeployEnv(name)
    local ok, value = pcall(function()
        if type(os) == "table" and type(os.getenv) == "function" then
            return os.getenv(name)
        end
        return nil
    end)
    if ok and type(value) == "string" and value ~= "" then
        return value
    end
    return "unknown"
end

Log("event=mod_load version=%s status=ok env=%s ref=%s",
    RBB.version, DeployEnv("RBB_ENV"), DeployEnv("RBB_REF"))
