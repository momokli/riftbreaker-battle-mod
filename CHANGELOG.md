Version: [Unreleased]
Date: —

  Features:
    - GO-Executor der VS-Welten: `broadcast_go` fächert je Welt die verifizierten Bridge-Routen `POST <bridge-base>/resume_game` → `POST <bridge-base>/start` (leerer Body) statt des toten `/exec`-Command-Pfads; `TOURNAMENT_GO_COMMANDS`/`debug_dom_resume` entfernt (#1027).
    - Ready→GO aus der Lobby: Queue postet je Welt `/ready` an den Referee; Relay-`POST /ready` ist kontextabhängig (Solo→Capsule, VS→Referee), zweiter Ready löst AUTO_GO/Broadcast an beide Bridges aus (#1025, PR #1052).
    - Referee-Bruecke im GNS-Relay: Lobby liest den Match-Zustand (`GET /referee/state`, Phase/Spieler/Welt/Sieger) und meldet Welten ready (`POST /referee/ready`); neue Lobby-UI (Phase-/Sieger-Badge + `READY (Referee)`-Button), fail-safe `503 referee_unconfigured` ohne Config (#1024, PR #1050).

  Bugfixes:
    - Provisioner: wiederholter `start()` re-assertiert die vier Sidecars (laufende unangetastet, gestoppte per `docker start`, fehlende aus der Container-Env `RIFTBREAKER_MODE`/`RBB_VS_WORLD` rekonstruiert, fail-loud) — kein stiller Halb-Stack nach Sidecar-Crash/-Remove; kein zweiter Container (#1026).

  Refactor:
    - Deployment-Cleanup: prod-only CD. `deploy/deploy-prod.yml` ist das einzige Playbook (Play 0 host-services + Prod-A + Prod-B + Relay-Teardown); dev (site.yml) und staging (deploy-staging.yml/staging-vars.yml) entfallen. CD nur noch bei Tag-Push `v*`; `deploy-ssh.sh`/`deploy-wrapper.sh` lehnen jeden anderen ref fail loud ab (#1034).
    - Host-Singleton-Dienste (gns-relay, image-retention, host-hygiene, parked-pool, capsule-flow, queue) laufen jetzt im prod-Play mit; parked/capsule/queue mit env-freien Unit-/Pfadnamen. GNS-Relay routet nur noch A/B/Default (#1034).
    - Neues manuelles, idempotentes Teardown-Playbook `deploy/teardown-dev-staging.yml` (NICHT im CD verdrahtet) (#1034).

  Docs:
    - `docs/VS_MATCH.md` §6.5/§9 + `docs/TOURNAMENT_API.md`: GO-Fan-out auf die verifizierten Bridge-Routen `resume_game`+`start` dokumentiert, `/exec`-Legacy-Env (`TOURNAMENT_GO_COMMANDS`) gestrichen (#1027).
    - `docs/STAGING.md` als retired/Archiv gekennzeichnet; `deploy/README.md` auf prod-only-Topologie/Trigger/Rollen/CD/Teardown aktualisiert (#1034).
    - `docs/VS_MATCH.md` §6.5/§6.7 + `deploy/provisioner/README.md`: kalte Provisionierung startet Container + vier Sidecars, Welt bootet PAUSED/joinbar, „Resume" = Ready-Handover (kein Pre-GO-`resume_game`) + Idempotenz-Garantie (#1026).

  Security:
    - Tournament-API fail-closed abgesichert (Loopback-Bind + Caddy basic_auth + Rust-Bearer 401); Secret `vault_tournament_token` als Single-Source fuer Rust, Caddy und Queue (#298, PR #1023).

Version: 1.0.15
Date: 29. 09. 2026

  Features:
    - Queue-Dienst (Casual): eigener Sidecar `deploy/queue/` paart Spieler FIFO (aktiv nur 1v1, kein MMR) und provisioniert je Paarung zwei frische VS-Welten (A/B) kalt, mit verschiedenen GNS-Endpoints; Relay `POST /queue` (+`/queue/leave`, `GET /queue/status`), `/sessions` additiv um `queuePhase`/`queuePosition`/`matchId`/`vsWorld` (#998, PR #1017).
    - Match-Records: Ergebnis + Teilnehmer persistieren (ranking-faehig, kein MMR) und `GET /matches/{id}` (#999, PR #1019).
    - Lobby: Queue-Button + Status — join/leave ueber den Proxy, Live-Fortschritt im Lobby-Screen (#1000, PR #1020).

  Bugfixes:
    - boot-test: Test-Ports disjunkt (Tournament/Bridge/Attack-Cycle) + Regressionsguard (#988, PR #1018).

  Docs:
    - Matchmaking-Design festgehalten: Queue casual jetzt, Daten ranking-faehig, generisch (N/Team), aktiv nur 1v1, kalt provisioniert (#943).
    - Lobby-/VS-Doku um Queue-Dienst, `POST /queue` und Flow aktualisiert (#998).

Version: 1.0.14
Date: 29. 09. 2026

  Features:
    - Zweite Welt (B): zweiter prod-Server + Multi-Session-GNS-Routing fuer A/B (#995, PR #1007).
    - Referee als VS-Gehirn: Cross-World-Sends + per-Welt-HQ-Sieg (#996, PR #1013).
    - VS-Flow: gemeinsamer Start/Ready + Pause-Fan-out im Referee (#997, PR #1014).

  Bugfixes:
    - capsule-dev: `identity.py` wird jetzt mit ausgerollt (Import-Fehler beim Start behoben) (#1011, PR #1012).

  Docs:
    - Player-Lobby-API-Liste: Relay + Backends, Exposition, Zustandsmodell (#1009, PR #1010).
    - VS-Design festgehalten: Welt A/B, Referee, Start-Symmetrie + HQ-Sieg (#942).

Version: 1.0.13
Date: 29. 09. 2026

  Features:
    - Player-Identitaet abstrahiert (`rbident`: kind + canonical, account-ready): der Inline-`str:`/`steamid:`-Prefix-Check ist durch `isIdentityLike` ersetzt, das Identitaets-`kind` durch Session/SessionRecord/SessionInfo + `/sessions`-JSON gefaedelt; Python-Spiegel `deploy/capsule/identity.py` + Tests (#992, PR #1001).
    - Provisioner-Modus-Parameter `solo_self | solo_persona:<name>` im Schema (#993, PR #1002).
    - Lobby Main-Screen: Modi-Kacheln, mode-Plumbing, Provision-Trigger und Status-Badge (#994, PR #1003).

  Docs:
    - Match-View-Konzept: UI + Struktur/Architektur (1v1/Solo) (#873, PR #874).
    - VS-Welt-A/B-Konzept: Routing + Infra + Game (#875, PR #876).

Version: 1.0.12
Date: 29. 09. 2026

  Features:
    - Chat-Announcer-Sidecar (#940, PR #987): liest den Attack-Cycle-State (`GET /status`) und schickt ereignis-/schwellenbasiert Status-Zeilen in den In-Game-Chat (Bridge `POST /send_chat`) — Warmup-Schwellen (`3:00`…`0:10`) + `GO`, `next attack in 60s/30s/10s`, `incoming [W1]x3 [W4]x1` beim Feuern, Rundenende-Ergebnis; genau einmal pro Schwelle/Epoche, kein 1-Hz-Spam. Format an einer Stelle gekapselt (`style=short` Default).

  Docs:
    - Chat-Format-Messbericht (Spike #939, PR #989): Monospace/Wrap/Encoding gemessen — Grundlage fuer das Announcer-Format.

Version: 1.0.6
Date: 29. 09. 2026

  Features:
    - /ready im Chat: Das Spiel startet erst, wenn ALLE verbundenen Spieler `/ready` getippt haben (PAUSED/WARMUP -> RUNNING); der Status zeigt die fehlenden Spieler, Timeout (Default 180 s) faellt ohne Kick nach PAUSED zurueck (#937, PR #984).

Version: 1.0.5
Date: 29. 09. 2026

  Features:
    - Solo-Spiel: mehrere Clients auf derselben Instanz — `POST /solo {instance}` joint einer bestehenden Solo-Instanz (kein neuer Claim), `--max-players` (Default 4) begrenzt die Aufnahme (`409 instance_full`/`unknown_instance`), `/sessions` zeigt `soloMembers`/`soloMemberCount`/`soloMaxPlayers`, Lobby-Karte mit Join-Button (#936, PR #961).

Version: 1.0.11
Date: 28. 09. 2026

  Features:
    - Parked-/Provisioned-Instanz-Haertung: `RBB_ENV`/`RBB_REF` im Container, Log-Rotation-Limits, restart-Policy und `Locale=C.UTF-8` (#968, PR #978).
    - Parked-Instanz-Identitaet: eigene `config.cfg`/Spielname (Server-Name-Suffix) + `sessions`-Mount (#970, PR #980).

  Bugfixes:
    - Self-Send auf Parked-/Provisioner-Instanz: Chat-Nachricht postet, Carbonium wird jetzt korrekt abgezogen (#966, PR #977).
    - Parked-Pool: Orphan-Leak durch In-Memory-State behoben — Reconciliation beim Start gegen persistierten Pool-State (#969, PR #979).
    - Attack-Cycle-Host-Port je Env eindeutig (prod 9103, staging 9104) — Prod-Deploy-Kollision behoben (#976, PR #975).

  Docs:
    - Parity-Regel dokumentiert: Parked/Provisioned = voller Stack (dedicated + send-tailer + attack-cycle + match-loop + session-recorder) (#974, PR #971).

Version: 1.0.4
Date: 28. 09. 2026

  Features:
    - Server→Spieler-Chat nativ: `POST /send_chat` bzw. Pipe-Cmd `send_chat` broadcastet eine Chat-Nachricht an alle Spieler (Vanilla-Chat), `type` 2/4/8 (system/announcement/message); Wire-Event `send_chat_result`. Text wird JSON-escaped (framing-sicher) (#934, PR #944).
    - Cockpit-Chat-Panel: Verlauf der `player_chat`-Events (ueber den bestehenden `/events`-SSE-Kanal, kein zweiter EventSource) + Eingabezeile zum Senden via `POST /send_chat`; Absender-Umschalter (`system` = ohne Prefix, sonst `[Absender] `), Formatier-Toolbar, XSS-sicher (`textContent`) (#935, PR #945).

  CI:
    - boot-test: Attack-Cycle-Host-Port je Lauf eindeutig aufloesen — die feste Bindung `127.0.0.1:9102` kollidierte mit dem Dev-/Test-Stack und blockierte als Required-Check alle PR-Merges (#967, PR #972).

Version: 1.0.3
Date: 25. 09. 2026

  Features:
    - Spielkapsel-Flow: `[ solo | self-send on ]` -> provisioniertes, pausiertes Solo-Spiel -> `ready` -> Runde laeuft -> Ergebnis sichtbar -> Server zurueck in den Pool (#931).
    - Parked-Pool-Dienst: Solo-Warmserver warm halten, claimen, recyceln, reap (#928).
    - GNS-Entry-Relay: Backends zur Laufzeit registrieren/abmelden (`POST`/`DELETE /backends`) und Spieler per `POST /solo` automatisch einer geparkten Solo-Instanz zuweisen (#929).
    - GNS-Entry-Relay-Lobby: Solo-Button `[ solo | self-send on ]` — claimt eine geparkte Instanz und schickt den Spieler in einem Schritt hin (`POST /solo` mit additivem `self_send`, Default true); `/sessions` zeigt additiv `soloPhase`/`soloInstance`/`soloEndpoint`, auch fuer geclaimte Identitaeten ohne verbundenen Client (#930).
    - Solo-Spiel: mehrere Clients auf derselben Instanz — `POST /solo {instance}` joint einer bestehenden Solo-Instanz (kein neuer Claim), `--max-players` (Default 4) begrenzt die Aufnahme (`409 instance_full`/`unknown_instance`), `/sessions` zeigt `soloMembers`/`soloMemberCount`/`soloMaxPlayers`, Lobby-Karte mit Join-Button (#936).

  Bugfixes:
    - Provisioner Live-Pfad: Container-Port auf 9001 korrigiert + reale Deploy-Mounts -> Health-Timeout gegen das reale Image behoben (#918).

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