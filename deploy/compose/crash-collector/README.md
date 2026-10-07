# crash-collector (compose service) — Issue #1093

Containerized twin of the systemd unit `rbmods-crash-collector`
(`deploy/roles/crash-collector`, Issues #462/#480/#481). Runs the **same**
`deploy/crash-collector/crash_collector.sh`; the image ships only the runtime.

## Files

| File | Purpose |
|---|---|
| `Dockerfile` | `ubuntu:24.04` + `python3` + `llvm-18` (`llvm-symbolizer`) + Docker CLI (copied from `docker:cli`). Build context: **this directory**. |
| `entrypoint.sh` | Exports the `RB_CRASH_*` defaults of the role (container paths) and execs `/opt/crash/crash_collector.sh "$@"`. Forwards `--once`. |

## Run contract

```yaml
crash-collector:
  build:
    context: ./deploy/compose/crash-collector
  image: rbb-crash-collector:${RBB_REF:-dev}
  restart: unless-stopped
  depends_on:
    dedicated:
      condition: service_started
  environment:
    RB_CRASH_CONTAINER: rbb-dedicated
    RB_CRASH_ENV: ${RBB_ENV:-dev}
    RB_CRASH_REF: ${RBB_REF:-dev}
  volumes:
    - /var/run/docker.sock:/var/run/docker.sock          # docker logs/cp/exec/inspect
    - ./deploy/crash-collector:/opt/crash:ro             # the scripts (1:1 with the role)
    - ${RBB_HOST_ROOT:-/srv/rbbattle}/game:/game:ro # DLL + PDB (PDB stays OUTSIDE the image)
    - ${RBB_HOST_ROOT:-/srv/rbbattle}/rbtools:/rbtools:ro # rbbridge.dll
    - ${RBB_HOST_ROOT:-/srv/rbbattle}/crashes:/crashes # bundle output
```

* **Image / build context:** `./deploy/compose/crash-collector`.
* **Command:** entrypoint → `bash /opt/crash/crash_collector.sh` (daemon). Add
  `command: ["--once"]` for a one-shot run.
* **Ports:** none.
* **Volumes:** see above. Host-root bind mounts under
  `${RBB_HOST_ROOT:-/srv/rbbattle}` (no named volume; the top-level `volumes:`
  only declares `rb-wine`, `rb-saves`, `rb-relay-wine`): `game` = content/PDB
  dir; `rbtools` = the rbtools-build output dir (`rbbridge.dll`); `crashes` =
  bundle output dir.
* **Host access:** the Docker socket (the collector observes the game container
  via `docker logs -f` and copies `crash_info/<uuid>.{dmp,log,trace}` via
  `docker cp`). No direct log/volume mount needed.
* **Python deps:** stdlib only (`minidump_meta.py`, `symbolize.py`); plus the
  `llvm-symbolizer` binary at `/usr/lib/llvm-18/bin/llvm-symbolizer`.

### Env (defaults set by the entrypoint; all overridable)

| Var | Default | Notes |
|---|---|---|
| `RB_CRASH_CONTAINER` | `rbb-dedicated` | observed container (compose `container_name`) |
| `RB_CRASH_ENV` | `dev` | bundle path segment `<env>/<ref>/<ts>-<uuid>` |
| `RB_CRASH_REF` | `${RBB_REF}` | build ref; falls back to image tag |
| `RB_CRASH_DIR` | `/crashes` | bundle destination (volume) |
| `RB_CRASH_CRASHINFO` | `/data/.wine/…/crash_info` | path **inside** the game container |
| `RB_CRASH_DLL` | `/game/bin/riftbreaker_dll_win_release.dll` | game DLL (`game` mount) |
| `RB_CRASH_PDB` | `/game/bin/riftbreaker_dll_win_release.pdb` | PDB (`game` mount, 252 MB — outside the image, #480) |
| `RB_CRASH_RBBRIDGE_DLL` | `/rbtools/rbbridge.dll` | injected DLL (`rbtools` mount) |
| `RB_CRASH_SYMBOLIZE` | `1` | master switch (#480) |
| `RB_CRASH_LLVM_SYMBOLIZER` | `/usr/lib/llvm-18/bin/llvm-symbolizer` | shipped in the image |
| `RB_CRASH_SYMBOLIZE_BIN` / `…_TOOL` / `RB_CRASH_MINIDUMP_PY` | `/opt/crash/…` | the mounted scripts |
| `RB_CRASH_RETENTION` / `…_CONTEXT_LINES` / `…_WAIT_SECS` / `…_PRUNE_WINE` | `20` / `200` / `30` / `1` | role defaults |

## Validation (ran locally)

```bash
docker build -f deploy/compose/crash-collector/Dockerfile deploy/compose/crash-collector   # OK
# runtime: docker --version 29.8.2, python3 3.12.3, /usr/lib/llvm-18/bin/llvm-symbolizer present
# guard without mount  -> rc=2 ("/opt/crash/crash_collector.sh fehlt")
# --once against a fake log stream -> detects "CRASH:" marker, exits rc=0
```

## Open points

* The observed container is fixed by `RB_CRASH_CONTAINER`; in `versus` mode
  (two dedis A/B) a per-dedi instance is needed — the coordinator picks the name.
* `--user`/socket permissions: the daemon's socket is mounted rw; the collector
  runs as root in-container (as on the host).
