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

## Server-Passwort (Vault)

Das Server-Passwort liegt **nie im Klartext** im Repo. Es steht in
`deploy/inventory/host_vars/planet/vault.yml` (Variable
`riftbreaker_server_password`) und wird mit `ansible-vault` verschlüsselt.
Befüllung: siehe `deploy/README.md` → „Vault".

## Aktueller Zustand (2026-09-10)

Stack teilweise ad-hoc ohne Ansible (Docker manuell, Website aus manueller
Kopie — siehe Issue-Kommentar zur stale Landing). Gap: `deploy/` fehlte → Issue #45.

## Betriebsregeln

- Mod-Parität vor jedem Release prüfen (md5).
- Live-Tests nur bei leerem Server.
- Keine Credentials in Repo/Logs; `exor_logs` im Container, `rbbridge.log` im Temp.
- SSH mesh-first (Tailscale), nie über Public-IPs.
