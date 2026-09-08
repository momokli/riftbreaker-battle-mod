# Findings — verifiziert (Stand 08.09.2026, Spike-Research)

Machbarkeits-Findings für den Runden-Duell-Modus. Basis: exorstudios-Wiki
(github.com/exorstudios/riftbreaker-wiki), fandom-Wiki (riftbreaker.fandom.com,
„Mod service:*“-Signatur-Dumps, „Console commands“), echte Workshop-Mods
(github.com/lilly1987/Riftbreaker-mods), **extrahierte Original-Spieldaten**
(github.com/PonomarevDmitry/RiftbreakersMods → `OriginalPacksData/`) sowie
Matheos Prototyp-Repo (<https://github.com/BestToasty/riftbreaker_mod>, Stand 07.09.2026).

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

Zusätzlich aus Matheos Prototyp (In-Game-Test 07.09.2026, Fortsetzung der Nummerierung):

11. **Mod-Struktur läuft** — `Mods/`-Ordner plus `lua/*_autoexec.lua` wird von der Engine fehlerfrei geladen.

12. **Custom Console Commands funktionieren** — `ConsoleService:RegisterCommand("name", fn)` registriert eigene Kommandos in der In-Game-Konsole.

13. **Spawn zur Laufzeit funktioniert** — `ConsoleService:ExecuteCommand("debug_spawn_entity units/ground/spawner_canoptrix")` spawnt das Boss-Nest an der Spielerposition.

14. **Global-Events funktionieren** — `RegisterGlobalEventHandler("PlayerCreatedEvent", fn)` wird zuverlässig ausgelöst.

15. **Konsolen-Ausgabe funktioniert** — `ConsoleService:Write(text)` gibt Text in der In-Game-Konsole aus.

16. **`io.open` CRASHT das Spiel** — Datei-I/O ist im Lua-Sandbox blockiert und beendet das Spiel hart (nicht nur ein Fehler!). `io.*` im Mod niemals anfassen.

## Entscheidung (08.09.2026): Trainer-only-Architektur

**Der Mod hat keinen eigenen I/O-Kanal** — Ingress und Egress laufen
ausschließlich über die **Trainer-DLL** (die Lua-Sandbox blockiert File-I/O
ohnehin hart, s. o.). Pfad: Server → Relay-Client → Named Pipe
(`\\.\pipe\rbbattle`) → DLL → Mod-Command (Ingress); DLL liest Game-State /
fängt Events ab → Pipe → Relay-Client → Server (Egress). **Konsole-Route
verworfen** — Konsole-Buffer-Injektion ist fragil, UI-Automation/SendInput kein
echtes Ingress (Fokus-Probleme); Log-File-Tailing nur noch Notnagel. Konsequenz:
Mod = reine Spiellogik (Steam-Workshop-tauglich), Trainer-DLL = externes
„für uns“-Tool (runtime-only Injection, keine Game-Datei-Änderung).
Details: `docs/concept.md` → „Tournament-Architektur (Trainer-only)“.
