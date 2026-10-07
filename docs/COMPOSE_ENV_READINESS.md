# Compose-Environment — Readiness & Runbook (Issue #1093)

Das **Software-Artefakt** des Projekts ist `docker compose up --build -d`: es baut
und startet den aktuellen Checkout (Ansible bleibt höchstens noch Host-Provisioning).
Dieses Dokument ist das **Runbook für den Live-Smoke** — für den Dev und den
Live-Smoke-Träger. Es beschreibt, was hochkommen soll, wie getestet wird und was
„grün" heißt.

## 0. Zielbild in einem Satz

`deploy/compose/up.sh` (bzw. `docker compose up --build -d`) bringt **21 Services**
hoch: Content, config.cfg, rbtools + gns_probe, Mod-Rollout, Dedicated Server,
Sidecars, Entry-Proxy/Lobby, Caddy, Observer/Control, den VS-Pfad (Queue +
Provisioner) und den Hygiene-Timer (Ofelia).

## 1. Voraussetzungen (Host)

- Docker Engine + `docker compose` (v2).
- Platz: Wine-Image (~2 GB) + Game-Content (~660 MB) + PDB (~252 MB) + Backups.
- HTTPS-Zugriff auf die Content-Route (`RBB_CONTENT_BASE`, Default planet).
- `RBB_HOST_ROOT` (absolut, Default `/srv/rbbattle`) beschreibbar — der Provisioner
  braucht **Host-1:1-Pfade** (Sibling-Container via `docker run`).
- Ports frei (alle an `127.0.0.1`): `6321` (Entry/UDP), `6322` (Game), `9001`
  (Bridge), `9200` (Relay-API/Lobby-UI), `8088` (Lobby), `8092` (server-control),
  `8094` (provisioner), `8081` (tournament), `9211` (capsule), `9201` (warm),
  `9221` (queue).

## 2. Erstlauf (Cold)

```bash
cp .env.example .env       # RBB_SERVER_PASSWORD etc. anpassen
deploy/compose/up.sh       # setzt RBB_REF + RBB_HOST_ROOT, legt Host-Root an
docker compose ps
docker compose logs -f
```

## 3. Was „grün" heißt (Akzeptanz)

### 3a. Init-Kette + Health

- `content-init`, `config-init`, `rbtools-build`, `mod-build` → **`Exited (0)`**.
- `dedicated` → **`Up (healthy)`** (`pgrep DedicatedServer.exe` + Bridge `/health` 200).
- `warm` parkt die Dedi (Welt pausiert).

```bash
docker compose ps
docker inspect --format '{{.State.Health.Status}}' rbb-dedicated   # healthy
curl -fsS http://127.0.0.1:9001/health                              # Bridge OK
curl -fsS http://127.0.0.1:9201/status                              # warm: state=parked
```

### 3b. Test A — SOLO (P0)

Connect → hold → Lobby SOLO → Kapsel `open` → Dedicated → Runde → recycle.

1. Client (Windows, Mod = aktueller `rbbattle.zip`) verbindet auf `<hostname>` (kein Port).
2. Loading-Screen (gehalten, `--hold`).
3. `http://localhost:8088` → Lobby; die eigene Connection ist sichtbar.
4. **SOLO** klicken → Welt pausiert → `ready` → Warmup → Wellen → HQ-Tod →
   recycle → wieder warm (2. `[solo]` gibt erneut ein Spiel).

```bash
curl -fsS http://127.0.0.1:9200/sessions        # Connection sichtbar (held)
curl -fsS http://127.0.0.1:9211/capsule/status  # phase: claimed → warmup → running → parked
```

### 3c. Test B — VS (P1)

2 Clients → queue → Paarung → kalte A/B-Welten → ready → GO → Sieger → Rematch.

1. **Zwei** Clients, **gleiche** Mod, beide auf den Host.
2. Beide `queue (vs)` in der Lobby.
3. Paarung → je eine **kalte** Welt A/B (Provisioner startet Container via `:8094`).
4. Beide landen pausiert → beide `ready` → **GO** (Countdown).
5. Match läuft → Sieger in der Lobby → Instanzen **gestoppt**.
6. **Rematch** aus der Lobby → neues Match.

```bash
curl -fsS http://127.0.0.1:9221/queue/status     # Matches + assignments (A/B)
curl -fsS http://127.0.0.1:8094/status           # provisionierte Instanzen
docker ps --filter label=rb.provisioner.env       # die kalten Welten A/B
```

### 3d. Timer — Hygiene (`hygiene` / `ofelia`)

- `hygiene` → **`Up`** (idle); `ofelia` → **`Up`**.
- Job-Läufe + Exit-Codes landen in den Ofelia-Logs.

```bash
docker compose ps                      # hygiene + ofelia: Up
docker compose logs ofelia             # job-exec-Läufe + Exit-Codes
# Dry-Run der beiden Jobs (schreibt nichts):
docker compose exec hygiene /opt/rbmods/image-retention/docker_image_tag_retention.sh --dry-run
docker compose exec -e RB_HYGIENE_DRY_RUN=1 hygiene /opt/rbmods/host-hygiene/host_hygiene.sh
```

## 4. Bekannte Lücken / worauf achten

- **Erstbuild langsam:** das Wine-Image (`deploy/dedicated-server`) lädt/buildet
  ~2 GB inkl. `wineboot`/`winetricks` — der erste `up --build` dauert.
- **Content beim Erstlauf:** `content-init` zieht ~254 MB Bundle + ~241 MB PDB;
  danach idempotent/offline (#1095).
- **Host-1:1-Pfade (Provisioner):** jeder `PROVISIONER_*`-Pfad muss als Host-Pfad
  unter `RBB_HOST_ROOT` existieren (der Daemon löst `-v` gegen den Host). Diese
  Kopplung ist **noch nicht live gefahren** — erster Verdachtspunkt im Smoke.
- **Stabilität (#1089):** Thread-Leak im `pipe_bridge` killt always-on-Welten nach
  Tagen → für längere Playtests konsequent per Server-Control restarten.
- **`warm` (Solo) vs. provisioniert (VS):** beide nutzen den Host-Root; die
  Koexistenz ist noch nicht live belegt.
- **Ofelia-Label-Discovery ist nicht realtime:** `ofelia` `depends_on` `hygiene`
  (`condition: service_started`), damit die Job-Labels beim Start vorliegen;
  neue/geänderte Jobs erfordern ein Recreate des Schedulers.
- **Ofelia 0.3.x Cron-Format:** das numerische Schedule-Format hat ein
  Sekundenfeld; die Jobs nutzen deshalb `@daily`/`@weekly` (robfig-Descriptors)
  statt z. B. `0 4 * * *`.
- **Secrets:** nur in `.env` (git-ignoriert); nie ins Repo.

## 5. Befund melden

Live-Stand/Bugs an den Fahrplan-`report.md` bzw. ein GitHub-Issue — mit
`docker compose logs`-Auszug, `docker ps` und (bei Crash) dem Crash-Bundle
(Skill `crash-debugging`: `/opt/rbmods/crashes…`).

## 6. Referenzen

- `deploy/compose/README.md` — Stack, Services, Pfad-Modell.
- Fahrplan (P0 Solo → P1 VS → … → P5 1v1).
- Issues: #1093 (Umbrella), #1083 (VS-Provisioner), #1112 (Pfad-Modell),
  #1103 (Hygiene-Timer).
