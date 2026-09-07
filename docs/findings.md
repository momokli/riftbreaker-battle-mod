# Findings — verifiziert (Stand 07.09.2026)

Machbarkeits-Findings für den Runden-Duell-Modus, aus Doku-Crawl und Recherche verifiziert.

## Verifiziert

1. **Mod-API offiziell seit 2022** — Lua-basierte Mods, Distribution über Steam Workshop und mod.io.
   - Doku: <https://github.com/exorstudios/riftbreaker-wiki>
   - Tools: <https://github.com/exorstudios/riftbreaker-tools>

2. **Kein File-I/O in der Lua-API** — 0 Treffer für `io.open`, `WriteFile`, `ReadFile`, `serialize`, `http`, `socket`, `os.getenv` in der offiziellen Doku (komplett gegreppt). Ein Lua-Mod kann also **nicht direkt** von außen Daten lesen oder schreiben.

3. **Outbound via `LogService:Log` ins Spiel-Log möglich** — als Notnagel nutzbar, wird durch den Trainer-Ansatz aber obsolet (der Trainer liest den State direkt aus dem Prozess).

4. **Kein PvP im Spiel** — Co-op ist eine gemeinsame Welt/Simulation. Zwei parallele Spieler = zwei getrennte Partien, die nicht gegeneinander laufen können. Ein Duell braucht daher immer unsere eigene Kopplung (Relay).

5. **`mp_deathmatch` nicht nutzbar** — interner EXOR-Netcodetest (versteckte, Key-geschützte Beta 2023), kein offizieller Modus und nicht zugänglich.

## Offene Fragen (klärt Spike / Reverse Engineering)

- Existiert eine nutzbare In-Game-Konsole?
- Lassen sich Custom Console Commands registrieren?
- Ist Wave-Spawn zur Laufzeit aus Lua möglich?
- Ist Custom-UI (HUD) umsetzbar?
