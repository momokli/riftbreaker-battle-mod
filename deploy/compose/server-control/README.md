# server-control (compose service) — Issue #1093

Containerized twin of the former systemd unit `server-control`
(Issue #424). Runs the **same**
`deploy/server-control/server_control.py` (pure stdlib): status/logs/restart/start/
stop + `config.cfg` rendering, via the Docker CLI on the host.

## Files

| File            | Purpose                                                                                                                                                                                                                                                              |
| --------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Dockerfile`    | `python:3.12-slim` + Docker CLI (copied from `docker:cli`). Build context: **`./deploy/server-control`** (the Dockerfile lives here).                                                                                                                                |
| `entrypoint.sh` | Lives in `deploy/server-control/entrypoint.sh` (inside the build context; **baked** into the image). Exports the `SERVER_CONTROL_*` defaults, optionally provisions `config-vars.json` from `RBB_SERVER_*`, execs `/app/server_control.py "$@"`. Forwards `--check`. |

## Run contract

```yaml
server-control:
  build:
    context: ./deploy/server-control
    dockerfile: ../compose/server-control/Dockerfile
  image: rbb-server-control:${RBB_REF:-dev}
  restart: unless-stopped
  depends_on:
    dedicated:
      condition: service_started
  environment:
    SERVER_CONTROL_TOKEN: ${RBB_SERVER_CONTROL_TOKEN:?set in .env — cp .env.example .env}
    SERVER_CONTROL_CONTAINER: rbb-dedicated
    SERVER_CONTROL_ENV: ${RBB_ENV:-dev}
    SERVER_CONTROL_REF: ${RBB_REF:-dev}
    SERVER_CONTROL_CONFIG_PATH: /config/config.cfg
    # config.cfg rendering (POST /server/config) — same values as config-init:
    RBB_SERVER_NAME: "${RBB_SERVER_NAME:-RBBattle}"
    RBB_SERVER_PASSWORD: "${RBB_SERVER_PASSWORD:?set in .env}"
    RBB_SERVER_RCON_PASSWORD: "${RBB_SERVER_RCON_PASSWORD:-}"
    RBB_SERVER_MAX_PLAYERS: "${RBB_SERVER_MAX_PLAYERS:-4}"
    RBB_SERVER_BROADCAST_ENABLED: "${RBB_SERVER_BROADCAST_ENABLED:-1}"
    RBB_SERVER_PAUSE_GAME_WHEN_EMPTY: "${RBB_SERVER_PAUSE_GAME_WHEN_EMPTY:-0}"
    RBB_SERVER_CAMPAIGN: "${RBB_SERVER_CAMPAIGN:-mp_survival/mp_survival}"
    RBB_SERVER_MISSION: "${RBB_SERVER_MISSION:-survival/jungle}"
    RBB_SERVER_DIFFICULTY: "${RBB_SERVER_DIFFICULTY:-coop_normal}"
  ports:
    - "127.0.0.1:${RBB_SERVER_CONTROL_PORT:-8092}:8092"
  volumes:
    - /var/run/docker.sock:/var/run/docker.sock
    - ${RBB_HOST_ROOT:-/srv/rbbattle}/config:/config # target config.cfg (shared with dedicated)
    - ./deploy/compose/config/config.cfg.j2:/etc/rbmods/server-control/config.cfg.j2:ro # config-render template
```

- **Image / build context:** `context: ./deploy/server-control`, `dockerfile: ../compose/server-control/Dockerfile`. The agent is **baked** into `/app/server_control.py`; `docker compose up --build` rebuilds it on code changes.
- **Command:** entrypoint → `python3 /app/server_control.py` (HTTP daemon). Add
  `command: ["--check"]` to only validate config.
- **Ports:** HTTP **8092** in-container, publish on **`127.0.0.1:<port>`** only.
  Inside the container the bind is `0.0.0.0` (so the published loopback is
  reachable); the agent keeps its Bearer guard regardless.
- **Volumes:** Docker socket + the host-root `config` dir (bind-mounted at
  `/config`, rw) + the optional Jinja template. The dedicated reads the same file
  at `/data/config/config.cfg`.
- **Host access:** the Docker socket (the agent wraps `docker`).
- **Python deps:** stdlib only. The narrow `{{ var }}`-fallback renders
  `config.cfg.j2` exactly (no Jinja2 needed — the template uses no `{% %}`).

### Fail-closed (mandatory)

Without a non-empty `SERVER_CONTROL_TOKEN` the service **does not start** (the
agent exits `2`; the entrypoint does not default the token). This mirrors the
role's assert (#424/#298).

### `config.cfg` rendering (`POST /server/config`)

- Target file: `SERVER_CONTROL_CONFIG_PATH` (default `/config/config.cfg`).
- Template: `SERVER_CONTROL_CONFIG_TEMPLATE` (default the mounted
  `config.cfg.j2`).
- Base values: `SERVER_CONTROL_CONFIG_VARS`. If no file is mounted, the
  entrypoint generates `/run/rbb-server-control/config-vars.json` from the
  `RBB_SERVER_*` variables (keys `riftbreaker_server_*`, same as
  `config-vars.json.j2`). If neither is present the endpoint returns
  `503 config_unavailable` (unchanged code path).

## Validation (ran locally)

```bash
docker build -f deploy/compose/server-control/Dockerfile deploy/server-control   # OK
# no token   -> rc=2 "SERVER_CONTROL_TOKEN fehlt"
# with token -> rc=0 "Konfiguration OK (bind=0.0.0.0 port=8092 container=rbb-dedicated)"
# config-vars.json generated from RBB_SERVER_* -> valid JSON
# HTTP: no/wrong bearer -> 401; correct bearer -> reaches the agent
```

## Open points

- In `versus` mode, `SERVER_CONTROL_CONTAINER` must name the intended dedi
  (single agent controls one container name).
- The token comes from `.env` (local-first secret), not a vault.
