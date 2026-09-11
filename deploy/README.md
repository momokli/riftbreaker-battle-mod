# deploy/ — Ansible-Deployment (planet)

Ziel-Stack + Betriebsregeln: [`docs/DEPLOYMENT.md`](../docs/DEPLOYMENT.md).
**Deploy NUR über dieses Playbook** — kein manuelles Gedudel. Seit 2026-09-11
läuft der CD (main→dev) per **SSH über einen dedizierten deploy-User** auf
planet (Abschnitt [„CD: SSH-Deploy"](#cd-ssh-deploy-dedizierter-deploy-user));
die forced command führt genau dieses Playbook aus.

## Voraussetzungen

- `ansible` (core ≥ 2.19) auf dem Control-Node (dem Rechner, von dem du deployst; beim CD ist das planet selbst, als root — siehe CD-Abschnitt).
- SSH mesh-first: Alias `planet` in `~/.ssh/config` (Tailscale), `root`-Login.
- Auf planet: Docker + `docker compose`, `systemd`,
  Caddy als Container `mellon-caddy`.
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

## Solo-Page-Zugangsschutz (`/solo`, Issue #159)

Die Operator-Match-Page `/solo` ([site/solo.html](../site/solo.html)) wird per
**Caddy basic_auth** geschützt. Der Passwort-**Hash** liegt ausschließlich im
Vault — **kein Wert im Repo, in Logs oder PRs**; dokumentiert ist nur der Name.

- **Variablenname (dokumentiert, ohne Wert):** `vault_solo_basic_auth_hash`
  (Vault, bcrypt) → nicht-geheime Referenz `solo_basic_auth_hash` in
  `inventory/host_vars/planet/vars.yml`.
- **Benutzer:** `solo_basic_auth_user` (Default `operator`).
- **ENV-Variablenname (Betreiber setzt den Wert host-seitig, nie im Repo):**
  `SOLO_BASIC_AUTH_HASH`.

Hash erzeugen und in den Vault übernehmen (**Wert nie ausgeben/committen**):

```bash
# bcrypt-Hash im Caddy-Container erzeugen; Ausgabe direkt in den Vault übernehmen:
docker exec -i mellon-caddy caddy hash-password
ansible-vault edit deploy/inventory/host_vars/planet/vault.yml
#    → vault_solo_basic_auth_hash: <bcrypt-hash>
```

Ist der Hash leer/nicht gesetzt, bleibt `/solo` bewusst **ungeschützt**
(graceful Default) — der Deploy bricht nicht ab. Das Caddy-Snippet routet
`/solo` außerdem auf `/solo.html`.

## Deploy

```bash
ansible-playbook -i deploy/inventory deploy/site.yml --ask-vault-pass
```

Reihenfolge der Rollen (site.yml): `mods-zip` → `headless-client-image` →
`game-content` → `riftbreaker-server` → `tournament-server` →
`website` → `probe-timer`.

### From-zero (ein Kommando, Issue #209)

`deploy/` ist die **einzige Quelle der Wahrheit**: Auf einem frischen Host
reicht ein Lauf — es gibt keine manuellen „einmalig auf planet"-Schritte.

```bash
# 1) Vault-Passwort bereitstellen (siehe „Vault"; root-only auf dem Zielhost).
# 2) Ein Kommando:
ansible-playbook -i deploy/inventory deploy/site.yml --ask-vault-pass
```

Was das Playbook selbst besitzt:

- **Laufzeit-Image** (`headless-client-image`): baut
  `rb-headless-client:<deploy-sha>` auf dem Zielhost aus
  `tools/headless-client` (Wine + Xvfb + Mesa-llvmpipe). Der Tag ist der
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

### Vault

Wie bisher: Server-Passwort nur in `deploy/inventory/host_vars/planet/vault.yml`
(`ansible-vault`), Passwort-Referenz via `--ask-vault-pass` bzw. beim CD root-only
unter `/etc/rbbattle-deploy/vault.pass`. **Nie** im Repo/Log.

## Continuous Deploy (CD)

Nach jedem Merge auf `main` rollt der Workflow
[`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml) den aktuellen
Mod-Stand automatisch auf den Solo-DEV-Server aus (planet, Port 6321).

Topologie (Issue #91, 2026-09-10): **EIN** Server auf `:6321` statt
Steam-/Non-Steam-Dualität (Direct-IP, `disable_steam "1"` deckt beide Stores ab).
Der **Tag→prod-Kanal ist on hold** (vorerst gestrichen): reaktiviert, sobald
ein **zweites Deploy-Target** existiert — aktuell gibt es genau EINEN Server
(planet, :6321). Der Workflow kennt bewusst keinen Tag-Trigger und keine
prod-Umgebung.

## CD: SSH-Deploy (dedizierter deploy-User)

Seit 2026-09-11 deployt der Workflow **per SSH über einen dedizierten
deploy-User** (ersetzt den früheren HTTP-Hook aus #91 — kein Token, kein
Polling, Ergebnis-Streaming direkt im Job-Log):

```text
push auf main
  → GitHub-Actions-Job auf dem self-hosted Runner (planet)
  → ssh rbd "<sha> <ref>"                     [Runner-Key, User deploy]
  → forced command /opt/rbbattle-deploy/deploy-ssh.sh (läuft als deploy):
       SHA validieren → git fetch + Hard-Checkout im Checkout
       → sudo -n /usr/local/bin/rbbattle-deploy (ansible-playbook als root)
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
root-owned Wrapper `/usr/local/bin/rbbattle-deploy` (ohne Argumente), der das
`ansible-playbook` aus dem gepinnten venv ausführt. Der deploy-User selbst
läuft durchgehend non-root (forced command + git-Checkout als deploy).

```bash
# a) Ansible root-owned installieren (gepinnt; deploy darf nicht schreiben):
sudo python3 -m venv /opt/rb-ansible
sudo /opt/rb-ansible/bin/pip install --disable-pip-version-check "ansible-core==2.19.*"

# b) Root-Wrapper (führt das Playbook im Checkout aus; ignoriert Argumente):
sudo tee /usr/local/bin/rbbattle-deploy >/dev/null <<'WRAPPER'
#!/bin/sh
set -eu
export HOME=/opt/rbbattle-deploy
export ANSIBLE_CONFIG=/etc/rbbattle-deploy/ansible.cfg
cd /opt/rbbattle-deploy/repo
exec /opt/rb-ansible/bin/ansible-playbook \
  -i deploy/inventory deploy/site.yml \
  --vault-password-file /etc/rbbattle-deploy/vault.pass
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

[`.github/workflows/deploy-check.yml`](../.github/workflows/deploy-check.yml) ist
der Required Check `deploy-check` und läuft auf dem planet-Runner. Er prüft
read-only gegen planet: `yamllint` über `deploy/`, `docker compose config` für
jede gerenderte Compose-Datei und `ansible-playbook --check --diff`
(`--tags server,website`). Das **Vault wird nie entschlüsselt**: für den Lauf
wird ein Dummy-Vault in ein temporäres Inventar kopiert. Nur PRs aus diesem
Repo (keine Forks).

Host-Voraussetzungen (einmalig, **nicht** im Repo — Secrets bleiben host-seitig):

```bash
# 1) Ansible + yamllint im Runner-Home (macht der Workflow selbst, idempotent).
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

| Rolle | Typ | Was |
|---|---|---|
| `headless-client-image` | docker | baut `rb-headless-client:<deploy-sha>` auf planet (Laufzeit :6321) |
| `game-content` | steamcmd/sync | Dedicated-Server-Content (App 4114030) nach `riftbreaker_game_dir` (idempotent, fail loud) |
| `riftbreaker-server` | docker | Dev-SP-Server 6321 (1v1 vs sich selbst), Mod-Install + Restart-Handler + Guard (keine Fremd-Mods in `mods/`) + Post-Deploy-Verifikation |
| `tournament-server` | systemd | Rust/axum Referee + Web-UI. Binary aus `tournament/` — wird beim Deploy auf planet gebaut (Rust-Toolchain via rustup unter `/opt/rbbattle-deploy/`, idempotent von der Rolle bereitgestellt) |
| `website` | statics + Caddy | `site/*` → Docroot, Caddy-Snippet + `/tournament/*`-Proxy |
| `mods-zip` | — | Paketierung + md5-Paritäts-Check (hart) |
| `probe-timer` | systemd | `probe_servers.sh` alle 2 Min → `status.json` |

## Struktur

```text
deploy/
├── site.yml                       # Haupt-Playbook (pre_tasks + Rollenreihenfolge)
├── check-render.yml               # deploy-check: rendert Compose-Templates lokal
├── deploy-ssh.sh                  # CD: forced command für den deploy-User (SSH)
├── inventory/
│   ├── hosts.yml                  # Host "planet" (mesh-first, Tailscale)
│   └── host_vars/planet/
│       ├── vars.yml               # nicht-geheime Konfiguration
│       └── vault.yml              # Geheimnis (ansible-vault verschlüsselt)
└── roles/
    ├── headless-client-image/     # baut rb-headless-client:<sha>
    ├── game-content/              # Steam-Content (App 4114030) deklarativ
    ├── riftbreaker-server/        # docker 6321 (+ Restart-Handler)
    ├── tournament-server/         # systemd
    ├── website/                   # statics + Caddy
    ├── mods-zip/                  # Paketierung + md5-Parität
    └── probe-timer/               # systemd-Timer
```

## Sicherheit

- **Kein Klartext-Secret im Repo** — Passwort nur via Vault; das
  Vault-Passwort liegt root-only auf planet (`/etc/rbbattle-deploy/vault.pass`).
- Compose-/Vault-Dateien mit Passwort: restriktive Rechte am Ziel
  (Compose-Datei `0600`, sie enthält das Server-Passwort im `command`).
- Kein Deploy ohne Freigabe; nichts manuell am produktiven Server.
- SSH ausschließlich mesh-first über den `planet`-Alias (Tailscale).
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

## Logs

| Ort | Was |
|---|---|
| `gh run view <id> --log` | CD-Job-Log (der `ssh`-Step streamt das ganze ansible-Log) |
| `journalctl -u tournament-server`, `-u rbmods-probe.timer` | systemd-Rollen |
| `docker logs riftbreaker-dedicated` | Container-Logs (Wine/Server) |
| `git -C /opt/rbbattle-deploy/repo log --oneline -3` | zuletzt deployte SHA |

## Was CI/CD besitzt (und was nicht)

**Owned von der Pipeline (`deploy/`):** Laufzeit-Image (`rb-headless-client:<sha>`),
Spiel-Content (Steam-App 4114030), Compose-Rendering + Containerstart der
Server-Rollen, Mod-Auslieferung + Restart, Website-Statics + Caddy-Snippet,
Caddy-Import-Zeile, systemd-Unit/Timer (tournament/probe), md5-Parität des
Mod-Zips.

**Nicht owned (bewusst host-seitig/manuell):** Vault-Passwort
(`/etc/rbbattle-deploy/vault.pass`, root-only), SSH-Zugang + forced command des
deploy-Users (siehe CD-Abschnitt), der Actions-Runner + seine Dependencies
(`.github/runner/setup.sh`), der SSH-Zugang des Runners für `deploy-check`,
Caddy-Container selbst (`mellon-caddy`), DNS/TLS.

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
