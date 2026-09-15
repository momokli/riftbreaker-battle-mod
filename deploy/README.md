# deploy/ — Ansible-Deployment (planet + satellite)

Ziel-Stack + Betriebsregeln: [`docs/DEPLOYMENT.md`](../docs/DEPLOYMENT.md) ·
Host-Anforderungen (CPU/RAM/Storage je Szenario):
[`docs/SERVER_SIZING.md`](../docs/SERVER_SIZING.md).
**Deploy NUR über dieses Playbook** — kein manuelles Gedudel. Seit 2026-09-11
läuft der CD (main→dev) per **SSH über einen dedizierten deploy-User** auf
planet (Abschnitt [„CD: SSH-Deploy"](#cd-ssh-deploy-dedizierter-deploy-user));
die forced command führt genau dieses Playbook aus.

## Voraussetzungen

- `ansible` (core ≥ 2.19) auf dem Control-Node (dem Rechner, von dem du deployst; beim CD ist das planet selbst, als root — siehe CD-Abschnitt).
- SSH mesh-first: Aliase `planet` (dev-/prod-Dedi) **und** `satellite`
  (Relay-Host) in `~/.ssh/config` (Tailscale), jeweils `root`-Login. Wie `planet`
  ist `satellite` ein Mesh-Alias — SSH **niemals** über die Public-IP
  (`65.21.181.48` ist nur für den öffentlichen UDP-Port). Ohne den Alias läuft
  `deploy/deploy-prod.yml` (`hosts: satellite`) ins Leere.
- Auf planet: Docker + `docker compose`, `systemd`,
  Caddy als geteilter Container `mellon-caddy` (Host-Gateway) — der Rift-Stack
  betreibt zusätzlich einen eigenen, schlanken `rift-caddy` (Image `caddy:2`,
  Issue #322).
- Auf dem Control-Node: `zip` **oder** `python3` (für
  `scripts/package_bausteine.sh` — die Paketierung läuft dort, nicht im
  Playbook-Ziel; siehe Rolle `mods-zip`, `delegate_to: localhost`; beim CD
  ist der Control-Node planet selbst).

## Vault (Server-Passwort)

Das Server-Passwort liegt **nie im Klartext** im Repo. Einmalig einrichten:

```bash
# 1) Datei verschlüsseln und Wert eintragen (statt "CHANGE_ME"):
ansible-vault encrypt deploy/inventory/host_vars/planet/vault.yml
ansible-vault edit   deploy/inventory/host_vars/planet/vault.yml
#    → riftbreaker_server_password: <echtes Server-Passwort>
```

Beim Lauf das Vault-Passwort angeben:

```bash
ansible-playbook -i deploy/inventory deploy/site.yml --ask-vault-pass
```

Alternativ `vault_password_file = .vault_pass` in `ansible.cfg` setzen und die
Datei lokal ablegen (`chmod 600`, **nicht** committen).

Für den CD-Betrieb liegt das Vault-Passwort ausschließlich auf planet
(`/etc/rbbattle-deploy/vault.pass`, root-only) — **nie** als GitHub-Secret,
nie im Repo, nie in Logs.

## Deploy

```bash
ansible-playbook -i deploy/inventory deploy/site.yml --ask-vault-pass
```

Reihenfolge der Rollen (site.yml): `mods-zip` → `dedicated-server-image` →
`game-content` → `riftbreaker-server` → `tournament-server` →
`website` → `image-retention` → `host-hygiene` → `crash-collector`.

**Vor** den Rollen (in den `pre_tasks`) prüft ein Preflight den freien Platz auf
`/` (Disk-Space-Gate, Issue #310): zu wenig Platz → Abbruch **vor** Image-Build
und Backup-Tarball. Schwelle `riftbreaker_disk_min_free_gb` (Default 10 GB),
Details in [`docs/DEPLOYMENT.md`](../docs/DEPLOYMENT.md#disk-space-gate--deploy-bremse-vor-voller-platte-issue-310).

### From-zero (ein Kommando, Issue #209)

`deploy/` ist die **einzige Quelle der Wahrheit**: Auf einem frischen Host
reicht ein Lauf — es gibt keine manuellen „einmalig auf planet"-Schritte.

```bash
# 1) Vault-Passwort bereitstellen (siehe „Vault"; root-only auf dem Zielhost).
# 2) Ein Kommando:
ansible-playbook -i deploy/inventory deploy/site.yml --ask-vault-pass
```

Was das Playbook selbst besitzt:

- **Laufzeit-Image** (`dedicated-server-image`): baut
  `rb-dedicated:<deploy-sha>` auf dem Zielhost aus
  `tools/dedicated-server` (Wine-Laufzeit für :6321, Community-Rezept). Der Tag ist der
  Deploy-SHA der ausgecheckten Revision; das gerenderte `docker-compose.yml`
  referenziert **exakt** diesen Tag (kein `latest`). Der Tag im Namen macht den
  Lauf trivially idempotent: unveränderter Stand → Image existiert → kein Build;
  geänderter Stand → Docker-Layer-Cache, billig. Vor dem Containerstart prüft
  die Rolle hart, dass das Image existiert.
- **Spiel-Content** (`game-content`): provisioniert Steam-App `4114030`
  deklarativ nach `riftbreaker_game_dir` (SteamCMD anonym,
  `+app_update 4114030 validate`). Idempotent (No-Op, wenn das Server-Binary da
  ist) und konvergent nach `rm -rf` des Game-Dirs. **Fail loud**: schlägt
  SteamCMD fehl oder fehlt das Binary danach, bricht der Deploy ab — nie ein
  stiller Deploy ohne Spieldateien.
  - Fallback (falls SteamCMD anonym nicht zuverlässig ist): idempotenter Sync
    aus einem kanonischen Cache —
    `-e riftbreaker_content_mode=sync -e riftbreaker_content_cache_dir=/srv/riftbreaker/data/server`
    (setzt einen vollständigen Steam-Stand im Cache voraus).
  - Patch erzwingen: `-e riftbreaker_content_force=true`.
- **Mod-Auslieferung** (`riftbreaker-server`): Mod-Install nur bei geändertem
  md5-Marker; der Marker-Schreibvorgang **notifyt einen Restart-Handler**
  (`docker compose up -d --force-recreate`). Ohne den bliebe ein reines
  Mod-Update wirkungslos (Compose startet einen unveränderten Container nicht
  neu) — Ziel: „Merge → Mod ist auf :6321 wirklich geladen".

### Prod-Instanz + Satellite-Relay (Issue #328)

Zweite, **koexistierende** Instanz auf planet (`:6322`) plus der Relay-Host
`satellite`, der als „IP-Lender" den client-seitig hardgewireten Port `6321`
per DNAT auf die prod-Instanz übersetzt:

```bash
# Vault-Passwort nötig (Server-Passwort), analog site.yml:
ansible-playbook -i deploy/inventory deploy/deploy-prod.yml \
  -e @deploy/prod-vars.yml --ask-vault-pass
```

- `deploy/deploy-prod.yml`: Play 1 = prod-Stack auf `planet` (`mods-zip` →
  `dedicated-server-image` → `game-content` → `rbtools` → `riftbreaker-server`),
  Play 2 = Rolle `satellite-relay` auf `satellite`.
- `deploy/prod-vars.yml`: Overrides der prod-Instanz (eigene Container-/Port-/
  Pfad-/Volume-Namen, IO-Bridge `9002`), damit sie nicht mit dev (`:6321`)
  kollidiert. Das Einfrieren auf einen Git-Tag ist Follow-up.
- Rolle `satellite-relay`: reboot-fester UDP-DNAT auf dem Satellite (inbound
  `satellite_relay_port` → `satellite_relay_target_host:target_port`) via
  iptables-PREROUTING + MASQUERADE, persistiert über eine systemd-Oneshot-Unit;
  nur Core-Module.
- **Einzige Quelle der `satellite_relay_*`-Werte** sind die Rollen-Defaults
  (`roles/satellite-relay/defaults/main.yml`) — bewusst **keine**
  `host_vars/satellite/`, damit keine Precedence-Falle entsteht (host_vars
  würde die Defaults still überschreiben). Die Rolle läuft nur gegen `satellite`.

Der Relay ergänzt den dev-Stack; `deploy/site.yml` (CD) bleibt unverändert der
dev-Rollout.

### Vault

Wie bisher: Server-Passwort nur in `deploy/inventory/host_vars/planet/vault.yml`
(`ansible-vault`), Passwort-Referenz via `--ask-vault-pass` bzw. beim CD root-only
unter `/etc/rbbattle-deploy/vault.pass`. **Nie** im Repo/Log.

## Continuous Deploy (CD)

Nach jedem Merge auf `main` rollt der Workflow
[`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml) den aktuellen
Mod-Stand automatisch auf den Solo-**DEV**-Server aus (planet, Port 6321).

Topologie (Issue #328): **zwei** Instanzen, ein Dedi-Port.

- **DEV** (planet, `:6321`): rolling, von diesem CD-Workflow deployt
  (`deploy/site.yml`, Werte aus `inventory/host_vars/planet/`).
- **PROD** (planet, `:6322`): koexistierende zweite Instanz
  (`riftbreaker-dedicated-prod`), öffentlich erreichbar über den
  **Satellite-Relay** (eigene IPv4, inbound `:6321` → DNAT → planet `:6322`).
  Deploy separat per [`deploy-prod.yml`](deploy-prod.yml) +
  [`prod-vars.yml`](prod-vars.yml). Der Client ist effektiv auf Port `6321`
  hardgewired — der zweite öffentliche Zugang läuft deshalb über eine zweite
  **Adresse** (den Satellite), nicht über einen zweiten Port.

Der **Tag→prod-Kanal ist verdrahtet**: ein Tag-Push `v*` rollt
`deploy/deploy-prod.yml` (+ `-e @deploy/prod-vars.yml`) auf prod aus. Der
forced command reicht den `<ref>` per Marker-Datei an den root-Wrapper, der
`main` → `site.yml` (dev) und `refs/tags/v*` → `deploy-prod.yml` (prod)
dispatched (siehe [„CD: SSH-Deploy"](#cd-ssh-deploy-dedizierter-deploy-user)).

```text
push auf main          → deploy-dev  (site.yml,          dev  :6321)
push auf Tag v*        → deploy-prod (deploy-prod.yml,   prod :6322 via Satellite)
```

## CD: SSH-Deploy (dedizierter deploy-User)

Seit 2026-09-11 deployt der Workflow **per SSH über einen dedizierten
deploy-User** (ersetzt den früheren HTTP-Hook aus #91 — kein Token, kein
Polling, Ergebnis-Streaming direkt im Job-Log):

```text
push auf main / push auf Tag v*
  → GitHub-Actions-Job auf dem self-hosted Runner (planet)
  → ssh rbd "<sha> <ref>"                     [Runner-Key, User deploy]
  → forced command /opt/rbbattle-deploy/deploy-ssh.sh (läuft als deploy):
       SHA validieren → <sha>/<ref> in Marker-Dateien schreiben
       → sudo -n /usr/local/bin/rbbattle-deploy (als root):
            git fetch + Hard-Checkout (als root) → chown -R deploy:deploy
            refs/heads/main → site.yml        (dev)
            refs/tags/v*    → deploy-prod.yml (prod)
  → exit code = Deploy-Ergebnis (kein Polling, kein Secret)
```

**Kein GitHub-Secret nötig** — Auth läuft über den SSH-Key des Runners
(`~/.ssh/id_rb_deploy`), der auf planet im `authorized_keys` des deploy-Users
**nur** die forced command ausführen darf (`from="127.0.0.1"`, kein PTY, kein
Port-Forwarding). Das Vault-Passwort verlässt planet nicht. Das alte
`DEPLOY_TOKEN`-Secret im Environment `dev` ist obsolet und kann gelöscht
werden.

### Installation (einmalig, auf planet)

Voraussetzungen: root-Shell auf planet; das Repo ist öffentlich (anonymes
`git fetch` genügt). `deploy/deploy-ssh.sh` ist die Quelle für Schritt 2.

```bash
# 1) Service-User + Verzeichnisse + Checkout:
sudo useradd --system --home /opt/rbbattle-deploy --shell /usr/sbin/nologin deploy
sudo install -d -o deploy -g deploy -m 0750 /opt/rbbattle-deploy
sudo -u deploy git clone https://github.com/momokli/riftbreaker-battle-mod.git \
  /opt/rbbattle-deploy/repo

# 2) forced-command-Skript installieren (root-owned, 0755):
sudo install -m 0755 deploy/deploy-ssh.sh /opt/rbbattle-deploy/deploy-ssh.sh

# 3) Runner-Key als forced command für deploy freigeben (nur von 127.0.0.1):
sudo install -d -o deploy -g deploy -m 0700 /home/deploy/.ssh
sudo sh -c 'printf "from=\"127.0.0.1\",command=\"/opt/rbbattle-deploy/deploy-ssh.sh\",no-pty,no-port-forwarding,no-agent-forwarding,no-X11-forwarding %s\n" "$(cat /home/runner/.ssh/id_rb_deploy.pub)" > /home/deploy/.ssh/authorized_keys'
sudo chown deploy:deploy /home/deploy/.ssh/authorized_keys
sudo chmod 600 /home/deploy/.ssh/authorized_keys

# 4) Runner-SSH-Alias (User deploy):
sudo tee -a /home/runner/.ssh/config >/dev/null <<'SSHALIAS'

Host rbd
  HostName 127.0.0.1
  User deploy
  IdentityFile ~/.ssh/id_rb_deploy
  IdentitiesOnly yes
  StrictHostKeyChecking accept-new
SSHALIAS

# 5) Smoke-Test (führt den ECHTEN Deploy aus; exit 0 = grün):
sudo -u runner ssh -o BatchMode=yes rbd \
  "$(git -C /opt/rbbattle-deploy/repo rev-parse origin/main) refs/heads/main"
```

### Root-Weg (Standard): Ansible `become` über eng begrenztes sudoers

Das Playbook läuft mit `become: true` und braucht root. Root gibt es
ausschließlich über das enge sudoers-Snippet unten — NOPASSWD **nur** für den
root-owned Wrapper `/usr/local/bin/rbbattle-deploy` (ohne Argumente), der den
git-Checkout (als root) + `ansible-playbook` aus dem gepinnten venv ausführt.
Der deploy-User läuft nur für Validierung + Marker-Schreiben non-root.

```bash
# a) Ansible root-owned installieren (gepinnt; deploy darf nicht schreiben):
sudo python3 -m venv /opt/rb-ansible
sudo /opt/rb-ansible/bin/pip install --disable-pip-version-check "ansible-core==2.19.*"

# b) Root-Wrapper (führt das Playbook im Checkout aus; dispatched dev/prod
#    anhand der Marker-Datei /opt/rbbattle-deploy/.deploy-ref, die der forced
#    command vorher geschrieben hat — main -> site.yml, Tag v* -> deploy-prod.yml):
sudo tee /usr/local/bin/rbbattle-deploy >/dev/null <<'WRAPPER'
#!/bin/sh
set -eu
export HOME=/opt/rbbattle-deploy
export ANSIBLE_CONFIG=/etc/rbbattle-deploy/ansible.cfg

# Checkout als root + danach Ownership normalisieren: Agent-/RE-Arbeit legt
# auf planet teils root-owned Dateien ab; liefe der Checkout als deploy,
# schlüge er mit "unable to unlink ... Permission denied" fehl.
sha="$(cat /opt/rbbattle-deploy/.deploy-sha 2>/dev/null || true)"
cd /opt/rbbattle-deploy/repo
git fetch --prune --quiet origin
git checkout --force "$sha" >/dev/null
chown -R deploy:deploy /opt/rbbattle-deploy/repo

ref="$(cat /opt/rbbattle-deploy/.deploy-ref 2>/dev/null || true)"
case "$ref" in
  refs/tags/v*)
    exec /opt/rb-ansible/bin/ansible-playbook \
      -i deploy/inventory deploy/deploy-prod.yml \
      -e @deploy/prod-vars.yml \
      --vault-password-file /etc/rbbattle-deploy/vault.pass
    ;;
  *)
    exec /opt/rb-ansible/bin/ansible-playbook \
      -i deploy/inventory deploy/site.yml \
      --vault-password-file /etc/rbbattle-deploy/vault.pass
    ;;
esac
WRAPPER
sudo chown root:root /usr/local/bin/rbbattle-deploy
sudo chmod 0755 /usr/local/bin/rbbattle-deploy

# c) Vault-Passwort root-only ablegen (entsperrt deploy/.../vault.yml):
sudo install -o root -g root -m 0600 /dev/null /etc/rbbattle-deploy/vault.pass
sudo <editor> /etc/rbbattle-deploy/vault.pass     # Passwort eintragen

# d) Enges sudoers-Snippet — nur dieses Kommando, ohne Argumente:
sudo tee /etc/sudoers.d/rbbattle-deploy >/dev/null <<'SUDOERS'
deploy ALL=(root) NOPASSWD: /usr/local/bin/rbbattle-deploy ""
SUDOERS
sudo chmod 0440 /etc/sudoers.d/rbbattle-deploy
sudo visudo -cf /etc/sudoers.d/rbbattle-deploy

# e) Loopback-SSH für den Wrapper-Kontext (ansible → planet, mesh-first):
sudo install -d -o root -g root -m 0700 /opt/rbbattle-deploy/.ssh
sudo ssh-keyscan -t ed25519 100.77.143.105 >> /opt/rbbattle-deploy/.ssh/known_hosts
sudo install -m 0600 -o root -g root /root/.ssh/id_ed25519 /etc/rbbattle-deploy/id_ed25519
sudo tee /etc/rbbattle-deploy/ssh_config >/dev/null <<'SSHCONF'
Host planet
  HostName 100.77.143.105
  User root
  IdentityFile /etc/rbbattle-deploy/id_ed25519
  IdentitiesOnly yes
  BatchMode yes
  StrictHostKeyChecking accept-new
  UserKnownHostsFile /opt/rbbattle-deploy/.ssh/known_hosts
SSHCONF
sudo chmod 600 /etc/rbbattle-deploy/ssh_config
sudo tee /etc/rbbattle-deploy/ansible.cfg >/dev/null <<'ANSIBLECFG'
[defaults]
remote_tmp = /opt/rbbattle-deploy/.ansible/tmp
[ssh_connection]
ssh_args = -F /etc/rbbattle-deploy/ssh_config
ANSIBLECFG
sudo chmod 640 /etc/rbbattle-deploy/ansible.cfg
```

**SSH-Ziel:** Das Playbook verbindet sich per SSH mit dem Inventory-Host
`planet` (mesh-first über Tailscale). `ssh` liest `~/.ssh/config` aus dem
passwd-Home (`/root`) — das `HOME`-Env des Wrappers genügt dafür nicht.
Deshalb liegt die Loopback-Konfiguration root-only unter
`/etc/rbbattle-deploy/`: `ssh_config` (nutzt per `ssh -F` den Key
`/etc/rbbattle-deploy/id_ed25519`) + `ansible.cfg` (`ssh_args = -F …` +
`remote_tmp`), aktiviert über `ANSIBLE_CONFIG=/etc/rbbattle-deploy/ansible.cfg`
im Wrapper; known_hosts unter `/opt/rbbattle-deploy/.ssh/`.

### Betrieb

```bash
# Deploy manuell anstoßen (exit code = Ergebnis; Ausgabe = ansible-Log):
sudo -u runner ssh -o BatchMode=yes rbd "<sha> refs/heads/main"
# Zuletzt deployte SHA:
git -C /opt/rbbattle-deploy/repo log --oneline -3
```

`deploy/deploy-ssh.sh` aktualisieren (bei Änderungen):
`sudo install -m 0755 deploy/deploy-ssh.sh /opt/rbbattle-deploy/deploy-ssh.sh`.

### Park, Force & Timeout (Issue #238)

Seit 2026-09-12 parkt der CD-Lauf **vor** dem SSH-Deploy, bis **0 Spieler
online** sind (Gate-Step in `deploy.yml`, Provider
`tools/deploy-gate/player_count.py`, Quelle = Container-Log). Der geparkte
Zustand steht im Job-Log (`deploy-gate: geparkt (n Spieler online)`) und in der
Step-Summary („geparkt, n Spieler online, warte auf 0").

- **Force (Sofort-Deploy):** Actions → „Deploy (CD)" → **Run workflow** auf
  `main` mit `force=true` — deployt unabhängig von der Spielerzahl.
- **Timeout:** Park-Deadline ist per Default 1800 s (30 min; Job
  `timeout-minutes: 60`), überschreibbar über den Dispatch-Input `timeout`.
  Läuft die Deadline ab, wird der Step **rot** und es findet **kein** Deploy
  statt (fail loud, nichts hängt unbegrenzt).
- **Re-run:** Bei Timeout/Failure den Workflow **re-run** (oder Dispatch mit
  `force=true`).

**Troubleshooting:** Parkt der Lauf direkt nach einem Server-Restart, obwohl
niemand spielt, fehlt im Log noch die `PauseGame`-Zeile → der Provider ist
bewusst konservativ unsicher und parkt bis Timeout (nie blind deployen). Ausweg:
re-run oder `force=true`.

### Migrations-Checkliste

- [ ] `deploy`-User + Verzeichnisse + Checkout (Schritt 1)
- [ ] forced command + `authorized_keys` (Schritte 2–3), Alias `rbd` (Schritt 4)
- [ ] Smoke-Test grün (Schritt 5)
- [ ] Root-Weg: Ansible (venv) + Wrapper + sudoers + `vault.pass` + Loopback-SSH
- [ ] `vault.yml` verschlüsselt + befüllt (falls noch `CHANGE_ME`)
- [ ] Alter Hook dekommissioniert: `systemctl disable --now rbbattle-deploy-hook` + Unit-Datei entfernt
- [x] GitHub: `DEPLOY_TOKEN`-Secret gelöscht (obsolet)
- [ ] Erster Merge auf `main`: Deploy-Lauf grün

## deploy-check (PR-Gate)

Das PR-Gate ist seit Issue #349 in **zwei Workflows** aufgeteilt; **required ist
nur `deploy-check-local`**:

- [`deploy-check-local.yml`](../.github/workflows/deploy-check-local.yml)
  (**Required Check**, GitHub-Hosted-Runner, `ubuntu-latest`) prüft rein
  lokal: `yamllint` über `deploy/`, Playbook-`--syntax-check` für `site.yml` +
  `deploy-prod.yml` (prod-Playbook, Issue #328), Compose-Templates rendern
  (`check-render.yml`) und jedes gerenderte Compose-File durch
  `docker compose config`. Kein Host-/SSH-Zugriff, keine Secrets. Läuft damit
  immer, auch wenn der planet-Runner gerade nicht erreichbar ist.
- [`deploy-check.yml`](../.github/workflows/deploy-check.yml)
  (self-hosted Runner, planet) fährt den echten Host-Check read-only gegen
  planet: `ansible-playbook --check --diff` (`--tags server,website`). Seit
  Issue #349 ist dieser Lauf **informational** (nicht required): ein transienter
  `startup_failure` des planet-Runners reißt den lokalen Required-Check nicht
  mehr mit, die Rückmeldung („Image fehlt / Container-Name belegt / Pfad
  falsch") bleibt aber sichtbar.

Das **Vault wird nie entschlüsselt**: für den Lauf wird ein Dummy-Vault in ein
temporäres Inventar kopiert. Nur PRs aus diesem Repo (keine Forks).

Host-Voraussetzungen (einmalig, **nicht** im Repo — Secrets bleiben host-seitig):

```bash
# 1) Ansible im Runner-Home (deploy-check, planet; macht der Workflow selbst,
#    idempotent). yamllint nur für deploy-check-local auf dem GH-Runner.
# 2) SSH-Brücke runner@planet -> root@planet über den Alias `planet`:
sudo -u runner ssh-keygen -t ed25519 -N '' -f /home/runner/.ssh/id_rb_deploy
sudo -u runner cat /home/runner/.ssh/id_rb_deploy.pub \
  | sudo tee -a /root/.ssh/authorized_keys >/dev/null   # from="127.0.0.1" empfohlen
# /home/runner/.ssh/config:
#   Host planet
#     HostName 127.0.0.1
#     User root
#     IdentityFile ~/.ssh/id_rb_deploy
#     IdentitiesOnly yes
sudo -u runner ssh planet id -u     # muss "0" liefern
```

Hinweis: Der Runner-User hat bereits (über die `docker`-Gruppe)
root-äquivalenten Zugriff; der SSH-Weg ist nur der Zugang für den read-only
`--check`.

## Rollen

| Rolle                    | Typ                | Was                                                                                                                                                                                                                                                   |
| ------------------------ | ------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `dedicated-server-image` | docker             | baut `rb-dedicated:<deploy-sha>` auf planet (Laufzeit :6321)                                                                                                                                                                                          |
| `game-content`           | steamcmd/sync      | Dedicated-Server-Content (App 4114030) nach `riftbreaker_game_dir` (idempotent, fail loud)                                                                                                                                                            |
| `riftbreaker-server`     | docker             | Dev-SP-Server 6321 (1v1 vs sich selbst), Mod-Install + Restart-Handler + Guard (keine Fremd-Mods in `mods/`) + Post-Deploy-Verifikation                                                                                                               |
| `satellite-relay`        | iptables + systemd | UDP-DNAT-Relay auf `satellite`: inbound `:6321` → planet prod `:6322` (reboot-fest; Rollen-Defaults = einzige Wertquelle)                                                                                                                             |
| `tournament-server`      | systemd            | Rust/axum Referee + Web-UI. Binary aus `tournament/` — wird beim Deploy auf planet gebaut (Rust-Toolchain via rustup unter `/opt/rbbattle-deploy/`, idempotent von der Rolle bereitgestellt)                                                          |
| `website`                | eigener Caddy      | eigener `rift-caddy` (plain HTTP: Landing + `/mod.zip` + Cockpit `/contract/*` + `/tournament/*`) + ZWEI Einträge im geteilten Host-Caddy (Issue #322); Host-Caddy-Reload deterministisch + fehlersichtbar, `rift-caddy` mit `admin off` (Issue #355) |
| `mods-zip`               | —                  | Paketierung + md5-Paritäts-Check (hart)                                                                                                                                                                                                               |
| `host-hygiene`           | systemd            | wöchentlicher Timer: entfernt **dangling** Docker-Images (`docker image prune`, **kein** `-a`; Issue #308)                                                                                                                                            |
| `crash-collector`        | systemd            | Dauer-Dienst: sichert bei Crash-Markern das neueste `crash_info/<uuid>.{dmp,log,trace}` als Bundle nach `/opt/rbmods/crashes/` (+ Kontext/Meta, Retention; Issue #462)                                                                                  |

## Host-Caddy-Reload (Issue #355)

Der Deploy schreibt genau EINEN Eintrag in `/home/momo/Caddyfile` und lädt den
geteilten Host-Caddy (`mellon-caddy`) per `docker exec … caddy validate` +
`caddy reload`. Zwei Details halten den Lauf deterministisch und sichtbar:

- **Admin-Port gehört exklusiv dem Host-Caddy.** `caddy reload` dialt
  `localhost:2019`. Der eigene `rift-caddy` läuft im `network_mode: host` und
  setzt deshalb `admin off` (siehe `rift-caddy.Caddyfile.j2`). Sonst belegen
  zwei Prozesse `127.0.0.1:2019`; das Kernel-Load-Balancing schickt den Reload
  dann mal an den plain `caddy:2` (ohne cloudflare-DNS-Provider) → HTTP 400
  `unknown module: dns.providers.cloudflare`.
- **Kein stilles `ok`.** Der Reload hängt nicht mehr allein am `changed` der
  Caddyfile-Tasks: jeder Lauf liest die LIVE-Config aus der Admin-API und
  reloadet, wenn der Domain-Eintrag dort fehlt (fängt einen früher
  fehlgeschlagenen Reload ab). Schlägt `validate`/`reload` fehl oder fehlt der
  Eintrag danach, bricht der Deploy ab.

## Struktur

```text
deploy/
├── site.yml                       # Haupt-Playbook dev (pre_tasks + Rollenreihenfolge)
├── deploy-prod.yml                # Prod-Instanz (planet :6322) + Satellite-Relay
├── prod-vars.yml                  # Overrides der prod-Instanz (dev-kokexistierend)
├── test-deploy.yml / test-vars.yml # Boot-Test-Instanz (CI)
├── check-render.yml               # deploy-check-local: rendert Compose-Templates lokal
├── deploy-ssh.sh                  # CD: forced command für den deploy-User (SSH)
├── inventory/
│   ├── hosts.yml                  # Hosts "planet" (dev/prod) + "satellite" (Relay, mesh-first)
│   └── host_vars/planet/
│       ├── vars.yml               # nicht-geheime Konfiguration
│       └── vault.yml              # Geheimnis (ansible-vault verschlüsselt)
└── roles/
    ├── dedicated-server-image/    # baut rb-dedicated:<sha>
    ├── game-content/              # Steam-Content (App 4114030) deklarativ
    ├── riftbreaker-server/        # docker 6321 (+ Restart-Handler)
    ├── satellite-relay/           # UDP-DNAT-Relay (planet prod :6322 via satellite)
    ├── tournament-server/         # systemd
    ├── website/                   # eigener rift-caddy: Landing + Cockpit (Issue #322)
    ├── mods-zip/                  # Paketierung + md5-Parität
    ├── host-hygiene/              # systemd-Timer (dangling Images, #308)
    └── crash-collector/           # systemd-Dienst (Crash-Artefakte, #462)
```

## Sicherheit

- **Kein Klartext-Secret im Repo** — Passwort nur via Vault; das
  Vault-Passwort liegt root-only auf planet (`/etc/rbbattle-deploy/vault.pass`).
- Compose-/Vault-Dateien mit Passwort: restriktive Rechte am Ziel
  (Compose-Datei `0600`, sie enthält das Server-Passwort im `command`).
- Kein Deploy ohne Freigabe; nichts manuell am produktiven Server.
- SSH ausschließlich mesh-first über die Aliase `planet` und `satellite`
  (Tailscale) — nie über Public-IPs.
- **SSH-Deploy:** der Runner-Key darf im `authorized_keys` des deploy-Users
  NUR die forced command ausführen (`from="127.0.0.1"`, kein PTY, kein
  Port-/Agent-Forwarding); die SHA wird im Skript strikt validiert und nie in
  Shell-Kommandos interpoliert. Kein Root-Login — root nur über enges sudoers
  (ein Kommando, ohne Argumente).

## Rollback

Der Deploy ist an die Revision (SHA) gebunden (Image-Tag + Checkout). Rollback
= alte SHA deployen:

```bash
# auf planet (root-Weg) — alten Stand auschecken und deployen:
cd /opt/rbbattle-deploy/repo && git checkout --force <alte-sha>
sudo -n /usr/local/bin/rbbattle-deploy
# oder per SSH forced command (Workflow-tauglich):
sudo -u runner ssh -o BatchMode=yes rbd "<alte-sha> refs/heads/main"
```

Nur das Mod zurückdrehen (ohne Image/Content):

```bash
# vorherige rbbattle.zip aus der Git-History bauen/sichern und erneut deployen.
```

Das vorherige `tournament-server`-Binary bzw. die vorherige `rbbattle.zip`
(git-History) zurückkopieren und erneut deployen.

**Image-Rollback-Stand:** Ein Image-Rollback braucht das **getaggte** Image
der alten Revision (`rb-dedicated:<alte-sha>`). Die Rolle `host-hygiene`
entfernt **nur dangling (ungetaggte)** Images — getaggte Rollback-Stände
bleiben also liegen. Solange der Tag existiert, ist ein Rollback ohne Neu-Build
möglich.

## Host-Hygiene — dangling Docker-Images (Issue #308)

Auf `planet` sammeln sich ungetaggte (`<none>`) Docker-Layer an (Messung
2026-09-12: 351 Stück / 88,9 GB). Die Rolle `host-hygiene` räumt sie
automatisch auf:

```bash
docker image prune          # = dangling only, KEIN -a
```

- **Automatik statt Handarbeit:** systemd-Timer `rbmods-host-hygiene.timer`
  (wöchentlich, `Persistent=true` — holt verpasste Läufe nach Reboot nach),
  Unit + Skript werden vom Playbook installiert. Das Skript
  (`scripts/host_hygiene.sh`) protokolliert Vorher/Nachher-Zähler ins Journal.
- **⚠️ Niemals `docker image prune -a`:** `-a` entfernt **alle** Images ohne
  laufenden Container — inklusive des getaggten Rollback-Stands
  `rb-dedicated:<alte-sha>`. Ohne den ist der nächste kaputte Deploy nicht mehr
  zurückrollbar.
- **Wirkung host-weit:** dangling Images entstehen in allen Stacks auf planet
  (nicht nur im Mod-Stack); der Prune wirkt deshalb absichtlich host-weit.
  Abgestimmt mit der Host-Infra (momokli/planet-media#8).
- **Wie lange bleiben dangling Images liegen?** Bis zum nächsten Timer-Lauf,
  also bis zu ~7 Tage (wöchentlicher Takt). Getaggte Images sind davon nicht
  betroffen.

Manuell prüfen (read-only):

```bash
docker system df -v                                   # was ist dangling/tagged/in use
docker images -f dangling=true -q | wc -l             # Anzahl dangling
journalctl -u rbmods-host-hygiene.timer -n 20         # letzter Timer-Lauf
```

Der **Einmal-Lauf** über die bereits liegenden ~88,9 GB ist ein
Operator-Schritt (nicht Teil des Deploys): auf planet `docker image prune`
ausführen, danach `df -h /` zum Messen.

## Logs

| Ort                                                               | Was                                                       |
| ----------------------------------------------------------------- | --------------------------------------------------------- |
| `gh run view <id> --log`                                          | CD-Job-Log (der `ssh`-Step streamt das ganze ansible-Log) |
| `journalctl -u tournament-server`, `-u rbmods-host-hygiene.timer` | systemd-Rollen                                            |
| `docker logs riftbreaker-dedicated`                               | Container-Logs (Wine/Server)                              |
| `git -C /opt/rbbattle-deploy/repo log --oneline -3`               | zuletzt deployte SHA                                      |

## Was CI/CD besitzt (und was nicht)

**Owned von der Pipeline (`deploy/`):** Laufzeit-Image (`rb-dedicated:<sha>`),
Spiel-Content (Steam-App 4114030), Compose-Rendering + Containerstart der
Server-Rollen, Mod-Auslieferung + Restart, eigener `rift-caddy` (Landing + `/mod.zip` + Cockpit `/contract/*` + `/tournament/*`) + ZWEI Host-Caddy-Einträge,
systemd-Unit/Timer (tournament/hygiene), md5-Parität des Mod-Zips.

**Nicht owned (bewusst host-seitig/manuell):** Vault-Passwort
(`/etc/rbbattle-deploy/vault.pass`, root-only), SSH-Zugang + forced command des
deploy-Users (siehe CD-Abschnitt), der Actions-Runner + seine Dependencies
(`.github/runner/setup.sh`), der SSH-Zugang des Runners für `deploy-check`,
der geteilte Host-Caddy-Container selbst (`mellon-caddy` — die Rolle schreibt
nur den einen Rift-Eintrag und reloadet ihn sichtbar fehlschlagend; DNS/TLS
bleiben host-seitig). Damit dessen Admin-Port `127.0.0.1:2019` exklusiv bleibt,
hält der `rift-caddy` seinen Admin-Port aus (`admin off`, Issue #355).

## Mod-Backups & mods/-Guard (Issue #212)

Mod-Backups liegen **nie** in `<server>/mods/` (der Dedicated Server lädt jeden
Ordner mit `*.manifest` als eigene Mod → Versionskonflikt + doppelte Handler).
Die Rolle `riftbreaker-server`

- sichert den alten Mod-Stand nach `{{ riftbreaker_backup_dir }}` (`rbbattle-<ts>.tar.gz`),
- fährt vor dem Deploy einen **Guard** (Fremd-Ordner mit `*.manifest` in `mods/`
  werden weggeschoben bzw. der Deploy bricht ab),
- **verifiziert** nach dem Deploy `docker logs` (genau eine `event=mod_load`-Zeile,
  erwartete Version, keine `handler_errors`/`event_unreadable`) und rollt sonst
  aus dem Backup zurück.

Regel, Befund und Kontrollwerkzeug (`tools/mods-guard/check_mods_dir.py`):
[`docs/DEPLOYMENT.md`](../docs/DEPLOYMENT.md) → „Mod-Backups & mods/-Guard".
