# Developer-Guide — Tester-/Dev-Runbook für den Compose-Stack (Issue #1124)

> **Zielgruppe:** ein Tester („besttoasy") mit einem **Windows-Rechner** (Riftbreaker
> via Steam/GOG) und einem **Linux-Laptop**, auf dem der lokale Compose-Stack läuft.
> Dies ist ein **How-to/Runbook**, keine Architektur-Spezifikation. Source of Truth:
> [`deploy/compose/README.md`](../deploy/compose/README.md),
> [`docs/COMPOSE_ENV_READINESS.md`](COMPOSE_ENV_READINESS.md),
> [`tools/gns-proxy/README.md`](../tools/gns-proxy/README.md),
> [`deploy/capsule/README.md`](../deploy/capsule/README.md) und
> [`deploy/queue/README.md`](../deploy/queue/README.md).

## 1. Ziel & Zielgruppe

Der Guide führt einen Tester durch das **Hochfahren** des lokalen Compose-Stacks auf
dem Linux-Laptop, das **Verbinden** eines Windows-Riftbreaker-Clients und das
**Durchspielen** von SOLO und VS bis zum 1v1 — inklusive „was heißt grün" und
Evidence-Sammlung.

## 2. Voraussetzungen (Host, Linux-Laptop)

- Docker Engine + `docker compose` **v2**.
- Platz: Wine-Image (~2 GB) + Game-Content (~660 MB) + PDB (~252 MB) + Backups.
- `RBB_HOST_ROOT` (absolut, Default `/srv/rbbattle`) **beschreibbar** — der Provisioner
  braucht Host-1:1-Pfade (Sibling-Container via `docker run`).
- HTTPS-Zugriff auf die Content-Route (`RBB_CONTENT_BASE`, Default planet).
- `.env` angelegt (aus `.env.example`): `RBB_SERVER_PASSWORD` u. a. anpassen.
- **Ports frei** (alle an `127.0.0.1`):

| Port | Dienst |
| --- | --- |
| `6321` | Entry-Proxy (UDP) |
| `6322` | Game (Dedicated) |
| `9001` | Bridge |
| `9200` | Relay-API / Lobby-UI |
| `8088` | Lobby (Caddy) |
| `8092` | server-control |
| `8094` | provisioner |
| `8081` | tournament (Referee) |
| `9211` | capsule |
| `9201` | warm |
| `9221` | queue |

## 3. Hochfahren

```bash
cp .env.example .env      # einmalig anpassen (Ports, Passwort, Content-Base)
deploy/compose/up.sh      # = docker compose up --build -d (setzt RBB_REF aus git)
docker compose ps         # aus dem Repo-Root
docker compose logs -f    # aus dem Repo-Root, ohne -p/-f-Flags
```

Der Wrapper `deploy/compose/up.sh` setzt `RBB_REF` auf den Git-SHA des Checkouts
(→ Image-Tag `rb-dedicated:<sha>` + `RBB_REF` in Containern/Logs), setzt
`RBB_HOST_ROOT` (absolut) und legt dessen Unterordner an. Äquivalent direkt:
`docker compose up --build -d` (dann kommen `RBB_REF`/`RBB_HOST_ROOT` aus `.env`).

## 4. Client verbinden (Windows)

- In Riftbreaker auf `<hostname>` verbinden — **kein Port** (der Client landet hinter
  dem Proxy auf `:6321` und wird im Loading-Screen gehalten, `--hold`).
- **Mod-Parität ist Pflicht (VS):** der Windows-Client (bereits eingerichtet) muss
  exakt die aktuelle `rbbattle.zip` verwenden, die der Stack baut (`mod-build`).
  Für VS gilt: **zwei Clients mit der gleichen Mod**.

## 5. Lobby + SOLO-Flow

1. `http://localhost:8088` öffnen → Lobby (Steuer-UI des Relays).
2. Die eigene Connection ist sichtbar (`state` held).
3. **`solo`** klicken → Kapsel `open`/claim → **warmed capsule** (Welt pausiert,
   Cycle `PAUSED`).
4. **`READY`** klicken → `resume_game` + Attack-Cycle startet (`warmup` → `running`).
5. Runde spielen (Wellen → HQ-Tod).
6. **`finish`** → `recycle` → Instanz wieder **warm** (ein zweiter `solo` gibt erneut
   ein Spiel).

**Solo-Join:** mehrere Clients können derselben Instanz beitreten
(`POST /solo {identitaet, instance}`), begrenzt auf `--max-players` (Default **4**).
Ein neuer Client ohne `instance` legt einen neuen Claim an; mit `instance` joint er
nur (kein zweiter Claim). Über dem Limit → `409 instance_full`.

```bash
curl -fsS http://127.0.0.1:9200/sessions        # Connection sichtbar (held)
curl -fsS http://127.0.0.1:9211/capsule/status  # phase: claimed → warmup → running → parked
```

## 6. VS-Flow

1. **Zwei** Clients (gleiche Mod) auf den Host.
2. Beide `queue (vs)` in der Lobby (`POST /queue` → Queue-Dienst `POST /queue/join`).
3. **FIFO-Pairing 1v1** → je eine **kalte** Welt A/B (Provisioner startet Container
   via `:8094`), beide Spieler im Referee registriert.
4. Beide `ready` → Referee **GO** (Countdown).
5. Match läuft → **Sieger** in der Lobby → Instanzen **gestoppt** (kaltes Cleanup).
6. **Rematch** aus der Lobby → neues Match derselben Paarung (frische kalte Welten).

```bash
curl -fsS http://127.0.0.1:9221/queue/status     # Matches + assignments (A/B)
curl -fsS http://127.0.0.1:8094/status           # provisionierte Instanzen
docker ps --filter label=rb.provisioner.env      # die kalten Welten A/B
```

## 7. Was „grün" heißt

Init-Kette und Health (aus `COMPOSE_ENV_READINESS` §3):

```bash
docker compose ps
docker inspect --format '{{.State.Health.Status}}' rbb-dedicated   # healthy
curl -fsS http://127.0.0.1:9001/health                              # Bridge OK
curl -fsS http://127.0.0.1:9201/status                              # warm: state=parked
```

- `content-init`, `config-init`, `rbtools-build`, `mod-build` → **`Exited (0)`**.
- `dedicated` → **`Up (healthy)`** (`pgrep DedicatedServer.exe` + Bridge `/health` 200).
- `warm` parkt die Dedi (Welt pausiert).
- `hygiene` + `ofelia` → `Up` (Job-Läufe + Exit-Codes in den Ofelia-Logs).
- Bei Problemen: `docker compose logs <service>` (z. B. `ofelia`).

## 8. Evidence sammeln

- **Logs:** `docker compose logs` (je Service).
- **Crash-Bundle:** `${RBB_HOST_ROOT}/crashes/<env>/<ref>/…` — wird vom
  `crash-collector` automatisch erzeugt.
- **Crash-Symbolik** läuft automatisch (Symbolizer mit DLL/PDB); das private PDB kommt
  über `content-init`. Für die Root-Cause-Analyse: Skill `crash-debugging`.

## 9. Troubleshooting / bekannte Lücken

- **Erstbuild langsam (Wine):** das Wine-Image (`deploy/dedicated-server`) lädt/buildet
  ~2 GB inkl. `wineboot`/`winetricks` — der erste `up --build` dauert.
- **Content beim Erstlauf:** `content-init` zieht ~254 MB Bundle + ~241 MB PDB; danach
  idempotent/offline.
- **Host-1:1-Pfade (Provisioner):** jeder `PROVISIONER_*`-Pfad muss als Host-Pfad unter
  `RBB_HOST_ROOT` existieren (der Daemon löst `-v` gegen den Host). Noch **nicht live
  gefahren** — erster Verdachtspunkt im Smoke.
- **#1117 — Referee ↔ dynamische Bridge-Ports im VS:** Blocker für den VS-Pfad
  (siehe Roadmap).
- **Stabilität (#1089):** Thread-Leak im `pipe_bridge` killt always-on-Welten nach
  Tagen → für längere Playtests per Server-Control restarten.
- **`warm` (Solo) vs. provisioniert (VS):** beide nutzen den Host-Root; Koexistenz noch
  nicht live belegt.
- **Live-Smoke noch offen** (`up` + SOLO/VS-Flow) → an den Tester.

## 10. Roadmap to 1v1

| Schritt | Inhalt | Status |
| --- | --- | --- |
| 1 | SOLO end-to-end lokal (Proxy → Lobby → warmed capsule → Dedicated → Runde → recycle) | 10/10 |
| 2 | Zwei Clients + VS-Maschinerie (kalte A/B-Welten, Referee, Queue) | offen — Blocker **#1117** |
| 3 | 1v1 gespielt (zwei Clients, voller Match-Loop inkl. Rematch) | offen |

---

## Selbst-Kontrolle (Quellen-Beleg)

- **Ports** (6321/6322/9001/9200/8088/8092/8094/8081/9211/9201/9221): alle aus
  `COMPOSE_ENV_READINESS.md` §1 bzw. `deploy/compose/README.md` (Services-Tabelle).
- **Aufruf** (`cp .env.example .env` + `deploy/compose/up.sh` + `docker compose ps`/
  `logs -f`): `deploy/compose/README.md` „Aufruf" und `COMPOSE_ENV_READINESS.md` §2.
- **Solo-Flow** (`solo` → claim/pausiert → `READY` → `finish` → warm; Solo-Join
  `--max-players 4`): `deploy/capsule/README.md` + `tools/gns-proxy/README.md`
  (§ Solo-Claim, § Solo-Join, § Modus-Gate).
- **VS-Flow** (FIFO 1v1, kalte A/B, Referee GO, Cleanup, Rematch):
  `deploy/queue/README.md` + `tools/gns-proxy/README.md` § Queue (vs).
- **Crash-Bundle** (`${RBB_HOST_ROOT}/crashes/<env>/<ref>/…`, Symbolik automatisch,
  PDB via `content-init`): `deploy/compose/README.md` (Volumes/Services) +
  `COMPOSE_ENV_READINESS.md` §5 + `AGENTS.md` §10.
- **Lücken** (Erstbuild/Content/Host-1:1/#1117/Live-Smoke): `COMPOSE_ENV_READINESS.md`
  §4 und `deploy/compose/README.md` „Offen".
