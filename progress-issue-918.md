# Progress — Issue #918

**Issue:** [Bug] Provisioner (#908) Live-Pfad: Container-Port 8080 + falsche Mounts → Health-Timeout gegen reales Image
**Repo:** momokli/riftbreaker-battle-mod · **Basis:** main · **PR-Ziel:** main
**Branch:** fix/918-provisioner-real-image
**Fokus-Milestone:** 1.0.3

## Ist-Stand (Recon durch Orchestrator, 2026-09-24)

Kein gemergter PR für #918 (gh pr list --search 918 → nur #917/#920/#923, alle unabhängig).
Kein Branch/PR exists. → **Normale Pipeline.**

### Root Cause (belegt, aus Issue)
`deploy/provisioner/provisioner.py::Provisioner._create_container` (~Zeile 574) baut den
Container mit einem Layout, das nicht zum realen Image passt:

| Provisioner (#908) | reales Compose (`deploy/roles/riftbreaker-server`) |
|---|---|
| `-p 127.0.0.1:<host>:8080` | Bridge im Container auf **9001** (`RBB_BRIDGE_PORT=9001`) |
| `-v <game_dir>:/srv/game` | `-v <game_dir>:/opt/riftbreaker` |
| `-v <wine>:/wine` | `-v <wine_volume>:/data/.wine` |
| `-v <saves>:/saves` | `-v <saves_volume>:/data/saves` |
| (fehlt) | `-v <config.cfg>:/data/config/config.cfg:ro` |
| (fehlt) | `-v <rbtools>:/opt/rbtools:ro` |
| `-e RIFTBREAKER_MODE=<mode>` | zusätzlich `RBB_BRIDGE_BIND=0.0.0.0`, `RBB_BRIDGE_PORT=9001` |

### Beleg-Stellen im Repo
- `deploy/provisioner/provisioner.py` — `_create_container` (Mounts/Ports), `Config`/`load_config`
  (fehlende Felder für config.cfg + rbtools), `InstanceSpec` (health_url, Pfade).
- `deploy/provisioner/test_provisioner.py` — kodiert die FALSCHEN Erwartungen
  (`-p 127.0.0.1:%d:8080` Z. 497/508, `"8080/tcp"` Z. 564, Fake-Docker `8080/tcp` Z. 88).
- `deploy/parked/measure_boot.py` — Live-Harness `#909`, nutzt Provisioner-`start()`.
- `deploy/provisioner/README.md` — Abschnitt „Offener Punkt" (Live-Beweis ungeprüft).
- `deploy/roles/riftbreaker-server/templates/docker-compose.yml.j2` + `defaults/main.yml`
  — reale Mounts/Ports (Soll-Referenz).

### Reale Umgebung (planet) für Integrationsnachweis
- Image: `rb-dedicated:cb69b20db7b2` (= main HEAD cb69b20) vorhanden, weitere Tags je Commit.
- Reale Quellen für Mounts (dev): game `/srv/rift-dev/game`,
  config `/opt/rbmods/compose/rift-dev/riftbreaker/config/config.cfg`,
  rbtools `/opt/rbmods/rbtools/dev`, rbtools-Dir-Override in host_vars.
- Laufende Container: `riftbreaker-dedicated` (prod auf 9001), `-staging` (9003),
  `-880` (9004), `-prod` (9002) — Kollisionsgefahr bei Port-Wahl beachten.

### Soll (Issue)
1. `Provisioner.start` läuft gegen das reale Image: Container-Port 9001 publizieren,
   Deploy-Mounts nutzen (`/opt/riftbreaker`, `/data/.wine`, `/data/saves`,
   `/data/config/config.cfg:ro`, `/opt/rbtools:ro`); fehlende Quellen (config, rbtools)
   als Config-Felder aufnehmen.
2. Mindestens ein Integrationsnachweis gegen das reale Image (echter `start()` →
   `/health {"ok":true}` innerhalb Deadline, `stop()` ohne Reste).

### PR-Konvention
PR-Body MUSS `Closes #918` enthalten.

## Stage-Log

| Stage | Status | Notiz |
|---|---|---|
| 1 planner | done | Plan unten |
| 2 setup | pending | |
| 3 developer | pending | |
| 4 verifier | pending | |
| 5 tester | pending | |
| 6 developer (PR) | done | PR #932 |
| 7 reviewer | done | APPROVE (CI-Boot-Checks noch pending) |

## Plan (Stage 1)

### Betroffene Dateien

- `deploy/provisioner/provisioner.py` — `Config` (+`_JSON_KEYS`/`_ENV_KEYS`/`_INT_FIELDS`),
  `InstanceSpec` (+`to_dict`), `_create_container` (+ggf. `_preflight`-Validierung der Quellen).
- `deploy/provisioner/test_provisioner.py` — Fake-Docker-Port-String, kodierte
  `8080`-Erwartungen, neue Argument-/Config-Tests.
- `deploy/provisioner/README.md` — Config-Tabelle, Namensschema, „Offener Punkt".
- `deploy/parked/measure_boot.py` — **keine Code-Änderung**; dient als Live-Harness
  (nutzt `Provisioner.start()` + `stop()`, bereits restfrei via `cleanup`).
- `progress-issue-918.md` — dieser Plan + Live-Beweis.

### Soll-Layout (Referenz, aus `docker-compose.yml.j2`)

| Aspekt | Wert |
|---|---|
| Host-Publish | `127.0.0.1:<spec.bridge_port>:9001` (Container immer 9001) |
| Game-UDP | `127.0.0.1::6321/udp` (unverändert) |
| Mounts | `<game_source>:/opt/riftbreaker`, `<wine_vol>:/data/.wine`, `<saves_vol>:/data/saves`, `<config.cfg>:/data/config/config.cfg:ro`, `<rbtools_dir>:/opt/rbtools:ro` |
| Env | `RIFTBREAKER_MODE=<mode>`, `RBB_BRIDGE_BIND=0.0.0.0`, `RBB_BRIDGE_PORT=9001` |
| Health (Host) | `http://127.0.0.1:<spec.bridge_port>/health` (bleibt) |

### User Stories (in Reihenfolge)

1. **US1 — Config + InstanceSpec um die fehlenden realen Quellen erweitern**
   - Neue `Config`-Felder mit plausiblen Defaults (jeweils JSON-Key + `PROVISIONER_*`):
     - `bridge_container_port: int = 9001` (JSON `bridge_container_port`,
       `PROVISIONER_BRIDGE_CONTAINER_PORT`; in `_INT_FIELDS`).
     - `config_cfg: str = "/opt/rbmods/compose/rift-{env}/riftbreaker/config/config.cfg"`
       (JSON `config_cfg`, `PROVISIONER_CONFIG_CFG`).
     - `rbtools_dir: str = "/opt/rbmods/rbtools/{env}"` (JSON `rbtools_dir`,
       `PROVISIONER_RBTOOLS_DIR`).
     - `game_source: str = "/srv/rift-{env}/game"` (JSON `game_source`,
       `PROVISIONER_GAME_SOURCE`) — **nötig**, siehe Risiko R1: ein leerer
       run-scoped `game_dir` als `/opt/riftbreaker` kann nicht booten.
   - `InstanceSpec` löst `{env}`-Platzhalter auf und exponiert
     `spec.bridge_container_port`, `spec.config_cfg`, `spec.rbtools_dir`,
     `spec.game_source`; `to_dict()` nimmt die neuen Felder auf.
   - `health_url()` bleibt `http://127.0.0.1:<bridge_port>/health`.
   - Akzeptanzkriterien: Defaults = obige Werte; Env/JSON überschreiben
     (Env > Datei); `{env}` wird durch `spec.env` ersetzt (z. B.
     `.../rift-dev/...`); ungültige `bridge_container_port` → `ConfigError`;
     `InstanceSpec` bleibt rein rechnend/deterministisch.
   - Betroffen: `provisioner.py` (Config/InstanceSpec).

2. **US2 — `_create_container` auf das reale This-Image-Layout umstellen**
   - Port: `-p 127.0.0.1:<spec.bridge_port>:<spec.bridge_container_port>`
     (Host bleibt `bridge_port`, Container 9001); `-p 127.0.0.1::6321/udp` bleibt.
   - Mounts exakt: `<spec.game_source>:/opt/riftbreaker`,
     `<spec.wine_volume>:/data/.wine`, `<spec.saves_volume>:/data/saves`,
     `<spec.config_cfg>:/data/config/config.cfg:ro`,
     `<spec.rbtools_dir>:/opt/rbtools:ro`.
   - Env: `RIFTBREAKER_MODE=<mode>` (bleibt) plus
     `RBB_BRIDGE_BIND=0.0.0.0`, `RBB_BRIDGE_PORT=<spec.bridge_container_port>`.
   - Labels/`--network`/`--name`/Image als letztes Argument unverändert.
   - Optional (Risiko R2, Empfehlung): `WINEESYNC=0`, `WINEFSYNC=0` aus dem
     Compose-Rezept mitgeben (sonst Crash vor `bind()`).
   - Akzeptanzkriterien: `docker run`-Argliste enthält genau die fünf Mounts
     und die drei Env-Paare; Container-Port ist 9001, Host-Port `bridge_port`;
     keine Regression bei Idempotenz/Rollback.
   - Betroffen: `provisioner.py` (`_create_container`).

3. **US3 — Hermetische Tests auf das reale Layout umstellen + Argument-Test**
   - Fake-Docker: `print("8080/tcp -> 127.0.0.1:%d" ...)` → `"9001/tcp"`.
   - Erwartungen in `StartTestCase` mit hartkodiertem `:8080` (`-p`-Setup in
     `test_idempotent_start_with_unhealthy_existing_fails_loud`,
     `test_existing_stopped_container_is_restarted_not_recreated`) → `:9001`.
   - `test_status_with_running_container`: `assertIn("8080/tcp", ...)` →
     `"9001/tcp"`.
   - **Neuer Test** (DoD): `run`-Aufruf enthält exakt
     `-p 127.0.0.1:<bridge_port>:9001`, alle fünf `-v`-Mounts (inkl. `:ro`) und
     `RBB_BRIDGE_BIND=0.0.0.0` / `RBB_BRIDGE_PORT=9001`.
   - Neue Tests für US1 (Defaults/Override/`{env}`-Substitution/`ConfigError`).
   - Akzeptanzkriterien: `cd deploy/provisioner && python3 -m unittest
     test_provisioner -v` grün; kein `8080` mehr in `test_provisioner.py`.
   - Betroffen: `test_provisioner.py`.

4. **US4 — Live-Beweis auf planet + README**
   - Ablauf: `PROVISIONER_*` (Image `rb-dedicated:<sha>`, `game_source`,
     `config_cfg`, `rbtools_dir`, eindeutiger `bridge_port_base` ohne Kollision
     mit 9001–9004) setzen; echter `start()`; `/health` → `{"ok":true}`
     innerhalb der Deadline; `stop()` → keine Reste.
   - Harness: `python3 deploy/parked/measure_boot.py --json` (nutzt
     `Provisioner.start()`/`stop()` inkl. `cleanup`) **oder** direkter CLI-Aufruf
     `provisioner.py start|status|stop`.
   - Nachweis: JSON-Ergebnis + `docker ps -a`/`docker inspect` (kein Container),
     Volume-/Netz-Abwesenheit, Host-Port wieder frei.
   - README: Abschnitt „Offener Punkt" durch dokumentierten, ausgeführten
     Live-Beweis (Befehl + beobachtete Ausgabe) ersetzen/ergänzen.
   - Akzeptanzkriterien: reproduzierbare Kommandos + beobachtetes
     `/health`-`ok` + restfreier `stop()` dokumentiert; README konsistent.
   - Betroffen: `README.md`, `progress-issue-918.md`.

### Teststrategie

- **Hermetisch (Primär-Gate):** `unittest`-Suite wie oben; Fake-Docker +
  HTTP-Health-Stub; deckt Layout, Idempotenz, Rollback, Config.
- **Negativ:** Port belegt / Disk voll / Image fehlt / Health-Timeout.
- **Live (Sekundär, planet):** realer `start`/`stop` gegen `rb-dedicated:<sha>`;
  Ergebnis + Restfreiheit im Bericht/Progress dokumentiert.

### Live-Beweis-Strategie

1. `sha` = main HEAD bzw. verfügbarer `rb-dedicated:<sha>`; Config per Env:
   `PROVISIONER_IMAGE=rb-dedicated:<sha>`,
   `PROVISIONER_GAME_SOURCE=/srv/rift-dev/game`,
   `PROVISIONER_CONFIG_CFG=/opt/rbmods/compose/rift-dev/riftbreaker/config/config.cfg`,
   `PROVISIONER_RBTOOLS_DIR=/opt/rbmods/rbtools/dev`,
   `PROVISIONER_BRIDGE_PORT_BASE=<frei, kollisionsfrei>`,
   `PROVISIONER_INSTANCE_ID=<run-id>`.
2. `--check` → Config ok; dann `measure_boot.py --json` (oder CLI
   `start` → `status` → `stop`).
3. Beweis: `{"ok":true}` innerhalb Deadline; danach `docker ps -a`,
   `docker volume ls`, `docker network ls` — keine `rb-<env>-<id>*`-Reste,
   Host-Port frei.
4. Ergebnis (Kommando + beobachtete Ausgabe) in `progress-issue-918.md` und
   README aufnehmen.

### Risiken

- **R1 (hoch) — leerer Game-Mount:** `spec.game_dir` ist run-scoped und leer;
  als `/opt/riftbreaker` gemountet überschattet er den Image-Inhalt → kein Boot.
  Gegenmaßnahme: US1 führt `game_source` (realer Spielstand, host_vars
  `/srv/rift-<env>/game`) als eigene Config-Quelle ein. Vor US2 klären, ob die
  Cold-Instanz denselben Content wie der Deploy nutzen soll.
- **R2 (mittel) — Rig-Env fehlt:** Compose setzt `WINEESYNC=0`/`WINEFSYNC=0`
  („Sync-Primitive AUS, sonst Crash vor `bind()`"). Ohne die Werte kann der
  Container vor dem Health-Bind crashen. Empfehlung: in US2 mitführen.
- **R3 (mittel) — `config.cfg` fehlt:** Bind-Mount eines nicht existierenden
  Files legt ein Verzeichnis an bzw. der Server mountet `:ro` auf ein Dir.
  Preflight sollte `config_cfg`-Existenz (und `rbtools_dir`/`game_source`)
  fail-loud prüfen, bevor der Container erzeugt wird.
- **R4 (mittel) — Port-Kollision:** `9001` ist host-seitig von
  `riftbreaker-dedicated` (prod) belegt; der Provisioner publiziert nur
  `bridge_port` (Default-Basis 30000) → Kollision nur bei manuell gewählter
  Basis nahe 9001. Live-Test mit kollisionsfreier `bridge_port_base` fahren.
- **R5 (mittel) — Deadline vs. Cold-Boot:** Compose-Healthcheck hat
  `start_period: 300s`; `PROVISIONER_HEALTH_DEADLINE`-Default ist `180s`.
  Kalter Boot kann die Deadline reißen → ggf. Default anheben bzw.
  Live-Messung (`measure_boot.py`) zur Kalibrierung nutzen.
- **R6 (niedrig) — Entrypoint/Command:** Provisioner übergibt kein
  Command/Args; das Image-Default-Entrypoint muss `cli=1 config=config.cfg`
  fahren und `/data/config/config.cfg` + `/opt/rbtools` lesen (Compose-Parität).
  Beim Live-Beweis verifizieren.
- **R7 (niedrig) — Fake-Docker-Parsing:** Der Fake leitet `bridge_port` aus
  `-p`-Feld 2 ab; nach US3 bleibt Feld 2 der Host-Port, Feld 3 = 9001. Test für
  `9001/tcp` in `port`-Ausgabe stellen.
## Verify (Stage 4)

**Verdikt: PASS** — Diff `cb69b20..HEAD` (`deploy/provisioner/`, Commit `1620731`) ist
korrekt gegen das reale Deploy-Layout, sicher und ohne Regressionen. Hermetische Suite
grün: `44 tests OK` (`python3 -m unittest test_provisioner -v`).

### Prüfpunkte (alle bestanden)

1. **Layout-Parität** (`provisioner.py:610-625` vs. `docker-compose.yml.j2` +
   `defaults/main.yml`): Host-Publish `127.0.0.1:<bridge_port>:<bridge_container_port>`
   == Compose `127.0.0.1:{{ riftbreaker_bridge_port }}:{{ riftbreaker_bridge_container_port }}`
   (Default 9001). Die **fünf** Mounts stimmen exakt:
   `<game_source>:/opt/riftbreaker`, `<wine_volume>:/data/.wine`,
   `<saves_volume>:/data/saves`, `<config_cfg>:/data/config/config.cfg:ro`,
   `<rbtools_dir>:/opt/rbtools:ro`. Env exakt: `RBB_BRIDGE_BIND=0.0.0.0`,
   `RBB_BRIDGE_PORT=<container_port>`, `WINEESYNC=0`, `WINEFSYNC=0` (+ `RIFTBREAKER_MODE`,
   aus #908). Defaults `config_cfg`/`rbtools_dir`/`game_source` treffen die realen
   host_vars-Pfade (`/opt/rbmods/compose/rift-<env>/...`, `/opt/rbmods/rbtools/<env>`,
   `/srv/rift-<env>/game`).
2. **Health-Pfad** (`provisioner.py:247-248`): `health_url()` bleibt
   `http://127.0.0.1:<bridge_port>/health`; Host-Publish mappt `bridge_port`→9001.
   Konsistent.
3. **Config/InstanceSpec** (`provisioner.py:75-78,97-101,115-118,185-189,225-234`):
   neue Felder + `{env}`-Substitution in `config_cfg`/`rbtools_dir`/`game_source`;
   Env>Datei-Precedence; `bridge_container_port` in `_INT_FIELDS` und auf 1..65535
   validiert (`ConfigError`). `InstanceSpec` rein rechnend/deterministisch.
4. **Sicherheit** (`provisioner.py:287-301,605-625`): `docker` weiter als Argumentliste
   (`subprocess.run(cmd, ...)`, kein `shell=True`/`os.system`); alle Namen aus
   `InstanceSpec`; keine Shell-/Injektionsfläche über `{env}`/Pfade/Mounts.
5. **Regressionen**: Preflight-Reihenfolge korrekt — Idempotenz-Check, dann
   `_preflight` (Port/Disk/Image/**Quellen**) VOR `_create_dirs`/Netz/Volumes/Container
   (`provisioner.py:449-476`); fehlende Quellen → `ProvisionError` ohne Container.
   Idempotenz/Rollback unverändert grün.
6. **Testqualität**: kein `8080` mehr im Modul (grep: none); Fake-Docker-`port`-Ausgabe
   auf `9001/tcp`; `test_run_args_real_image_layout` prüft Host:Container-Port, UDP,
   alle fünf Mounts (inkl. `:ro`) und die Bridge-/Sync-Env. Keine abgeschwächten
   Assertions gefunden.

### Restrisiken / Hinweise (kein Blocker)

- **Niedrig — TCP-Publish-Parität** (`provisioner.py:611`): Compose publiziert neben
  `6321/udp` auch `6321/tcp`; der Provisioner nur UDP. Pre-existing (vor #918), kein
  Regressionsrisiko für den Dedicated-Server (bindet UDP).
- **Niedrig — `spec.game_dir` jetzt ungenutzt** (`provisioner.py:223,509,587`): wird noch
  angelegt/entfernt, aber nach dem Mount-Umstieg auf `game_source` nirgends mehr gemountet
  → toter Pfad (Cleanup-Kandidat, kein Funktionsfehler).
- **Mittel — Deadline vs. Cold-Boot (R5, weiterhin offen):**
  `PROVISIONER_HEALTH_DEADLINE`-Default 180s < Compose `start_period: 300s`. Live-
  Kalibrierung in Stage 5; ggf. Default anheben.
- **Cosmetisch:** README endet ohne Trailing-Newline.

→ Stage 5 (Live-Beweis) kann starten.

## Test (Stage 5)

**Datum:** 2026-09-24 · **Host:** planet · **Branch:** fix/918-provisioner-real-image (HEAD 1620731)

### A) Hermetisch (Pflicht-Gate) — PASS

```
$ cd deploy/provisioner && python3 -m unittest test_provisioner -v
Ran 44 tests in 24.639s
OK

$ cd deploy/parked && python3 -m unittest test_measure_boot test_parked_pool test_parked_vs
Ran 52 tests in 0.003s
OK
```

Fake-Docker-`run`-Aufruf belegt das reale Layout (Auszug):
```
docker run -d --name riftbreaker-dedicated-test-0 --network rb-test-0_default \
  --label rb.provisioner.env=test --label rb.provisioner.instance=0 \
  -p 127.0.0.1:54459:9001 -p 127.0.0.1::6321/udp \
  -v /tmp/.../sources/game:/opt/riftbreaker \
  -v rb-test-wine-0:/data/.wine -v rb-test-saves-0:/data/saves \
  -v /tmp/.../sources/config.cfg:/data/config/config.cfg:ro \
  -v /tmp/.../sources/rbtools:/opt/rbtools:ro \
  -e RIFTBREAKER_MODE=solo -e RBB_BRIDGE_BIND=0.0.0.0 -e RBB_BRIDGE_PORT=9001 \
  -e WINEESYNC=0 -e WINEFSYNC=0 test-image:latest
```
→ Container-Port **9001**, Host-Publish = `bridge_port`, alle fünf Mounts inkl. `:ro`,
Env-Paare und Sync-Env (R2) vorhanden. Kein `8080` mehr.

### B) LIVE-Integrationsnachweis (reales Image) — PASS

Quellen-Vorprüfung (alle vorhanden):
```
/srv/rift-dev/game                                  (Verzeichnis, Content ok)
/opt/rbmods/compose/rift-dev/riftbreaker/config/config.cfg   (File, 532 B)
/opt/rbmods/rbtools/dev                             (Directory, pipe_bridge.exe/rbbridge.dll)
docker images | grep rb-dedicated:cb69b20db7b2      -> 5.14GB (main HEAD-Tag)
```

Env (host_vars planet, dev):
```
PROVISIONER_IMAGE=rb-dedicated:cb69b20db7b2
PROVISIONER_GAME_SOURCE=/srv/rift-dev/game
PROVISIONER_CONFIG_CFG=/opt/rbmods/compose/rift-dev/riftbreaker/config/config.cfg
PROVISIONER_RBTOOLS_DIR=/opt/rbmods/rbtools/dev
PROVISIONER_BRIDGE_PORT_BASE=31000
PROVISIONER_INSTANCE_ID=918live
PROVISIONER_HEALTH_DEADLINE=360
PROVISIONER_ENV=test
```

**1) `--check`** → `configuration OK (env=test image=rb-dedicated:cb69b20db7b2 base_dir=/srv min_free_gb=10.0 health_deadline=360.0s)` (EXIT=0)

**2) `start`** (15:50:17 → healthy 15:50:29, **Bootdauer ≈ 12 s** ≪ 360 s Deadline):
Der erzeugte `docker run` nutzte exakt das reale Layout:
```
docker run -d --name riftbreaker-dedicated-test-918live --network rb-test-918live_default \
  --label rb.provisioner.env=test --label rb.provisioner.instance=918live \
  -p 127.0.0.1:48059:9001 -p 127.0.0.1::6321/udp \
  -v /srv/rift-dev/game:/opt/riftbreaker \
  -v rb-test-wine-918live:/data/.wine -v rb-test-saves-918live:/data/saves \
  -v /opt/rbmods/compose/rift-dev/riftbreaker/config/config.cfg:/data/config/config.cfg:ro \
  -v /opt/rbmods/rbtools/dev:/opt/rbtools:ro \
  -e RIFTBREAKER_MODE=solo -e RBB_BRIDGE_BIND=0.0.0.0 -e RBB_BRIDGE_PORT=9001 \
  -e WINEESYNC=0 -e WINEFSYNC=0 rb-dedicated:cb69b20db7b2
```
Start-Ergebnis:
```json
{"instance": "918live", "container": "riftbreaker-dedicated-test-918live", "running": true, "health": "healthy", "ports": {"bridge": 48059, "docker": {"6321/udp": "127.0.0.1:32987", "9001/tcp": "127.0.0.1:48059"}}, "created": true}
```

**3) direkter Health-Check** → `curl -sS http://127.0.0.1:48059/health`:
```json
{"ok":true,"pipe":true}
```
Container-Log belegt realen Boot (Auszug):
```
[RBBATTLE:cb69b20db7b2...] event=mod_load version=cb69b20db7b2... status=ok mode=server
[entrypoint] Server entered ServerGameplayState — config loaded successfully
[entrypoint] UDP port 6321 is open — server should accept connections
```
`docker ps` während Lauf: `riftbreaker-dedicated-test-918live  Up 11 seconds (healthy)`.

**4) `status`** →
```json
{"running": true, "health": "healthy", "ports": {"bridge": 48059, "docker": {"6321/udp": "127.0.0.1:32987", "9001/tcp": "127.0.0.1:48059"}}, "container": "riftbreaker-dedicated-test-918live"}
```

**5) `stop`** → `{"instance": "918live", "removed": {"container": true, "network": true, "volumes": true, "dirs": true}}` (EXIT=0)

**Restfreiheits-Belege nach `stop`:**
```
$ docker ps -a | grep 918live        -> (leer)
$ docker volume ls | grep 918live    -> (keine rb-test-*-918live)
$ docker network ls | grep 918live   -> (keine 918live-Netze)
$ ss -ltn | grep -E ':48[0-9]{3}|:31[0-9]{3}'  -> (Port frei)
$ ls /srv | grep 918live             -> (kein /srv/rift-test-918live)
```
Kein Container am Ende hinterlassen.

### Evidence-Snippet (kopierbar für README/PR)

````markdown
### Live-Beweis gegen das reale Image (planet, 2026-09-24)

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

Ergebnis: echter Cold-Boot gegen `rb-dedicated:cb69b20db7b2` erreicht
`/health {"ok":true,"pipe":true}` in ≈12 s (Deadline 360 s); `stop()` restfrei
(kein Container/Volume/Netz/Port/Pfad).
````

### Fallstricke / Beobachtungen

- **R5 (Deadline):** Der reale Cold-Boot war mit ≈12 s sehr schnell; die Default-Deadline
  180 s hätte gereicht. 360 s bleibt als sicherer Puffer (Compose `start_period: 300s`).
- **R6 (Entrypoint):** Image-Default-Entrypoint läuft korrekt ohne Command/Args und liest
  `/data/config/config.cfg` + `/opt/rbtools` (Log: `ServerGameplayState — config loaded`).
- **R1 (Game-Mount):** `game_source` zeigt korrekt auf `/srv/rift-dev/game`; der run-scoped
  `run_root` (`/srv/rift-test-918live`) wird angelegt, aber NICHT als `/opt/riftbreaker`
  gemountet — kein Overshadowing des Image-Contents.
- **R4 (Port):** `bridge_port_base=31000` kollisionsfrei; effektiver Host-Port 48059
  (31000 + crc32('918live')%20000), keine Kollision mit 9001–9004.

## Review (Stage 7)

**Datum:** 2026-09-24 · **Reviewer:** feature-dev-reviewer · **PR #932** (main ← `fix/918-provisioner-real-image`, HEAD `7f9eeb2`) · **Diff-Basis:** `cb69b20..HEAD`

### Verdikt: **APPROVE** (Code/DoD) — Merge ausgesetzt bis CI-Checks grün

Kein Merge, keine Code-Änderung, kein Commit durch den Reviewer.

### 1) DoD des Issues — erfüllt

- **Reales Layout 9001 + Deploy-Mounts/Env:** `_create_container` publiziert
  `127.0.0.1:<bridge_port>:<bridge_container_port>` (Default 9001) und mountet exakt die
  fünf realen Ziele `game_source:/opt/riftbreaker`,
  `wine_volume:/data/.wine`, `saves_volume:/data/saves`,
  `config_cfg:/data/config/config.cfg:ro`, `rbtools_dir:/opt/rbtools:ro`; Env
  `RBB_BRIDGE_BIND=0.0.0.0`, `RBB_BRIDGE_PORT=9001`, `WINEESYNC=0`, `WINEFSYNC=0`
  (+ `RIFTBREAKER_MODE`). Deckt die Issue-Root-Cause (8080 statt 9001, falsche Mounts).
- **Integrationsnachweis gegen reales Image:** `rb-dedicated:cb69b20db7b2`, `start()` →
  `health=healthy` in ≈12 s, `curl /health` → `{"ok":true,"pipe":true}`,
  `stop()` restfrei (kein Container/Volume/Netz, Port frei, kein `/srv/rift-test-918live`).
  In `progress-issue-918.md` (Stage 5) und README ausgeführt dokumentiert.

### 2) PR-Konvention — erfüllt

- **Erste Body-Zeile `Closes #918`** vorhanden (Closing-Keyword; CI-Check
  „Issue-Referenz im PR" = pass).
- **Autor:** `app/momo-clanker` (Bot, `is_bot:true`) — entspricht `momo-clanker[bot]`.
- Branch → main, nicht draft, `mergeable: MERGEABLE`.

### 3) Diff-Qualität — sauber

- `cb69b20..HEAD`: 4 Dateien, +646/−18 (README, provisioner.py, test_provisioner.py,
  progress). Keine Debug-Reste (`print(` nur im CLI-Ausgabepfad, kein `TODO`/`pdb`/`breakpoint`).
- Kein `8080` mehr in `deploy/provisioner/*.py` (grep: none). Fake-Docker-Portausgabe auf
  `9001/tcp`.
- Doku konsistent: README-Config-Tabelle um die vier neuen `PROVISIONER_*`-Felder ergänzt,
  neuer Abschnitt „Container-Layout (reales Image)", `{env}`-Substitution und
  `1..65535`-Validierung beschrieben. Der frühere Abschnitt **„Offener Punkt" ist jetzt
  als geführter Live-Beweis** dokumentiert (nicht mehr „ungeprüft").
- Tests aussagekräftig: `test_run_args_real_image_layout` prüft Host:Container-Port, UDP,
  alle fünf Mounts inkl. `:ro`, Bridge-/Sync-Env und Image-als-letztes-Argument;
  `test_missing_sources_fail_loud`, `{env}`-Substitution, Env>Datei, `ConfigError`.
  **Lokal reproduziert: `Ran 44 tests ... OK`** (`python3 -m unittest test_provisioner`).

### 4) CI-Status (Stand 15:54)

| Check | Status |
|---|---|
| Test (Unit-Tests + rbbridge host-test) | pass |
| Build + Package | pass |
| Conventional-Commit-Titel | pass |
| Issue-Referenz im PR | pass |
| Lint | pass |
| deploy-check | pass |
| Relevante Pfade (CI/Boot-Gate/Deploy) | pass |
| Test tournament-server (Rust) | skipping (pfad-bedingt) |
| **boot-test** | **pending** |
| **deploy-check-local** | **pending** |

`mergeStateStatus: BLOCKED` (pending Checks). **Beobachtung, kein eigenmächtiger Merge** —
vor dem Merge müssen `boot-test` und `deploy-check-local` grün sein.

### 5) Restrisiken — akzeptabel (kein Blocker)

- **TCP-Publish-Parität:** Compose publiziert zusätzlich `6321/tcp`, Provisioner nur UDP.
  **Pre-existing** (nicht von #918 eingeführt), Dedicated-Server bindet UDP → kein
  Regressionsrisiko. Akzeptabel.
- **Ungenutzter `spec.game_dir`:** wird weiter angelegt/entfernt, aber nach dem Umstieg auf
  `game_source` nirgends gemountet. Toter Pfad, **kein Funktionsfehler** — Cleanup-Kandidat
  (kein Merge-Blocker; als Nacharbeit/Issue optional).
- **Deadline-Default 180 s < Compose `start_period` 300 s:** realer Cold-Boot war ≈12 s
  (Benchmark Stage 5), 180 s Default deckt das mit großem Puffer. Der Live-Beweis nutzte 360 s.
  Akzeptabel; eine spätere Kalibrierung/Erhöhung ist optional, kein Blocker.

### Nacharbeiten — keine (blockierenden)

Empfehlungen (nicht blockierend, können als Folge-Issue geführt werden):
1. Toten `game_dir`-Pfad entfernen (Cleanup).
2. `6321/tcp`-Publish für volle Compose-Parität prüfen (niedrige Prio).
3. Optional `PROVISIONER_HEALTH_DEADLINE`-Default an Compose `start_period` annähern.

**Fazit:** Code, Doku und Live-Nachweis erfüllen die DoD; Konvention und Diff sind sauber.
APPROVE. Merge erst nach grünen `boot-test`/`deploy-check-local` (nicht Teil dieses Auftrags).

