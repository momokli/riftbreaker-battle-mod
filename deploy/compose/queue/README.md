# queue (compose service) — Issue #1093

Containerized twin of the systemd unit `rbmods-queue` (`deploy/roles/queue`,
Issue #998). Runs the **same** `deploy/queue/queue_service.py` (pure stdlib): it
pairs players (casual **1v1**, FIFO), cold-provisions two fresh VS worlds (A/B)
per match, registers both players in the referee (tournament-server) and keeps
the match record.

It talks to the **provisioner** (`:8094`) and the **referee** (`:8081`) purely
over **HTTP** — **no Docker socket, no game code, no host mounts** besides the
optional state dir. See `deploy/queue/README.md` for the full service contract.

## Files

| File | Purpose |
|---|---|
| `Dockerfile` | `python:3.12-slim` (stdlib only). Build context is **`./deploy/queue`** (see below). |
| `README.md` | This run contract. |

The Dockerfile lives here but the build context is the module directory (the
same pattern as `provisioner` / `tournament-server`), because the four scripts
live in `deploy/queue/`:

```yaml
build:
  context: ./deploy/queue
  dockerfile: ../compose/queue/Dockerfile
```

`queue_service.py` imports `queue_flow.py` + `queue_core.py`; `queue_flow.py`
imports `identity.py` — so all four are copied into `/app` (1:1 with the role,
single source of truth).

## Run contract

| | |
|---|---|
| **Image / build context** | `context: ./deploy/queue`, `dockerfile: ../compose/queue/Dockerfile` |
| **Command** | `python3 -u /app/queue_service.py`; add `command: ["--check"]` to only validate config (rc `0` OK, rc `2` invalid) |
| **Ports** | HTTP **9221** in-container, publish on **`127.0.0.1:9221`** only. Bind inside is `0.0.0.0` (set by the image ENV). |
| **Volumes** | only the (optional) state dir for match-record persistence — a host-root bind (`${RBB_HOST_ROOT:-/srv/rbbattle}/queue`, #1112); nothing else |
| **Host access** | none (no Docker socket) |
| **Python deps** | stdlib only (`queue_service.py`, `queue_flow.py`, `queue_core.py`, `identity.py`) |

### Routes (JSON, Bearer when `QUEUE_TOKEN` is set)

| Method + path | Body | Semantics |
|---|---|---|
| `GET  /health` | — | `200 {"ok":true,"env":…}` |
| `GET  /queue/status` | — | queue + matches snapshot |
| `POST /queue/join` | `{"identitaet","mode":"vs"}` | `queued`/`position` or `matched` (A/B assignments) |
| `POST /queue/leave` | `{"identitaet"}` | remove a waiting player (`409` if already matched) |
| `POST /queue/finish` | `{"match_id","result"?}` | result + cold cleanup (idempotent) |
| `POST /queue/rematch` | `{"match_id"}` or `{"identitaet"}` | rematch of the same pairing (idempotent) |

Auto-finish reconciler (`QUEUE_RECONCILE_INTERVAL_S`, default `5`s) polls the
referee's `GET /state` and finishes matches that reached `phase=finished`.

## Env

| Var | Default | Notes |
|---|---|---|
| `QUEUE_ENV` | `dev` (or `RIFT_ENV`) | env namespace (also returned by `/health`) |
| `QUEUE_BIND` / `QUEUE_PORT` | `127.0.0.1` / `9221` | image sets bind `0.0.0.0`, port `9221` |
| `QUEUE_TOKEN` | — | Bearer; **empty = open** (local dev only) |
| `QUEUE_PROVISIONER_URL` | `http://127.0.0.1:8094` | cold-world provisioner (compose: `http://provisioner:8094`) |
| `QUEUE_PROVISIONER_TOKEN` | — | Bearer for the provisioner |
| `QUEUE_REFEREE_URL` | `http://127.0.0.1:8081` | referee / lobby (compose: `http://tournament-server:8080`) |
| `QUEUE_REFEREE_TOKEN` | — | Bearer for the referee |
| `QUEUE_STATE_DIR` | (empty) | match-record dir (JSON, survives restart); empty = no persistence |
| `QUEUE_TEAM_SIZE` | `1` | match model (active 1v1) |
| `QUEUE_ALLOW_TEAMS` | `false` | allow `team_size > 1` |
| `QUEUE_RECONCILE_INTERVAL_S` | `5` | auto-finish tick (s); `0` = off |

Config is **fail-closed** for numbers (`QUEUE_PORT`, `QUEUE_TIMEOUT`,
`QUEUE_PROVISIONER_TIMEOUT`): invalid/`<= 0` → refuse start (`--check` rc `2`).
`QUEUE_RECONCILE_INTERVAL_S` additionally allows `0` (reconciler off). Also
honoured: `QUEUE_TIMEOUT` (default `5`) and `QUEUE_LOG_LEVEL` (default `INFO`).

## Volumes / persistence (important)

The state dir is the **only** optional mount. Set `QUEUE_STATE_DIR` to a path
inside the container and bind a host dir there (compose: the host-root bind
`${RBB_HOST_ROOT:-/srv/rbbattle}/queue:/var/lib/rbmods/queue`, #1112) so the
match record (and the waiting queue) survives a service restart; without it the
queue is in-memory only. The service itself needs **no** Docker socket and
**no** game/rbtools paths — everything towards the provisioner and referee is
HTTP.

## Paste-ready compose fragment

```yaml
queue:
  build:
    context: ./deploy/queue
    dockerfile: ../compose/queue/Dockerfile
  image: rbb-queue:${RBB_REF:-dev}
  restart: unless-stopped
  environment:
    QUEUE_ENV: ${RBB_ENV:-local}
    QUEUE_BIND: 0.0.0.0
    QUEUE_PORT: "9221"
    QUEUE_TOKEN: ${RBB_QUEUE_TOKEN:-}
    QUEUE_PROVISIONER_URL: http://provisioner:8094
    QUEUE_PROVISIONER_TOKEN: ${RBB_PROVISIONER_TOKEN:-}
    QUEUE_REFEREE_URL: http://tournament-server:8080
    QUEUE_REFEREE_TOKEN: ${RBB_TOURNAMENT_TOKEN:-}
    QUEUE_STATE_DIR: /var/lib/rbmods/queue
    QUEUE_TEAM_SIZE: "1"
    QUEUE_ALLOW_TEAMS: "false"
    QUEUE_RECONCILE_INTERVAL_S: "5"
  ports:
    - "127.0.0.1:${RBB_QUEUE_PORT:-9221}:9221"
  volumes:
    - ${RBB_HOST_ROOT:-/srv/rbbattle}/queue:/var/lib/rbmods/queue
```

* The service URLs above assume the provisioner/referee compose services are on
  the same network under those names; they are HTTP endpoints, not mounts.
* **Token:** keep `QUEUE_TOKEN` non-empty on shared hosts; empty means the
  service is open (local dev only).

## Validation

```bash
docker build -f deploy/compose/queue/Dockerfile deploy/queue
docker run --rm \
  -e QUEUE_ENV=dev -e QUEUE_BIND=0.0.0.0 -e QUEUE_PORT=9221 \
  -e QUEUE_PROVISIONER_URL=http://provisioner:8094 \
  -e QUEUE_REFEREE_URL=http://tournament-server:8080 \
  rbb-queue:local --check
```

`docker build deploy/compose/queue` (context = the Dockerfile's dir) will
**not** work: the four scripts live in `deploy/queue/`, which is the build
context (same pattern as `provisioner`).

## Open points

* Hermetic tests / the real service contract live in `deploy/queue/` (see its
  README); this image is a thin wrapper and adds no logic.
* `GET /health` also requires the Bearer token when `QUEUE_TOKEN` is set (auth
  runs before routing) — a liveness probe must send it.
* `QUEUE_TOKEN` comes from `.env` (local-first secret), not a vault.
