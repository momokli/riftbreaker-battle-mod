# provisioner (compose service) — Issue #1083

HTTP wrapper around the on-demand provisioner (`deploy/provisioner/provisioner.py`,
Issue #908). The queue's `ProvisionerClient`
(`deploy/queue/queue_flow.py`) expects a service on `127.0.0.1:8094` — that
service did not exist, so the VS path was dead. This compose service builds and
runs exactly that wrapper (`deploy/provisioner/provisioner_service.py`, stdlib
only): it constructs `Provisioner(load_config())` once and exposes
`start`/`stop`/`status` as JSON.

It **shells out to `docker`** (`DockerCli`) and creates **sibling** containers
via `docker run` (the dedi + its five sidecars, its network and named volumes),
so unlike `capsule-flow` it needs the **Docker CLI + the host socket**.

## Files

| File | Purpose |
|---|---|
| `Dockerfile` | `python:3.12-slim` + Docker CLI (copied from `docker:cli`). Build context is **`./deploy/provisioner`** (see below). |
| `README.md` | This run contract. |

The Dockerfile lives here but the build context is the module directory (the
same pattern as `tournament-server`), because the two scripts live in
`deploy/provisioner/`:

```yaml
build:
  context: ./deploy/provisioner
  dockerfile: ../compose/provisioner/Dockerfile
```

## Run contract

| | |
|---|---|
| **Image / build context** | `context: ./deploy/provisioner`, `dockerfile: ../compose/provisioner/Dockerfile` |
| **Command** | `python3 -u /app/provisioner_service.py`; add `command: ["--check"]` to only validate config |
| **Ports** | HTTP **8094** in-container, publish on **`127.0.0.1:8094`** only. Bind inside is `0.0.0.0` (set by the image ENV). |
| **Host access** | `/var/run/docker.sock` (the service wraps `docker`) |
| **Python deps** | stdlib only (`provisioner.py`, `provisioner_service.py`) |

### Routes (JSON)

| Method + path | Body / query | Calls |
|---|---|---|
| `GET  /health` | — | `200 {"ok":true,"env":…}` |
| `POST /start` | `{"env","mode","instance_id","world"?}` | `Provisioner.start(...)` |
| `POST /stop` | `{"env","instance_id"}` | `Provisioner.stop(...)` |
| `GET  /status` | `?instance_id=&env=` | `Provisioner.status(...)` |

`/start` and `/status` return the method's dict verbatim (plus `"ok":true`),
including `ports` — and in particular `ports.gns` (`"127.0.0.1:<port>"`, the
GNS-UDP host endpoint), which the queue reads defensively
(`{"ports":{"gns":…}}`, else `gns_endpoint`/`endpoint`). `mode` defaults to
`solo_self` when the body omits it; `world` (`A`/`B`) drives the VS double
provisioning (#998) and is forwarded only when present.

Errors are fail-loud, never a silent `200`:
`401` (bad/missing Bearer when `PROVISIONER_TOKEN` is set), `404` unknown route,
`405` wrong method, `400` bad JSON/oversized body, `500` `ProvisionError`/
`ConfigError`, `503` `DockerError`.

### Env

| Var | Default | Notes |
|---|---|---|
| `PROVISIONER_ENV` | `dev` | env namespace (also returned by `/health`) |
| `PROVISIONER_BIND` / `PROVISIONER_PORT` | `127.0.0.1` / `8094` | image sets bind `0.0.0.0`, port `8094` |
| `PROVISIONER_TOKEN` | — | Bearer; **empty = open** (local dev). Applies to all routes, incl. `/health`. |
| `PROVISIONER_LOG_LEVEL` | `INFO` | log level |
| `PROVISIONER_IMAGE` | — (**mandatory**) | dedi image ref (fail-closed: missing → `--check` rc 2, no start) |
| `PROVISIONER_GAME_SOURCE` | `/srv/rift-{env}/game` | host game dir → `/opt/riftbreaker` in the dedi |
| `PROVISIONER_CONFIG_CFG` | `/opt/rbmods/compose/rift-{env}/riftbreaker/config/config.cfg` | source for the per-instance config.cfg |
| `PROVISIONER_RBTOOLS_DIR` | `/opt/rbmods/rbtools/{env}` | host rbtools → `/opt/rbtools:ro` |
| `PROVISIONER_SESSIONS_SCRIPT` / `..._SEND_TAILER_SCRIPT` / `..._MATCH_LOOP_SCRIPT` / `..._ATTACK_CYCLE_SCRIPT` / `..._CHAT_ANNOUNCER_SCRIPT` | `deploy/<module>/*.py` | sidecar script paths passed to the sibling containers |
| `PROVISIONER_PERSONAS_FILE` | `deploy/attack-cycle/personas.example.json` | persona source for `mode=solo_persona:<name>` |
| `PROVISIONER_DEPLOY_REF` | `unknown` | deploy identity → `RBB_REF` in containers |
| `PROVISIONER_BRIDGE_PORT_BASE` | `30000` | base for the per-instance bridge host port |

The full `PROVISIONER_*` set (plus the optional JSON via `PROVISIONER_CONFIG`)
is documented in `deploy/provisioner/README.md`.

### Volumes / host-path visibility (important)

`docker run` (for the sibling dedi + sidecars) is executed by the **host
daemon**, so every `-v` path the service passes is resolved against the **host**
filesystem — not the container. Mount the host paths at the **same path inside
the container** (1:1) so that what the service creates (run-scoped dirs under
`base_dir`, the staged config.cfg) and what it references (game, rbtools,
sidecar scripts) line up with the host.

## Paste-ready compose fragment

```yaml
provisioner:
  build:
    context: ./deploy/provisioner
    dockerfile: ../compose/provisioner/Dockerfile
  image: rbb-provisioner:${RBB_REF:-dev}
  restart: unless-stopped
  environment:
    PROVISIONER_ENV: ${RBB_ENV:-dev}
    PROVISIONER_BIND: 0.0.0.0
    PROVISIONER_PORT: "8094"
    PROVISIONER_TOKEN: ${RBB_PROVISIONER_TOKEN:-}
    # Fachkonfiguration — PROVISIONER_IMAGE ist Pflicht (fail-closed).
    PROVISIONER_IMAGE: ${PROVISIONER_IMAGE:?set in .env}
    PROVISIONER_GAME_SOURCE: /srv/rift-${RBB_ENV:-dev}/game
    PROVISIONER_CONFIG_CFG: /opt/rbmods/compose/rift-${RBB_ENV:-dev}/riftbreaker/config/config.cfg
    PROVISIONER_RBTOOLS_DIR: /opt/rbmods/rbtools/${RBB_ENV:-dev}
    PROVISIONER_SESSIONS_SCRIPT: /opt/rbmods/deploy/session-recorder/session_recorder.py
    PROVISIONER_SEND_TAILER_SCRIPT: /opt/rbmods/deploy/send-tailer/send_tailer.py
    PROVISIONER_MATCH_LOOP_SCRIPT: /opt/rbmods/deploy/match-loop/match_loop.py
    PROVISIONER_ATTACK_CYCLE_SCRIPT: /opt/rbmods/deploy/attack-cycle/attack_cycle.py
    PROVISIONER_CHAT_ANNOUNCER_SCRIPT: /opt/rbmods/deploy/chat-announcer/announcer.py
    PROVISIONER_PERSONAS_FILE: /opt/rbmods/deploy/attack-cycle/personas.json
    PROVISIONER_DEPLOY_REF: ${RBB_REF:-unknown}
    PROVISIONER_BRIDGE_PORT_BASE: "30000"
  ports:
    - "127.0.0.1:8094:8094"
  volumes:
    - /var/run/docker.sock:/var/run/docker.sock
    # Same-path (1:1) mounts — the daemon resolves `-v` against the HOST:
    - ${RBB_BASE_DIR:-/srv}:${RBB_BASE_DIR:-/srv}                                   # base_dir + game
    - ${RBB_RBTOOLS_HOST:-/opt/rbmods/rbtools}:/opt/rbmods/rbtools                 # rbtools
    - ${RBB_CONFIG_HOST:-/opt/rbmods/compose}:/opt/rbmods/compose                  # config.cfg + personas
    - ${RBB_DEPLOY_HOST:-/opt/rbmods/deploy}:/opt/rbmods/deploy                    # sidecar scripts
```

* **`PROVISIONER_IMAGE`** is the dedi image the service runs as a sibling
  (`docker run <PROVISIONER_IMAGE>`). Without it the service refuses to start.
* **Sibling containers:** each `POST /start` creates a dedi +
  `rbbattle-<env>-<instance>-session-recorder` / `-send-tailer` / `-match-loop` /
  `-attack-cycle` / `-chat-announcer`, a dedicated network and named volumes
  (see `deploy/provisioner/README.md` for the naming scheme). `POST /stop`
  removes them again (idempotent).
* **Token:** keep `PROVISIONER_TOKEN` non-empty on shared hosts; empty means the
  service is open (local dev only).

## Validation

Hermetic tests (no Docker, no network — `FakeProvisioner`, bind `127.0.0.1:0`):

```bash
python3 -m unittest deploy/provisioner/test_provisioner_service.py -v
python3 -m py_compile deploy/provisioner/provisioner_service.py
```

Image build (context = `./deploy/provisioner`, not the Dockerfile's own dir):

```bash
docker build -f deploy/compose/provisioner/Dockerfile -t rbb-provisioner:local deploy/provisioner
```

`docker build deploy/compose/provisioner` (context = the Dockerfile's dir) will
**not** work: the two scripts live in `deploy/provisioner/`, which is the build
context (same pattern as `tournament-server`).

## Open points

* The service runs `docker run` for **sibling** containers; the mounted paths
  must be host-visible 1:1 (see “Volumes / host-path visibility”). On a plain
  host (no container) this is a no-op.
* `GET /health` also requires the Bearer token when `PROVISIONER_TOKEN` is set
  (auth runs before routing) — a liveness probe must send it.
* `PROVISIONER_TOKEN` comes from `.env` (local-first secret), not a vault.
