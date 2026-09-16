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
  Symbolisierung ist AN (die private PDB kommt über das HTTP-Content-Bundle
  mit, siehe unten) — der `llvm-symbolizer`-Pfad kann trotzdem distro-
  abhängig nachjustiert werden müssen, siehe "Crash-Symbolik" unten.
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
- **≥ 5 GB frei auf `/`** (Disk-Space-Gate, Issue #310/#566 — bricht sonst
  VOR dem Image-Build ab, mit klarer Fehlermeldung. `riftbreaker_disk_min_free_gb`
  in `local-vars.yml` bei Bedarf anpassen; Live-Befund: 7,7 GB frei reichten
  mit dem auf 5 GB gesenkten lokalen Default).

**Game-Content kommt per HTTP-Download von planet** (`riftbreaker_content_mode:
http`, Default in `local-vars.yml`): ein vorbereitetes, checksum-verifiziertes
Bundle (`riftbreaker-game-content.tar.gz`, ~254 MiB) **inklusive der privaten
PDB** fürs Crash-Symbolik-Modul (~241 MiB) — kein SteamCMD, keine
distro-spezifischen 32-bit-Abhängigkeiten. Struktur geprüft (2026-09-16,
Checksum verifiziert): `./bin/DedicatedServer.exe` passt 1:1 auf
`riftbreaker_server_bin`.

**Alternativen** (falls der HTTP-Download mal nicht erreichbar ist):

```bash
# SteamCMD (anonym, App 4114030, kostenlos) -- braucht 32-bit-Multilib:
scripts/local-dev.sh -e riftbreaker_content_mode=steamcmd

# eigener kanonischer Cache (du brauchst dafuer selbst schon einen
# vollstaendigen Steam-Content-Stand irgendwo liegen):
scripts/local-dev.sh -e riftbreaker_content_mode=sync -e riftbreaker_content_cache_dir=/pfad
```

Bei `mode=steamcmd` ist die 32-bit-Laufzeitabhängigkeit distro-abhängig, die
Rolle erkennt das über `ansible_os_family`:

| Distro | Was die Rolle installiert | Manuell vorher |
|---|---|---|
| Debian/Ubuntu | `dpkg --add-architecture i386` + `apt install lib32gcc-s1` | nichts |
| Fedora/RHEL | `dnf install glibc.i686 libstdc++.i686` | nichts |
| andere | — (kein Zweig) | `mode=sync` verwenden |

**mingw-w64-Paketnamen** (für die Bridge-Tools, unabhängig vom Content-Modus):

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

**Achtung, unverifiziert:** der HTTP-Download selbst wurde geprüft (Checksum +
Archiv-Struktur, s. o.) — was NICHT in dieser Session getestet werden konnte,
ist der komplette Rest der Kette danach: `unarchive` durch Ansible,
Docker-Image-Build, Wine-Boot, tatsächliches Hochfahren von
`DedicatedServer.exe` (kein Docker/Wine hier). `steamcmd`/`sync` als Modus
sind ebenfalls nur syntaktisch/durch Doku-Recherche geprüft, nicht live
gebootet — planet selbst nutzt übrigens `sync` (host_vars/planet/vars.yml),
nicht `steamcmd`.

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

`--tags crash` ALLEIN (Review #567) läuft ins Leere, solange der
Dedicated-Server-Container noch nicht existiert (`--tags server` einmal
vorher laufen lassen) — der Collector beobachtet einen Container, den es
noch nicht gibt.

### Server-Control (Plane B) — Status/Restart ohne Docker-Befehle

```bash
curl -H "Authorization: Bearer localdev" http://127.0.0.1:8091/status
curl -X POST -H "Authorization: Bearer localdev" http://127.0.0.1:8091/restart
```

Token = `server_control_token` aus `deploy/local-vars.yml` (Default
`localdev` — bei Bedarf mit `-e server_control_token=...` überschreiben).

### Crash-Symbolik

Standardmäßig AN (`crash_collector_symbolize: true`) — das HTTP-Content-
Bundle liefert die volle **private** PDB mit (kein SteamCMD-Blocker mehr).
Falls Symbolik im Crash-Bundle trotzdem leer bleibt: der Rollen-Default für
`llvm-symbolizer` (`/usr/lib/llvm-18/bin/llvm-symbolizer`) ist ein
Debian-Paketpfad, auf Fedora anders — mit
`-e crash_collector_llvm_symbolizer=<dein lokaler Pfad>` korrigieren (z. B.
nach `sudo dnf install llvm`, Pfad via `which llvm-symbolizer` finden).
Rohe Bundles (Minidump+Trace+Log) landen so oder so immer in
`{{ riftbreaker_local_root }}/crashes`, auch wenn die Symbolisierung
fehlschlägt. Ganz ausschalten: `-e crash_collector_symbolize=false`.

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
