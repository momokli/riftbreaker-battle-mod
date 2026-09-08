# Rift Breaker Battle Mod

Ein **Biter-Battles-artiger Runden-Duell-Modus** für *The Rift Breaker* (EXOR Studios) — Kollaborations-Repo für Konzept, Findings und später den Code.

## Ziel

Runden-Duell 1v1: Beide Spieler spielen eine eigene Rift-Breaker-Partie. Pro Runde Punkte ansparen (Ressourcen, Kills), um damit **Kreaturen-Wellen zum Gegner zu schicken** oder die **eigene Defense auszubauen**. Gewonnen hat, wer die gegnerische Basis zerstört — oder am Ende die meisten Punkte hat.

## Status

- **Feasibility verifiziert** — Mod-API vorhanden, Grenzen bekannt (Details: [docs/findings.md](docs/findings.md))
- **Spike gebaut** — Mod-Skeleton + Experimente A (Wave-Spawn), B (Custom-UI-Popup), C (Log-Bridge + Console-Command) in `mod/`; In-Game-Test ausstehend ([mod/README.md](mod/README.md))

## Komponenten

| Komponente | Ort | Aufgabe |
|---|---|---|
| **Lua-Mod** | `mod/` (Spike) | gesamte Spiellogik im Spiel (Wellen, Punkte, Defense, HUD) — Spike-Skeleton mit Experimenten A/B/C |
| **Trainer / Sidecar** | `trainer/` (geplant) | I/O-Schicht zwischen Spiel und Netz (Windows-first) |
| **Relay-Server** | `server/` (geplant) | Matchmaking + Event-Routing (Node) |

Architektur & Design: [docs/concept.md](docs/concept.md) · Install & Spike-Test: [mod/README.md](mod/README.md)

## Für uns

Dieses Projekt enthält einen **Trainer-Anteil** (Prozess-I/O am Spiel) — deshalb bewusst **kein Workshop-Release**, das Projekt bleibt privat.

## Repo-Struktur

```
riftbreaker-battle-mod/
├── mod/       # Lua-Mod (Spike: Skeleton + Experimente A/B/C, Install siehe mod/README.md)
├── trainer/   # Sidecar/Trainer (geplant, Windows-first, macOS später)
├── server/    # Relay-Server (geplant, Node)
├── docs/      # Konzept & Findings
└── README.md
```
