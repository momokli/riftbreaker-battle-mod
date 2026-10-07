# server-control (compose service) — Issue #1093

Containerized twin of the systemd unit `server-control`
(`deploy/roles/server-control`, Issue #424). Runs the **same**
`deploy/server-control/server_control.py` (pure stdlib): status/logs/restart/start/
stop + `config.cfg` rendering, via the Docker CLI on the host.

## Files

| File | Purpose |
|---|---|
| `Dockerfile` | `python:3.12-slim` + Docker CLI (copied from `docker:cli`). Build context: **this directory**. |
| `entrypoint.sh` | Exports the `SERVER_CONTROL_*` defaults, optionally provisions `config-vars.json` from `RBB_SERVER_*`, execs `/app/server_control.py "$@"`. Forwards `--check`. |

## Run contract

```yaml
server-control:
  build:
    context: ./deploy/compose/server-control
  image: rbb-server-control:${RBB_REF:-dev}
  restart: unless-stopped
  depends_on:
    dedicated:
      condition: service_started
  environment:
    SERVER_CONTROL_TOKEN: ${RBB_SERVER_CONTROL_TOKEN:?set in .env}
    SERVER_CONTROL_CONTAINER: rbb-dedicated
    SERVER_CONTROL_ENV: ${RBB_ENV:-dev}
    SERVER_CONTROL_REF: ${RBB_REF:-unknown}
    # config.cfg rendering (POST /server/config) — same values as config-init:
    RBB_SERVER_NAME: ${RBB_SERVER_NAME}
    RBB_SERVER_PASSWORD: ${RBB_SERVER_PASSWORD}
    RBB_SERVER_RCON_PASSWORD: ${RBB_SERVER_RCON_PASSWORD:-}
    RBB_SERVER_MAX_PLAYERS: ${RBB_SERVER_MAX_PLAYERS}
    RBB_SERVER_BROADCAST_ENABLED: ${RBB_SERVER_BROADCAST_ENABLED}
    RBB_SERVER_PAUSE_GAME_WHEN_EMPTY: ${RBB_SERVER_PAUSE_GAME_WHEN_EMPTY}
    RBB_SERVER_CAMPAIGN: ${RBB_SERVER_CAMPAIGN}
    RBB_SERVER_MISSION: ${RBB_SERVER_MISSION}
    RBB_SERVER_DIFFICULTY: ${RBB_SERVER_DIFFICULTY}
  ports:
    - "127.0.0.1:${RBB_SERVER_CONTROL_PORT:-8092}:8092"
  volumes:
    - /var/run/docker.sock:/var/run/docker.sock
    - rb-config:/config:ro                                # target config.cfg (shared with dedicated)
    - ./deploy/roles/riftbreaker-server/templates/config.cfg.j2:/etc/rbmods/server-control/config.cfg.j2:ro  # config-render template
```

* **Image / build context:** `./deploy/compose/server-control`.
* **Command:** entrypoint → `python3 /app/server_control.py` (HTTP daemon). Add
  `command: ["--check"]` to only validate config.
* **Ports:** HTTP **8092** in-container, publish on **`127.0.0.1:<port>`** only.
  Inside the container the bind is `0.0.0.0` (so the published loopback is
  reachable); the agent keeps its Bearer guard regardless.
* **Volumes:** Docker socket + the `rb-config` volume (at `/config`) + the
  optional Jinja template. `rb-config` → the dedicated reads the same file at
  `/data/config/config.cfg`.
* **Host access:** the Docker socket (the agent wraps `docker`).
* **Python deps:** stdlib only. The narrow `{{ var }}`-fallback renders
  `config.cfg.j2` exactly (no Jinja2 needed — the template uses no `{% %}`).

### Fail-closed (mandatory)

Without a non-empty `SERVER_CONTROL_TOKEN` the service **does not start** (the
agent exits `2`; the entrypoint does not default the token). This mirrors the
role's assert (#424/#298).

### `config.cfg` rendering (`POST /server/config`)

* Target file: `SERVER_CONTROL_CONFIG_PATH` (default `/config/config.cfg`).
* Template: `SERVER_CONTROL_CONFIG_TEMPLATE` (default the mounted
  `config.cfg.j2`).
* Base values: `SERVER_CONTROL_CONFIG_VARS`. If no file is mounted, the
  entrypoint generates `/run/rbb-server-control/config-vars.json` from the
  `RBB_SERVER_*` variables (keys `riftbreaker_server_*`, same as
  `config-vars.json.j2`). If neither is present the endpoint returns
  `503 config_unavailable` (unchanged code path).

## Validation (ran locally)

```bash
docker build -f deploy/compose/server-control/Dockerfile deploy/compose/server-control   # OK
# no token   -> rc=2 "SERVER_CONTROL_TOKEN fehlt"
# with token -> rc=0 "Konfiguration OK (bind=0.0.0.0 port=8092 container=rbb-dedicated)"
# config-vars.json generated from RBB_SERVER_* -> valid JSON
# HTTP: no/wrong bearer -> 401; correct bearer -> reaches the agent
```

## Open points

* In `versus` mode, `SERVER_CONTROL_CONTAINER` must name the intended dedi
  (single agent controls one container name).
* The token comes from `.env` (local-first secret), not a vault.
