# tournament-server (compose service) — Issue #1093

Containerized twin of the former systemd unit `tournament-server`
(Issues #29/#30/#298). Built from the current
`tournament/` checkout (`cargo build --release --locked`); the static Web-UI is
served by the binary.

## Files

| File | Purpose |
|---|---|
| `Dockerfile` | Multi-stage: `rust:1-bookworm` builds the release binary; `debian:bookworm-slim` runs it + serves the Web-UI + `curl` HEALTHCHECK. |

**Build context is `./tournament`, not this directory** (the Dockerfile is
referenced relatively):

```yaml
build:
  context: ./tournament
  dockerfile: ../deploy/compose/tournament-server/Dockerfile
```

## Run contract

```yaml
tournament-server:
  build:
    context: ./tournament
    dockerfile: ../deploy/compose/tournament-server/Dockerfile
  image: rbb-tournament:${RBB_REF:-dev}
  restart: unless-stopped
  environment:
    TOURNAMENT_HOST: 0.0.0.0
    TOURNAMENT_PORT: "8080"
    TOURNAMENT_TOKEN: ${RBB_TOURNAMENT_TOKEN:?set in .env — cp .env.example .env}
    TOURNAMENT_AUTO_GO: "true"
    RBBRIDGE_A_URL: ${RBBRIDGE_A_URL:-http://dedicated:9001/exec}
    RBBRIDGE_B_URL: ${RBBRIDGE_B_URL:-}
    TOURNAMENT_WEB_DIR: /usr/share/tournament/web
    TOURNAMENT_DB_PATH: /data/rbbattle.db
    TOURNAMENT_ENV: ${RBB_ENV:-dev}
    TOURNAMENT_REF: ${RBB_REF:-dev}
    RUST_LOG: info
  ports:
    - "127.0.0.1:${RBB_TOURNAMENT_PORT:-8081}:8080"
  volumes:
    - ${RBB_HOST_ROOT:-/srv/rbbattle}/tournament:/data
```

* **Image / build context:** `context: ./tournament`,
  `dockerfile: ../deploy/compose/tournament-server/Dockerfile`.
* **Command:** `tournament-server` (the image `CMD`). No entrypoint wrapper —
  it is the upstream Env contract (`TOURNAMENT_*` / `RBBRIDGE_*_URL`).
* **Ports:** HTTP **8080** in-container, publish on **`127.0.0.1:<port>`**.
* **Volumes:** `${RBB_HOST_ROOT:-/srv/rbbattle}/tournament:/data`
  (host-root bind, #1112) for the SQLite match records (`TOURNAMENT_DB_PATH`).
  Not a named volume; the top-level `volumes:` only declares `rb-wine`,
  `rb-saves`, `rb-relay-wine`.
* **Host access:** none (no Docker socket). Talks to the bridges over HTTP
  (`RBBRIDGE_A_URL` / `B_URL`).
* **Python deps:** none (Rust). Runtime needs only `ca-certificates` + `curl`
  (healthcheck); `rusqlite` is statically bundled.

### Env (all optional except the token for mutating routes)

`TOURNAMENT_ENV` / `TOURNAMENT_REF` are surfaced in `GET /health`. Empty
`TOURNAMENT_TOKEN` = the mutating routes answer `401` (fail-closed, #298). Web-UI
default dir is overridden to the baked `/usr/share/tournament/web`.

## Validation (ran locally)

```bash
docker build -f deploy/compose/tournament-server/Dockerfile tournament    # OK (~46s on arm64)
# runtime: GET /health -> {"env":"dev","ok":true,"phase":"lobby","ref":"local"}
#          GET /state  -> JSON state;  GET / -> Web-UI (HTTP 200);  HEALTHCHECK: healthy
```

The build needs network (crates.io) and compiles the bundled SQLite — it is the
one image here that requires a real toolchain.

## Open points

* In `versus` mode point `RBBRIDGE_B_URL` at the second bridge
  (`http://dedicated-b:9001/exec`); `solo` leaves it empty.
* The Web-UI is served from the image; changes need an image rebuild.
