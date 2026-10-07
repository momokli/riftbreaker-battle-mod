# capsule-flow (compose service) — Issue #1093

Containerized twin of the systemd unit `rbmods-capsule-<env>`
(`deploy/roles/capsule-flow`, Issue #931). Runs the **same**
`deploy/capsule/capsule_service.py` (pure stdlib); it talks to Parked,
Attack-Cycle and the Bridge over HTTP only — **no Docker, no volumes**.

## Files

| File | Purpose |
|---|---|
| `Dockerfile` | `python:3.12-slim` (stdlib only). Build context: **this directory**. |
| `entrypoint.sh` | Exports the `CAPSULE_*` defaults, enforces a non-empty token (fail-closed), execs `/app/capsule_service.py "$@"`. Forwards `--check`. |

## Run contract

```yaml
capsule-flow:
  build:
    context: ./deploy/compose/capsule-flow
  image: rbb-capsule-flow:${RBB_REF:-dev}
  restart: unless-stopped
  depends_on:
    attack-cycle:
      condition: service_started
    warm:
      condition: service_started
  environment:
    CAPSULE_ENV: ${RBB_ENV:-dev}
    CAPSULE_TOKEN: ${RBB_CAPSULE_TOKEN:?set in .env — cp .env.example .env}
    CAPSULE_CYCLE_URL: http://attack-cycle:9102
    CAPSULE_PARKED_URL: "http://warm:9201"
    CAPSULE_PARKED_TOKEN: "${RBB_WARM_TOKEN:-}"
  ports:
    - "127.0.0.1:${RBB_CAPSULE_PORT:-9211}:9211"
  volumes:
    - ./deploy/capsule:/app:ro
```

* **Image / build context:** `./deploy/compose/capsule-flow`.
* **Command:** entrypoint → `python3 /app/capsule_service.py` (HTTP daemon). Add
  `command: ["--check"]` to only validate config.
* **Ports:** HTTP **9211** in-container, publish on **`127.0.0.1:<port>`** only.
  Bind inside is `0.0.0.0`.
* **Volumes:** the script dir `/app` (read-only). None else.
* **Host access:** none (no Docker socket).
* **Python deps:** stdlib only (`capsule_service.py`, `capsule_flow.py`,
  `identity.py` — all mounted into `/app`, so the imports resolve).

### Env (defaults set by the entrypoint; all overridable)

| Var | Default | Notes |
|---|---|---|
| `CAPSULE_ENV` | `${RBB_ENV:-dev}` | env namespace |
| `CAPSULE_BIND` / `CAPSULE_PORT` | `0.0.0.0` / `9211` | bind in-container / port |
| `CAPSULE_TOKEN` | — (**mandatory**) | Bearer; empty → exit `2` |
| `CAPSULE_PARKED_URL` | `http://parked-pool:9201` | Parked service |
| `CAPSULE_PARKED_TOKEN` | — | Bearer for Parked |
| `CAPSULE_CYCLE_URL` | `http://attack-cycle:9102` | Attack-Cycle control |
| `CAPSULE_TIMEOUT` / `CAPSULE_LOG_LEVEL` | `5` / `INFO` | role defaults |

### Fail-closed (mandatory)

Without a non-empty `CAPSULE_TOKEN` the service **does not start** (exit `2`),
mirroring the role's assert (#931/#424). Note: the agent code alone would run
"open" on an empty token — the entrypoint enforces the deployed precondition.

## Validation (ran locally)

```bash
docker build -f deploy/compose/capsule-flow/Dockerfile deploy/compose/capsule-flow   # OK
# no token   -> rc=2 (fail-closed)
# with token -> rc=0 "Konfiguration OK (bind=0.0.0.0 port=9211 …)"
# HTTP: /capsule/status without bearer -> 401; with bearer -> 200 {"ok":true,"phase":"idle",…}
```

## Open points

* **Parked target is the static warmed capsule** — compose points
  `CAPSULE_PARKED_URL` at the `warm` service (`http://warm:9201`, #1108) with
  `depends_on: warm`; the image default remains the future `parked-pool` service
  (`ready`/`auto` only need the bridge/cycle).
* `GET /health` also requires the Bearer token in the current code (auth runs
  before routing) — a liveness probe must send it.
