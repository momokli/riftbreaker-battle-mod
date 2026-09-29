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
         -> Referee POST /lobby {player, world} je Spieler
         -> Match-Record {participants:[{identitaet,world,instance,endpoint}], …}
finish(m1, result?) -> Ergebnis nachtragen + KALTES Cleanup (beide Instanzen stoppen)
```

## Phasen / Modelle

`queue_core.py` haelt das generische Modell (N Slots je Team,
`teams_per_match=2`), aktiv `team_size == 1`. Ein Match durchlaeuft
`provisioning -> ready -> finished` (Fehler -> `failed`). `finish` ist
**idempotent**: der zweite Aufruf stoppt nicht erneut und aendert das Ergebnis
nicht.

| Begriff | Bedeutung |
|---|---|
| `QueueEntry` | wartender Spieler (FIFO-Slot, `seq`) |
| `Team` | Team-Index + Welt (`A`/`B`) + Spieler |
| `Match` | `match_id` (monoton), `state`, `result?`, `teams`, `participants` |
| Welt-Zuordnung | Team 0 -> `A`, Team 1 -> `B` |
| Ergebnis | `winnerA` / `winnerB` / `draw` (kein MMR) |

## Kalte Provisionierung (US2)

Je Paarung werden **zwei** Instanzen gestartet (`queue-<match_id>-a` /
`queue-<match_id>-b`), beide mit **verschiedenen** GNS-UDP-Endpoints. Der
Provisioner wird mit dem expliziten `world`-Parameter aufgerufen (`A`/`B`); das
seedet `RBB_VS_WORLD` + `RBB_REFEREE_URL` je Instanz und schaltet self-send aus
— `parse_mode` bleibt unangetastet (eigene Achse, Issue #998).

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

```bash
curl -s 127.0.0.1:9221/health
curl -s -X POST 127.0.0.1:9221/queue/join -d '{"identitaet":"str:<A>","mode":"vs"}'   # -> position
curl -s -X POST 127.0.0.1:9221/queue/join -d '{"identitaet":"str:<B>","mode":"vs"}'   # -> matched A/B
curl -s 127.0.0.1:9221/queue/status
curl -s -X POST 127.0.0.1:9221/queue/finish -d '{"match_id":1,"result":"winnerA"}'
```

**Fehler-Mapping:** `mode != "vs"` -> `400 bad_mode`; fehlende `identitaet` ->
`400 bad_request`; `team_size > 1` (ohne `QUEUE_ALLOW_TEAMS`) ->
`400 unsupported_team_size`; unbekannter Match / bereits gepaart -> `409`;
Provisioner/Referee nicht erreichbar -> `503 provision_failed`; ohne/mit
falschem Bearer -> `401 unauthorized`.

## Aufbau

| Datei | Inhalt |
|---|---|
| `queue_core.py` | reine Matchmaking-Logik (`QueueCore`, `Match`, `Team`, `QueueEntry`, `QueueError`) + Persistenz (`to_state`/`load_state`). |
| `queue_flow.py` | `QueueCoordinator` (kalte Doppel-Provisionierung, Referee-Lobby, Rollback, Cleanup) + HTTP-Clients `ProvisionerClient`/`RefereeClient`. |
| `queue_service.py` | HTTP-Dienst (`ThreadingHTTPServer`) + `QUEUE_*`-Config (fail-closed) + Bearer + Routen. |
| `identity.py` | Identitaets-Spiegel (Kopie `deploy/capsule/identity.py`; kein Cross-Import). |
| `test_queue_core.py` / `test_queue_flow.py` / `test_queue_service.py` | hermetische Tests (stdlib, `127.0.0.1:0`). |
| `e2e_998_queue.py` | Tester-Stage-Harness: echter Dienst-HTTP-Pfad gegen Stub-Provisioner/-Referee; Rohbeleg nach `evidence/`. |

## Konfiguration (`QUEUE_*`)

| Var | Default | Zweck |
|---|---|---|
| `QUEUE_ENV` | `dev` (o. `RIFT_ENV`) | Env-Namensraum |
| `QUEUE_BIND` / `QUEUE_PORT` | `127.0.0.1` / `9221` | Bind (nur localhost) |
| `QUEUE_TOKEN` | (leer) | Bearer-Token (Vault); leer = offen (nur Tests/dev) |
| `QUEUE_PROVISIONER_URL` / `_TOKEN` / `_TIMEOUT` | `http://127.0.0.1:8094` / — / `30` | Provisioner (kalte Welten) |
| `QUEUE_REFEREE_URL` / `_TOKEN` | `http://127.0.0.1:8080` / — | Referee-Lobby |
| `QUEUE_TIMEOUT` | `5` | HTTP-Timeout (s) |
| `QUEUE_STATE_DIR` | (leer) | Match-Record (JSON, uebersteht Restart) |
| `QUEUE_TEAM_SIZE` / `QUEUE_ALLOW_TEAMS` | `1` / `false` | Match-Modell (aktiv 1v1) |
| `QUEUE_LOG_LEVEL` | `INFO` | Log-Level |

Zahlen (`PORT`/`TIMEOUT`/`PROVISIONER_TIMEOUT`) sind **fail-closed** validiert:
fehlend -> Default, ungueltig/<=0 -> Start-Abbruch (`--check` -> rc 2).

## Tests

```bash
cd deploy/queue && python3 -m unittest -v                 # Core + Flow + Service (stdlib)
cd deploy/queue && TMPDIR=/dev/shm python3 e2e_998_queue.py   # E2E-Abnahme (§0) + Evidence
bash deploy/tests/queue/run.sh                            # Ansible-Rollen-Render + --check (CI)
```

**KERN-NACHWEIS:** `e2e_998_queue.py` belegt die Issue-Abnahme ueber den echten
Dienst-HTTP-Pfad: zwei Spieler -> **ein** Match, A<->Welt A / B<->Welt B, zwei
verschiedene GNS-Endpoints, zwei Referee-`/lobby`-Registrierungen, ein
Match-Record mit beiden Teilnehmern.

## Deploy

Rolle `deploy/roles/queue/` (Muster `capsule-flow`): Unit
`rbmods-queue-<env>.service`, Env-Datei `/etc/rbmods/queue-<env>.env` (0600,
Token aus Vault), Bind `127.0.0.1:$QUEUE_PORT`, Zustandsverzeichnis
`/var/lib/rbmods/queue-<env>`. Sie wird nur vom dev-Play (`deploy/site.yml`)
aufgenommen. Der Relay (`tools/gns-proxy`) proxyt `POST /queue` an den Dienst,
wenn `--queue-url`/`RBB_QUEUE_URL` gesetzt ist.

## Abgrenzung

Kein Warm-Pool, kein MMR/Ranking, kein `team_size > 1` im Betrieb, keine
Match-Result-UI (#999), keine Queue-Status-Veredelung ueber die additiven
`/sessions`-Felder hinaus (#1000).