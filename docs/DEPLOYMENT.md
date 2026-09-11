# RIFT BATTLE — Deployment & Stack

> Ziel-Stack + Deployment-Plan. **Deploy NUR über das Ansible-Playbook in
> `deploy/`** (kein manuelles Gedudel). Umsetzung: Issue #45.

## Ziel-Stack (was IMMER betrieben wird)

| Komponente | Host | Container/Unit | Port | Zweck |
|---|---|---|---|---|
| riftbreaker-dedicated | planet | docker (wine) | 6321/udp | Dev-SP-Server: 1v1 „vs sich selbst" (SP-Mode; rbbattle-Mod + rbbridge) |
| rb-winetest | planet | docker (wine) | 6322/udp | Vanilla-Server (kein Mod, schnelle Test-Joins; gleiches Image wie :6321) |
| tournament-server | planet | systemd (Rust/axum, `tournament/`) | 8080 | Turnier 1v1: Lobby/Ready/GO/Wave-Routing/Score (2 Welten) |
| test-Instanzen | planet | docker, on-demand | frei | Test-Server aller Art (Mod-Tests, Balance, Experimente) |
| Website | planet | statics + Caddy (`mellon-caddy`) | 443 | Landing `/` · `/connectivity.html` · `/solo.html` · `/status.json` · Proxy `/tournament/*` → tournament-server |
| Mod-Download | planet | statics (Caddy) | 443 | `rbbattle.zip` (Paketierung + md5-Parität) |
| rbmods-probe.timer | planet | systemd | — | Connectivity-Checks alle 2 Min → `status.json` |
| rbbridge | in Mod-Containern | Prozess | — | Command-Injection (`exec_cmd_client`, Argument IMMER als EIN gequotierter String) |

## Kanonische Landing

- **`site/` ist die einzige kanonische Landing** (GitHub Pages via
  `pages.yml`, Source-Pfad `site`, kein Build-Schritt). Der Download läuft über
  den **deployten Stand** `https://rift.projectmellon.de/mods/rbbattle.zip`
  (Caddy, vom Deploy atomar ausgetauscht). GitHub-Tags (`v*`) sind seit
  Issue #209 **reine Marker** — keine GitHub-Releases, keine Release-Artefakte.
- `docs/index.html` ist **keine zweite Landing** mehr: ein dünner
  Verweis/Redirect auf die Landing, ohne eigene Download-/Versions-Links.

## Deployment-Plan (Ansible, inventory `planet`)

Rollen in `deploy/roles/` (Details: `deploy/README.md`):

1. **mods-zip** — Mod aus `mod/` paketieren (`scripts/package_bausteine.sh`),
   `rbbattle.zip` nach planet; **md5-Paritäts-Check (Zip == Prod) hart als
   Fehlschlag**.
2. **headless-client-image** — baut `rb-headless-client:<deploy-sha>` IM
   Playbook auf planet aus `tools/headless-client` (gemeinsame Wine-Laufzeit
   für :6321 + :6322; Docker-Layer-Cache → billig/idempotent). Das gerenderte
   Compose pinnt exakt diesen Tag (kein `latest`).
3. **game-content** — Dedicated-Server-Content (Steam-App 4114030) deklarativ
   nach `riftbreaker_game_dir` (SteamCMD anonym; Fallback: idempotenter Sync aus
   kanonischem Cache). Konvergiert nach `rm -rf`; Fehlschlag ist laut.
4. **riftbreaker-server** — Docker-Container + Server-Config (Welt
   `mp_survival`/`jungle`, `disable_steam`, Passwort aus Vault), Mod-Install
   in `<game>/mods/rbbattle`; Restart-Handler bei Mod-/Config-Änderung.
5. **vanilla-server** — zweite Instanz ohne Mods (6322), gleiches Image.
6. **tournament-server** — systemd-Unit, Env-Konfig (`RBBRIDGE_A_URL`/
   `RBBRIDGE_B_URL`), Binary + Web-UI aus `tournament/`.
7. **website** — statische Dateien (`site/*`) nach Docroot, Caddy-Snippet
   (statics + `/tournament/*`-Proxy) + Reload.
8. **probe-timer** — systemd-Timer für `scripts/probe_servers.sh` →
   `status.json`.

Grundsätze:

- **Idempotent** — jeder Lauf konvergiert auf denselben Zustand.
- **Deploy nur via Playbook** —
  `ansible-playbook -i deploy/inventory deploy/site.yml --ask-vault-pass`.
- **Rollback** = vorherige `rbbattle.zip` / vorheriges Binary wieder einspielen.

## Continuous Deploy (CD) — Issue #91

Nach jedem Merge auf `main` deployt
[`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml) automatisch auf
den Solo-DEV-Server (planet, Port 6321) — **rolling**, immer der aktuelle Stand
zum Testen. Der Job läuft auf dem self-hosted Runner auf planet und stößt dort
den lokalen **HTTP-Deploy-Hook** an (`rbbattle-deploy-hook`, `127.0.0.1:6323`):
Der Hook macht `git fetch` + Hard-Checkout der Commit-SHA und führt das
Ansible-Playbook aus (`ansible-playbook -i deploy/inventory deploy/site.yml
--vault-password-file …`). **Kein SSH aus CI mehr.** Installation/Migration:
`deploy/README.md` → „CD: HTTP-Deploy-Hook".

Topologie (Momo-Entscheidung, 2026-09-10): **EIN** Server auf `:6321` statt
Steam-/Non-Steam-Dualität; ein Direct-IP-Server (`disable_steam "1"`) deckt
beide Stores ab. Der **Tag→prod-Kanal ist gestrichen**: `tags: ['v*']` sind seit
Issue #209 **reine Marker** (kein Tag-Trigger, keine GitHub-Releases, keine
prod-Umgebung im Workflow). Veröffentlichter Download ist der deployte Stand
`https://rift.projectmellon.de/mods/rbbattle.zip`.

Einziges GitHub-Secret ist `DEPLOY_TOKEN` im Environment `dev` (Bearer-Token
Hook ↔ Workflow). Das Vault-Passwort liegt ausschließlich root-only auf planet
(`/etc/rbbattle-deploy/vault.pass`) und wird nie im Repo oder in Logs ausgegeben.

## Server-Passwort (Vault)

Das Server-Passwort liegt **nie im Klartext** im Repo. Es steht in
`deploy/inventory/host_vars/planet/vault.yml` (Variable
`riftbreaker_server_password`) und wird mit `ansible-vault` verschlüsselt.
Befüllung: siehe `deploy/README.md` → „Vault". Für den CD-Hook liegt das
Vault-Passwort als root-only Datei auf planet — **niemals** auf GitHub.

## Aktueller Zustand (2026-09-10, Issue #209)

`deploy/` **besitzt den Stack**: das Playbook baut das Laufzeit-Image
(`rb-headless-client:<deploy-sha>`) selbst, provisioniert den Steam-Content und
pinnt das Compose auf den Deploy-SHA. Ein from-zero-Aufbau braucht keine
manuellen Schritte auf planet (`deploy/README.md` → „From-zero").

Der alte Community-Stack (`j3n5-group/riftbreaker-docker`, Steam-basiert) unter
`/srv/riftbreaker` ist abgelöst; `/srv/riftbreaker/data/server` bleibt als
**kanonischer Steam-Content-Cache** liegen (Sync-Fallback für `game-content`).
Mod-Instanzen: `/srv/rbgame` (:6321), `/srv/rbgame-vanilla` (:6322); Compose
unter `/opt/rbmods/compose/…`.

## Interim-Deploy :6321 (Issue #156) — historisch, durch #209 überholt

Bis zur CD (#91) wird die Mod auf :6321 manuell eingespielt (reproduzierbar):

```bash
# 1) Mod-Zip aus Repo main bauen (Content-Root = mod/):
bash scripts/package_bausteine.sh          # → dist/rbbattle.zip

# 2) Auf planet kopieren + md5-Parität (lokal == remote):
scp dist/rbbattle.zip planet:/tmp/rbbattle.zip
md5sum dist/rbbattle.zip                   # lokal
ssh planet md5sum /tmp/rbbattle.zip        # remote, muss übereinstimmen

# 3) Spieler-Check VOR jedem Neustart (leer = letzter Log `PauseGame`).
#    Sind Spieler online: NICHT neu starten, im Issue vermerken.
ssh planet 'tail -3 "/srv/riftbreaker/data/wine/drive_c/users/steamuser/Documents/The Riftbreaker/exor_logs.txt"'

# 4) Mod ersetzen (Backup + entpacken, Ownership beibehalten):
ssh planet 'cd /srv/riftbreaker/data/server/mods && \
  tar -czf rbbattle.bak-$(date +%Y%m%d-%H%M%S).tar.gz rbbattle && \
  rm -rf rbbattle && mkdir rbbattle && \
  unzip -q /tmp/rbbattle.zip -d rbbattle && chown -R momo:momo rbbattle'

# 5) Container neu starten:
ssh planet 'cd /srv/riftbreaker && docker compose restart riftbreaker-server'

# 6) Smoke-Test: mod_load version=<VERSION> status=ok, Container healthy, Port 6321/udp offen.
ssh planet 'grep -a mod_load "/srv/riftbreaker/data/wine/drive_c/users/steamuser/Documents/The Riftbreaker/exor_logs.txt" | tail -1'
```

Mod-Ordner (Host → Container): `data/server/mods/rbbattle` →
`/opt/riftbreaker/mods/rbbattle`. `exor_logs.txt`:
`data/wine/drive_c/users/steamuser/Documents/The Riftbreaker/`.
Rollback: Backup-`tar.gz` unter `data/server/mods/` zurückentpacken + neu starten.

## Betriebsregeln

- Mod-Parität vor jedem Release prüfen (md5).
- Live-Tests nur bei leerem Server.
- Keine Credentials in Repo/Logs; `exor_logs` im Container, `rbbridge.log` im Temp.
- SSH mesh-first (Tailscale), nie über Public-IPs.
