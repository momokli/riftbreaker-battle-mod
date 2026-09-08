# Bausteine — eigenständig testbare Komponenten

Jeder Baustein kapselt **genau eine** Fähigkeit aus dem gemergten Spike/Trainer-Code
(PR #1 + #2) als eigenständig testbares Stück — ohne Feature-Creep. So lässt sich
jede Komponente einzeln validieren, bevor sie in den finalen Mod einfließt.

> Herkunft: Bausteine sind **abgeleitet** aus `mod/` (Spike: Skeleton + Experimente
> A/B/C) und `trainer/` (Harness v0), nicht neu erfunden. Abweichungen sind nur
> Kürzungen/Isolation, keine neuen API-Aufrufe.

## Index

| Baustein | Was es testet | Status | Wie testen |
|---|---|---|---|
| [00-mod-skeleton](00-mod-skeleton/) | Mod-Load: Ordner-Layout + `*_autoexec.lua` läuft bei Kartenerstellung, `LogService:Log` erreichbar | Code fertig — In-Game-Test offen | Installieren, Karte laden, exor_logs.txt prüfen |
| [01-wave-spawn](01-wave-spawn/) | `EntityService:SpawnEntity` zur Laufzeit via Konsolen-Command `rb_wave <level>` | Code fertig — In-Game-Test offen | Installieren, Karte laden, `rb_wave 1..3`, Wellen + Log prüfen |
| [02-custom-ui](02-custom-ui/) | `GuiService:OpenPopup` + `GuiPopupResultEvent` (Custom-UI-Popup) | Code fertig — In-Game-Test offen | Installieren, Karte laden, `rb_ui`, Popup + Button prüfen |
| [03-log-bridge](03-log-bridge/) | Outbound-Pfad Lua → `LogService:Log` → `exor_logs.txt`; Live-Parser `tail_events.py` | Code fertig — In-Game-Test offen | Installieren, Karte laden, `rb_bridge_test`, `tail_events.py` beobachten |
| [04-trainer-io](04-trainer-io/) | Injector + rbbridge-DLL: Named Pipe `\\.\pipe\rbbattle`, `ping`/`exec` | Code fertig — **ohne Spiel testbar** (notepad.exe) | DLL bauen, in notepad.exe injizieren, `pipe_client.py` starten |

## Spieltest-Status (muss Momo in-game bestätigen)

- [ ] 00: `[RBBATTLE] skeleton ok` erscheint in `exor_logs.txt`
- [ ] 01: `rb_wave 1` spawnt Brabits, `rb_wave 3` spawnt gemischte Welle
- [ ] 02: `rb_ui` öffnet Popup, OK-Button schließt, Log zeigt `status=closed`
- [ ] 03: `rb_bridge_test` erzeugt parsebare `event=bridge_test`-Zeilen

## Konventionen

- **Installation jeder Mod-Bausteins:** Unterordner `<baustein>/rbbattle_XX_*/`
  (Name = Mod-Name) nach `<game>/mods/` kopieren — Layout und Details siehe
  `mod/README.md` bzw. README des Bausteins.
- **Log-Pfad:** `<Documents>\The Riftbreaker\exor_logs.txt`, alle Mod-Zeilen mit
  Präfix `[RBBATTLE]` → von `03-log-bridge/tail_events.py` live parsebar.
- **Kein Datei-I/O im Lua-Mod** (`io.*` crasht das Spiel, s. `docs/findings.md`).
- **04-trainer-io ist Windows-only** (x64) und gehört **nie** in einen
  Workshop-Upload — siehe `docs/workshop.md`.
