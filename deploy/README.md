# deploy/ — Ansible-Deployment (planet)

Ziel-Stack + Betriebsregeln: [`docs/DEPLOYMENT.md`](../docs/DEPLOYMENT.md).
**Deploy NUR über dieses Playbook** — kein manuelles Gedudel.

## Voraussetzungen

- `ansible` (core ≥ 2.19) auf dem Control-Node (dem Rechner, von dem du deployst).
- SSH mesh-first: Alias `planet` in `~/.ssh/config` (Tailscale), `root`-Login.
- Auf planet: Docker + `docker compose`, `systemd`,
  Caddy als Container `mellon-caddy`.
- Auf dem Control-Node: `zip` **oder** `python3` (für
  `scripts/package_bausteine.sh` — die Paketierung läuft dort, nicht auf
  planet; siehe Rolle `mods-zip`, `delegate_to: localhost`).

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

## Deploy

```bash
ansible-playbook -i deploy/inventory deploy/site.yml --ask-vault-pass
```

Reihenfolge der Rollen (site.yml): `mods-zip` → `riftbreaker-server` →
`vanilla-server` → `tournament-server` → `website` → `probe-timer`.

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

- **Kein Klartext-Secret im Repo** — Passwort nur via Vault.
- Compose-/Vault-Dateien mit Passwort: restriktive Rechte am Ziel
  (Compose-Datei `0600`, sie enthält das Server-Passwort im `command`).
- Kein Deploy ohne Freigabe; nichts manuell am produktiven Server.
- SSH ausschließlich mesh-first über den `planet`-Alias (Tailscale).

## Rollback

Vorherige `rbbattle.zip` (Release/Git-History) bzw. das vorherige
`tournament-server`-Binary zurückkopieren und erneut deployen.
