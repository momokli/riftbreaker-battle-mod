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

## Deploy

```bash
ansible-playbook -i deploy/inventory deploy/site.yml --ask-vault-pass
```

Reihenfolge der Rollen (site.yml): `mods-zip` → `riftbreaker-server` →
`vanilla-server` → `tournament-server` → `website` → `probe-timer`.

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
```

**Wichtig — Härtung vs. sudo:** Die Unit setzt `NoNewPrivileges=no` (alles
andere der Härtung bleibt aktiv) — mit `NoNewPrivileges=yes` würde jede
setuid-Eskalation und damit auch `sudo` scheitern, der Deploy könnte den
Playbook-Lauf nie starten. Die root-Eskalation über Ansible (`become: true`)
ist für dieses Playbook unvermeidbar; sie bleibt aber auf das eine
sudoers-Kommando begrenzt (Wrapper, ohne Argumente, root-owned). Der
Hook-Prozess selbst läuft weiterhin non-root als User `deploy`.

**SSH-Ziel:** Das Playbook verbindet sich weiterhin per SSH mit dem
Inventory-Host `planet` (mesh-first über den `~/.ssh/config`-Alias). Weil
jetzt auf planet selbst deployt wird, muss dieser Loopback-Weg dort
funktionieren (wie zuvor für den Runner-User; der Wrapper nutzt
`HOME=/opt/rbbattle-deploy` — dort ggf. `.ssh/config`/Keys hinterlegen).
Ersten echten Lauf im Job-Log unter `/var/log/rbbattle-deploy/` prüfen.

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

## Rollen

| Rolle | Typ | Was |
|---|---|---|
| `riftbreaker-server` | docker | Dev-SP-Server 6321 (1v1 vs sich selbst), Mod-Install |
| `vanilla-server` | docker | Vanilla 6322, kein Mod |
| `tournament-server` | systemd | Rust/axum Referee + Web-UI (Binary aus `tournament/`) |
| `website` | statics + Caddy | `site/*` → Docroot, Caddy-Snippet + `/tournament/*`-Proxy |
| `mods-zip` | — | Paketierung + md5-Paritäts-Check (hart) |
| `probe-timer` | systemd | `probe_servers.sh` alle 2 Min → `status.json` |

## Struktur

```text
deploy/
├── site.yml                       # Haupt-Playbook (Rollenreihenfolge)
├── hook.py                        # CD: HTTP-Deploy-Hook (Python-Stdlib)
├── rbbattle-deploy-hook.service   # CD: systemd-Unit für den Hook
├── inventory/
│   ├── hosts.yml                  # Host "planet" (mesh-first, Tailscale)
│   └── host_vars/planet/
│       ├── vars.yml               # nicht-geheime Konfiguration
│       └── vault.yml              # Geheimnis (ansible-vault verschlüsselt)
└── roles/
    ├── riftbreaker-server/        # docker 6321
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

Vorherige `rbbattle.zip` (Release/Git-History) bzw. das vorherige
`tournament-server`-Binary zurückkopieren und erneut deployen.
