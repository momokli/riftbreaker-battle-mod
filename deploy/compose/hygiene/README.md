# hygiene + ofelia (compose services) — Issue #1103

Containerized twin of the two systemd timers `rbmods-image-retention`
(`deploy/roles/image-retention`, Issue #309) and `rbmods-host-hygiene`
(`deploy/roles/host-hygiene`, Issues #308/#606). Runs the **same**
`deploy/image-retention/docker_image_tag_retention.sh` and
`deploy/host-hygiene/host_hygiene.sh`; the image ships only the runtime.

## Files

| File | Purpose |
|---|---|
| `Dockerfile` | `ubuntu:24.04` + Docker CLI (copied from `docker:cli`) + shell toolchain (`bash coreutils findutils grep sed`). Pure shell — no python, no llvm. Build context: **this directory**. **No entrypoint exists**: `CMD ["sleep", "infinity"]` keeps the container idle, Ofelia execs the jobs into it. |

## Run contract

```yaml
  hygiene:
    build:
      context: ./deploy/compose/hygiene
    image: rbb-hygiene:${RBB_REF:-dev}
    restart: unless-stopped
    logging: *default-logging
    environment:
      # image-retention (#309): Tag-Retention der Mod-Runtime-Images.
      RB_IMAGE_REPOS: "rb-dedicated rb-headless-client"
      RB_ROLLBACK_TAGS: "2"
      RB_PROTECTED_TAGS: "rb-dedicated:${RBB_REF:-dev}"
      # host-hygiene (#308/#606): dangling-Image-Prune + rbtools test-* Retention.
      RB_HYGIENE_IMAGE_REPOS: "rb-dedicated rb-headless-client"
      RB_HYGIENE_RBTOOLS_DIR: "/opt/rbmods/rbtools"
      RB_HYGIENE_RBTOOLS_RETENTION: "10"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - ${RBB_HOST_ROOT:-/srv/rbbattle}/rbtools:/opt/rbmods/rbtools
      - ./deploy/image-retention:/opt/rbmods/image-retention:ro
      - ./deploy/host-hygiene:/opt/rbmods/host-hygiene:ro
    labels:
      ofelia.enabled: "true"
      ofelia.job-exec.image-retention.schedule: "@daily"
      ofelia.job-exec.image-retention.command: "/opt/rbmods/image-retention/docker_image_tag_retention.sh"
      ofelia.job-exec.host-hygiene.schedule: "@weekly"
      ofelia.job-exec.host-hygiene.command: "/opt/rbmods/host-hygiene/host_hygiene.sh"

  ofelia:
    image: mcuadros/ofelia:0.3.22
    restart: unless-stopped
    depends_on:
      hygiene:
        condition: service_started
    logging: *default-logging
    environment:
      TZ: UTC
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    command: daemon --docker
```

* **Image / build context:** `./deploy/compose/hygiene`.
* **Command:** none in compose — the image default (`sleep infinity`) idles.
  Ofelia runs the jobs INSIDE the running container via `job-exec` (the
  `ofelia.job-exec.*` labels above are the source of truth).
* **Ports:** none.

### Env (set by compose on `hygiene`; all overridable)

| Var | Value | Notes |
|---|---|---|
| `RB_IMAGE_REPOS` | `rb-dedicated rb-headless-client` | image-retention (#309): Repos whose tags are pruned |
| `RB_ROLLBACK_TAGS` | `2` | image-retention: kept rollback tags per repo |
| `RB_PROTECTED_TAGS` | `rb-dedicated:${RBB_REF}` | image-retention: never-removed `repo:tag` — the current deploy tag; inherited for free via `job-exec` |
| `RB_HYGIENE_IMAGE_REPOS` | `rb-dedicated rb-headless-client` | host-hygiene (#308): repos counted in the before/after proof |
| `RB_HYGIENE_RBTOOLS_DIR` | `/opt/rbmods/rbtools` | host-hygiene (#606): base of the rbtools `test-*` env dirs |
| `RB_HYGIENE_RBTOOLS_RETENTION` | `10` | host-hygiene: kept `test-*` dirs (mtime) |

`RB_HYGIENE_DRY_RUN` (`1` = show only) is not set by compose — pass it for
manual dry-runs (see runbook).

## Volumes

* `/var/run/docker.sock:/var/run/docker.sock` (**rw**) — both scripts talk to
  the host daemon: `docker image ls/rmi` (retention) and `docker image prune -f`
  + `docker ps -a` guards (hygiene).
* `${RBB_HOST_ROOT:-/srv/rbbattle}/rbtools:/opt/rbmods/rbtools` (**rw**) —
  host-hygiene removes stale `test-*` dirs here.
* `./deploy/image-retention:/opt/rbmods/image-retention:ro` and
  `./deploy/host-hygiene:/opt/rbmods/host-hygiene:ro` (**ro**) — the scripts
  (1:1 with the Ansible roles), mounted, never baked into the image.
* `ofelia`: the socket is mounted **`:ro`** — sufficient for `job-exec` (per
  upstream docs of Ofelia 0.3.x).

## Manual run / runbook

```bash
# image-retention (#309) — dry-run, no change:
docker compose exec hygiene /opt/rbmods/image-retention/docker_image_tag_retention.sh --dry-run

# host-hygiene (#308/#606) — dry-run via env:
docker compose exec -e RB_HYGIENE_DRY_RUN=1 hygiene /opt/rbmods/host-hygiene/host_hygiene.sh
```

Ofelia output (job stdout + exit codes) lands in `docker compose logs ofelia`.

## Validation (ran locally)

```bash
docker build -f deploy/compose/hygiene/Dockerfile deploy/compose/hygiene        # OK
docker run --rm rbb-hygiene:dev docker --version                                # OK
# the container idles by default: `CMD ["sleep", "infinity"]`, no entrypoint
```

## Caveats

* **Ofelia label discovery is NOT realtime** (Ofelia reads labels at startup),
  so `ofelia` must start AFTER `hygiene` — wired via compose
  `depends_on: hygiene: condition: service_started`.
* **Schedules use robfig/cron descriptors** `@daily` / `@weekly` to avoid the
  **6-field (seconds-first)** cron format that Ofelia 0.3.x requires for numeric
  specs (e.g. `0 0 3 * * *`) — a 5-field spec is interpreted with an added
  seconds field, hence the descriptors.
* The **ofelia socket is mounted `:ro`** — sufficient for `job-exec` (the
  scheduler only needs the daemon to inspect/exec; it does not create
  containers), per upstream docs.
* **Docker-Image-Hygiene ist host-weit**: `docker image prune -f` (without
  `-a`) affects dangling layers of all stacks on the host; that is intended
  (#308). The script's invariant proof guards that tagged mod images never
  change.
