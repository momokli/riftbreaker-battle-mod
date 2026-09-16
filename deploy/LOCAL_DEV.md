# RIFT BATTLE — lokales Solo-Dev-Setup (Issue #566)

Reproduziert, was `deploy/site.yml` auf planet für dev tut — auf deinem
eigenen Linux-Rechner, ohne planet-Zugriff. Gedacht für die IO-/Wave-/
Difficulty-Spikes (#508/#513): Server hochfahren, `POST /get_state` /
`activate_mission_flow` gegen die Bridge fahren.

## Was du bekommst

- Den Dedicated-Server (`DedicatedServer.exe` unter Wine, Docker) auf
  `:6321`.
- Die Server-I/O-Bridge (`rbbridge.dll` injiziert + `pipe_bridge.exe`) auf
  `http://127.0.0.1:9001/`.
- Den **Crash-Collector** (systemd, Review #566): sichert Minidump+Trace+Log
  bei einem Absturz nach `{{ riftbreaker_local_root }}/crashes` — essenziell
  für die Wave-/Difficulty-Spikes, die den Server crashen lassen können.
  Symbolisierung standardmäßig **aus** (siehe unten, "Crash-Symbolik").
- **Server-Control** (systemd, Plane B/#424, Review #566): Status/Logs/
  Restart/Start/Stop des Containers per HTTP, `127.0.0.1:8091`
  (`server_control_port`) — kein Browser-Panel dafür lokal, aber `curl`
  reicht (Beispiele unten).
- **Kein Caddy/Website/Image-Retention/Host-Hygiene nötig** —
  `pipe_bridge.exe` liefert `cockpit/cockpit.html` selbst unter `GET /` aus
  (Issue #474). `http://127.0.0.1:9001/` im Browser reicht.

## Voraussetzungen

- Docker + `docker compose`.
- `mingw-w64` ODER `zig` — cross-compiled die Server-I/O-Tools
  (`scripts/build_rbbridge_tools.sh`), kein Windows nötig.
- `ansible-core` (≥ 2.19) auf deinem Rechner (Control-Node = Zielhost hier).
- `sudo`-Rechte (das Playbook läuft mit `become: true`, wie `site.yml` auf
  planet — Docker/`/srv`/`/opt`-Schreibzugriff).

Die Rolle `game-content` bootstrapt `steamcmd` selbst (kein manueller Download
nötig) — anonymer Login, App-ID 4114030, **kostenlos, kein Steam-Account
nötig**. Ihre 32-bit-Laufzeitabhängigkeit ist distro-abhängig, die Rolle
erkennt das über `ansible_os_family`:

| Distro | Was die Rolle installiert | Manuell vorher |
|---|---|---|
| Debian/Ubuntu | `dpkg --add-architecture i386` + `apt install lib32gcc-s1` | nichts |
| Fedora/RHEL | `dnf install glibc.i686 libstdc++.i686` | nichts |
| andere | — (kein Zweig) | `-e riftbreaker_content_mode=sync` (Fallback, siehe unten) |

**mingw-w64-Paketnamen:**

```bash
# Debian/Ubuntu
sudo apt install mingw-w64

# Fedora/RHEL
sudo dnf install mingw64-gcc
```

**Docker:** auf Fedora liefert das offizielle Docker-CE-Repo
(`dnf config-manager addrepo --from-repofile=https://download.docker.com/linux/fedora/docker-ce.repo`,
dann `dnf install docker-ce docker-ce-cli containerd.io docker-compose-plugin`)
die verlässlichsten Ergebnisse — `podman` + `podman-docker`-Kompat-Shim wurde
hier nicht getestet und kann bei `docker compose`-Details abweichen.

**Achtung, unverifiziert:** planet selbst nutzt `riftbreaker_content_mode:
sync` statt `steamcmd` (host_vars/planet/vars.yml) — dieser Pfad läuft in
KEINEM CI-Job und wurde hier nicht live getestet (diese Session hat kein
Docker/Wine/Game-Content, um das zu verifizieren; die Debian/Fedora-Zweige
oben sind nur syntaktisch/durch Doku-Recherche geprüft, nicht live gebootet).
Schlägt SteamCMD trotz der obigen Abhängigkeiten fehl:
`-e riftbreaker_content_mode=sync -e riftbreaker_content_cache_dir=<dein
vollständiger Steam-Stand>` als Fallback (siehe `deploy/roles/game-content/`)
— dafür brauchst du dann allerdings selbst schon einen vollständigen
Steam-Content-Stand irgendwo liegen.

## Aufruf

```bash
scripts/local-dev.sh
```

Bootstrapt `ansible-core` beim ersten Aufruf automatisch in ein eigenes venv
(`~/.venvs/rift-deploy`, überschreibbar via `RBBATTLE_ANSIBLE_VENV`), fragt
dann per `-K` das `sudo`-Passwort ab (`become: true`) und fährt Image +
Game-Content + Server-I/O-Tools + Dedicated-Server + Server-Control +
Crash-Collector hoch. Idempotent — jeder weitere Aufruf ist ein billiger
No-Op, außer sich hat sich wirklich etwas geändert (Code, Content).

Danach:

```bash
curl -X POST http://127.0.0.1:9001/get_state
# im Browser: http://127.0.0.1:9001/  (Cockpit)
```

Äquivalent von Hand (falls du kein Wrapper-Script willst):

```bash
python3 -m venv ~/.venvs/rift-deploy
~/.venvs/rift-deploy/bin/pip install "ansible-core>=2.19"
~/.venvs/rift-deploy/bin/ansible-playbook \
  -i deploy/local-inventory.yml deploy/local-deploy.yml \
  -e @deploy/local-vars.yml -K
```

### Server aus-/wieder anschalten (ohne neu zu bauen)

Für den Alltag — kein Ansible, kein `sudo`-Passwort, kein Neu-Provisionieren:

```bash
scripts/local-dev-stop.sh     # anhalten
scripts/local-dev-start.sh    # wieder starten
```

(Server-Control und Crash-Collector laufen als systemd-Units durchgehend im
Hintergrund, unabhängig vom Dedicated-Server-Container.)

### Nur einen Teil laufen lassen

```bash
scripts/local-dev.sh --tags content      # nur Mod-Zip + Game-Content
scripts/local-dev.sh --tags server        # Image + Server-I/O-Tools + Dedicated-Server + Server-Control
scripts/local-dev.sh --tags crash          # nur den Crash-Collector
scripts/local-dev.sh --tags tournament    # zusätzlich den Tournament-Server (systemd, optional)
```

`--tags tournament` ALLEIN startet nur den Tournament-Server, nicht Server/
Bridge — für den vollen Stack ohne Filter laufen lassen (Server-Control und
Crash-Collector laufen dabei immer mit; nur `tournament` ist opt-in).

### Server-Control (Plane B) — Status/Restart ohne Docker-Befehle

```bash
curl -H "Authorization: Bearer localdev" http://127.0.0.1:8091/status
curl -X POST -H "Authorization: Bearer localdev" http://127.0.0.1:8091/restart
```

Token = `server_control_token` aus `deploy/local-vars.yml` (Default
`localdev` — bei Bedarf mit `-e server_control_token=...` überschreiben).

### Crash-Symbolik nachrüsten (optional)

Standardmäßig aus (`crash_collector_symbolize: false` in `local-vars.yml`) —
die volle **private** PDB (252 MB) ist planet-only, SteamCMD liefert sie
nicht, und der Rollen-Default für `llvm-symbolizer` ist ein Debian-Paketpfad.
Rohe Bundles (Minidump+Trace+Log) landen trotzdem immer in
`{{ riftbreaker_local_root }}/crashes`. Zum Nachrüsten:
`-e crash_collector_symbolize=true -e crash_collector_llvm_symbolizer=<dein
lokaler Pfad>` — Symbolisierung bleibt dann trotzdem nur so gut wie die
DLL/PDB, die SteamCMD dir gegeben hat.

### Aufräumen

Für ein bloßes Pausieren reicht `scripts/local-dev-stop.sh` (Container
angehalten, nichts gelöscht). Für vollständiges Entfernen: alles
Projekt-/Instanz-Bezogene liegt unter `riftbreaker_local_root`
(Default `/srv/rift-local`) + Containern/Volumes des Compose-Projekts
`riftbreaker` (`riftbreaker_compose_project`) + den systemd-Units. Der
Docker-Build-Kontext (`/opt/rbmods/dedicated-server`) und die
SteamCMD-Installation (`/opt/steamcmd`) liegen bewusst AUSSERHALB (reiner
Tooling-Cache, kein Instanz-Zustand — siehe `local-vars.yml`) und bleiben
beim Aufräumen unangetastet, außer du willst sie auch los sein:

```bash
docker compose -p riftbreaker -f /srv/rift-local/compose/riftbreaker/docker-compose.yml \
  down -v --remove-orphans
sudo systemctl disable --now server-control rbmods-crash-collector tournament-server-local 2>/dev/null
sudo rm -rf /srv/rift-local /etc/systemd/system/server-control.service \
  /etc/systemd/system/rbmods-crash-collector.service \
  /etc/systemd/system/tournament-server-local.service
sudo systemctl daemon-reload

# nur wenn du auch den Build-Cache/SteamCMD loswerden willst:
sudo rm -rf /opt/rbmods/dedicated-server /opt/steamcmd
```

## Warum ein eigener Einstiegspunkt statt eines vierten Envs

`deploy/site.yml`/`deploy-prod.yml`/`test-deploy.yml` teilen sich ein striktes
Env-Isolations-Schema (`deploy/env-schema.yml`, Issue #483) — das löst
Namensraum-Kollisionen auf **einem geteilten Mehr-Umgebungs-Host** (dev+prod+
test laufen alle auf planet). Auf deinem eigenen Rechner gibt es nur EINE
Instanz — die Kollision, die das Schema löst, existiert dort nicht. Ein
`local`-Eintrag im Schema wäre Zusatzaufwand ohne Nutzen. `deploy/
local-deploy.yml` nutzt deshalb dieselben Rollen (`mods-zip`,
`dedicated-server-image`, `game-content`, `rbtools`, `riftbreaker-server`,
`server-control`, `crash-collector`, optional `tournament-server`), aber
außerhalb des Schemas, mit einer eigenen, einfachen `deploy/local-vars.yml`.

## Was hier NICHT läuft (bewusst)

`website`, `image-retention`, `host-hygiene` — das sind Mehr-Instanz-/
Ops-Belange von planet (öffentliche Domains, geteilter Host-Caddy,
Image-Tag-Aufräumen über Zeit). Für Solo-Dev bringen sie nur Komplexität
ohne Nutzen; die Bridge liefert das Cockpit ja schon selbst aus.
`server-control` und `crash-collector` laufen dagegen mit (Review #566) —
Restart-ohne-Docker und Crash-Artefakte sind für die Spike-Arbeit zu wichtig,
um sie wegzulassen.
