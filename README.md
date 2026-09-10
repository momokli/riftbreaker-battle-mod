# Rift Breaker Battle Mod

[![CI](https://github.com/momokli/riftbreaker-battle-mod/actions/workflows/ci.yml/badge.svg)](https://github.com/momokli/riftbreaker-battle-mod/actions/workflows/ci.yml)
[![Lint](https://github.com/momokli/riftbreaker-battle-mod/actions/workflows/lint.yml/badge.svg)](https://github.com/momokli/riftbreaker-battle-mod/actions/workflows/lint.yml)
[![Latest Release](https://img.shields.io/github/v/release/momokli/riftbreaker-battle-mod)](https://github.com/momokli/riftbreaker-battle-mod/releases)
[![Open Issues](https://img.shields.io/github/issues/momokli/riftbreaker-battle-mod)](https://github.com/momokli/riftbreaker-battle-mod/issues)

Ein **Biter-Battles-artiger Runden-Duell-Modus** für *The Rift Breaker* (EXOR Studios) — Kollaborations-Repo für Konzept, Findings und später den Code.

## Ziel

Runden-Duell 1v1: Beide Spieler spielen eine eigene Rift-Breaker-Partie. Pro Runde Punkte ansparen (Ressourcen, Kills), um damit **Kreaturen-Wellen zum Gegner zu schicken** oder die **eigene Defense auszubauen**. Gewonnen hat, wer die gegnerische Basis zerstört — oder am Ende die meisten Punkte hat.

## Aktuelles Ziel: Solo-MVP

Nächster Meilenstein ist der **Solo-Modus** — allein gegen sich selbst spielen,
über [rift.projectmellon.de/solo.html](https://rift.projectmellon.de/solo.html)
verbinden und starten. Der komplette Weg von hier bis zum 4v4-Release steht in
[ROADMAP.md](ROADMAP.md).

## Status

- **Feasibility verifiziert** — Mod-API vorhanden, Grenzen bekannt (Details: [docs/findings.md](docs/findings.md))
- **Spike gebaut** — Mod-Skeleton + Experimente A (Wave-Spawn), B (Custom-UI-Popup), C (Log-Bridge + Console-Command) in `mod/`; In-Game-Test ausstehend ([mod/README.md](mod/README.md))
- **Trainer-Harness v0 (Grundgerüst)** — Injector + In-Game-Bridge-DLL + RE-Scan-Tools, siehe [trainer/README.md](trainer/README.md)

## Komponenten

| Komponente | Ort | Aufgabe |
|---|---|---|
| **Lua-Mod** | `mod/` (Spike) | gesamte Spiellogik im Spiel (Wellen, Punkte, Defense, HUD) — Spike-Skeleton mit Experimenten A/B/C |
| **Trainer / Harness** | `trainer/` (Harness v0) | I/O-Gateway zwischen Spiel und Netz: DLL-Injection + Named Pipe + RE-Scan-Tools (Windows-first) |
| **Bausteine** | `bausteine/` | eigenständig testbare Komponenten aus Mod + Trainer (Index: [bausteine/README.md](bausteine/README.md)) |
| **Relay-Server** | `server/` (geplant) | Matchmaking + Event-Routing (Node) |

Architektur & Design: [docs/concept.md](docs/concept.md) · Install & Spike-Test: [mod/README.md](mod/README.md)

## Bausteine & Verteilung

- **[bausteine/](bausteine/README.md)** — jede Fähigkeit als eigenständig testbarer
  Baustein: 00 Mod-Skeleton, 01 Wave-Spawn (`rb_wave`), 02 Custom-UI (`rb_ui`),
  03 Log-Bridge (`[RBBATTLE]` + `tail_events.py`), 04 Trainer-I/O (Injector +
  rbbridge-DLL + `pipe_client.py`, ohne Spiel testbar via notepad.exe).
- **[scripts/package_mod.sh](scripts/package_mod.sh)** — packt den Mod-Ordner
  (`mod/`) als ZIP nach `dist/` für manuelle Verteilung/Tests.
- **[site/index.html](site/index.html)** — kanonische Landing (GitHub Pages:
  <https://momokli.github.io/riftbreaker-battle-mod/>, Quelle Branch `main`
  Pfad `/site` via GitHub Actions). `docs/index.html` ist nur noch ein
  Verweis auf die Landing (keine eigenen Download-/Versions-Links).
- **[docs/workshop.md](docs/workshop.md)** — Steam-Workshop-Anleitung
  (AppID 780310, SteamCMD, friends-only). **Nur der Lua-Mod, nie der Trainer.**

## Für uns

Dieses Projekt enthält einen **Trainer-Anteil** (Prozess-I/O am Spiel) — der
Trainer bleibt bewusst **private Distribution** (kein Workshop-Release). Der
Lua-Mod selbst ist Workshop-tauglich (siehe [docs/workshop.md](docs/workshop.md)).

## Repo-Struktur

```
riftbreaker-battle-mod/
├── bausteine/ # eigenständig testbare Komponenten (00–04, Index: bausteine/README.md)
├── mod/       # Lua-Mod (Spike: Skeleton + Experimente A/B/C, Install siehe mod/README.md)
├── trainer/   # Sidecar/Trainer (Harness v0: Injector + DLL + RE-Scan-Tools)
├── server/    # Relay-Server (geplant, Node)
├── scripts/   # Tooling (package_mod.sh: Mod-ZIP bauen)
├── docs/      # Konzept, Findings, Workshop-Anleitung (+ index.html = Verweis auf site/)
└── README.md
```

## Mitmachen

Beiträge willkommen! Workflow, Claim-System (`!claim` im Issue-Kommentar) und
Quality-Gates stehen in [CONTRIBUTING.md](CONTRIBUTING.md); den Projektfortschritt
zeigt [docs/PROGRESS.md](docs/PROGRESS.md).
