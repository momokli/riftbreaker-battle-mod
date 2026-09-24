# deploy/provisioner — Dedi-Instanz on-demand starten/stoppen (Issue #908)

Kalter Pfad: Für **einen Spielwunsch** genau **eine** run-scoped
Dedicated-Server-Instanz starten und nach dem Spiel restfrei wieder stoppen —
Container + Netz + Named Volumes + run-scoped Verzeichnisse nach dem
Boot-Test-Muster. Nur Standardbibliothek, kein venv/pip.

## Abgrenzung

- **Nicht** Warm-Pool / „Parked" (eigene Issues **#909/#910**).
- **Nicht** Multi-Instanz-Orchestrierung / Proxy-Routing (**#290**).
- **Nicht** der Host-Agent [`deploy/server-control`](../server-control/) — der
  steuert einen **bestehenden** Container (Status/Logs/Restart/Config). Der
  Provisioner erzeugt und entfernt Container.
- Dieser PR implementiert bewusst nur den kalten Pfad `start`/`stop`/`status`.

## Namensschema (`InstanceSpec`)

Deterministisch aus `(env, instance_id)`; `instance_id` entspricht dem
run-scoped Suffix `rbbattle_run_suffix` (Boot-Test: `github.run_id`) und wird
gegen `^[A-Za-z0-9_.-]{1,40}$` validiert.

| Ressource | Muster | Beispiel (`env=test`, `instance_id=12345`) |
|---|---|---|
| Container | `riftbreaker-dedicated-<env>-<id>` | `riftbreaker-dedicated-test-12345` |
| Compose-Projekt | `rb-<env>-<id>` | `rb-test-12345` |
| Docker-Netz | `<projekt>_default` | `rb-test-12345_default` |
| Wine-Volume | `rb-<env>-wine-<id>` | `rb-test-wine-12345` |
| Saves-Volume | `rb-<env>-saves-<id>` | `rb-test-saves-12345` |
| Game-Dir | `<base_dir>/rift-<env>-<id>/game` | `/srv/rift-test-12345/game` |
| Backup-Dir | `<base_dir>/rift-<env>-<id>/backups` | `/srv/rift-test-12345/backups` |
| Sessions-Dir | `<base_dir>/rift-<env>-<id>/sessions` | `/srv/rift-test-12345/sessions` |
| Bridge-Host-Port | `<base> + (n(instance_id) % 20000)` | `42345` |
| Game-UDP-Port | ephemer, `127.0.0.1::6321/udp` | — |

Regel (hart): jeder Container-/Netz-/Volume-Name stammt **ausschließlich** aus
`InstanceSpec`; **nie** aus Nutzereingabe. `bridge_port` ist bei rein numerischer
`instance_id` exakt `base + int(id) % 20000`; nicht-numerische Suffixe (z. B.
`local`) bekommen einen stabilen Wert via `crc32`.

## Konfiguration (`PROVISIONER_*`)

Optionale JSON-Datei über `PROVISIONER_CONFIG` (Keys analog den Feldern unten);
Env überschreibt Dateiwerte.

| Variable | Default | Zweck |
|---|---|---|
| `PROVISIONER_IMAGE` | — (**Pflicht**) | Image-Ref (fail-closed ohne) |
| `PROVISIONER_ENV` | `test` | Env-Segment im Namensschema |
| `PROVISIONER_INSTANCE_ID` | `local` | Default-`instance_id` |
| `PROVISIONER_BASE_DIR` | `/srv` | Basis für run-scoped Pfade |
| `PROVISIONER_DOCKER` | `docker` | docker-Binary (in Tests: Fake) |
| `PROVISIONER_TIMEOUT` | `60` | docker-Aufruf-Timeout (s) |
| `PROVISIONER_HEALTH_DEADLINE` | `180` | Health-Poll-Deadline (s) |
| `PROVISIONER_HEALTH_INTERVAL` | `3` | Poll-Intervall (s) |
| `PROVISIONER_MIN_FREE_GB` | `10` | Disk-Schwelle (wie `riftbreaker_disk_min_free_gb`) |
| `PROVISIONER_BRIDGE_PORT_BASE` | `30000` | Basis Port-Ableitung (Host-Publish) |
| `PROVISIONER_BRIDGE_CONTAINER_PORT` | `9001` | Bridge-Port **im Container** (`RBB_BRIDGE_PORT`) |
| `PROVISIONER_CONFIG_CFG` | `/opt/rbmods/compose/rift-{env}/riftbreaker/config/config.cfg` | Host-`config.cfg` → `/data/config/config.cfg:ro` |
| `PROVISIONER_RBTOOLS_DIR` | `/opt/rbmods/rbtools/{env}` | Host-rbtools → `/opt/rbtools:ro` |
| `PROVISIONER_GAME_SOURCE` | `/srv/rift-{env}/game` | Host-Spielstand → `/opt/riftbreaker` |
| `PROVISIONER_IMAGE_BUILD_DIR` | — | Compose-Kontext (optional) |

`{env}` wird in `config_cfg`/`rbtools_dir`/`game_source` durch das Env-Segment
ersetzt (z. B. `/srv/rift-dev/game`). `bridge_container_port` wird auf
`1..65535` validiert, sonst `ConfigError`.

## Container-Layout (reales Image)

`_create_container` spiegelt das reale Compose-Layout
([`deploy/roles/riftbreaker-server`](../roles/riftbreaker-server/)):

| Aspekt | Wert |
|---|---|
| Host-Publish Bridge | `127.0.0.1:<spec.bridge_port>:<bridge_container_port>` (Container-Default `9001`) |
| Game-UDP | `127.0.0.1::6321/udp` |
| Game | `<game_source>:/opt/riftbreaker` |
| Wine | `<wine_volume>:/data/.wine` |
| Saves | `<saves_volume>:/data/saves` |
| Config | `<config_cfg>:/data/config/config.cfg:ro` |
| rbtools | `<rbtools_dir>:/opt/rbtools:ro` |
| Env | `RIFTBREAKER_MODE=<mode>`, `RBB_BRIDGE_BIND=0.0.0.0`, `RBB_BRIDGE_PORT=<bridge_container_port>`, `WINEESYNC=0`, `WINEFSYNC=0` |

Preflight prüft fail-loud VOR dem Container, dass `game_source` (Dir),
`config_cfg` (File) und `rbtools_dir` (Dir) existieren — sonst kein halber Start.

## API

`Provisioner(cfg, docker=None, spec_factory=InstanceSpec, health_probe=None, clock=None, sleep=None)`

- `start(env=None, mode="solo", instance_id=None) -> dict`
  `{instance, container, running, health, ports, created}`.
  **Idempotent**: läuft der Container schon, wird **kein** zweiter erzeugt
  (`created=False`); ein gestoppter vorhandener Container wird nur gestartet.
  **Preflight fail-loud VOR dem Container**: Bridge-Port frei, genug freier
  Platz, Image vorhanden. **Rollback** aller begonnenen Ressourcen
  (Container → Volumes → Netz → Verzeichnisse) bei jedem Fehler.
  **Health-Poll** auf `GET <bridge>/health` bis `{"ok": true}` innerhalb der
  Deadline; Timeout → Rollback + `ProvisionError`.
- `stop(instance_id=None, env=None) -> {instance, removed: {container, network,
  volumes, dirs}}` — idempotent; fehlende Ressourcen sind **kein** Fehler.
- `status(instance_id=None, env=None) -> {running, health, ports, container}`
  — fehlt der Container: `running=False`, `health="unreachable"`, kein Fehler.

CLI:

```sh
PROVISIONER_IMAGE=... python3 provisioner.py --check
PROVISIONER_IMAGE=... python3 provisioner.py start  --env test --instance-id "$RUN_ID"
PROVISIONER_IMAGE=... python3 provisioner.py status --env test --instance-id "$RUN_ID"
PROVISIONER_IMAGE=... python3 provisioner.py stop   --env test --instance-id "$RUN_ID"
```

Exit-Codes: `0` ok · `1` `ProvisionError`/`DockerError` · `2` `ConfigError`
(fail-closed).

## Test (hermetisch, ohne Docker/Netz/Spiel)

Fake-`docker`-Binary + lokaler HTTP-Health-Stub:

```sh
cd deploy/provisioner && python3 -m unittest test_provisioner -v
```

Abgedeckt ↔ DoD: start happy path (`healthy`, `created=True`), `docker run`-
Argumente gegen das reale Image-Layout (Host:Container `9001`, fünf Mounts inkl.
`:ro`, Bridge-/Sync-Env), start idempotent (kein zweiter `docker run`), stop
idempotent + Reste weg, Port belegt / Disk voll / Image fehlt / Quellen fehlen →
`ProvisionError` ohne Container, Health-Timeout → Rollback, Fehler in der Mitte →
Rollback der Vorstufen, status ohne/mit Container, `InstanceSpec`-Determinismus/
Validierung/`{env}`-Substitution, Config fail-closed (inkl. `bridge_container_port`).

## Sicherheits-/Robustheitsregeln

- `docker` immer als **Argumentliste** (`subprocess.run([...])`, kein
  `shell=True`).
- Namen ausschließlich aus `InstanceSpec`/Config, nie aus Nutzereingabe.
- `start` bricht **laut** ab statt halb zu starten; `stop` hinterlässt keine
  Reste.
- Löschen toleriert fehlende Ressourcen (wie `|| true` im Boot-Test-Cleanup).

## Live-Beweis gegen das reale Image (gefuehrt, #918)

Der **Live-Beweis** auf planet (echter `start` gegen das reale Image,
Bridge-`/health` → `ok:true` innerhalb der Deadline, realer `stop` ohne Reste)
ist umgebungs-/spielabhängig und **nicht** Teil der hermetischen Suite. Er wurde
im Rahmen von **#918** (Stage 5, 2026-09-24) gegen `rb-dedicated:cb69b20db7b2`
geführt und ist hier dokumentiert.

```bash
export PROVISIONER_IMAGE=rb-dedicated:cb69b20db7b2 \
       PROVISIONER_GAME_SOURCE=/srv/rift-dev/game \
       PROVISIONER_CONFIG_CFG=/opt/rbmods/compose/rift-dev/riftbreaker/config/config.cfg \
       PROVISIONER_RBTOOLS_DIR=/opt/rbmods/rbtools/dev \
       PROVISIONER_BRIDGE_PORT_BASE=31000 PROVISIONER_INSTANCE_ID=918live \
       PROVISIONER_HEALTH_DEADLINE=360 PROVISIONER_ENV=test
cd deploy/provisioner
python3 provisioner.py --check      # configuration OK ...
python3 provisioner.py start        # running=true, health=healthy, created=true
curl -sS http://127.0.0.1:48059/health   # {"ok":true,"pipe":true}
python3 provisioner.py status       # running=true, health=healthy
python3 provisioner.py stop         # removed: container/network/volumes/dirs=true
```

Beobachtetes Ergebnis (planet):

- `--check` → `configuration OK (env=test image=rb-dedicated:cb69b20db7b2 ... health_deadline=360.0s)`, EXIT 0.
- `start` → Container-Publish `127.0.0.1:48059:9001`, alle fünf realen Mounts
  (`/opt/riftbreaker`, `/data/.wine`, `/data/saves`, `/data/config/config.cfg:ro`,
  `/opt/rbtools:ro`), Env `RBB_BRIDGE_BIND=0.0.0.0`/`RBB_BRIDGE_PORT=9001` sowie
  `WINEESYNC=0`/`WINEFSYNC=0`; `running=true`, `health=healthy`, `created=true`.
- **Bootdauer ≈ 12 s** bis `healthy` (Deadline 360 s) — realer Cold-Boot.
- `curl http://127.0.0.1:48059/health` → `{"ok":true,"pipe":true}`.
- `stop` → `{container, network, volumes, dirs}` alle `true`; danach keine
  Reste: `docker ps -a`/`volume ls`/`network ls` ohne `918live`, Host-Port frei,
  kein `/srv/rift-test-918live`.

Der Live-Pfad ist damit als geführt dokumentiert (nicht mehr offen);
Reproduktion über obige Kommandos mit kollisionsfreier `bridge_port_base`.
