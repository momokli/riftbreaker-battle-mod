# RIFT BATTLE — Deployment & Stack

## Ziel-Stack (was IMMER betrieben wird)
| Komponente | Host | Container/Unit | Port | Zweck |
|---|---|---|---|---|
| riftbreaker-dedicated | planet | docker (wine) | 6321/udp | PROD DEV-SERVER: 1v1 "vs sich selbst" (SP-Mode; rbbattle-Mod + rbbridge) |
| rb-winetest | planet | docker (wine) | 6322/udp | PROD VANILLA-SERVER (kein Mod, schnelle Test-Joins) |
| tournament-server | planet | systemd (Rust/axum, aus tournament/) | tbd | PROD TURNIER 1v1: Lobby/Ready/GO/Wave-Routing/Score (2 Welten) |
| test-Instanzen | planet | docker, on-demand | frei | Test-Server aller Art (Mod-Tests, Balance, Experimente) |
| Websites | planet | /srv/rbmods-site (+ Caddy) | 443 | Landing /connectivity.html /solo.html /mods/* (dufs, rbbattle.zip) |
| rbmods-probe.timer | planet | systemd | — | Connectivity-Checks alle 2 Min → status.json |
| rbbridge | in Mod-Containern | Prozess | — | Command-Injection (exec_cmd_client, Argument IMMER als EIN quotierter String) |

## Deployment-Plan (SOLL: Ansible auf planet, dort systemd + docker)
- Repo: deploy/ (Ansible-Playbook, inventory=planet), Rollen:
  1. riftbreaker-server — Docker-Container + Server-Config (Welt mp_survival/jungle, disable_steam, Passwort aus Vault)
  2. vanilla-server — zweite Instanz ohne Mods (6322)
  3. tournament-server — systemd-Unit, Env-Konfig (RBBRIDGE URLs), Build via CI-Artefakt
  4. website — statische Dateien nach /srv/rbmods-site, Caddyfile-Merge + Reload
  5. mods-zip — Mod aus mod/ paketieren → /mods/rbbattle.zip + Prod-Mods-Dir; PARITÄTS-CHECK (md5 Zip == Prod) hart als Fehlschlag
  6. probe-timer — systemd timer für status.json
- Grundsätze: idempotent, push early+often, Deploy nur via Playbook (kein manuelles Gedudel), Rollback = vorherige Mod-Zip/Version
## Aktueller Zustand (2026-09-10): Stack teilweise ad-hoc ohne Ansible (Docker manuell, Websites per scp). Gap: deploy/ fehlt → Issue #45.
## Betriebsregeln: Mod-Parität vor jedem Release prüfen; Live-Tests nur bei leerem Server; keine Credentials in Repo/Logs; exor_logs im Container, rbbridge.log im Temp.
