# Queue-Dienst (`deploy/queue/`) — Casual-Pairing 1v1 (Issue #998)

Der Queue-Dienst paart Spieler (casual, **aktiv nur 1v1**, FIFO) und setzt bei
einer Paarung **kalt** zwei frische VS-Welten auf: Welt **A** und Welt **B**
werden provisioniert, beide Spieler im Referee registriert und der Match-Record
gefuehrt. **Kein Warm-Pool** (konsistent mit 1.0.14), **kein MMR/Ranking**.

Er ist ein eigener Sidecar (Muster `deploy/capsule/`) und spricht Provisioner +
Referee ausschliesslich per HTTP an — bewusst kein Docker-/Spiel-Code hier.

```
join(a) -> queued (position=1)
join(b) -> matched m1: A<->Welt A, B<->Welt B
         -> zwei KALT-Provisionierungen (verschiedene Instanzen/Endpoints)
         -> Referee POST /lobby {player, world, match_id} je Spieler
         -> Match-Record {participants:[{identitaet,world,instance,endpoint}], …}
finish(m1, result?) -> Ergebnis nachtragen + KALTES Cleanup (beide Instanzen stoppen)
reconcile() -> liest Referee GET /state; phase=finished -> finish(match_id, abgeleitetes result)
rematch(m1) -> Alt-Stop -> Referee POST /rematch (Reset) -> zwei FRISCHE Welten
               derselben Paarung (neue match_id m2, rematch_of=m1); idempotent
```

## Phasen / Modelle

`queue_core.py` haelt das generische Modell (N Slots je Team,
`teams_per_match=2`), aktiv `team_size == 1`. Ein Match durchlaeuft
`provisioning -> ready -> finished` (Fehler -> `failed`). `finish` ist
**idempotent**: der zweite Aufruf stoppt nicht erneut und aendert das Ergebnis
nicht.

**Auto-Finish (Pull, #1028):** Ein Reconciler-Takt (`QUEUE_RECONCILE_INTERVAL_S`,
Default `5`s, `0` = aus) liest den autoritativen Referee-Zustand ueber
`GET /state` (auth-frei) und ruft bei `phase=finished` `finish(match_id, result)`
selbst auf. Die Zuordnung laeuft ueber das `match_id`-Echo, das die Queue bei
`POST /lobby` mitschickt und der Referee als `teams.<W>.match_id` ausgibt
(defensiver Fallback: gleiche Spielernamen). Ergebnis: `winner=A -> winnerA`,
`winner=B -> winnerB`, sonst `draw`. Ist der Referee nicht erreichbar, passiert
nichts (nur Log, kein Zustandsverlust); scheitert das Cleanup, bleibt das
Ergebnis gesetzt und der naechste Takt wiederholt nur den Stop (US3).

| Begriff | Bedeutung |
|---|---|
| `QueueEntry` | wartender Spieler (FIFO-Slot, `seq`) |
| `Team` | Team-Index + Welt (`A`/`B`) + Spieler |
| `Match` | `match_id` (monoton), `state`, `result?`, `rematch_of?`, `teams`, `participants` |
| Welt-Zuordnung | Team 0 -> `A`, Team 1 -> `B` |
| Ergebnis | `winnerA` / `winnerB` / `draw` (kein MMR) |
| `rematch_of` | `match_id` des Quell-Matches eines Rematches (`null` bei regulaerem Match) |

## Kalte Provisionierung (US2)

Je Paarung werden **zwei** Instanzen gestartet (`queue-<match_id>-a` /
`queue-<match_id>-b`), beide mit **verschiedenen** GNS-UDP-Endpoints. Der
Provisioner wird mit dem expliziten `world`-Parameter aufgerufen (`A`/`B`); das
seedet `RBB_VS_WORLD` + `RBB_REFEREE_URL` je Instanz und schaltet self-send aus
— `parse_mode` bleibt unangetastet (eigene Achse, Issue #998).

**Erwartete Provisioner-Antwort** (`start`): ein JSON-Objekt; der GNS-UDP-Endpoint
wird defensiv gelesen — bevorzugt `{"ports": {"gns": "<ip>:<port>"}}`, sonst
`gns_endpoint` bzw. `endpoint` (jeweils String). Fehlt ein brauchbares Feld,
bleibt der `endpoint` im Match-Record `null` (kein harter Fehler, Welt laeuft
trotzdem). Rollback/`stop` nutzt nur die `instance_id`.

**Rollback:** scheitert die zweite Provisionierung oder die Referee-Lobby,
werden die bereits gestarteten Instanzen **kalt gestoppt** und das Match als
`failed` markiert — kein halber Zustand (lauter Fehler).

## Persistenz (US5)

Der Match-Record liegt als JSON unter `QUEUE_STATE_DIR` (atomar geschrieben)
und uebersteht einen Dienst-Restart (auch die wartende Queue).

## API des Dienstes (`queue_service.py`)

Bind `127.0.0.1:$QUEUE_PORT` (Default **9221**). Bearer-Token PFLICHT, wenn
`QUEUE_TOKEN` gesetzt. Fehlerformat einheitlich
`{"ok":false,"reason":…,"detail":…}`; `401`/`400`/`404`/`405`/`409`/`503`.

| Methode | Pfad | Body | Semantik |
|---|---|---|---|
| `GET` | `/health` | — | Liveness: `200 {"ok":true,"env":…}` |
| `GET` | `/queue/status` | — | Queue + Matches (Snapshot) |
| `POST` | `/queue/join` | `{"identitaet","mode":"vs"}` | wartend -> `{status:"queued",position}`; gepaart -> `{status:"matched",match:{…,assignments:[{identitaet,world,instance,target}]}}` |
| `POST` | `/queue/leave` | `{"identitaet"}` | wartenden Spieler entfernen (bereits gepaart -> `409`) |
| `POST` | `/queue/finish` | `{"match_id","result"?}` | Ergebnis + kaltes Cleanup (idempotent) |
| `POST` | `/queue/rematch` | `{"match_id"}` **oder** `{"identitaet"}` | Rematch derselben Paarung: Alt-Stop -> Referee-Reset -> zwei frische Kalt-Welten (`rematch_of`, `idempotent`) |

```bash
curl -s 127.0.0.1:9221/health
curl -s -X POST 127.0.0.1:9221/queue/join -d '{"identitaet":"str:<A>","mode":"vs"}'   # -> position
curl -s -X POST 127.0.0.1:9221/queue/join -d '{"identitaet":"str:<B>","mode":"vs"}'   # -> matched A/B
curl -s 127.0.0.1:9221/queue/status
curl -s -X POST 127.0.0.1:9221/queue/finish -d '{"match_id":1,"result":"winnerA"}'
curl -s -X POST 127.0.0.1:9221/queue/rematch -d '{"match_id":1}'        # -> rematch_of=1, neues Match 2
curl -s -X POST 127.0.0.1:9221/queue/rematch -d '{"identitaet":"str:<A>"}'  # Komfort-Pfad
```

**Fehler-Mapping:** `mode != "vs"` -> `400 bad_mode`; fehlende `identitaet` ->
`400 bad_request`; `team_size > 1` (ohne `QUEUE_ALLOW_TEAMS`) ->
`400 unsupported_team_size`; unbekannter Match / bereits gepaart -> `409`;
Provisioner/Referee nicht erreichbar -> `503 provision_failed`; ohne/mit
falschem Bearer -> `401 unauthorized`.

### Lobby-Rematch (`POST /queue/rematch`, Issue #1030)

Startet nach einem **beendeten** Match ein neues Match derselben Paarung
(gleiche zwei Spieler, gleiche A/B-Weltzuordnung) mit **zwei frischen
(kalten)** Welten. Reihenfolge (Anti-Zombie, Plan §4):

1. Quelle aufloesen (`match_id` **oder** letztes Match der `identitaet`);
   `state == finished`, sonst `409 match_not_finished`.
2. Idempotenz: liegt `_rematch_of[alte_id]` vor -> sofortiges Ergebnis,
   `idempotent:true`, **kein** Start.
3. `finish(alte_id)` — garantiertes, idempotentes Kalt-Stop beider Alt-Welten
   **vor** jedem Neustart. Fehler -> `503 cleanup_failed`, **kein** neuer Match.
4. Neuen Record anlegen (`rematch_of=alte_id`, neue monotone `match_id`).
5. Referee `POST /rematch` (Reset -> Lobby). `409` -> Record `failed`, **keine**
   Instanz gestartet -> `409 referee_rematch_failed`.
6. Zwei **frische** Kalt-Welten (`queue-<neu>-a/b`) + Referee `POST /lobby`
   (neue `match_id`) + `POST /ready`. Fehler -> Rollback (Stop) + `failed`,
   `503 provision_failed`.
7. `_rematch_of[alte_id] = neue_id` **erst nach** Erfolg setzen + persistieren.

Antwort `200`: `{ok:true, rematch_of:<alt>, idempotent:<bool>, match:{…}}`.
Body ohne/mit ungueltigem `match_id`/`identitaet` -> `400 bad_request`; beide
oder keine Angabe -> `400`; unbekannte `match_id` -> `409 unknown_match`;
Spieler ohne Match -> `409 no_match`. `_rematch_of` wird in
`queue-state.json` persistiert (Idempotenz ueberlebt einen Dienst-Restart).

**Referee-Seite:** `POST /rematch` ist ein reiner Reset in die Lobby; er leert
zusätzlich das Queue-`match_id`-Echo (`teams.<W>.match_id = null`), damit der
Reconciler zwischen Reset und neuem `/lobby` keine Alt-Welten zuordnet.

### #183-Lifecycle-Defaults (bewusst konservativ)

Issue #183 (Match-Lifecycle) ist **offen**; dieser PR legt konservative,
benannte Defaults fest (revidierbar):

| #183-Frage | Default #1030 |
|---|---|
| Spar-Pool reset? | **Ja** — Referee-Reset leert `resources`/HP; Queue startet frische kalte Welten (kein Warm-Reuse). |
| Seed/Settings identisch? | **Identisch** — gleiche Paarung, gleiche A/B-Zuordnung, gleicher `env`/`provision_mode`; kein Reseed. |
| Disconnect mitten im Match | **Out of scope** — Rematch nur bei `phase=finished`. |
| Ready/GO-Timeout | **Unveraendert** (#1025). |
| Server-Crash mitten im Match | **Out of scope**. |
| Wann gewertet/abgebrochen | Kein MMR; neuer Record (`rematch_of`), `result` bleibt am Alt-Record. |
| Rematch waehrend `running` | **Refused** (`409 referee_rematch_failed`), kein neuer Start. |
| Cleanup-Semantik | Alt-Welten **immer** vor neuen gestoppt; Cleanup-Fehler blockiert Neustart. |

## Aufbau

| Datei | Inhalt |
|---|---|
| `queue_core.py` | reine Matchmaking-Logik (`QueueCore`, `Match`, `Team`, `QueueEntry`, `QueueError`) + Persistenz (`to_state`/`load_state`). |
| `queue_flow.py` | `QueueCoordinator` (kalte Doppel-Provisionierung, Referee-Lobby, Rollback, Cleanup) + HTTP-Clients `ProvisionerClient`/`RefereeClient`. |
| `queue_service.py` | HTTP-Dienst (`ThreadingHTTPServer`) + `QUEUE_*`-Config (fail-closed) + Bearer + Routen. |
| `identity.py` | Identitaets-Spiegel (Kopie `deploy/capsule/identity.py`; kein Cross-Import). |
| `test_queue_core.py` / `test_queue_flow.py` / `test_queue_service.py` | hermetische Tests (stdlib, `127.0.0.1:0`). |
| `e2e_998_queue.py` | Tester-Stage-Harness: echter Dienst-HTTP-Pfad gegen Stub-Provisioner/-Referee; Rohbeleg nach `evidence/`. |
| `e2e_1030_rematch.py` | Tester-Stage-Harness fuer den Lobby-Rematch (#1030); Rohbeleg nach `evidence/`. |

## Konfiguration (`QUEUE_*`)

| Var | Default | Zweck |
|---|---|---|
| `QUEUE_ENV` | `dev` (o. `RIFT_ENV`) | Env-Namensraum |
| `QUEUE_BIND` / `QUEUE_PORT` | `127.0.0.1` / `9221` | Bind (nur localhost) |
| `QUEUE_TOKEN` | (leer) | Bearer-Token (Vault); leer = offen (nur Tests/dev) |
| `QUEUE_PROVISIONER_URL` / `_TOKEN` / `_TIMEOUT` | `http://127.0.0.1:8094` / — / `30` | Provisioner (kalte Welten) |
| `QUEUE_REFEREE_URL` / `_TOKEN` | `http://127.0.0.1:8081` / — | Referee-Lobby (tournament-server) |
| `QUEUE_TIMEOUT` | `5` | HTTP-Timeout (s) |
| `QUEUE_STATE_DIR` | (leer) | Match-Record (JSON, uebersteht Restart) |
| `QUEUE_TEAM_SIZE` / `QUEUE_ALLOW_TEAMS` | `1` / `false` | Match-Modell (aktiv 1v1) |
| `QUEUE_RECONCILE_INTERVAL_S` | `5` | Auto-Finish-Reconciler-Takt (s); `0` = aus |
| `QUEUE_LOG_LEVEL` | `INFO` | Log-Level |

Zahlen (`PORT`/`TIMEOUT`/`PROVISIONER_TIMEOUT`) sind **fail-closed** validiert:
fehlend -> Default, ungueltig/<=0 -> Start-Abbruch (`--check` -> rc 2).
`QUEUE_RECONCILE_INTERVAL_S` erlaubt zusaetzlich `0` (Reconciler aus), negative
Werte -> Start-Abbruch.

## Tests

```bash
cd deploy/queue && python3 -m unittest -v                 # Core + Flow + Service (stdlib)
cd deploy/queue && TMPDIR=/dev/shm python3 e2e_998_queue.py   # E2E-Abnahme (§0) + Evidence
bash deploy/tests/queue/run.sh                            # Ansible-Rollen-Render + --check (CI)
```

**Kaltes Cleanup (#1028):** `finish` sichert das Ergebnis, **bevor** gestoppt
wird. Scheitert ein `provisioner.stop` (Fail-fast), wird der Match **nicht** als
„cleaned" markiert: der naechste Reconciler-Takt wiederholt nur den Stop, das
Ergebnis bleibt erhalten und wird nie ueberschrieben.

**KERN-NACHWEIS:** `e2e_998_queue.py` belegt die Issue-Abnahme ueber den echten
Dienst-HTTP-Pfad: zwei Spieler -> **ein** Match, A<->Welt A / B<->Welt B, zwei
verschiedene GNS-Endpoints, zwei Referee-`/lobby`-Registrierungen, ein
Match-Record mit beiden Teilnehmern — und die #1025-Ready-Kette: je **distinct**
Welt genau **ein** `POST /ready`, alle **nach** beiden `/lobby` (Reihenfolge im
Stub-Assert).

## Deploy

Compose-Service `deploy/compose/queue` (Muster `capsule-flow`): Unit
`rbmods-queue.service`, Env-Datei `/etc/rbmods/queue.env` (0600,
Token aus Vault), Bind `127.0.0.1:$QUEUE_PORT`, Zustandsverzeichnis
`/var/lib/rbmods/queue`. Sie wird host-weit von `deploy/deploy-prod.yml`
(Play 0, Tag-Deploy) aufgenommen. Der Relay (`tools/gns-proxy`) proxyt `POST /queue` an den Dienst,
wenn `--queue-url`/`RBB_QUEUE_URL` gesetzt ist.

## Abgrenzung

Kein Warm-Pool, kein MMR/Ranking, kein `team_size > 1` im Betrieb, keine
Match-Result-UI (#999), keine Queue-Status-Veredelung ueber die additiven
`/sessions`-Felder hinaus (#1000).
