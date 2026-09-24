Version: 1.0.2
Date: 24. 09. 2026

  Features:
    - Parked Solo — vorgewaermter Solo-Server steht fuer den naechsten Spieler bereit (Warm-Pool + Cold-Boot-Messung) (#909).
    - Parked VS — vorgewaermter 1v1-Server mit Zwei-Spieler-Ready-Gate (#910).
    - Provisioner — Dedi-Instanz on-demand starten/stoppen, idempotent mit Rollback und Health-Wait (#908).
    - Nativer Welt-Pause/Resume ueber `GameplayState` (Game-Thread-Marshalling) + Cockpit (#880); Live-Nachweis "kein Weltfortschritt im geparkten Zustand" auf Staging gefuehrt (#919).
    - Multi-Session am GNS-Entry-Relay — mehrere parallele Clients gleichzeitig (#877).
    - Relay-PoC: Spieler halten und per Web-UI auf PROD/DEV/STAGING schicken (#857).
    - Suffix-Routing im GNS-Entry-Relay (`-dev`/`-staging`) (#843).
    - Single-Entry: DNAT-Relays (`satellite`/`sync`) abgebaut, `rift.projectmellon.de` auf planet (#846).
    - Multi-Instanz-Hosting auf einer Box — zwei Dedicated-Server gleichzeitig erreichbar (#290).
    - Natives `pause_dom` (#402), `end_game` (#403) und `resume_dom` (#404) als C++-Kommando statt `exec`.

  Bugfixes:
    - Dedicated-Server-Crash ~3-4 s nach erstem schweren Bridge-Call (`get_state`/`activate`/`deactivate`) behoben — Ursache war der rohe `q[i]`-Deref in `resolve_dom_node`; jetzt crash-sicher per `ReadProcessMemory`, auf Staging unter 1-Hz-`get_state` mit 0 Crashes verifiziert (#436, PR #924).
    - Boot: Server auf `:6321` antwortet wieder — Blockade auf Console-stdin (`cli=1`, kein TTY) geloest (#239).
    - planet laeuft nicht mehr auf 91% Platte ohne Auto-Cleanup — Disk-Aufraeumen aktiv (#301).

  CI:
    - Leanere CI — dependency-freie Node-Tests ohne `npm ci` + ccache-Stats (#306).
    - Backup-/Stray-Retention + Hygiene-Doku (#312, #313).

  Intern:
    - Epic 1.0.2 "Server Control Basics & Hardening" abgeschlossen (#911).
    - Game-Flow-Zustandsmaschine PAUSED -> WARMUP -> RUNNING -> GAME_OVER als Zielmodell spezifiziert (#823).
    - Tote `dom_paused`/`game_paused`-Felder aus `get_state` entfernt; Cockpit-Pause-UI auf pause/resume reduziert (#927).

Version: 1.0.1
Date: 23. 09. 2026

  Features:
    - Session-Trace — die DLL loggt pro Request `cmd` + `session`; die Bridge haengt die Session-ID zentral an jeden an die DLL gesendeten Command (#392).

  Bugfixes:
    - `get_state`-Crash behoben (DEP, jump-to-garbage in `dbg`->`fprintf`): stderr wird jetzt per Handle-Write ohne writable-`.data`-CRT-Zeiger geschrieben (#688).
    - `/health` meldet Pipe-Ausfall ehrlich (Readiness aus der persistenten Pipe statt Zweit-Connect) und der Pipe-Server heilt sich selbst (#902).

  CI:
    - boot-test pollt den Boot-Health statt hartcodiertem `sleep 30` (#893).
    - shellcheck als gepinntes Static-Binary statt apt-Installation (#894).
    - `build`-Job laeuft parallel zu `test` statt `needs: [changes, test]` (#896).
    - Required-Check deploy-check-local wieder gruen (deploy-wiring-Vertrag statt entfernter Park-Verdrahtung) (#337).
    - Path-Filter-Gate abgeschlossen: DLL-Änderung -> Boot-Test, Mod/WebUI -> leicht (#393).

  Intern:
    - Epic 1.0.1 "Solid & schnell" abgeschlossen — CI wieder verlaesslich gruen und deutlich kuerzer, Deploy-Gates luegen nicht mehr (#724).
