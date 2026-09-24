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
| 6 developer (PR) | pending | |
| 7 reviewer | pending | |

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