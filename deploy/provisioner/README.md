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
| Staged config.cfg | `<base_dir>/rift-<env>-<id>/config/config.cfg` | `/srv/rift-test-12345/config/config.cfg` |
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
| `PROVISIONER_CONFIG_CFG` | `/opt/rbmods/compose/rift-{env}/riftbreaker/config/config.cfg` | **Quelle** für die je Instanz abgeleitete `config.cfg` (Diff: nur die `server_name`-Zeile) |
| `PROVISIONER_SERVER_NAME_SUFFIX` | `-{env}-{instance_id}` | Additiver Suffix am `server_name` der je Instanz abgeleiteten `config.cfg` (s. „Instanz-eigene config.cfg“) |
| `PROVISIONER_RBTOOLS_DIR` | `/opt/rbmods/rbtools/{env}` | Host-rbtools → `/opt/rbtools:ro` |
| `PROVISIONER_GAME_SOURCE` | `/srv/rift-{env}/game` | Host-Spielstand → `/opt/riftbreaker` |
| `PROVISIONER_PERSONAS_FILE` | `deploy/attack-cycle/personas.example.json` | **Quelle** der Persona-Defs für `mode=solo_persona:<name>` (#993); auch JSON-Key `personas_file` |
| `PROVISIONER_IMAGE_BUILD_DIR` | — | Compose-Kontext (optional) |
| `PROVISIONER_DEPLOY_REF` | `unknown` | Deploy-Identität → `RBB_REF` im Container (Compose-Parität `rift_deploy_ref`) |

`deploy_ref` ist auch als JSON-Key `deploy_ref` in der `PROVISIONER_CONFIG`-Datei
zulässig (Env `PROVISIONER_DEPLOY_REF` überschreibt die Datei, wie bei allen
Feldern). `${env}` wird in `config_cfg`/`rbtools_dir`/`game_source` durch das Env-Segment
ersetzt (z. B. `/srv/rift-dev/game`). `bridge_container_port` wird auf
`1..65535` validiert, sonst `ConfigError`. `server_name_suffix` darf nur
`[A-Za-z0-9_.-{}]` enthalten (kein `"`/CR/LF) — sonst `ConfigError`,
leerer Wert = bewusstes Opt-out (#970).

## Container-Layout (reales Image)

`_create_container` spiegelt das reale Compose-Layout
(siehe `deploy/compose/README.md`):

| Aspekt | Wert |
|---|---|
| Host-Publish Bridge | `127.0.0.1:<spec.bridge_port>:<bridge_container_port>` (Container-Default `9001`) |
| Game-UDP | `127.0.0.1::6321/udp` (ephemerer Host-Port; als `ports["gns"]` surface, s. u., Issue #929) |
| Game | `<game_source>:/opt/riftbreaker` |
| Wine | `<wine_volume>:/data/.wine` |
| Saves | `<saves_volume>:/data/saves` |
| Config | `<run_root>/config/config.cfg` (je Instanz; `server_name` + `<suffix>`):`/data/config/config.cfg:ro` |
| rbtools | `<rbtools_dir>:/opt/rbtools:ro` |
| Restart-Policy | `--restart unless-stopped` |
| Log-Rotation | `json-file`, `--log-opt max-size=10m --log-opt max-file=3` |
| Locale | `LC_ALL=C.UTF-8`, `LANG=C.UTF-8` |
| Env | `RIFTBREAKER_MODE=<kanonischer mode>`, `RBB_BRIDGE_BIND=0.0.0.0`, `RBB_BRIDGE_PORT=<bridge_container_port>`, `WINEESYNC=0`, `WINEFSYNC=0`, `RBB_ENV=<env>`, `RBB_REF=<deploy_ref>` |

**Compose-Parität (Contract, #968):** `_create_container` verdrahtet dieselben
Härtungswerte wie das Compose-Layout der [Rolle
`riftbreaker-server`](../roles/riftbreaker-server/templates/docker-compose.yml.j2)
— `restart: unless-stopped`, `logging` (`json-file`/`max-size`/`max-file`),
`LC_ALL`/`LANG=C.UTF-8` sowie `RBB_ENV`/`RBB_REF`. So haben geparkte/provisionierte
Instanzen denselben Standard wie der dedizierte Server. `RBB_ENV` folgt dem
Env-Segment der Instanz; `RBB_REF` kommt aus `deploy_ref` (Default `unknown`,
analog zum Rollen-Fallback `rift_deploy_ref`). `--restart`/`--log-opt` gelten
nur beim Erzeugen — ein bereits laufender Container wird nur gestartet und
NICHT nachträglich migriert.

Preflight prüft fail-loud VOR dem Container, dass `game_source` (Dir),
`config_cfg` (File, inkl. vorhandener `server_name`-Zeile) und `rbtools_dir`
(Dir) existieren — sonst kein halber Start.

## Instanz-eigene `config.cfg` (#970)

Jede provisionierte/parked Instanz bekommt eine **eigene** `config.cfg` in
`<run_root>/config/config.cfg` (Attribut `config_cfg_staged`), damit zwei
Instanzen derselben Env nicht dieselbe Datei teilen:

1. **Quelle** = `PROVISIONER_CONFIG_CFG` (geteilte Env-Datei, `spec.config_cfg`).
2. **Staging** in `_stage_config` direkt nach `_create_dirs`, VOR dem ersten
   Container: Basisname aus der ersten `set server_name "..."`-Zeile lesen,
   `server_name` = `<Basisname>` + Suffix setzen, Rest **byte-identisch** kopieren.
3. **Schreiben** atomar (`*.tmp` → `os.replace`), Modus **`0644`**; die Quelle
   bleibt unberührt.
4. **Mount**: `_create_container` mountet `config_cfg_staged` (nicht mehr die
   geteilte Env-Datei) nach `/data/config/config.cfg:ro`.

Suffix-Präzedenz: `server_name_suffix` (Env `PROVISIONER_SERVER_NAME_SUFFIX` /
JSON-Key) → Default `-{env}-{instance_id}` (Beispiel: `RBBattle-dev-parked-1`).
Ein **leerer** Wert ist das Opt-out (identischer Name); Env-Leerstring wird
bewusst übernommen. Der Basisname wird aus der Quelle gelesen — kein Hardcode
`RBBattle`.

Fail-loud (#970): fehlt die Quelle oder deren `server_name`-Zeile, bricht
`start` **vor dem ersten Container** ab (`ProvisionError`, `run`-Calls = 0).
**Secret-Hygiene:** das Server-Passwort erscheint nie in Logs/`to_dict()`/Status
— geloggt werden nur Pfad + Byte-Größe. **Routing bleibt unberührt:** `server_name`
ist reiner Anzeigename; GNS-Relay/Ports pinnen `ip:port` (#929).

## Modus (`mode`, #993)

Der Claim/Provision gibt einen **Modus** mit, aus dem der Provisioner die
Self-Send-/Persona-Konfiguration der Instanz ableitet:

```
mode ::= "solo_self"              # Default — Spieler gegen sich selbst (self-send)
       | "solo_persona:" <name>   # Solo gegen Persona <name>
```

`name` ist nicht leer und matcht `^[A-Za-z0-9_.-]{1,63}$`. **Kein stiller
Fallback:** alles andere (leer, `solo`, `campaign`, `solo_persona:`,
`solo_persona:a:b`, ungültiger Name) → `ModeError` (Subklasse von
`ProvisionError`) **vor dem ersten Docker-Call**; CLI-Exit `1` mit
`{"ok": false, "error": ...}`.

| mode | `RIFTBREAKER_MODE` (Container) | `game_config` (Bridge) | Persona-Runtime | Attack-Cycle CLI |
|---|---|---|---|---|
| `solo_self` | `solo_self` | `{"send_yourself": true, "persona": false}` | `POST /persona_active {"name":""}` | `--send-yourself on`, kein `--persona` |
| `solo_persona:<n>` | `solo_persona:<n>` | `{"send_yourself": false, "persona": true}` | `POST /personas <doc>` + `POST /persona_active {"name":"<n>"}` | `--send-yourself off --persona <n> --persona-file /data/personas.json` |

**Bridge-Seeding ist die Runtime-Wahrheit:** `send_yourself`/`persona` (#851)
leben ausschließlich in `game_config` (kein Entrypoint-Env wird konsumiert).
Nach der Health und **vor** den Sidecars liest `_seed_bridge` `GET /game_config`,
merged `send_yourself`/`persona`/`mode` (übrige Keys wie `warmup_s` bleiben
erhalten) und `POST`et das Ergebnis; im Persona-Modus zusätzlich `POST /personas`
+ `POST /persona_active`. Ein Fehler → `ProvisionError` + Rollback (kein halb
konfigurierter Stack). Der Attack-Cycle-`--mode solo|vs` ist eine **andere**
Achse und bleibt unberührt.

**Persona-Datei:** im Persona-Modus wird `PROVISIONER_PERSONAS_FILE`
(Format `{"personas": {"<name>": [[9 counts] x n]}}`) nach
`<run_root>/config/personas.json` gestaged (atomar, `0644`) und als
`/data/personas.json:ro` in den Attack-Cycle gemountet. Preflight prüft
fail-loud **vor dem Container**, dass die Datei existiert, parsebar ist und den
Namen enthält. `solo_self` liest die Datei **nicht**.

## API

`Provisioner(cfg, docker=None, spec_factory=InstanceSpec, health_probe=None, clock=None, sleep=None, bridge_url=None)`

- `start(env=None, mode="solo_self", instance_id=None) -> dict`
  `{instance, container, running, health, ports, created}`.
  **Idempotent**: läuft der Container schon, wird **kein** zweiter erzeugt
  (`created=False`); ein gestoppter vorhandener Container wird nur gestartet.
  **Kein stilles Umschalten:** weicht der angeforderte `mode` vom
  `RIFTBREAKER_MODE`-Env des vorhandenen Containers ab (`docker inspect`), bricht
  `start` fail-loud als `ProvisionError` ab — ein laufender `solo_self` wird also
  nicht stillschweigend zu `solo_persona:<n>` (oder umgekehrt); erst `stop`, dann
  neu provisionieren. Fehlt `RIFTBREAKER_MODE` ganz (legacy/extern erzeugter
  Container), wird nicht verglichen und die Idempotenz bleibt erhalten.
  **Preflight fail-loud VOR dem Container**: Bridge-Port frei, genug freier
  Platz, Image vorhanden. **Rollback** aller begonnenen Ressourcen
  (Container → Volumes → Netz → Verzeichnisse) bei jedem Fehler.
  **Health-Poll** auf `GET <bridge>/health` bis `{"ok": true}` innerhalb der
  Deadline; Timeout → Rollback + `ProvisionError`.
- `stop(instance_id=None, env=None) -> {instance, removed: {container, network,
  volumes, dirs}}` — idempotent; fehlende Ressourcen sind **kein** Fehler.
- `status(instance_id=None, env=None) -> {running, health, ports, container}`
  — fehlt der Container: `running=False`, `health="unreachable"`, kein Fehler.
- `list_instances(env=None) -> [{container, env, instance, status, running,
  started_at}]` (#969) — alle Container der EIGENEN `env` (Label
  `rb.provisioner.env`; `env`-Default `cfg.env`). Grundlage ist
  `DockerCli.ps_all("rb.provisioner.env=<env>")` + `inspect_optional` je Name;
  `instance` stammt aus dem Label `rb.provisioner.instance` (Fallback:
  Dedi-Namens-Suffix), `status` = `State.Status`. Fremde/fehlende Env-Labels
  werden defensiv gefiltert; ein fehlgeschlagenes `inspect` (Container zwischen
  `ps` und `inspect` weg) wird uebersprungen — kein Crash. Ein fehlgeschlagenes
  `docker ps` wird dagegen **laut** als `DockerError` propagiert (#969 B2), damit
  ein Aufrufer „nichts laeuft" nicht mit „Discovery kaputt" verwechselt
  (`DockerCli.ps_all` wirft jetzt statt `[]`). Discovery-Fundament fuer
  `ParkedPool.reconcile()` (#969).

  `ports` ist `{bridge, gns, docker}` (Issue #929): `bridge` = der Bridge-Host-Port,
  `gns` = der **GNS-UDP-Host:Port** der Instanz aus dem `6321/udp`-Mapping
  (`"127.0.0.1:32768"`, `null` wenn kein UDP-Mapping vorhanden — kein Crash),
  `docker` = das vollständige `docker port`-Dict. `start(...)` liefert dieselbe
  `ports`-Form.

CLI:

```sh
PROVISIONER_IMAGE=... python3 provisioner.py --check
PROVISIONER_IMAGE=... python3 provisioner.py start  --env test --instance-id "$RUN_ID" --mode solo_self
PROVISIONER_IMAGE=... python3 provisioner.py start  --env test --instance-id "$RUN_ID" --mode solo_persona:aggro --personas-file /path/personas.json
PROVISIONER_IMAGE=... python3 provisioner.py status --env test --instance-id "$RUN_ID"
PROVISIONER_IMAGE=... python3 provisioner.py stop   --env test --instance-id "$RUN_ID"
```

Exit-Codes: `0` ok · `1` `ProvisionError`/`DockerError` · `2` `ConfigError`
(fail-closed).

## Idempotenz (#1026)

Wiederholter `start()` ist harmlos (DoD 2): existiert der Dedi-Container schon,
wird **kein zweiter** erzeugt (`created=False`); ein gestoppter vorhandener
Container wird nur gestartet. Seit **#1026** re-assertiert der Existing-Pfad
zusätzlich die **vier Sidecars** (`_ensure_sidecars`): **laufende** bleiben
unangetastet (kein zweites `docker run`, kein Duplikat), **gestoppte** werden per
`docker start <name>` reaktiviert, **fehlende** aus den Env-Werten des
bestehenden Containers (`RIFTBREAKER_MODE` → `parse_mode`, `RBB_VS_WORLD` →
`world`) über denselben Args-Builder nachgezogen. Schlägt die Rekonstruktion fehl
→ `ProvisionError` (fail-loud, kein stiller Halb-Stack). Legacy-Container ohne
`RIFTBREAKER_MODE` behalten die Skip-Logik (kein harter Bruch; ein vorhandener
gestoppter Sidecar wird trotzdem gestartet). Die Sidecar-`docker run`-Argumente
stammen aus EINER Quelle (`_sidecar_run_args`) für Erzeugung UND Re-Assert — kein
Drift zwischen Erststart und Wiederherstellung. Die `docker run`-Zahl eines
repeated `start()` steigt damit nicht.

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
Validierung/`{env}`-Substitution, Config fail-closed (inkl. `bridge_container_port`),
`list_instances` (#969: eigene Env inkl. `instance`/`status`/`running`/`started_at`,
Fremd-Env gefiltert, fehlendes `inspect` uebersprungen, Label-Fallback aus dem
Dedi-Namen, `ps`-Label-Filter).

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
