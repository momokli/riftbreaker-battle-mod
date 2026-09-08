# Findings — verifiziert (Stand 07.09.2026)

Machbarkeits-Findings für den Runden-Duell-Modus, aus Doku-Crawl und Recherche verifiziert. Die Punkte 6–11 stammen aus Matheos Prototyp-Repo (<https://github.com/BestToasty/riftbreaker_mod>, Stand 07.09.2026).

## Verifiziert

1. **Mod-API offiziell seit 2022** — Lua-basierte Mods, Distribution über Steam Workshop und mod.io.
   - Doku: <https://github.com/exorstudios/riftbreaker-wiki>
   - Tools: <https://github.com/exorstudios/riftbreaker-tools>

2. **Kein File-I/O in der Lua-API** — 0 Treffer für `io.open`, `WriteFile`, `ReadFile`, `serialize`, `http`, `socket`, `os.getenv` in der offiziellen Doku (komplett gegreppt). Ein Lua-Mod kann also **nicht direkt** von außen Daten lesen oder schreiben.

3. **Outbound via `LogService:Log` ins Spiel-Log möglich** — als Notnagel nutzbar, wird durch den Trainer-Ansatz aber obsolet (der Trainer liest den State direkt aus dem Prozess).

4. **Kein PvP im Spiel** — Co-op ist eine gemeinsame Welt/Simulation. Zwei parallele Spieler = zwei getrennte Partien, die nicht gegeneinander laufen können. Ein Duell braucht daher immer unsere eigene Kopplung (Relay).

5. **`mp_deathmatch` nicht nutzbar** — interner EXOR-Netcodetest (versteckte, Key-geschützte Beta 2023), kein offizieller Modus und nicht zugänglich.

6. **Mod-Struktur läuft** — `Mods/`-Ordner plus `lua/*_autoexec.lua` wird von der Engine fehlerfrei geladen.

7. **Custom Console Commands funktionieren** — `ConsoleService:RegisterCommand("name", fn)` registriert eigene Kommandos in der In-Game-Konsole.

8. **Spawn zur Laufzeit funktioniert** — `ConsoleService:ExecuteCommand("debug_spawn_entity units/ground/spawner_canoptrix")` spawnt das Boss-Nest an der Spielerposition.

9. **Global-Events funktionieren** — `RegisterGlobalEventHandler("PlayerCreatedEvent", fn)` wird zuverlässig ausgelöst.

10. **Konsolen-Ausgabe funktioniert** — `ConsoleService:Write(text)` gibt Text in der In-Game-Konsole aus.

11. **`io.open` CRASHT das Spiel** — Datei-I/O ist im Lua-Sandbox blockiert und beendet das Spiel hart (nicht nur ein Fehler!). `io.*` im Mod niemals anfassen.

## Offene Fragen (klärt Spike / Reverse Engineering)

- **Custom-UI (HUD) umsetzbar?** — bleibt offen; einziger ungeklärter Punkt.

Der Prototyp hat alles Weitere geklärt: Mod-Load ✓, Wave-Spawn ✓, Console-Commands ✓.
