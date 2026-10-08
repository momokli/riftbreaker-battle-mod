# Lokaler Compose-Stack (Issue #1093)

Self-contained lokaler Stack: `docker compose up --build -d` baut und startet
den aktuellen Checkout — Game-Content, config.cfg, rbtools + gns_probe, den
Dedicated Server, die Sidecars, den Entry-Proxy/Lobby und Caddy.

> **Stand:** Solo läuft end-to-end (Proxy → Lobby → **warmed capsule** → Dedicated).
> Der **VS-Pfad** (Queue → Provisioner → kalte A/B-Welten) ist verdrahtet. Der
> **Hygiene-Timer** (#1103) ist als Ofelia-Sidecar im Stack (siehe Services). Offen:
> CI-Gates (#1102), Ansible-Runtime-Rückbau; Live-Smoke → besttoasy.

## Aufruf

```bash
cp .env.example .env      # einmalig anpassen (Ports, Passwort, Content-Base)
deploy/compose/up.sh      # = docker compose up --build -d (setzt RBB_REF aus git)
docker compose logs -f    # aus dem Repo-Root, ohne -p/-f-Flags
```

Der Wrapper `deploy/compose/up.sh` setzt `RBB_REF` auf den Git-SHA des Checkouts
(→ Image-Tag `rb-dedicated:<sha>` + `RBB_REF` in Containern/Logs), setzt
`RBB_HOST_ROOT` (absolut, Pfad-Modell #1112) und legt dessen Unterordner an, dann
`docker compose --env-file .env up --build -d`. Äquivalent direkt:
`docker compose up --build -d` (dann kommen `RBB_REF`/`RBB_HOST_ROOT` aus `.env`).

## Zielbild (Flow)

1. `docker compose up --build -d`
2. Player verbindet in Riftbreaker auf `<hostname>` → landet hinter dem Proxy auf
   `:6321` und wird gehalten (Loading-Screen, `--hold`).
3. Player öffnet `http://localhost:8088` → Lobby (Steuer-UI des Relays).
4. Route auf SOLO → Relay `POST /solo` → Kapsel `open` → **warmed capsule**
   (`warm`-Claim) → Dedicated (`127.0.0.1:6322`) → Spiel läuft (Welt pausiert,
   `ready` resumed, `finish` recycelt → wieder warm).

## Services

| Service                                                            | Rolle                                                                     |
| ------------------------------------------------------------------ | ------------------------------------------------------------------------- |
| `content-init`                                                     | Game-Content + private PDB per HTTP (idempotent, #566/#1095)              |
| `config-init`                                                      | rendert `config.cfg` (envsubst; vorher Jinja-Template)                    |
| `rbtools-build`                                                    | baut die 4 Server-I/O-Binaries + `gns_probe.exe` aus dem Checkout         |
| `mod-build`                                                        | baut `rbbattle.zip` + Rollout: Marker/Backup/`#212`-Guard (#1099)         |
| `dedicated`                                                        | Dedicated Server (Wine/Xvfb), publiziert :6322 + Bridge :9001             |
| `session-recorder` / `send-tailer` / `match-loop` / `attack-cycle` | Sidecars (Code **gebacken** ins Image, `up --build`)                      |
| `warm`                                                             | warmed-capsule-Claim-Quelle (Parked-Subset) der statischen Dedi (#1108)   |
| `capsule-flow`                                                     | Kapsel-Flow (open/ready/finish) über `warm` + `attack-cycle`              |
| `gns-relay`                                                        | Entry-Proxy `:6321` (hold + route) + Lobby-API `:9200` + Mode-Gate        |
| `caddy`                                                            | http-only Entry; Lobby auf `:8088` → Relay-API                            |
| `crash-collector`                                                  | Crash-Collector + Symbolizer (`docker.sock` + DLL/PDB)                    |
| `server-control`                                                   | Server-Control-Agent (`docker.sock`)                                      |
| `tournament-server`                                                | Referee + Web-UI (Rust)                                                   |
| `provisioner`                                                      | Provisioner-HTTP `:8094` — kalte VS-Welten via `docker run` (#1083/#1110) |
| `queue`                                                            | VS-Queue `:9221` — 1v1-Paarung + kalte A/B-Welten (#998)                  |
| `hygiene`                                                          | Timer-Tooling: `image-retention` + `host-hygiene` (`job-exec`-Ziel)       |
| `ofelia`                                                           | Job-Scheduler (`:docker`), läuft die Hygiene-Jobs im `hygiene`-Container  |

`solo` läuft über die statische `dedicated` + **warmed capsule**. **VS (1v1)** läuft
über die **Queue → Provisioner → kalte A/B-Welten** (kein statischer 2. Dedi).
`RBB_MODE` steuert das Mode-Gate der Lobby.

### Scheduler: Ofelia statt eigenem Cron

Die Hygiene-Jobs laufen über **Ofelia** (`mcuadros/ofelia:0.3.22`, `:docker`).
Ofelia liest die Zeitpläne als **Labels am `hygiene`-Container** und führt sie per
**`job-exec`** _in_ diesem aus → Jobs erben env + Mounts, ein Scheduler-Image für
beide Jobs. Details: `deploy/compose/hygiene/README.md`.

## Volumes / Pfad-Modell

Geteilte Dirs liegen als **Bind-Mounts unter dem Host-Root `${RBB_HOST_ROOT}`
(Default `/srv/rbbattle`)**: `game/`, `config/`, `rbtools/`, `gns/`, `sessions/`,
`backups/`, `crashes/`, `tournament/`, `downloads/`, `queue/`. Grund: der
**Provisioner** erzeugt Sibling-Container via `docker run` und braucht dieselben
**Host-Pfade (1:1)**. Nur die Wine-/Saves-Prefixe bleiben Named Volumes
(`rb-wine`, `rb-saves`, `rb-relay-wine`).

## Offen (Folge-Issues)

- **CI-Gates** (#1102): `deploy-check-local` compose-nativ; env-Tests raus.
- **Ansible-Runtime-Rückbau**: Ansible nur noch host-Provisioning.
- **Live-Smoke** (`up` + SOLO/VS-Flow) → **besttoasy**.
