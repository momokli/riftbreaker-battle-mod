# Bausteine — eigenständig testbare Komponenten

Jeder Baustein kapselt **genau eine** Fähigkeit aus dem gemergten Spike/Trainer-Code
(PR #1 + #2) als eigenständig testbares Stück — ohne Feature-Creep. So lässt sich
jede Komponente einzeln validieren, bevor sie in den finalen Mod einfließt.

> Herkunft: Bausteine sind **abgeleitet** aus `mod/` (Spike: Skeleton + Experimente
> A/B/C) und dem Trainer-Harness v0 (C-Quellen kanonisch in
> `bausteine/04-trainer-io/`), nicht neu erfunden. Abweichungen sind nur
> Kürzungen/Isolation, keine neuen API-Aufrufe.

## Index

| Baustein | Was es testet | Status | Wie testen |
|---|---|---|---|
| [00-mod-skeleton](00-mod-skeleton/) | Mod-Load: Ordner-Layout + `*_autoexec.lua` läuft bei Kartenerstellung, `LogService:Log` erreichbar | Code fertig — In-Game-Test offen | Installieren, Karte laden, exor_logs.txt prüfen |
| [01-wave-spawn](01-wave-spawn/) | `EntityService:SpawnEntity` zur Laufzeit via Konsolen-Command `rb_wave <level>` | Code fertig — In-Game-Test offen | Installieren, Karte laden, `rb_wave 1..3`, Wellen + Log prüfen |
| [02-custom-ui](02-custom-ui/) | `GuiService:OpenPopup` + `GuiPopupResultEvent` (Custom-UI-Popup) | Code fertig — In-Game-Test offen | Installieren, Karte laden, `rb_ui`, Popup + Button prüfen |
| [03-log-bridge](03-log-bridge/) | **Diagnose-only, kein Architekturpfad.** Outbound-Pfad Lua → `LogService:Log` → `exor_logs.txt`; Live-Parser `tail_events.py` | Code fertig — In-Game-Test offen | Installieren, Karte laden, `rb_bridge_test`, `tail_events.py` beobachten |
| [04-trainer-io](04-trainer-io/) | Injector + rbbridge-DLL: Named Pipe `\\.\pipe\rbbattle`, `ping`/`exec` | Code fertig — **ohne Spiel testbar** (notepad.exe) | DLL bauen, in notepad.exe injizieren, `pipe_client.py` starten |
| [05-economy-loop](05-economy-loop/) | Economy-Kreis: Punkte verdienen (`EntityKilledEvent`/`HourEvent`-Dual-Mode) + ausgeben (`rb_buy_wave`, Kosten 10/25/50, Spawn aus Baustein 01), Konto in Global-Database (`rb_points`/`rb_status`); Recherche: `docs/research/api-deep-dive.md` | Code fertig — In-Game-Test offen | Installieren, Survival-Karte laden, `rb_points`, `rb_buy_wave 1..3`, Kills, Log prüfen |
| [07-relay](07-relay/) | Relay-Brücke: tailt `exor_logs.txt` ([RBBATTLE]-Zeilen), liefert Events per `POST /event` ein, Poll-Loop dispatcht `exec_command` via Pipe/rbbridge | untested — Unit-Tests grün | `python3 -m unittest test_dispatch test_referee` |
| [08-control-ui](08-control-ui/) | Operator-Cockpit (`cockpit.html`): **manuelles Backend** — konsumiert 04 (`pipe_bridge`: `get_state`/`add_resource`/`activate_mission_flow`/`deactivate_mission_flow`/`probe`) + Plane-B-Agent (`/server/*`, #424) | Code fertig — Unit-Test grün (Panel) | `cd tests/server-control-panel && npm test` |

## Spieltest-Status (muss Momo in-game bestätigen)

- [ ] 00: `[RBBATTLE] skeleton ok` erscheint in `exor_logs.txt`
- [ ] 01: `rb_wave 1` spawnt Brabits, `rb_wave 3` spawnt gemischte Welle
- [ ] 02: `rb_ui` öffnet Popup, OK-Button schließt, Log zeigt `status=closed`
- [ ] 03: `rb_bridge_test` erzeugt parsebare `event=bridge_test`-Zeilen
- [ ] 05: `rb_buy_wave 1..3` zieht Punkte ab und spawnt Wellen; Kills geben Punkte (auto→kill), sonst `hour_tick`-Fallback

## Lokaler Teststatus (Server/Relay — läuft ohne Spiel)

- [x] 07-relay: `python3 -m unittest test_dispatch test_referee` grün (Relay-Dispatch + Referee-Rückkanal)
- [x] 08-control-ui: `cd tests/server-control-panel && npm test` grün (Panel `server control (plane B)`, defensives Contract)

## Konventionen

- **Installation jeder Mod-Bausteins:** Unterordner `<baustein>/rbbattle_XX_*/`
  (Name = Mod-Name) nach `<game>/mods/` kopieren.
- **Log-Pfad:** `<Documents>\The Riftbreaker\exor_logs.txt`, alle Mod-Zeilen mit
  Präfix `[RBBATTLE]` → von `03-log-bridge/tail_events.py` live parsebar.
- **Kein Datei-I/O im Lua-Mod** (`io.*` crasht das Spiel, s. `docs/findings.md`).
- **04-trainer-io ist Windows-only** (x64) und gehört **nie** in einen
  Workshop-Upload — siehe `docs/workshop.md`.
