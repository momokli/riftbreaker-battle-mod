# deploy/ — Ansible-Deployment (planet)

Ziel-Stack + Betriebsregeln: [`docs/DEPLOYMENT.md`](../docs/DEPLOYMENT.md) ·
Host-Anforderungen (CPU/RAM/Storage je Szenario):
[`docs/SERVER_SIZING.md`](../docs/SERVER_SIZING.md) · Eigener Rechner statt
planet: [`LOCAL_DEV.md`](LOCAL_DEV.md).
**Deploy NUR über dieses Playbook** — kein manuelles Gedudel. Seit 2026-09-11
läuft der CD (main→dev) per **SSH über einen dedizierten deploy-User** auf
planet (Abschnitt [„CD: SSH-Deploy"](#cd-ssh-deploy-dedizierter-deploy-user));
die forced command führt genau dieses Playbook aus.

## Voraussetzungen

- `ansible` (core ≥ 2.19) auf dem Control-Node (dem Rechner, von dem du deployst; beim CD ist das planet selbst, als root — siehe CD-Abschnitt).
- SSH mesh-first: Aliase `planet` (dev-/prod-/staging-Dedi) **sowie** die
  beiden früheren Relay-Hosts `satellite` und `sync` in `~/.ssh/config`
  (Tailscale), jeweils `root`-Login. Wie `planet` sind `satellite`/`sync`
  Mesh-Aliase — SSH **niemals** über die Public-IP (`65.21.181.48` bzw.
  `65.21.253.64`). Die DNAT-Relays auf `satellite`/`sync` sind seit Issue #846
  **abgebaut**; die Hosts bleiben im Inventory, weil die Playbook-Plays (2) den
  Teardown idempotent nachfahren. Ohne den Alias läuft
  `deploy/deploy-prod.yml` (`hosts: satellite`) bzw.
  `deploy/deploy-staging.yml` (`hosts: sync`) ins Leere.
- Auf planet: Docker + `docker compose`, `systemd`,
  Caddy als geteilter Container `mellon-caddy` (Host-Gateway) — der Rift-Stack
  betreibt zusätzlich einen eigenen, schlanken `rift-caddy` (Image `caddy:2`,
  Issue #322).
- Auf dem Control-Node: `zip` **oder** `python3` (für
  `scripts/package.sh` — die Paketierung läuft dort, nicht im
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
  `deploy/dedicated-server` (Wine-Laufzeit für den Dedicated-Server, Community-Rezept). Der Tag ist der
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
  neu) — Ziel: „Merge → Mod ist auf :6324 (dev) wirklich geladen".

### Prod-Instanz + Relay-Abbau (Issue #328/#846)

Zweite, **koexistierende** Instanz auf planet (`:6322`). Der öffentliche
Einstieg läuft seit Issue #843/#846 **ausschließlich** über den GNS-Entry-Relay
auf planet:6321 (Suffix-Routing, s.u.); die früheren UDP-DNAT-Relays auf
`satellite`/`sync` sind **stillgelegt und werden abgebaut**:

```bash
# Vault-Passwort nötig (Server-Passwort), analog site.yml:
ansible-playbook -i deploy/inventory deploy/deploy-prod.yml \
  -e @deploy/prod-vars.yml --ask-vault-pass
```

- `deploy/deploy-prod.yml`: Play 1 = prod-Stack auf `planet` (`mods-zip` →
  `dedicated-server-image` → `game-content` → `rbtools` → `riftbreaker-server`),
  Play 2 = Rolle `satellite-relay` auf `satellite` mit
  `satellite_relay_state: absent` (**Teardown**).
- `deploy/prod-vars.yml`: Overrides der prod-Instanz (eigene Container-/Port-/
  Pfad-/Volume-Namen, IO-Bridge `9002`), damit sie nicht mit dev (`:6324`)
  kollidiert. Das Einfrieren auf einen Git-Tag ist Follow-up.
- Rolle `satellite-relay`: Zustandsschalter `satellite_relay_state` (Default
  `present`, Play-Var `absent`):
  - `present` = reboot-fester UDP-DNAT auf dem Relay-Host (inbound
    `satellite_relay_port` → `satellite_relay_target_host:target_port`) via
    iptables-PREROUTING + MASQUERADE, persistiert über eine systemd-Oneshot-Unit.
  - `absent` = Teardown: Script mit `down` (entfernt DNAT/MASQUERADE-Regeln,
    toleriert deren Abwesenheit), Unit stoppen + disablen, Unit-Datei /
    `/usr/local/bin/rbbattle-satellite-relay.sh` / `sysctl.d`-Datei löschen,
    UFW-Regeln zurücknehmen, `daemon-reload`. Idempotent + check-mode-sicher.
  - Nur Core-Module.
- Die Rolle lief früher gegen **zwei** Relay-Hosts: `satellite` (prod,
  `:6321 → :6322`) und `sync` (staging, `:6321 → :6323`); das Ziel-Port je
  Relay kam **auf Play-Ebene** (`satellite_relay_target_port`). Seit Issue #846
  fahren beide Plays den **Abbau** (`state: absent`) — bewusst **keine**
  `host_vars/satellite/` oder `host_vars/sync/`, damit keine Precedence-Falle
  entsteht.

Der Relay ergänzte den dev-Stack; `deploy/site.yml` (CD) bleibt unverändert der
dev-Rollout. Staging nutzte dasselbe Muster: `deploy/deploy-staging.yml` Play 2
fuhr die Rolle auf `sync` und baut sie dort (mit `satellite_relay_target_port:
6323` für den Regel-Match) jetzt ab.

### DNS (manuell, host-seitig bei Cloudflare)

Seit der Konsolidierung auf **einen** Einstieg (planet:6321, `gns-relay`) zeigen
die öffentlichen A-Records auf planet statt auf die Relay-Hosts:

| Record                     | Ziel (A)       |
| -------------------------- | -------------- |
| `drift.projectmellon.de`   | `65.21.27.234` |
| `rift.projectmellon.de`    | `65.21.27.234` |
| `staging.projectmellon.de` | `65.21.27.234` |

Der GNS-Entry-Relay unterscheidet dev/prod/staging über den **Spielnamen-**
Suffix, nicht über die Adresse. Der DNS-Schritt ist **nicht repo-owned** und
muss manuell gepflegt werden (Host-Einstieg/Cockpit-Domains); er ist Teil von
Issue #846.

### Vault

Wie bisher: Server-Passwort nur in `deploy/inventory/host_vars/planet/vault.yml`
(`ansible-vault`), Passwort-Referenz via `--ask-vault-pass` bzw. beim CD root-only
unter `/etc/rbbattle-deploy/vault.pass`. **Nie** im Repo/Log.

## Continuous Deploy (CD)

Nach jedem Merge auf `main` rollt der Workflow
[`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml) den aktuellen
Mod-Stand automatisch auf den Solo-**DEV**-Server aus (planet).

Topologie (Issue #328 + staging + GNS-Entry-Relay #843, Konsolidierung #846):
**drei** koexistierende Instanzen auf planet. Der client-hardgewirete
Einstiegsport `:6321` gehört auf planet dem **GNS-Entry-Relay** (Rolle
`gns-relay`), das GNS terminiert und per Spielnamen-Suffix auf die Backends
routet. Es gibt **einen einzigen öffentlichen Einstieg** (`65.21.27.234:6321`);
die früheren DNAT-Relays auf `satellite`/`sync` sind abgebaut.

| Env     | Trigger        | Host   | Game-Port | Öffentlicher Einstieg (planet `:6321`)           |
| ------- | -------------- | ------ | --------- | ------------------------------------------------ |
| DEV     | push `main`    | planet | `:6324`   | GNS-Entry-Relay, Spielname `*-dev` → `:6324`     |
| PROD    | push Tag `v*`  | planet | `:6322`   | GNS-Entry-Relay, Default (kein Suffix) → `:6322` |
| STAGING | push `staging` | planet | `:6323`   | GNS-Entry-Relay, Spielname `*-staging` → `:6323` |

- **DEV** (planet, `:6324`): rolling, von diesem CD-Workflow deployt
  (`deploy/site.yml`, Werte aus `inventory/host_vars/planet/`). Der frühere
  Dev-Port `:6321` ist an den GNS-Entry-Relay gegangen (Issue #843).
- **PROD** (planet, `:6322`): koexistierende zweite Instanz
  (`riftbreaker-dedicated-prod`), erreichbar über den GNS-Entry-Relay
  (`rift.projectmellon.de` → `65.21.27.234:6321`, Default-Route → `:6322`). Das
  frühere `satellite:6321 → :6322`-DNAT-Relay ist **retired** (Issue #846).
  Deploy separat per [`deploy-prod.yml`](deploy-prod.yml) +
  [`prod-vars.yml`](prod-vars.yml).
- **STAGING** (planet, `:6323`): dritter Twin (wie prod), erreichbar über den
  GNS-Entry-Relay (`staging.projectmellon.de` → `65.21.27.234:6321`,
  Spielname `*-staging` → `:6323`). Das frühere `sync:6321 → :6323`-DNAT-Relay
  ist **retired** (Issue #846). Deploy bei Push auf `staging` per
  `deploy/deploy-staging.yml` + `deploy/staging-vars.yml`.

Der Client ist effektiv auf Port `6321` hardgewired — es gibt deshalb **genau
einen** öffentlichen Zugang (planet `:6321`), und die Umgebung wird über den
Spielnamen-Suffix gewählt, nicht über weitere Ports oder Adressen.

### GNS-Entry-Relay + Suffix-Routing (Issue #843)

Auf **planet** terminiert die Rolle **`gns-relay`** den hardgewireten Port `6321`
selbst (Container `gns-relay`, `network_mode: host`, UDP `6321`): sie ist der
GNS-Gegenspieler des Clients, liest den Spielnamen und **routet** anhand eines
Suffix auf ein Backend — Regeln in `roles/gns-relay/templates/routes.j2`:

```text
*-dev     = 127.0.0.1:6324     # Suffix-Wildcard
*-staging = 127.0.0.1:6323     # Suffix-Wildcard
*         = 127.0.0.1:6322     # Default
```

Auswertung: **exakt** > **längster Suffix** > **Default**. Damit wählt der
Spielname die Umgebung (`momo-staging` → staging, `momo-dev` → dev, sonst prod)
— ohne Drop/Kick/Reconnect. Die `gns_probe.exe` wird zur Deploy-Zeit aus
`tools/gns-proxy/` gebaut (MinGW-w64/zig, wie die Rolle `rbtools`) und auf
demselben Laufzeit-Image `rb-dedicated:<sha>` betrieben; sie braucht die
Game-DLLs (`GameNetworkingSockets.dll`) read-only gemountet und ein eigenes
Wine-Prefix-Volume.

Seit Issue #857 läuft der Relay im **Hold-Modus**: unentschiedene Joins werden
gehalten (kein Backend-Aufbau), der Client bleibt im Loading; ein Operator sieht
sie in einer kleinen Web-UI (`--api-port`, Standardbind nur `127.0.0.1`) und
schickt sie per Klick auf ein Ziel (`--target NAME=ip:port`). Suffix-/
Identitäts-Regeln haben weiter Vorrang. Die API ist ohne Auth — Zugriff per
SSH-Tunnel (`ssh -L 9200:127.0.0.1:9200 planet`).

Weil der Relay `6321` übernimmt, ist der **dev-Server von `6321` auf `6324`
umgezogen** (`riftbreaker_server_port_udp`); prod (`:6322`) und staging (`:6323`)
bleiben unverändert, haben aber seit Issue #846 **keinen** eigenen Relay mehr —
ihr öffentlicher Einstieg ist derselbe planet-GNS-Entry-Relay auf `:6321`
(Suffix-Routing).

Der **Tag→prod- und Branch→staging-Kanal ist verdrahtet**: ein Tag-Push `v*`
rollt `deploy/deploy-prod.yml` (+ `-e @deploy/prod-vars.yml`) auf prod aus, ein
Push auf `staging` rollt `deploy/deploy-staging.yml` (+ `-e @deploy/staging-vars.yml`)
aus. Der forced command reicht den `<ref>` per Marker-Datei an den root-Wrapper,
der `main` → `site.yml` (dev), `refs/tags/v*` → `deploy-prod.yml` (prod) und
`refs/heads/staging` → `deploy-staging.yml` (staging) dispatched (siehe
[„CD: SSH-Deploy"](#cd-ssh-deploy-dedizierter-deploy-user)).

```text
push auf main          → deploy-dev     (site.yml,           dev     :6324 via GNS-Entry-Relay planet:6321)
push auf Tag v*        → deploy-prod    (deploy-prod.yml,    prod    :6322 via GNS-Entry-Relay planet:6321)
push auf staging       → deploy-staging (deploy-staging.yml, staging :6323 via GNS-Entry-Relay planet:6321)
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
       SHA validieren → env aus <ref> ableiten (main→dev, Tag v*→prod)
       → env-spezifische Marker .deploy-<env>.sha/-ref + .deploy-env schreiben
       → sudo -n /usr/local/bin/rbbattle-deploy (als root):
            cd /opt/rbbattle-deploy/repo-<env> (eigener Checkout je Env)
            git fetch + Hard-Checkout (als root) → chown -R deploy:deploy
            refs/heads/main → site.yml        (dev)
            refs/tags/v*    → deploy-prod.yml (prod)
  → exit code = Deploy-Ergebnis (kein Polling, kein Secret)
```

**Getrennte Checkouts (Issue #483):** dev und prod haben je einen EIGENEN
Checkout (`repo-dev/`, `repo-prod/`) und eigene Ref/SHA-Marker
(`.deploy-dev.*` / `.deploy-prod.*`). Ein dev-Lauf (`main`) und ein prod-Lauf
(`Tag v*`) können so **parallel** laufen, ohne sich Ref/SHA zu überschreiben.
Der Umstieg ist rückwärts-kompatibel: die alten, env-losen Marker
(`.deploy-sha`/`.deploy-ref`) liest der Wrapper nur noch als **Fallback**,
wenn `.deploy-env` fehlt — dann benutzt er weiter den bestehenden Checkout
`repo/`. Nach der Einmal-Migration (s. Checkliste) ist jeder Lauf env-lokal.

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
# 1) Service-User + Verzeichnisse + getrennte Checkouts (je Env einer, Issue #483):
sudo useradd --system --home /opt/rbbattle-deploy --shell /usr/sbin/nologin deploy
sudo install -d -o deploy -g deploy -m 0750 /opt/rbbattle-deploy
sudo -u deploy git clone https://github.com/momokli/riftbreaker-battle-mod.git \
  /opt/rbbattle-deploy/repo-dev
sudo -u deploy git clone https://github.com/momokli/riftbreaker-battle-mod.git \
  /opt/rbbattle-deploy/repo-prod

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
  "$(git -C /opt/rbbattle-deploy/repo-dev rev-parse origin/main) refs/heads/main"
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

# b) Root-Wrapper installieren (kanonische Quelle im Repo:
#    deploy/deploy-wrapper.sh). Er arbeitet je Env in
#    /opt/rbbattle-deploy/repo-<env> und dispatched anhand der Env-Marker
#    .deploy-<env>.* (.deploy-env = aktuelle Env), die der forced command
#    vorher geschrieben hat — main -> site.yml, Tag v* -> deploy-prod.yml:
sudo install -m 0755 deploy/deploy-wrapper.sh /usr/local/bin/rbbattle-deploy
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
# Zuletzt deployte SHA (je Env eigener Checkout):
git -C /opt/rbbattle-deploy/repo-dev  log --oneline -3
git -C /opt/rbbattle-deploy/repo-prod log --oneline -3
```

`deploy/deploy-ssh.sh` aktualisieren (bei Änderungen):
`sudo install -m 0755 deploy/deploy-ssh.sh /opt/rbbattle-deploy/deploy-ssh.sh`.
`deploy/deploy-wrapper.sh` (root-Wrapper) aktualisieren:
`sudo install -m 0755 deploy/deploy-wrapper.sh /usr/local/bin/rbbattle-deploy`.

### Migration: geteilter Checkout/Marker → `repo-<env>` (Issue #483)

Einmalig auf planet (Wartungsfenster; der Umstieg ist rückwärts-kompatibel):

1. **Checkouts trennen** — den bestehenden dev-Checkout verschieben und einen
   prod-Zweig klonen (der Wrapper klont sonst beim ersten prod-Lauf selbst):
   ```bash
   sudo mv /opt/rbbattle-deploy/repo /opt/rbbattle-deploy/repo-dev
   sudo -u deploy git clone https://github.com/momokli/riftbreaker-battle-mod.git \
     /opt/rbbattle-deploy/repo-prod
   ```
2. **Marker migrieren** — den letzten Legacy-Stand als dev-Marker übernehmen
   (prod entsteht mit dem ersten prod-Lauf neu):
   ```bash
   cd /opt/rbbattle-deploy
   sudo sh -c 'cp -f .deploy-sha .deploy-dev.sha 2>/dev/null || true
                cp -f .deploy-ref .deploy-dev.ref 2>/dev/null || true
                printf dev > .deploy-env'
   ```
3. **forced command + Wrapper aktualisieren** (Schritte 2 + Root-Weg `b`)
   und den Smoke-Test (dev) fahren.

**Rollback:** `repo`-Checkout samt `.deploy-sha`/`.deploy-ref` und den alten
forced-command/Wrapper zurückspielen — die Legacy-Marker/-Checkouts bleiben
erhalten (der Wrapper liest sie als Fallback, wenn `.deploy-env` fehlt).

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
- [ ] Checkout-/Marker-Migration auf `repo-<env>` (s. §„Migration“, Issue #483)
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
  `deploy-prod.yml` (prod-Playbook, Issue #328), die hermetischen
  Rollen-Selbsttests (`deploy/tests/`: Disk-Gate #310,
  `/server/*`-Route #463), Compose-Templates rendern (`check-render.yml`) und
  jedes gerenderte Compose-File durch `docker compose config`. Kein
  Host-/SSH-Zugriff, keine Secrets. Läuft damit immer, auch wenn der
  planet-Runner gerade nicht erreichbar ist.
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

| Rolle                    | Typ                | Was                                                                                                                                                                                                                                                                                                                                   |
| ------------------------ | ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `dedicated-server-image` | docker             | baut `rb-dedicated:<deploy-sha>` auf planet (geteiltes Laufzeit-Image)                                                                                                                                                                                                                                                                |
| `game-content`           | steamcmd/sync      | Dedicated-Server-Content (App 4114030) nach `riftbreaker_game_dir` (idempotent, fail loud)                                                                                                                                                                                                                                            |
| `riftbreaker-server`     | docker             | Dev-SP-Server 6324 (umgezogen von 6321, Issue #843; 1v1 vs sich selbst), Mod-Install + Restart-Handler + Guard (keine Fremd-Mods in `mods/`) + Post-Deploy-Verifikation                                                                                                                                                               |
| `satellite-relay`        | iptables + systemd | UDP-DNAT-Relay — **retired (Issue #846)**: `satellite_relay_state` (Default `present`) schaltet zwischen Aufbau und Teardown (`absent`) der früheren Relays `satellite` (prod, `:6321 → :6322`) und `sync` (staging, `:6321 → :6323`); Ziel-Port je Relay als Play-Var (`satellite_relay_target_port`), reboot-fest, kein `host_vars` |
| `gns-relay`              | docker             | GNS-Entry-Relay auf planet (`network_mode: host`, UDP `:6321`): terminiert GameNetworkingSockets und routet per Suffix auf prod/staging/dev; hält seit #857 unentschiedene Joins und lässt sie per Web-UI (`--api-port`, lokal) auf ein Ziel routen; baut `gns_probe.exe` aus `tools/gns-proxy` (Issue #843/#857)                     |
| `tournament-server`      | systemd            | Rust/axum Referee + Web-UI. Binary aus `tournament/` — wird beim Deploy auf planet gebaut (Rust-Toolchain via rustup unter `/opt/rbbattle-deploy/`, idempotent von der Rolle bereitgestellt)                                                                                                                                          |
| `website`                | eigener Caddy      | eigener `rift-caddy` (plain HTTP: Landing + `/mod.zip` + Cockpit `/contract/*` + `/tournament/*`) + ZWEI Einträge im geteilten Host-Caddy (Issue #322); Host-Caddy-Reload deterministisch + fehlersichtbar, `rift-caddy` mit `admin off` (Issue #355); `/server/*` nur bei deploytem Agenten (`server_control_enabled`, Issue #463)   |
| `mods-zip`               | —                  | Paketierung + md5-Paritäts-Check (hart)                                                                                                                                                                                                                                                                                               |
| `host-hygiene`           | systemd            | wöchentlicher Timer: entfernt **dangling** Docker-Images (`docker image prune`, **kein** `-a`; Issue #308)                                                                                                                                                                                                                            |
| `crash-collector`        | systemd            | Dauer-Dienst: sichert bei Crash-Markern das neueste `crash_info/<uuid>.{dmp,log,trace}` als Bundle nach `/opt/rbmods/crashes/` (+ Kontext/Meta, Retention; Issue #462)                                                                                                                                                                |

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
├── deploy-prod.yml                # Prod-Instanz (planet :6322) + Relay-Teardown (satellite, #846)
├── prod-vars.yml                  # Overrides der prod-Instanz (dev-kokexistierend)
├── deploy-staging.yml             # Staging-Instanz (planet :6323) + Relay-Teardown (sync, #846)
├── staging-vars.yml               # Overrides der staging-Instanz (dev-/prod-kokexistierend)
├── test-deploy.yml / test-vars.yml # Boot-Test-Instanz (CI)
├── check-render.yml               # deploy-check-local: rendert Compose-Templates lokal
├── deploy-ssh.sh                  # CD: forced command für den deploy-User (SSH)
├── inventory/
│   ├── hosts.yml                  # Hosts "planet" (dev/prod/staging) + "satellite"/"sync" (Relay-Teardown, mesh-first)
│   └── host_vars/planet/
│       ├── vars.yml               # nicht-geheime Konfiguration
│       └── vault.yml              # Geheimnis (ansible-vault verschlüsselt)
└── roles/
    ├── dedicated-server-image/    # baut rb-dedicated:<sha>
    ├── game-content/              # Steam-Content (App 4114030) deklarativ
    ├── riftbreaker-server/        # docker 6324 (+ Restart-Handler; 6321 → gns-relay, #843)
    ├── gns-relay/                 # GNS-Entry-Relay (UDP 6321, Suffix-Routing; #843)
    ├── satellite-relay/           # UDP-DNAT-Relay, state present|absent (retired, #846)
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
- SSH ausschließlich mesh-first über die Aliase `planet`, `satellite` und
  `sync` (Tailscale) — nie über Public-IPs.
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
  (`deploy/host-hygiene/host_hygiene.sh`) protokolliert Vorher/Nachher-Zähler ins Journal.
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

## Environment-Isolation & Deploy-Identitaet (Issue #483)

Jeder Deploy traegt genau eine Identitaet `<env> · <ref>`:

| Baustein   | Datei                                                                                                  | Zweck                                                                                       |
| ---------- | ------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------- |
| Identitaet | `deploy/tasks/deploy-identity.yml`                                                                     | setzt `rift_env`/`rift_deploy_ref`/`rift_deploy_identity` (in `pre_tasks` aller drei Plays) |
| Schema     | `deploy/env-schema.yml`                                                                                | `per_env` (Pflicht je Env explizit) vs. `shared` (begruendet)                               |
| Audit      | `tools/deploy-gate/check_env_isolation.py`                                                             | stdlib-Audit (kein PyYAML); Marker `ENV-ISOLATION-GATE`                                     |
| Assert     | `deploy/tasks/env-assert.yml`                                                                          | Audit + Distinctness der per-env-Pfade; non-zero rc bricht den Deploy ab                    |
| Tests      | `deploy/tests/env-identity/`, `deploy/tests/env-isolation/`, `tools/deploy-gate/test_env_isolation.py` | hermetisch (kein Host, kein Vault)                                                          |

`rift_env` ist bewusst eine **Play-Var** in `site.yml` (dev), `deploy-prod.yml`
(prod) und `test-deploy.yml` (test): Play-Vars schlagen Rollen-Defaults und
`host_vars` — sonst erbt prod/test still den dev-Wert (dieselbe Praezedenz wie
`server_control_enabled`, #463). `dev` hat keine Override-Datei: dev **ist** die
Basis (`inventory/host_vars/planet/vars.yml`).

**Assert-Semantik (fail-loud):**

- Eine Variable in `prod-vars.yml`/`test-vars.yml` ohne Schema-Eintrag → rot.
- Ein `per_env`-Key, der in einer gelisteten Env-Datei fehlt → rot.
- Ein `shared`-Eintrag ohne Begruendung → rot.
- Fuer `env != dev` muss jeder per-env-Pfad (`website_docroot`,
  `website_mods_dir`, `mods_zip_dest`, `riftbreaker_game_dir`,
  `riftbreaker_deploy_dir`, `riftbreaker_sessions_dir`, `rbtools_dir`) vom
  dev-Basiswert abweichen.

Neue Env-Variable hinzufuegen: zuerst in `deploy/env-schema.yml` klassifizieren,
dann in der Env-Datei setzen — sonst schlaegt das Gate fehl.

### Offener Punkt: host-seitiges Marker-Namensschema

Der host-seitige Checkout (`/opt/rbbattle-deploy/repo-…`) und der Wrapper
(`/usr/local/bin/rbbattle-deploy`, `/opt/rbbattle-deploy/*`) sind **nicht** im
Repo versioniert (forced-command des deploy-Users). Der vorgesehene, noch nicht
umgesetzte Umbau trennt sie je Env:

- Checkout: `/opt/rbbattle-deploy/repo-<env>` (dev|prod|test|staging)
- Marker: `.deploy-<env>.sha` (Checkout-SHA) und `.deploy-<env>.ref` (Tag bzw. SHA)
- Damit deployt jeder Lauf aus seinem eigenen Checkout statt alle aus einem
  gemeinsamen — Ziel des Issues, aber **eigener PR mit Rollback-Runbook**
  (Live-Eingriff auf prod). Bis dahin gilt: Host-Umbau = offen.
