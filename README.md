# Rift Breaker Battle Mod

[![CI](https://github.com/momokli/riftbreaker-battle-mod/actions/workflows/ci.yml/badge.svg)](https://github.com/momokli/riftbreaker-battle-mod/actions/workflows/ci.yml)
[![Lint](https://github.com/momokli/riftbreaker-battle-mod/actions/workflows/lint.yml/badge.svg)](https://github.com/momokli/riftbreaker-battle-mod/actions/workflows/lint.yml)
[![Latest Release](https://img.shields.io/github/v/release/momokli/riftbreaker-battle-mod)](https://github.com/momokli/riftbreaker-battle-mod/releases)
[![Open Issues](https://img.shields.io/github/issues/momokli/riftbreaker-battle-mod)](https://github.com/momokli/riftbreaker-battle-mod/issues)

Ein **Biter-Battles-artiger Runden-Duell-Modus** für _The Rift Breaker_ (EXOR Studios).

## Ziel

Runden-Duell 1v1: Beide Spieler spielen eine eigene Rift-Breaker-Partie. Pro Runde Punkte ansparen (Ressourcen, Kills), um damit **Kreaturen-Wellen zum Gegner zu schicken** oder die **eigene Defense auszubauen**. Gewonnen hat, wer die gegnerische Basis zerstört — oder am Ende die meisten Punkte hat.

## Aktueller Stand

Der Operator-Zugang läuft über das Cockpit
[cockpit.rift.projectmellon.de/contract/](https://cockpit.rift.projectmellon.de/contract/)
(prod) bzw. [cockpit.drift.projectmellon.de/contract/](https://cockpit.drift.projectmellon.de/contract/)
(dev). Die Spiellogik liegt im Backend (rbbridge.dll C++ + Cockpit als manueller
Operator); der Lua-Mod ist nur die Player-HUD-Schicht. Rust-Backend/Referee = **1.1**.

## Komponenten (1.0)

| Komponente            | Ort                             | Aufgabe                                             |
| --------------------- | ------------------------------- | --------------------------------------------------- |
| **Client-Mod (Lua)**  | `client-mod/`                   | HUD-/Display-Schicht, **keine** Business-Logik      |
| **Server (rbbridge)** | `server/`                       | native C++ Read/Write: DLL + HTTP-Bridge + Injector |
| **Cockpit**           | `cockpit/`                      | manueller Operator (1.0-Backend)                    |
| **Deploy/CI**         | `deploy/`, `.github/workflows/` | Dedi-Setup, Caddy, Pipeline                         |
| **Rust-Backend**      | `tournament/`                   | autoritativer Referee (**1.1**)                     |

Details: [docs/1.0-COMPONENTS.md](docs/1.0-COMPONENTS.md) · Priority: [docs/1.0-PRIORITY.md](docs/1.0-PRIORITY.md)

## Verteilung

- `client-mod/` = Workshop-tauglicher Lua-Mod (nur HUD).
- `server/` = **private Distribution** (Server-Anteil, nie Workshop).
- `scripts/package_mod.sh` packt `client-mod/` → `dist/`.
- `docs/workshop.md` = Steam-Workshop-Anleitung (AppID 780310, SteamCMD).

## Repo-Struktur

```
riftbreaker-battle-mod/
├── client-mod/  # Lua-PLAYERMOD (nur HUD)
├── server/      # SERVERMOD: rbbridge.dll + pipe_bridge + injector
├── cockpit/     # Web-UI Cockpit (manueller Operator)
├── tournament/  # Rust-Referee (1.1)
├── deploy/      # Ansible/Docker/Caddy (Dedi-Setup + CI)
├── docs/        # Konzept, Findings, Playtest, Komponenten/Priority
├── scripts/     # Build-/Package-Tooling
├── tools/       # RE, server-control, session-recorder, deploy-gate, …
└── tests/       # Host-/Unit-Tests
```

## Mitmachen

Beiträge willkommen! Workflow, Claim-System (`!claim` im Issue-Kommentar) und
Quality-Gates stehen in [CONTRIBUTING.md](CONTRIBUTING.md); den Projektfortschritt
zeigt [docs/PROGRESS.md](docs/PROGRESS.md).
