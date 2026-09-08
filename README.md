# Rift Breaker Battle Mod

Ein **Biter-Battles-artiger Runden-Duell-Modus** für *The Rift Breaker* (EXOR Studios) — Kollaborations-Repo für Konzept, Findings und später den Code.

## Ziel

Runden-Duell 1v1: Beide Spieler spielen eine eigene Rift-Breaker-Partie. Pro Runde Punkte ansparen (Ressourcen, Kills), um damit **Kreaturen-Wellen zum Gegner zu schicken** oder die **eigene Defense auszubauen**. Gewonnen hat, wer die gegnerische Basis zerstört — oder am Ende die meisten Punkte hat.

## Status

- **Feasibility verifiziert** — Mod-API vorhanden, Grenzen bekannt (Details: [docs/findings.md](docs/findings.md))
- **Spike läuft** — Mod-Skeleton + Wave-Spawn-Experimente + Custom-UI (`feature/spike-mod-skeleton`)
- **Trainer-Harness v0 (Grundgerüst)** — Injektor + In-Game-Bridge-DLL + RE-Scan-Tools, siehe [trainer/README.md](trainer/README.md)

## Komponenten

| Komponente | Ort | Aufgabe |
|---|---|---|
| **Lua-Mod** | `mod/` (geplant) | gesamte Spiellogik im Spiel (Wellen, Punkte, Defense, HUD) |
| **Trainer / Harness** | `trainer/` (Harness v0) | I/O-Gateway zwischen Spiel und Netz: DLL-Injection + Named-Pipe + RE-Scan-Tools (Windows-first) |
| **Relay-Server** | `server/` (geplant) | Matchmaking + Event-Routing (Node) |

Architektur & Design: [docs/concept.md](docs/concept.md)

## Für uns

Dieses Projekt enthält einen **Trainer-Anteil** (Prozess-I/O am Spiel) — deshalb bewusst **kein Workshop-Release**, das Projekt bleibt privat.

## Geplante Repo-Struktur

```
riftbreaker-battle-mod/
├── mod/       # Lua-Mod (Spiellogik, HUD)
├── trainer/   # Sidecar/Trainer (Windows-first, macOS später)
├── server/    # Relay-Server (Node)
├── docs/      # Konzept & Findings
└── README.md
```
