# RIFT BATTLE — Deployment & Stack

> Ziel-Stack + Deployment-Plan. **Deploy NUR über das Ansible-Playbook in
> `deploy/`** (kein manuelles Gedudel). Umsetzung: Issue #45.

## Ziel-Stack (was IMMER betrieben wird)

| Komponente | Host | Container/Unit | Port | Zweck |
|---|---|---|---|---|
| riftbreaker-dedicated | planet | docker (wine) | 6321/udp | Dev-SP-Server: 1v1 „vs sich selbst" (SP-Mode; rbbattle-Mod + rbbridge) |
| rb-winetest | planet | docker (wine) | 6322/udp | Vanilla-Server (kein Mod, schnelle Test-Joins) |
| tournament-server | planet | systemd (Rust/axum, `tournament/`) | 8080 | Turnier 1v1: Lobby/Ready/GO/Wave-Routing/Score (2 Welten) |
| test-Instanzen | planet | docker, on-demand | frei | Test-Server aller Art (Mod-Tests, Balance, Experimente) |
| Website | planet | statics + Caddy (`mellon-caddy`) | 443 | Landing `/` · `/connectivity.html` · `/solo.html` · `/status.json` · Proxy `/tournament/*` → tournament-server |
| Mod-Download | planet | statics (Caddy) | 443 | `rbbattle.zip` (Paketierung + md5-Parität) |
| rbmods-probe.timer | planet | systemd | — | Connectivity-Checks alle 2 Min → `status.json` |
| rbbridge | in Mod-Containern | Prozess | — | Command-Injection (`exec_cmd_client`, Argument IMMER als EIN gequotierter String) |

## Kanonische Landing

- **`site/` ist die einzige kanonische Landing** (GitHub Pages via
  `pages.yml`, Source-Pfad `site`, kein Build-Schritt). Downloads bleiben
  GitHub Releases (dist-Zips).
- `docs/index.html` ist **keine zweite Landing** mehr: ein dünner
  Verweis/Redirect auf die Landing, ohne eigene Download-/Versions-Links.

## Deployment-Plan (Ansible, inventory `planet`)

Rollen in `deploy/roles/` (Details: `deploy/README.md`):

1. **mods-zip** — Mod aus `mod/` paketieren (`scripts/package_bausteine.sh`),
   `rbbattle.zip` nach planet; **md5-Paritäts-Check (Zip == Prod) hart als
   Fehlschlag**.
2. **riftbreaker-server** — Docker-Container + Server-Config (Welt
   `mp_survival`/`jungle`, `disable_steam`, Passwort aus Vault), Mod-Install
   in `<game>/mods/rbbattle`.
3. **vanilla-server** — zweite Instanz ohne Mods (6322).
4. **tournament-server** — systemd-Unit, Env-Konfig (`RBBRIDGE_A_URL`/
   `RBBRIDGE_B_URL`), Binary + Web-UI aus `tournament/`.
5. **website** — statische Dateien (`site/*`) nach Docroot, Caddy-Snippet
   (statics + `/tournament/*`-Proxy) + Reload.
6. **probe-timer** — systemd-Timer für `scripts/probe_servers.sh` →
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
zum Testen. Der Lauf nutzt ausschließlich das Ansible-Playbook
(`ansible-playbook -i deploy/inventory deploy/site.yml --vault-password-file …`),
SSH mesh-first über den Tailscale-Alias `planet`.

Topologie (Momo-Entscheidung, 2026-09-10): **EIN** Server auf `:6321` statt
Steam-/Non-Steam-Dualität; ein Direct-IP-Server (`disable_steam "1"`) deckt
beide Stores ab. Der **Tag→prod-Kanal ist vorerst gestrichen** — es gibt
bewusst keinen Tag-Trigger und keine prod-Umgebung im Workflow.

Secrets liegen ausschließlich als GitHub-Secrets im Environment `dev`
(`SSH_HOST`, `SSH_KEY`, `ANSIBLE_VAULT_PASS`) und werden nie im Repo oder in
Logs ausgegeben.

## Server-Passwort (Vault)

Das Server-Passwort liegt **nie im Klartext** im Repo. Es steht in
`deploy/inventory/host_vars/planet/vault.yml` (Variable
`riftbreaker_server_password`) und wird mit `ansible-vault` verschlüsselt.
Befüllung: siehe `deploy/README.md` → „Vault".

## Aktueller Zustand (2026-09-10)

Stack teilweise ad-hoc ohne Ansible (Docker manuell, Website aus manueller
Kopie — siehe Issue-Kommentar zur stale Landing). Gap: `deploy/` fehlte → Issue #45.

Der laufende Dev-SP-Server (:6321) ist ein Community-Docker-Setup
(`j3n5-group/riftbreaker-docker`) unter `/srv/riftbreaker` (Compose, Wine).
Die `deploy/`-Rollen (`riftbreaker-server`, `mods-zip`) sind der Zielstand,
aber auf planet noch nicht an diese Instanz angebunden — `/srv/rbgame` und
`/opt/rbmods/compose/…` existieren dort (noch) nicht.

## Interim-Deploy :6321 (Issue #156)

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
