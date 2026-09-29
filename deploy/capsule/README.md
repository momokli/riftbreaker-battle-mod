# Kapsel-Flow (`deploy/capsule/`) — eine gespielte Solo-Runde (Issue #931)

Der Kapsel-Koordinator verkettet die bereits vorhandenen Bausteine zu **einer
gespielten Runde**. Die Bausteine existierten einzeln, aber niemand verband
sie: Parked-Pool (#909/#928), Attack-Cycle, Bridge.

Kapsel-Phasen (eigene, kleine ZSM — **nicht** die Parked-States und **nicht**
die `deriveSoloPhase`-Anzeige aus #930):

```
idle ──open/claim──▶ claimed ──ready──▶ warmup ──(Warmup-Ende+HQ)──▶ running
                       │                                             │
                       │                                    (HQ-Tod)▼
                       └─────── finish/end_game ◀─────────── game_over
                                          │
                                    recycling ──(round_reset+pause)──▶ parked
```

| Phase | Bedeutung | Quelle |
|---|---|---|
| `idle` | keine Kapsel aktiv | Coordinator |
| `claimed` | Instanz übergeben, **Welt pausiert** (kein `resume_game`), Cycle `PAUSED` | Parked `CLAIMED` |
| `warmup` | `ready`: Welt resumed, Cycle `WARMUP` | Cycle `state` |
| `running` | Cycle `RUNNING` (Wellen feuern) | Cycle `state` |
| `game_over` | Runde beendet (HQ-Tod / `end_game`), Ergebnis sichtbar | Cycle `state` |
| `recycling` | `end_game(result)` + `round_reset` + `pause_game` laufen | Parked `RECYCLING` |
| `parked` | zurück im Pool, für den Nächsten verfügbar | Parked `PARKED` |

**Phasenquelle:** `warmup`/`running`/`game_over` werden aus dem
Attack-Cycle-`state` **abgeleitet** (`status()`); `claimed`/`recycling`/`parked`
führt der Coordinator selbst.

## Zwei Pause-Begriffe (wichtig!)

Getrennt gehalten — nicht verwechseln:

* **Welt-Pause** (`pause_game`/`resume_game`, Bridge): friert die Simulation ein
  (kein Welt-Tick). `claimed`-Kapseln sind so geparkt; `ready` resumed.
* **Cycle-Pause** (Attack-Cycle `PAUSED`): es feuern keine Wellen, obwohl die
  Welt läuft.

`claimed` heißt also „Welt pausiert **und** Cycle `PAUSED`"; `warmup`/`running`
heißen „Welt läuft **und** Cycle `WARMUP`/`RUNNING`".

## Bindende Design-Entscheidungen (#931)

* **Q1:** `open` claimt **ohne** `resume_game` (Parked `claim(resume=False)`):
  die Welt bleibt nach der Übergabe PAUSIERT. `ready` resumed + startet den
  Cycle. (`resume=True` bleibt dort Default, wo es rückwärts-kompatibel nötig
  ist — z. B. Pool/VS.)
* **Q4:** `finish` ohne `result` ist erlaubt → `round_reset` + `pause_game`,
  **kein** `end_game(None)` (die Bridge lehnt das mit HTTP 400 ab). Mit
  `result` (`win`/`lose`) wird `end_game(result)` gerufen.
* **Q5:** eine aktive Kapsel pro Env; das Datenmodell ist `instance_id`-fähig
  (`Capsule.instance_id`).
* **Q6:** `finish` ist **operator-/cockpit-getrieben** (expliziter Aufruf),
  **keine** Auto-Rundenende-Erkennung im MVP. Auto-Erkennung = Folge-Issue.

## API des Dienstes (`capsule_service.py`)

Bind `127.0.0.1:$CAPSULE_PORT` (Default 8093). Bearer-Token PFLICHT, wenn
`CAPSULE_TOKEN` gesetzt. Fehlerformat einheitlich
`{"ok":false,"reason":…,"detail":…}`; `401`/`404`/`405`/`409`/`503`.

| Methode | Pfad | Body | Semantik |
|---|---|---|---|
| `GET` | `/health` | — | Liveness: `200 {"ok":true,"env":…}` |
| `GET` | `/capsule/status` | — | `{phase, instance, env, bridge_url, gns_endpoint, round, cycle:{state,…}}` |
| `POST` | `/capsule/open` | `{"env"?,"instance_id"?,"identitaet"?}` | Parked `claim` **ohne resume**; `phase=claimed`; Antwort enthält `gns_endpoint` |
| `POST` | `/capsule/ready` | `{}` | Bridge `resume_game` + Cycle `POST /start`; `phase=warmup` |
| `POST` | `/capsule/finish` | `{"result"?:"win"\|"lose"}` | Parked `recycle(result)` → `phase=parked` |
| `POST` | `/capsule/auto` | `{}` | Bridge `POST /pause_game {"op":"auto"}` (Operator-Override frei) |

```bash
curl -s 127.0.0.1:8093/health
curl -s -X POST 127.0.0.1:8093/capsule/open  -d '{"identitaet":"str:<id>"}'   # -> phase=claimed (pausiert)
curl -s 127.0.0.1:8093/capsule/status                                        # phase=claimed
curl -s -X POST 127.0.0.1:8093/capsule/ready -d '{}'                         # -> phase=warmup
curl -s -X POST 127.0.0.1:8093/capsule/finish -d '{"result":"win"}'          # -> phase=parked
curl -s -X POST 127.0.0.1:8093/capsule/auto  -d '{}'                         # Override frei
```

## Aufbau

| Datei | Inhalt |
|---|---|
| `capsule_flow.py` | `CapsulePhase`, `CapsuleError`, `Capsule`, `CapsuleCoordinator` (Clients injizierbar) + die HTTP-Clients `ParkedServiceClient`, `CycleClient`, `BridgeControl`. Reine stdlib. |
| `capsule_service.py` | HTTP-Dienst (`ThreadingHTTPServer`) + `CAPSULE_*`-Config (fail-closed) + Bearer-Auth + Routen. |
| `test_capsule_flow.py` / `test_capsule_service.py` | Hermetische Tests (Fake-Stack, `127.0.0.1:0`), inkl. Ketten-Integrationstest. |

Der Koordinator spricht die Nachbarn **ausschließlich per HTTP** an — kein
Docker-/Bridge-Code hier:

* Parked-Dienst (`CAPSULE_PARKED_URL`, Bearer `CAPSULE_PARKED_TOKEN`): `POST /claim` (`resume=false`), `POST /recycle`.
* Attack-Cycle-Control (`CAPSULE_CYCLE_URL`): `POST /start`, `GET /status`.
* Bridge (aus der Claim-Antwort `bridge_url`): `POST /resume_game`, `POST /pause_game {"op":"auto"}`.

## Konfiguration (`CAPSULE_*`)

| Var | Default | Zweck |
|---|---|---|
| `CAPSULE_ENV` | `dev` (o. `RIFT_ENV`) | Env-Namensraum |
| `CAPSULE_BIND` / `CAPSULE_PORT` | `127.0.0.1` / `8093` | Bind (nur localhost) |
| `CAPSULE_TOKEN` | (leer) | Bearer-Token (Vault); leer = offen (nur Tests/dev) |
| `CAPSULE_PARKED_URL` | `http://127.0.0.1:8095` | Parked-Dienst |
| `CAPSULE_PARKED_TOKEN` | (leer) | Bearer für den Parked-Dienst |
| `CAPSULE_CYCLE_URL` | `http://127.0.0.1:9102` | Attack-Cycle-Control |
| `CAPSULE_TIMEOUT` | `5` | HTTP-Timeout (s) |
| `CAPSULE_LOG_LEVEL` | `INFO` | Log-Level |

Zahlen (`PORT`/`TIMEOUT`) sind **fail-closed** validiert: fehlend → Default,
ungültig/≤0 → Start-Abbruch (`--check` → rc 2).

## Fehler-Mapping

| Fall | Antwort |
|---|---|
| Kapsel für die Env aktiv (`open`) | `409 already_open` |
| keine aktive Kapsel (`ready`/`finish`/`auto`) | `409 no_instance` |
| falsche Phase (`ready` außerhalb `claimed`; doppeltes `finish`) | `409 wrong_phase` |
| `result` weder `win`/`lose` | `400 bad_request` |
| Parked/Bridge/Cycle nicht erreichbar | `503` (`claim_failed`/`bridge_unreachable`/`cycle_unreachable`) |
| ohne/mit falschem Bearer | `401 unauthorized` |

`ready` ist idempotent, wenn die Kapsel schon `warmup`/`running` ist. Scheitert
`resume_game`, bleibt die Phase `claimed` (Wiederholung möglich); scheitert der
Cycle-Start nach erfolgreichem Resume, wird die Welt best-effort wieder pausiert
und die Phase bleibt `claimed` — **kein stiller Teilzustand**.

## Tests

```bash
cd deploy/capsule && python3 -m unittest -v        # Flow + Service (stdllib, kein Netz)
bash deploy/tests/capsule-flow/run.sh              # Ansible-Rollen-Render + --check
```

**KERN-NACHWEIS:** `FullChainTests`/`FullChainHttpTests` spielen eine ganze
Runde hermetisch durch (Fake-Parked + Fake-Cycle + Fake-Bridge mit gemeinsamem
Zustand):

```
open (claimed, Welt pausiert) → ready (warmup→running) → finish(win)
(game_over→recycled→parked) → auto (Override frei)
```

Der echte Staging-Lauf (`/solo` → join → pausiert → `ready` → WARMUP/RUNNING →
`finish` → wieder `PARKED` → `auto`) ist **Abnahme-Evidence**, nicht PR-Gate.

## Deploy

Rolle `deploy/roles/capsule-flow/` (Muster `parked-pool`): Unit
`rbmods-capsule-<env>.service`, Env-Datei `/etc/rbmods/capsule-<env>.env` (0600,
Token aus Vault), Bind `127.0.0.1:$CAPSULE_PORT`. Sie wird nur vom dev-Play
(`deploy/site.yml`) aufgenommen — der Boot-Test (`test-deploy.yml`) bootet nur
den Game-Server und kennt Parked/Cycle nicht.

Der Relay (`tools/gns-proxy`) kann `POST /solo` auf den Kapsel-Dienst zeigen
lassen: ist `--capsule-url`/`RBB_CAPSULE_URL` gesetzt, ruft `/solo`
`POST /capsule/open` (Claim ohne resume → pausiertes Spiel) statt `POST /claim`.