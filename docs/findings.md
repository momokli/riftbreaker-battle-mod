# Findings — verifiziert (Stand 08.09.2026, Spike-Research)

Machbarkeits-Findings für den Runden-Duell-Modus. Basis: exorstudios-Wiki
(github.com/exorstudios/riftbreaker-wiki), fandom-Wiki (riftbreaker.fandom.com,
„Mod service:*“-Signatur-Dumps, „Console commands“), echte Workshop-Mods
(github.com/lilly1987/Riftbreaker-mods) und **extrahierte Original-Spieldaten**
(github.com/PonomarevDmitry/RiftbreakersMods → `OriginalPacksData/`).

## Verifiziert

1. **Mod-API offiziell seit 2022** — Lua-basierte Mods, Distribution über Steam
   Workshop und mod.io (Doku: exorstudios-Wiki; Tools: exorstudios/riftbreaker-tools).
2. **Kein File-I/O in der Lua-API** — 0 Treffer für `io.open`, `WriteFile`,
   `ReadFile`, `serialize`, `http`, `socket`, `os.getenv` in der offiziellen Doku.
3. **Mod-Layout & Einstiegspunkt** — Mod = Ordner, der die Content-Struktur des
   Spiels spiegelt: `<game>/mods/<ModName>/` mit `lua/*_autoexec.lua` als
   „Master-Skript“. Autoexec läuft bei Kartenerstellung, Zugriff auf alle
   Services + Reflection + `RegisterGlobalEventHandler`.
4. **Custom Console Commands gehen** — `ConsoleService:RegisterCommand(name, cb)`,
   `ExecuteCommand(...)`, `Write(...)`, `GetConfig(...)`. EXOR nutzt das selbst
   (`lua/commands/cheat.lua` → `debug_spawn_entity`). Hotkeys via
   `ExecuteCommand('bind f7 "cmd"')`.
5. **Wave-Spawn zur Laufzeit geht** — `EntityService:SpawnEntity(blueprint, x, y, z, team)`
   (mehrere Overloads, u.a. an Entity/Position). EXORs `debug_spawn_entity`
   spawnt exakt darüber am Spieler (Team `""`). Kreaturen-Blueprints
   `units/ground/<name>` gegen `.ent`-Dateien der Spieldaten verifiziert.
6. **Custom-UI geht, mit Einschränkung** — `GuiService:OpenPopup(entity, template, text)`
   + `GuiPopupResultEvent` (Wiki-Beispiel, Template in Spieldaten vorhanden).
   `GuiService:ShowHudText(id, content)` existiert, aber gültige HUD-`id`s sind
   undokumentiert → Popup-Ansatz fürs UI-Experiment.
7. **Outbound-Logging** — `LogService:Log(...)` → `<Documents>\The Riftbreaker\exor_logs.txt`
   (6 rotierende Dateien); `ConsoleService:Write(...)` → In-Game-Konsole.
8. **In-Game-Konsole existiert** — Tasten ´/ö/'/ñ/ù/`~`/` (Layout-abhängig);
   GamePass: `enable_developer_console 1` in `Conf/initial_config_win`.
9. **Kein PvP im Spiel** — Co-op ist eine gemeinsame Welt. Duell = eigene
   Kopplung zweier Partien (Relay/Trainer, siehe concept.md).
10. **`mp_deathmatch` nicht nutzbar** — interner EXOR-Netcodetest, kein Zugang.

## Offene Punkte (klärt der In-Game-Test, nicht mehr die Doku)

- macOS-Mod-Support (offiziell „Steam/GamePass PC“; Ordner-Pfad analog anlegen).
- Exakte Feind-Team-Zuordnung bei `SpawnEntity(..., "")` (Blueprint-Standard erwartet).
- Popup-/HUD-Verhalten in realistischen Spielsituationen (Fokus, Mehrfach-Popups).
- Bind-Persistenz der Konsole über Sessions hinweg (unschädlich, s. mod/README).

## Spike-Ergebnisse

`mod/` enthält Skeleton + Experimente A (Wave-Spawn), B (Custom-UI-Popup) und
C (Log-Bridge `[RBBATTLE] event=...` + Konsolen-Command `rb_wave <level>`).
Installation & FINDINGS-Tabelle: [`mod/README.md`](../mod/README.md).
In-Game-Test: ausstehend (Momo).
