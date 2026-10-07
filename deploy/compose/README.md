# Lokaler Compose-Stack (Issue #1093)

Self-contained lokaler Stack: `docker compose up --build -d` baut und startet
den aktuellen Checkout — Game-Content, config.cfg, rbtools + gns_probe, den
Dedicated Server, die Sidecars, den Entry-Proxy/Lobby und Caddy.

> **Stand:** Der lokale `solo`-Stack läuft end-to-end (Proxy → Lobby → **warmed
> capsule** → Dedicated). Offen: `versus` (#1104), Timer (#1103), CI-Gates
> (#1102) — siehe „Offen".

## Aufruf

```bash
cp .env.example .env      # einmalig anpassen (Ports, Passwort, Content-Base)
deploy/compose/up.sh      # = docker compose up --build -d (setzt RBB_REF aus git)
docker compose logs -f    # aus dem Repo-Root, ohne -p/-f-Flags
```

Der Wrapper `deploy/compose/up.sh` setzt `RBB_REF` auf den Git-SHA des Checkouts
(→ Image-Tag `rb-dedicated:<sha>` + `RBB_REF` in Containern/Logs) und ruft
`docker compose --env-file .env up --build -d` auf. Äquivalent direkt:
`docker compose up --build -d` (dann kommt `RBB_REF` aus `.env`).

## Zielbild (Flow)

1. `docker compose up --build -d`
2. Player verbindet in Riftbreaker auf `<hostname>` → landet hinter dem Proxy auf
   `:6321` und wird gehalten (Loading-Screen, `--hold`).
3. Player öffnet `http://localhost:8088` → Lobby (Steuer-UI des Relays).
4. Route auf SOLO → Relay `POST /solo` → Kapsel `open` → **warmed capsule**
   (`warm`-Claim) → Dedicated (`127.0.0.1:6322`) → Spiel läuft (Welt pausiert,
   `ready` resumed, `finish` recycelt → wieder warm).

## Services

| Service                                                            | Rolle                                                                   |
| ------------------------------------------------------------------ | ----------------------------------------------------------------------- |
| `content-init`                                                     | Game-Content + private PDB per HTTP (idempotent, #566/#1095)            |
| `config-init`                                                      | rendert `config.cfg` (envsubst; vorher Jinja-Template)                  |
| `rbtools-build`                                                    | baut die 4 Server-I/O-Binaries + `gns_probe.exe` aus dem Checkout       |
| `mod-build`                                                        | baut `rbbattle.zip` + Rollout: Marker/Backup/`#212`-Guard (#1099)       |
| `dedicated`                                                        | Dedicated Server (Wine/Xvfb), publiziert :6322 + Bridge :9001           |
| `session-recorder` / `send-tailer` / `match-loop` / `attack-cycle` | Sidecars                                                                |
| `warm`                                                             | warmed-capsule-Claim-Quelle (Parked-Subset) der statischen Dedi (#1108) |
| `capsule-flow`                                                     | Kapsel-Flow (open/ready/finish) über `warm` + `attack-cycle`            |
| `gns-relay`                                                        | Entry-Proxy `:6321` (hold + route) + Lobby-API `:9200` + Mode-Gate      |
| `caddy`                                                            | http-only Entry; Lobby auf `:8088` → Relay-API                          |
| `crash-collector`                                                  | Crash-Collector + Symbolizer (`docker.sock` + DLL/PDB)                  |
| `server-control`                                                   | Server-Control-Agent (`docker.sock`)                                    |
| `tournament-server`                                                | Referee + Web-UI (Rust)                                                 |

Modus steuert die Topologie: `RBB_MODE=solo` → 1 Dedi, `RBB_MODE=versus` → 2
Dedis (A/B). (`versus` noch offen — #1104.)

## Volumes

Named Volumes (`rb-*`, projekt-präfixt zu `rbbattle_*`): `rb-game`
(Content+Mods), `rb-config`, `rb-wine`, `rb-saves`, `rb-rbtools`, `rb-gns`,
`rb-sessions`, `rb-downloads`, `rb-relay-wine`, `rb-backups`, `rb-crashes`,
`rb-tournament`.

## Offen (Folge-Issues)

- **`versus`-Topologie** (#1104): zweiter Dedi (A/B) + Bridge-Paarung; braucht
  Queue- (9221) + Referee- (8082) Services.
- **Timer** (#1103): `image-retention`, `host-hygiene` — Host vs. Compose.
- **CI-Gates** (#1102): `deploy-check-local` compose-nativ; env-Tests raus;
  planet-Check stilllegen.
