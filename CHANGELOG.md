Version: [Unreleased]
Date: —

  Security:
    - Tournament-API fail-closed abgesichert (Loopback-Bind + Caddy basic_auth + Rust-Bearer 401); Secret `vault_tournament_token` als Single-Source fuer Rust, Caddy und Queue (#298, PR #1023).

  Features:
    - Queue-Dienst (Casual, #998): eigener Sidecar `deploy/queue/` paart Spieler (FIFO, **aktiv nur 1v1**, kein MMR) und provisioniert bei einer Paarung **kalt** zwei frische VS-Welten (A/B) — je Paarung zwei neue Instanzen mit **verschiedenen** GNS-Endpoints, kein Warm-Pool. Reiner Kern `queue_core.py` (Enqueue/Leave/Pairing/Welt-Zuordnung/Match-Record + Persistenz), `queue_flow.py` (`QueueCoordinator`: kalte Doppel-Provisionierung, Referee-`/lobby` A+B, Rollback bei Fehler, kaltes Cleanup, idempotentes `finish`), HTTP-Dienst `queue_service.py` (`/queue/join|leave|status`, `/health`, Bearer fail-closed, `--check`, SIGTERM) und Ansible-Rolle `deploy/roles/queue/`. Provisioner erweitert um einen expliziten `world`-Parameter (seedet `RBB_VS_WORLD` + `RBB_REFEREE_URL`, self-send aus; `parse_mode` unangetastet). Relay `POST /queue` (+`/queue/leave`, `GET /queue/status`) proxyt an den Dienst und pinnt **beide** Teilnehmer; `/sessions` additiv um `queuePhase`/`queuePosition`/`matchId`/`vsWorld`. E2E-Harness `deploy/queue/e2e_998_queue.py` belegt die Abnahme (zwei Spieler → ein Match A/B).
    - Referee als VS-Gehirn (Cross-World-Sends + per-Welt-HQ-Sieg, #996): Welt-getaggte Feed-Events (`LogEntry.world`, US1), wellen-basierter Send mit `level` (`POST /send {world,level,value}`, `SendBatch.level`, US2), C-Bridge-Ingress `POST /incoming_send` (Ziel-Event `incoming_wave`, US3), Ingress-Push an die Ziel-Bridge beim `wave_start` inkl. `ingress`-Block (US4/G5), Attack-Cycle-`send_enemy` als echter Referee-Egress via `RBB_REFEREE_URL`/`RBB_VS_WORLD` (US5), per-Welt-HQ-Reporter im `match-loop` → Referee (`hq_hp`/`hq_dead`, US6/G6). Ohne `RBB_REFEREE_URL` bleibt das SOLO-Verhalten bitgleich.
    - Pause-Fan-out im Referee (#997): `POST /pause` / `POST /resume` fächert `POST <bridge>/pause_dom` bzw. `/resume_dom` an **beide** Welten (`cfg.bridge_for(w)`, analog GO-Broadcast); neuer match-weiter Zustand `MatchState.paused` + `teams.<W>.pause_broadcast` additiv in `GET /state`; Idempotenz (`already`) + `{"retry":true}`, 409 außerhalb Phase `running`, Partial-Fehler je Welt = HTTP 200 mit `ok:false` (kein 5xx). Kein C-/Bridge-/DLL-/Cockpit-/Attack-Cycle-Eingriff.

  Docs:
    - VS-Konzept §6.3/§6.4 + Gap-Liste G5/G6 auf „umgesetzt" gezogen; `docs/TOURNAMENT_API.md` um die `level`-Sendform + den `wave_start`-`ingress`-Block ergänzt; `server/protocol.md` um den Ingress-Endpoint `/incoming_send`; `deploy/env-schema.yml` um `RBB_REFEREE_URL`/`RBB_VS_WORLD`/`RBB_INCOMING_DELAY_S` (#996).
    - `docs/TOURNAMENT_API.md` um `POST /pause`/`POST /resume` (Request/Response/Bridge-Payload) + `/state.paused`/`teams.<W>.pause_broadcast`; `docs/VS_MATCH.md` §6.5/§7 (Ready/Start „umgesetzt", Pause-Fan-out „umgesetzt (#997)", G7 ✅ mit Fußnote „Sidecar-Mitpause offen #553"); `docs/MATCH_VIEW.md` Referee-Route `POST /pause`/`/resume` (#997).
    - `docs/LOBBY.md` §1/§2/§3/§5 um Queue-Dienst, `POST /queue` (+leave/status), `queuePhase`-Felder und „#998 done"; `docs/VS_MATCH.md` §6.7 + Phasenplan (#998); `deploy/queue/README.md` (#998).

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
