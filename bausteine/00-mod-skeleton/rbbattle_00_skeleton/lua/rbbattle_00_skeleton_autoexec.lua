-- ============================================================================
-- rbbattle_00_skeleton_autoexec.lua  (Baustein 00: Mod-Skeleton)
--
-- Abgeleitet aus mod/lua/rbbattle_autoexec.lua (Spike, PR #1) - reduziert auf
-- den Skeleton-Lebenszeichen-Log. Kein Command, kein UI, keine Bindings.
--
-- Zweck: validiert den Mod-Load-Pfad. Autoexec-Dateien ("*_autoexec.lua"
-- unter lua/) werden von EXOR bei jeder Kartenerstellung ausgefuehrt und
-- haben Zugriff auf alle Services (exorstudios/riftbreaker-wiki, autoexec.md).
--
-- Erwartung: GENAU EINE Zeile im Spiel-Log (Documents/The Riftbreaker/
-- exor_logs.txt):  [RBBATTLE] skeleton ok
--
-- Alle API-Aufrufe sind pcall-gesichert (graceful no-op, Muster Spike).
-- ============================================================================

-- Lebenszeichen via LogService:Log (Spike-Muster: Praefix im Text).
local service = LogService
if service then
    pcall(service.Log, service, "[RBBATTLE] skeleton ok")
end

-- Zusaetzlich in die In-Game-Konsole schreiben (falls offen), damit der
-- Erfolg auch ohne Log-Datei sichtbar ist.
local cs = ConsoleService
if cs and cs.Write then
    pcall(cs.Write, cs, "[RBBATTLE] skeleton ok")
end

-- Kein return: Autoexec-Dateien sind keine Modul-Requires (Muster der
-- Spiel-eigenen lua/commands/*.lua und der Workshop-Mods von lilly1987).
