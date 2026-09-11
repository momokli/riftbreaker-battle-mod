# deploy/ — Ansible-Deployment (planet)

Ziel-Stack + Betriebsregeln: [`docs/DEPLOYMENT.md`](../docs/DEPLOYMENT.md).
**Deploy NUR über dieses Playbook** — kein manuelles Gedudel. Seit Issue #91
läuft der CD (main→dev) über den lokalen **HTTP-Deploy-Hook** auf planet
(Abschnitt [„CD: HTTP-Deploy-Hook"](#cd-http-deploy-hook-planet)); der Hook
führt genau dieses Playbook aus.

## Voraussetzungen

- `ansible` (core ≥ 2.19) auf dem Control-Node (dem Rechner, von dem du deployst; beim CD ist das planet selbst, als root — siehe Hook-Abschnitt).
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
`game-content` → `riftbreaker-server` → `vanilla-server` → `tournament-server` →
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
Der **Tag→prod-Kanal ist vorerst gestrichen** — der Workflow kennt bewusst
keinen Tag-Trigger und keine prod-Umgebung.

## CD: HTTP-Deploy-Hook (planet)

Seit der Umstellung (Issue #91) deployt der Workflow **ohne SSH**:

```text
push auf main
  → GitHub-Actions-Job auf dem self-hosted Runner (planet)
  → POST http://127.0.0.1:6323/deploy  {sha, ref}      [Bearer DEPLOY_TOKEN]
  → Hook (systemd: rbbattle-deploy-hook)
       git fetch + Hard-Checkout der SHA im CHECKOUT_DIR
       → DEPLOY_CMD (führt `ansible-playbook … site.yml` aus)
  → Workflow pollt GET /deploy/<job_id>/status bis success/failed (max. 10 min)
```

Hook-Port ist bewusst **6323**: `6321` ist auf planet vom laufenden Dev-Server
belegt (docker-proxy, TCP+UDP), `6322` gehört der Vanilla-Instanz — auf
`127.0.0.1:6323` lauscht sonst niemand (auf planet verifiziert, 2026-09-10).

**Einziges GitHub-Secret:** `DEPLOY_TOKEN` (Environment `dev`). Die alten
Secrets `SSH_HOST`, `SSH_KEY`, `ANSIBLE_VAULT_PASS` werden nicht mehr benutzt
und können gelöscht werden. Das Vault-Passwort verlässt planet nicht.

Dateien: [`hook.py`](hook.py) (Python-Stdlib-HTTP-Server für `/deploy` +
Status, Job-Logs unter `/var/log/rbbattle-deploy/`) und
[`rbbattle-deploy-hook.service`](rbbattle-deploy-hook.service) (systemd,
User `deploy`, Hardening).

### Installation (einmalig, auf planet)

Voraussetzungen: root-Shell auf planet; das Repo ist öffentlich (anonymes
`git fetch` genügt). Platzhalter `<TOKEN>` = Ergebnis von `openssl rand -hex 32`.

```bash
# 1) Service-User + Verzeichnisse
sudo useradd --system --home /opt/rbbattle-deploy --shell /usr/sbin/nologin deploy
sudo install -d -o deploy -g deploy -m 0750 /opt/rbbattle-deploy
sudo install -d -o deploy -g deploy -m 0750 /var/log/rbbattle-deploy
sudo install -d -o root   -g deploy -m 0750 /etc/rbbattle-deploy

# 2) Checkout initial klonen (öffentliches Repo, nur Lesen)
sudo -u deploy git clone https://github.com/momokli/riftbreaker-battle-mod.git \
  /opt/rbbattle-deploy/repo

# 3) Token erzeugen — Ausgabe an ZWEI Stellen nötig (planet + GitHub):
openssl rand -hex 32

# 4) hook.env schreiben (root:deploy 0640; <TOKEN> ersetzen):
sudo install -o root -g deploy -m 0640 /dev/null /etc/rbbattle-deploy/hook.env
sudo tee /etc/rbbattle-deploy/hook.env >/dev/null <<'EOF'
LISTEN=127.0.0.1:6323
DEPLOY_TOKEN=<TOKEN>
CHECKOUT_DIR=/opt/rbbattle-deploy/repo
DEPLOY_CMD=sudo -n /usr/local/bin/rbbattle-deploy
VAULT_PASS_FILE=/etc/rbbattle-deploy/vault.pass
EOF
sudo stat -c '%U:%G %a' /etc/rbbattle-deploy/hook.env   # → root:deploy 640

# 5) Hook + Unit installieren und starten:
sudo install -m 0755 deploy/hook.py /opt/rbbattle-deploy/hook.py
sudo install -m 0644 deploy/rbbattle-deploy-hook.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rbbattle-deploy-hook
systemctl status rbbattle-deploy-hook --no-pager

# 6) Smoke-Test (Antwort muss 202 + {"job_id": …} sein):
TOKEN="$(sudo awk -F= '/^DEPLOY_TOKEN=/{print $2}' /etc/rbbattle-deploy/hook.env)"
SHA="$(git -C /opt/rbbattle-deploy/repo rev-parse HEAD)"
curl -sS -X POST http://127.0.0.1:6323/deploy \
  -H "Authorization: Bearer ${TOKEN}" -H 'Content-Type: application/json' \
  -d "{\"sha\":\"${SHA}\",\"ref\":\"refs/heads/main\"}"
```

GitHub-Seite (Environment `dev`) — Token aus Schritt 3:

```bash
gh secret set DEPLOY_TOKEN --env dev --repo momokli/riftbreaker-battle-mod
gh secret list --env dev --repo momokli/riftbreaker-battle-mod
gh secret delete SSH_HOST --env dev --repo momokli/riftbreaker-battle-mod
gh secret delete SSH_KEY --env dev --repo momokli/riftbreaker-battle-mod
gh secret delete ANSIBLE_VAULT_PASS --env dev --repo momokli/riftbreaker-battle-mod
```

### Root-Weg (Standard): Ansible `become` über eng begrenztes sudoers

Das Playbook läuft mit `become: true` und braucht root — dieser Weg ist der
**Standard für den CD**. Deshalb **nicht** die Hook-Unit als root betreiben:
Der Hook bleibt non-root (User `deploy`); root gibt es ausschließlich über das
enge sudoers-Snippet unten — NOPASSWD **nur** für den root-owned Wrapper
`/usr/local/bin/rbbattle-deploy` (ohne Argumente), der das `ansible-playbook`
aus dem gepinnten venv ausführt.

```bash
# a) Ansible root-owned installieren (gepinnt; deploy darf nicht schreiben):
sudo python3 -m venv /opt/rb-ansible
sudo /opt/rb-ansible/bin/pip install --disable-pip-version-check "ansible-core==2.19.*"

# b) Root-Wrapper (führt das Playbook im Checkout aus; ignoriert Argumente):
sudo tee /usr/local/bin/rbbattle-deploy >/dev/null <<'EOF'
#!/bin/sh
set -eu
export HOME=/opt/rbbattle-deploy
export ANSIBLE_CONFIG=/etc/rbbattle-deploy/ansible.cfg
cd /opt/rbbattle-deploy/repo
exec /opt/rb-ansible/bin/ansible-playbook \
  -i deploy/inventory deploy/site.yml \
  --vault-password-file /etc/rbbattle-deploy/vault.pass
EOF
sudo chown root:root /usr/local/bin/rbbattle-deploy
sudo chmod 0755 /usr/local/bin/rbbattle-deploy

# c) Vault-Passwort root-only ablegen (entsperrt deploy/.../vault.yml):
sudo install -o root -g root -m 0600 /dev/null /etc/rbbattle-deploy/vault.pass
sudo <editor> /etc/rbbattle-deploy/vault.pass     # Passwort eintragen

# d) Enges sudoers-Snippet — nur dieses Kommando, ohne Argumente:
sudo tee /etc/sudoers.d/rbbattle-deploy >/dev/null <<'EOF'
deploy ALL=(root) NOPASSWD: /usr/local/bin/rbbattle-deploy ""
EOF
sudo chmod 0440 /etc/sudoers.d/rbbattle-deploy
sudo visudo -cf /etc/sudoers.d/rbbattle-deploy

# e) Loopback-SSH für den Hook-/Sandbox-Kontext (Details: „SSH-Ziel")
sudo install -d -o root -g root -m 0700 /opt/rbbattle-deploy/.ssh
sudo ssh-keyscan -t ed25519 100.77.143.105 >> /opt/rbbattle-deploy/.ssh/known_hosts
sudo install -m 0600 -o root -g root /root/.ssh/id_ed25519 /etc/rbbattle-deploy/id_ed25519
sudo tee /etc/rbbattle-deploy/ssh_config >/dev/null <<'EOF'
Host planet
  HostName 100.77.143.105
  User root
  IdentityFile /etc/rbbattle-deploy/id_ed25519
  IdentitiesOnly yes
  BatchMode yes
  StrictHostKeyChecking accept-new
  UserKnownHostsFile /opt/rbbattle-deploy/.ssh/known_hosts
EOF
sudo chmod 600 /etc/rbbattle-deploy/ssh_config
sudo tee /etc/rbbattle-deploy/ansible.cfg >/dev/null <<'EOF'
[defaults]
remote_tmp = /opt/rbbattle-deploy/.ansible/tmp
[ssh_connection]
ssh_args = -F /etc/rbbattle-deploy/ssh_config
EOF
sudo chmod 640 /etc/rbbattle-deploy/ansible.cfg
```

**Wichtig — Härtung vs. sudo:** Die Unit setzt `NoNewPrivileges=no` (alles
andere der Härtung bleibt aktiv) — mit `NoNewPrivileges=yes` würde jede
setuid-Eskalation und damit auch `sudo` scheitern, der Deploy könnte den
Playbook-Lauf nie starten. Die root-Eskalation über Ansible (`become: true`)
ist für dieses Playbook unvermeidbar; sie bleibt aber auf das eine
sudoers-Kommando begrenzt (Wrapper, ohne Argumente, root-owned). Der
Hook-Prozess selbst läuft weiterhin non-root als User `deploy`.

**SSH-Ziel:** Das Playbook verbindet sich weiterhin per SSH mit dem
Inventory-Host `planet` (mesh-first über Tailscale). Auf planet verifiziert
(2026-09-10): `ssh` liest `~/.ssh/config` aus dem passwd-Home (`/root`) —
das `HOME`-Env des Wrappers genügt dafür nicht, und `/root` ist in der
Unit-Sandbox (`ProtectHome=yes`) unsichtbar. Deshalb liegt die
Loopback-Konfiguration root-only unter `/etc/rbbattle-deploy/`: `ssh_config`
(nutzt per `ssh -F` den Key `/etc/rbbattle-deploy/id_ed25519`) +
`ansible.cfg` (`ssh_args = -F …` + `remote_tmp` für die Sandbox), aktiviert über
`ANSIBLE_CONFIG=/etc/rbbattle-deploy/ansible.cfg` im Wrapper; known_hosts
unter `/opt/rbbattle-deploy/.ssh/`. Ersten echten Lauf im Job-Log unter
`/var/log/rbbattle-deploy/` prüfen.

### Betrieb

```bash
systemctl status rbbattle-deploy-hook               # Service-Zustand
journalctl -u rbbattle-deploy-hook -f               # Live-Log (Requests/Fehler)
ls -lt /var/log/rbbattle-deploy/                    # Job-Logs (0640)
curl -sS http://127.0.0.1:6323/deploy/<job_id>/status \
  -H "Authorization: Bearer ${TOKEN}"               # {status, exit_code, log_tail}
```

`hook.py` aktualisieren (bei Änderungen am Hook):
`sudo install -m 0755 deploy/hook.py /opt/rbbattle-deploy/hook.py`
+ `sudo systemctl restart rbbattle-deploy-hook`.

### Migrations-Checkliste

- [ ] `deploy`-User + Verzeichnisse angelegt (Schritt 1)
- [ ] Checkout geklont (Schritt 2), Token + `hook.env` (Schritte 3–4)
- [ ] Hook + Unit aktiv, Smoke-Test 202 (Schritte 5–6)
- [ ] Root-Weg: Ansible (venv) + Wrapper + sudoers + `vault.pass`
- [ ] `vault.yml` verschlüsselt + befüllt (falls noch `CHANGE_ME`)
- [ ] GitHub: `DEPLOY_TOKEN` gesetzt, SSH-Secrets gelöscht
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
| `headless-client-image` | docker | baut `rb-headless-client:<deploy-sha>` auf planet (gemeinsame Laufzeit :6321/:6322) |
| `game-content` | steamcmd/sync | Dedicated-Server-Content (App 4114030) nach `riftbreaker_game_dir` (idempotent, fail loud) |
| `riftbreaker-server` | docker | Dev-SP-Server 6321 (1v1 vs sich selbst), Mod-Install + Restart-Handler |
| `vanilla-server` | docker | Vanilla 6322, kein Mod, gleiches Image |
| `tournament-server` | systemd | Rust/axum Referee + Web-UI (Binary aus `tournament/`) |
| `website` | statics + Caddy | `site/*` → Docroot, Caddy-Snippet + `/tournament/*`-Proxy |
| `mods-zip` | — | Paketierung + md5-Paritäts-Check (hart) |
| `probe-timer` | systemd | `probe_servers.sh` alle 2 Min → `status.json` |

## Struktur

```text
deploy/
├── site.yml                       # Haupt-Playbook (pre_tasks + Rollenreihenfolge)
├── check-render.yml               # deploy-check: rendert Compose-Templates lokal
├── hook.py                        # CD: HTTP-Deploy-Hook (Python-Stdlib)
├── rbbattle-deploy-hook.service   # CD: systemd-Unit für den Hook
├── inventory/
│   ├── hosts.yml                  # Host "planet" (mesh-first, Tailscale)
│   └── host_vars/planet/
│       ├── vars.yml               # nicht-geheime Konfiguration
│       └── vault.yml              # Geheimnis (ansible-vault verschlüsselt)
└── roles/
    ├── headless-client-image/     # baut rb-headless-client:<sha>
    ├── game-content/              # Steam-Content (App 4114030) deklarativ
    ├── riftbreaker-server/        # docker 6321 (+ Restart-Handler)
    ├── vanilla-server/            # docker 6322
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
- **Deploy-Hook:** lauscht nur auf `127.0.0.1`; beide Endpunkte benötigen das
  Bearer-Token (constant-time compare); die SHA aus dem Payload wird strikt
  validiert und nie in Shell-Kommandos interpoliert; Job-Logs `0640` unter
  `/var/log/rbbattle-deploy/`. Kein Root-Service — root nur über enges
  sudoers (ein Kommando, ohne Argumente).

## Rollback

Der Deploy ist an die Revision (SHA) gebunden (Image-Tag + Checkout). Rollback
= alte SHA deployen:

```bash
# auf planet (root-Weg) — alten Stand auschecken und deployen:
cd /opt/rbbattle-deploy/repo && git checkout --force <alte-sha>
sudo -n /usr/local/bin/rbbattle-deploy
# oder den Hook mit der alten SHA anstoßen (Workflow-tauglich):
curl -sS -X POST http://127.0.0.1:6323/deploy \
  -H "Authorization: Bearer <TOKEN>" -H 'Content-Type: application/json' \
  -d '{"sha":"<alte-sha>","ref":"refs/heads/main"}'
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
| `/var/log/rbbattle-deploy/<job_id>.log` | CD-Job-Log (Hook, 0640; Task `deploy.yml` zeigt bei Fehlern die letzten Zeilen) |
| `journalctl -u rbbattle-deploy-hook` | Hook-Service (Requests/Fehler) |
| `journalctl -u tournament-server`, `-u rbmods-probe.timer` | systemd-Rollen |
| `docker logs riftbreaker-dedicated` / `rb-winetest` | Container-Logs (Wine/Server) |
| `git -C /opt/rbbattle-deploy/repo log --oneline -3` | zuletzt deployte SHA |

## Was CI/CD besitzt (und was nicht)

**Owned von der Pipeline (`deploy/`):** Laufzeit-Image (`rb-headless-client:<sha>`),
Spiel-Content (Steam-App 4114030), Compose-Rendering + Containerstart der
Server-Rollen, Mod-Auslieferung + Restart, Website-Statics + Caddy-Snippet,
Caddy-Import-Zeile, systemd-Unit/Timer (tournament/probe), md5-Parität des
Mod-Zips.

**Nicht owned (bewusst host-seitig/manuell):** Vault-Passwort
(`/etc/rbbattle-deploy/vault.pass`, root-only), Hook-Installation + `DEPLOY_TOKEN`
(siehe unten), der Actions-Runner + seine Dependencies
(`.github/runner/setup.sh`), der SSH-Zugang des Runners für `deploy-check`,
Caddy-Container selbst (`mellon-caddy`), DNS/TLS.
