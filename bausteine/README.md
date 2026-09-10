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
| [03-log-bridge](03-log-bridge/) | **Diagnose-only, kein Architekturpfad.** Outbound-Pfad Lua → `LogService:Log` → `exor_logs.txt`; Live-Parser `tail_events.py` | Code fertig — In-Game-Test offen | Installieren, Karte laden, `rb_bridge_test`, `tail_events.py` beobachten |
| [04-trainer-io](04-trainer-io/) | Injector + rbbridge-DLL: Named Pipe `\\.\pipe\rbbattle`, `ping`/`exec` | Code fertig — **ohne Spiel testbar** (notepad.exe) | DLL bauen, in notepad.exe injizieren, `pipe_client.py` starten |
| [05-economy-loop](05-economy-loop/) | Economy-Kreis: Punkte verdienen (`EntityKilledEvent`/`HourEvent`-Dual-Mode) + ausgeben (`rb_buy_wave`, Kosten 10/25/50, Spawn aus Baustein 01), Konto in Global-Database (`rb_points`/`rb_status`); Recherche: `docs/research/api-deep-dive.md` | Code fertig — In-Game-Test offen | Installieren, Survival-Karte laden, `rb_points`, `rb_buy_wave 1..3`, Kills, Log prüfen |
| [06-tournament-server](06-tournament-server/) | Zentraler Tournament-Server (node:http, In-Memory) + Mock-Client: Register/Match, Event-Routing per Outbox/Polling, Runden-Lifecycle (round_end/match_end), Scoreboard | Code fertig — **ohne Spiel testbar** (nur Node.js) | `bash test_e2e.sh` (2 Mock-Clients, 2 Runden) |
| [07-relay](07-relay/) | Relay-Brücke: tailt `exor_logs.txt` ([RBBATTLE]-Zeilen), liefert Events per `POST /event` beim 06-Server ein (In-Memory-Queue, Backoff-Retry), Poll-Loop dispatcht `exec_command` per `dispatch_exec` auf die Named Pipe (`{"cmd":"exec",...}`, Backoff-Retry bei nicht erreichbarer Pipe, Issue #60) | untested — Prototyp-Harness (inkl. Pipe-Fake) lokal grün, In-Game-Test offen | `bash 07-relay/test_e2e_prototype.sh` (Server + Relay + Fake-Log + FIFO-Pipe-Fake, 27 Assertions), `python3 -m unittest test_dispatch_pipe` |
| [08-web-ui](06-tournament-server/web/) | Control-Dashboard im Terminal-Stil (vom 06-Server aus `web/` statisch serviert): PLAYERS + EVENT FEED (live via SSE `/stream`) + CONTROLS (`rb_wave`/`rb_points`/`rb_buy_wave` … als `exec_command`), localStorage, vanilla JS | untested — Prototyp-Harness lokal grün, In-Game-Test offen | 06-Server starten → `http://localhost:8080` öffnen, `bash 07-relay/test_e2e_prototype.sh` |

## Spieltest-Status (muss Momo in-game bestätigen)

- [ ] 00: `[RBBATTLE] skeleton ok` erscheint in `exor_logs.txt`
- [ ] 01: `rb_wave 1` spawnt Brabits, `rb_wave 3` spawnt gemischte Welle
- [ ] 02: `rb_ui` öffnet Popup, OK-Button schließt, Log zeigt `status=closed`
- [ ] 03: `rb_bridge_test` erzeugt parsebare `event=bridge_test`-Zeilen
- [ ] 05: `rb_buy_wave 1..3` zieht Punkte ab und spawnt Wellen; Kills geben Punkte (auto→kill), sonst `hour_tick`-Fallback

## Lokaler Teststatus (Server/Relay/UI — läuft ohne Spiel)

- [x] 06: `bash test_e2e.sh` grün (2 Runden, Routing, Scoreboard, Error-Kontrakt) — node v24
- [x] 07+08-Prototyp: `bash 07-relay/test_e2e_prototype.sh` grün (27 Assertions: Strecke Spiel-Log → Relay → Server → SSE-Stream/Web-UI → `exec_command` → `dispatch_exec` auf Named-Pipe-Ersatz/FIFO, Issue #60)

## Konventionen

- **Installation jeder Mod-Bausteins:** Unterordner `<baustein>/rbbattle_XX_*/`
  (Name = Mod-Name) nach `<game>/mods/` kopieren — Layout und Details siehe
  `mod/README.md` bzw. README des Bausteins.
- **Log-Pfad:** `<Documents>\The Riftbreaker\exor_logs.txt`, alle Mod-Zeilen mit
  Präfix `[RBBATTLE]` → von `03-log-bridge/tail_events.py` live parsebar.
- **Kein Datei-I/O im Lua-Mod** (`io.*` crasht das Spiel, s. `docs/findings.md`).
- **04-trainer-io ist Windows-only** (x64) und gehört **nie** in einen
  Workshop-Upload — siehe `docs/workshop.md`.
