# warm (compose service) — Issue #1093

The **"warmed capsule" claim source** for the STATIC compose dedicated server
(`compose.yaml` → `dedicated`). The dedicated already runs continuously; this
slim service exposes it as **one** warm, parked capsule and implements the
**subset** of the Parked-service HTTP contract
([`deploy/parked/parked_service.py`](../../parked/parked_service.py), Issue #928)
that the capsule needs — **no provisioner, no pool**, one instance.

It is the drop-in claim/recycle target for
[`deploy/capsule/capsule_flow.py`](../../capsule/capsule_flow.py)
(`ParkedServiceClient` + `BridgeControl`): point `CAPSULE_PARKED_URL` at it and
the capsule's `open`/`finish` work against the static dedi.
Pure stdlib; it talks to the dedicated's HTTP bridge only — **no Docker**.

## Files

| File | Purpose |
|---|---|
| `warm_service.py` | The service (stdlib `ThreadingHTTPServer`): config, `BridgeClient`, `WarmController` (one static instance), HTTP `Handler`, `main`/`--check`. |
| `Dockerfile` | `python:3.12-slim`; copies `warm_service.py` to `/app`. Build context: **this directory**. |
| `test_warm_service.py` | Hermetic tests (FakeBridge, `127.0.0.1:0`). |

## Run contract

Paste-ready fragment for `compose.yaml` (the coordinator wires it — this file
does **not** edit `compose.yaml`):

```yaml
  # === Control: warm capsule claim source for the static dedi (#1093) ========
  warm:
    build:
      context: ./deploy/compose/warm
    image: rbb-warm:${RBB_REF:-dev}
    restart: unless-stopped
    depends_on:
      dedicated:
        condition: service_healthy
    logging: *default-logging
    labels:
      RBB_REF: *rbb-ref
    environment:
      WARM_BIND: "0.0.0.0"
      WARM_PORT: "9201"
      WARM_BRIDGE_URL: "http://dedicated:9001"
      # The GNS-UDP endpoint the RELAY dials (host side of the dedi's 6321/udp
      # publish). Reported back to the capsule as `gns_endpoint`.
      WARM_GNS_ENDPOINT: "127.0.0.1:${RBB_SERVER_PORT:-6322}"
      WARM_INSTANCE: "solo"
      WARM_ENV: ${RBB_ENV:-local}
      WARM_TOKEN: "${RBB_WARM_TOKEN:-}"
      WARM_PARK_ON_START: "true"
    ports:
      - "127.0.0.1:${RBB_WARM_PORT:-9201}:9201"
```

* **Image / build context:** `./deploy/compose/warm`.
* **Command:** `python3 -u /app/warm_service.py`. Add `command: ["python3",
  "/app/warm_service.py", "--check"]` to only validate config (rc `0`/`2`).
* **Ports:** HTTP **9201** in-container (bind `0.0.0.0`), publish on
  **`127.0.0.1:<port>`** only.
* **Volumes / host access:** none (stdlib only, no Docker socket).
* **Ordering:** `depends_on: dedicated: service_healthy` — `WARM_PARK_ON_START`
  calls `pause_game` at startup and **fails loud** (exit `3`, `restart` retries)
  if the bridge is not up yet.

### Wiring the capsule at it

Point the capsule-flow service's Parked target at this service (bearer = the
same `WARM_TOKEN`):

```yaml
      CAPSULE_PARKED_URL: "http://warm:9201"
      CAPSULE_PARKED_TOKEN: "${RBB_WARM_TOKEN:-}"
```

`open` → `POST /claim {"resume": false}` (world stays **paused**), `finish` →
`POST /recycle {"result"?:…}` (→ `round_reset` + `pause_game`, back to `PARKED`).
The claim response deliberately carries **no `cycle_url`**: the capsule falls
back to its own `CAPSULE_CYCLE_URL` (the compose `attack-cycle`), which is
correct for the single static dedi.

### Env (all overridable)

| Var | Default | Notes |
|---|---|---|
| `WARM_ENV` | `local` | env namespace (echoed by `/health`, `/status`, claim) |
| `WARM_BIND` | `0.0.0.0` | bind address (in-container) |
| `WARM_PORT` | `9201` | HTTP port (fail-closed: number `> 0`) |
| `WARM_BRIDGE_URL` | `http://dedicated:9001` | the static dedi's HTTP bridge |
| `WARM_GNS_ENDPOINT` | `127.0.0.1:6322` | GNS-UDP endpoint the **relay** dials (reported to the capsule) |
| `WARM_INSTANCE` | `solo` | identity of the single instance (returned as `instance`) |
| `WARM_TOKEN` | *(empty)* | Bearer; empty = open (local/dev) |
| `WARM_PARK_ON_START` | `true` | call bridge `pause_game` at startup to park the world |
| `WARM_LOG_LEVEL` | `INFO` | log level |

`WARM_PORT` is fail-closed (number `> 0`), `WARM_PARK_ON_START` is a boolean
(`1/true/yes/on`, `0/false/no/off`) — invalid values abort the start
(`WarmConfigError`, `--check` → rc `2`).

## HTTP API

Bind `0.0.0.0:$WARM_PORT` (Default 9201). `WARM_TOKEN` non-empty ⇒ **every**
request needs `Authorization: Bearer …` (else `401` + `WWW-Authenticate`).
Answers are always JSON; error format `{"ok":false,"reason":…,"detail":…}`.

| Method | Path | Body | Answer | Semantics |
|---|---|---|---|---|
| `GET` | `/health` | — | `200 {"ok":true,"env":…}` | service liveness |
| `GET` | `/status` | — | `200 {"instance","env","state","gns_endpoint","bridge_url"}` | current snapshot (`state` ∈ `parked`/`claimed`) |
| `POST` | `/claim` | `{"resume"?:bool}` | `200`/`400`/`409`/`503` | `PARKED` → `CLAIMED`; returns `{instance,env,bridge_url,gns_endpoint,state:"claimed",resumed}`. `resume=false` (capsule solo) leaves the world **paused**; `resume` non-bool → `400`. Not `PARKED` → `409 none_parked`; bridge unhealthy → `503 bridge_unhealthy`. |
| `POST` | `/recycle` | `{"result"?,"keep_warm"?}` | `200`/`400`/`409` | `CLAIMED` → bridge `round_reset` + `pause_game` → `PARKED`; with `result` (`win`/`lose`) also `end_game(result)`; `keep_warm` non-bool → `400`; not `CLAIMED` → `409 not_recyclable`. |

Unknown route → `404 not_found`; wrong method on a known route → `405
method_not_allowed`.

### State machine (single instance)

```
PARKED --claim(resume=?)--> CLAIMED
CLAIMED --recycle(round_reset + pause_game)--> PARKED
```

`resume=true` (default) resumes the world on claim; `resume=false` keeps it
paused (the capsule's solo path). There is **no** `WARMING`/`STOPPED`: the dedi
is static and always running — the only thing that changes is the world pause
flag.

### Error mapping (differences from the Parked service)

| Case | Answer |
|---|---|
| `claim` not `PARKED` | `409 none_parked` |
| `claim`/`recycle` with a foreign `instance_id` | `409 unknown_instance` |
| `recycle` not `CLAIMED` | `409 not_recyclable` |
| `recycle {"keep_warm": false}` | `409 cold_not_supported` — a **static** instance cannot be stopped (no provisioner); fail-loud instead of a silent no-op |
| bridge unhealthy (`claim`) / bridge error (`recycle`) | `503 bridge_unhealthy` / `409 not_recyclable` |
| missing/wrong bearer | `401 unauthorized` |

## Validation (ran locally)

```bash
python3 -m py_compile deploy/compose/warm/warm_service.py
python3 -m unittest deploy/compose/warm/test_warm_service.py -v
```

Proven by the tests: claim → `claimed` with `gns_endpoint` + `bridge_url`;
`resume=false` keeps the world paused (no `resume_game`); recycle → `parked`
with `round_reset` + `pause_game` (and `end_game` only with `result`);
wrong-phase → `409`; `keep_warm=false` → `409 cold_not_supported`; bearer
(`401`/`200`); fail-closed config.

## Open points

* **Not (yet) in `compose.yaml`** — the coordinator wires the fragment above and
  decides whether the capsule's `CAPSULE_PARKED_URL` points here.
* **`WARM_GNS_ENDPOINT` is a hint, not a lookup**: unlike `ParkedPool` (which
  reads the fresh host UDP port from `provisioner.status()`), a static dedi has
  a fixed publish, so the endpoint is configured once. If the dedi is recreated
  on a different host port, this value must follow it.
* **`keep_warm=false` is intentionally unsupported** (no stop path). If a cold
  recycle is ever needed, the coordinator must stop/restart the dedi itself.
* **`GET /health` also requires the bearer** in the current code (auth runs
  before routing), matching `parked_service.py` — a liveness probe must send it.
