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
- **Kein Caddy/Website/Server-Control/Crash-Collector nötig** —
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
# Einmalig: ansible-core (falls nicht vorhanden)
python3 -m venv ~/.venvs/rift-deploy
~/.venvs/rift-deploy/bin/pip install "ansible-core>=2.19"

# Server + Bridge hochfahren (Server-Passwort + Pfade: deploy/local-vars.yml)
~/.venvs/rift-deploy/bin/ansible-playbook \
  -i deploy/local-inventory.yml deploy/local-deploy.yml \
  -e @deploy/local-vars.yml -K
```

`-K` fragt das `sudo`-Passwort ab (`become: true`). Danach:

```bash
curl -X POST http://127.0.0.1:9001/get_state
# im Browser: http://127.0.0.1:9001/  (Cockpit)
```

### Nur einen Teil laufen lassen

```bash
--tags content     # nur Mod-Zip + Game-Content
--tags server       # Image + Server-I/O-Tools + Dedicated-Server
--tags tournament   # zusätzlich den Tournament-Server (systemd, optional)
```

### Aufräumen

Alles liegt unter `riftbreaker_local_root` (Default `/srv/rift-local`) +
`rb-dedicated`/`riftbreaker-sessions`-Containern + den `rb-wine`/`rb-saves`-
Volumes:

```bash
docker rm -f riftbreaker-dedicated riftbreaker-sessions
docker volume rm rb-wine rb-saves
sudo rm -rf /srv/rift-local
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
optional `tournament-server`), aber außerhalb des Schemas, mit einer eigenen,
einfachen `deploy/local-vars.yml`.

## Was hier NICHT läuft (bewusst)

`website`, `server-control`, `crash-collector`, `image-retention`,
`host-hygiene` — das sind Mehr-Instanz-/Ops-Belange von planet
(öffentliche Domains, Vault, geteilter Host-Caddy, Crash-Retention). Für
Solo-Dev bringen sie nur Komplexität ohne Nutzen; die Bridge liefert das
Cockpit ja schon selbst aus.
