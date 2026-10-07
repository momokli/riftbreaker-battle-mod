# Lokaler Compose-Stack (Issue #1093)

Self-contained lokaler Stack: `docker compose up --build -d` baut und startet
den aktuellen Checkout — Game-Content, config.cfg, rbtools + gns_probe, den
Dedicated Server, die Sidecars, den Entry-Proxy/Lobby und Caddy.

> **Stand: Walking Skeleton (Modus `solo`).** Der dünnste End-to-End-Slice.
> `versus`-Topologie, Mod-Rollout-Container, das Mode-Gate im Proxy und die
> Observer/Control-Dienste folgen als eigene PRs (siehe „Offen").

## Aufruf

```bash
cp .env.example .env      # einmalig anpassen (Ports, Passwort, Content-Base)
docker compose up --build -d
docker compose logs -f    # aus dem Repo-Root, ohne -p/-f-Flags
```

## Zielbild (Flow)

1. `docker compose up --build -d`
2. Player verbindet in Riftbreaker auf `<hostname>` → landet hinter dem Proxy auf
   `:6321` und wird gehalten (Loading-Screen, `--hold`).
3. Player öffnet `http://localhost:8088` → Lobby (Steuer-UI des Relays).
4. Route auf SOLO → der Proxy dialt den Dedicated (`127.0.0.1:6322`) → Spiel läuft.

## Services

| Service         | Rolle                                                            |
| --------------- | ---------------------------------------------------------------- |
| `content-init`  | Game-Content + private PDB per HTTP (idempotent, #566/#1095)      |
| `config-init`   | rendert `config.cfg` (envsubst; vorher Jinja-Template)            |
| `rbtools-build` | baut die 4 Server-I/O-Binaries + `gns_probe.exe` aus dem Checkout |
| `dedicated`     | Dedicated Server (Wine/Xvfb), publiziert :6322 + Bridge :9001     |
| `session-recorder` / `send-tailer` / `match-loop` / `attack-cycle` | Sidecars |
| `gns-relay`     | Entry-Proxy `:6321` (hold + route) + Lobby-API `:9200`            |
| `caddy`         | http-only Entry; Lobby auf `:8088` → Relay-API                    |

Modus steuert die Topologie: `RBB_MODE=solo` → 1 Dedi, `RBB_MODE=versus` → 2
Dedis (A/B). (Noch nicht implementiert — Walking Skeleton ist `solo`.)

## Volumes

Named Volumes (`rb-*`, projekt-präfixt zu `rbbattle_*`): `rb-game`
(Content+Mods), `rb-config`, `rb-wine`, `rb-saves`, `rb-rbtools`, `rb-gns`,
`rb-sessions`, `rb-downloads`, `rb-relay-wine`.

## Offen (Folge-PRs)

- **versus-Topologie:** zweiter Dedi (A/B) + Bridge-Paarung; `RBB_MODE` schaltet.
- **Mod-Rollout-Container:** Marker/Backup außerhalb `mods/` + `#212`-Fremd-Mod-
  Guard + Verify/Rollback, 1:1 aus der Rolle `riftbreaker-server`.
- **Mode-Gate im Proxy:** `RBB_MODE` steuert die Lobby-Buttons **und** wird
  serverseitig enforced (`--mode` im `gns_probe`); Vokabular-Abgleich
  (`versus` vs. `vs` in `attack-cycle`).
- **Observer/Control:** `crash-collector` (+ Symbolizer), `server-control`,
  `tournament-server`, `capsule-flow` als Compose-Services (teils `docker.sock`).
- **Timer:** `image-retention`, `host-hygiene` — bleiben voraussichtlich Host
  (Docker-weite `prune` + Host-Dirs + Schedule).
- **Wrapper:** dünnes Skript, das `RBB_REF` aus `git rev-parse` setzt und
  `docker compose --env-file .env up --build -d` aufruft.
- **CI-Gates:** `deploy-check-local` compose-nativ; env-Tests raus; planet-Check
  stilllegen.
