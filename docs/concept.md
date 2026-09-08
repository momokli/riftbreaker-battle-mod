# Konzept — Rift Breaker Battle Mod

Runden-Duell-Modus („Biter Battles“-artig) für *The Rift Breaker*.

## Spielidee

Zwei Spieler spielen **parallel je eine eigene Rift-Breaker-Partie**. Über Runden hinweg sammeln sie Punkte (Ressourcen, Kills, Überleben) und investieren sie in zwei Dinge:

- **Offense:** Kreaturen-Wellen, die beim Gegner einfallen,
- **Defense:** eigene Verteidigung ausbauen (Türme, Mauern, Fallen).

Wer die gegnerische Basis zerstört, gewinnt sofort; läuft das Runden-Limit ab, entscheidet der Punkte-Stand.

## Spielmodus-Design

- **Runden:** Feste Taktung (z. B. 60–90 s). Jede Runde hat eine Planungs- und eine Angriffsphase.
- **Punkte:** Verdient durch eigene Kills und gehaltene Wellen; ausgegeben für neue Wellen (teurer = stärker) und Defense-Upgrades.
- **Wellen:** Vom Lua-Mod im eigenen Spiel gespawnt (Kreaturen-Pool wie im Hauptspiel). Die „gegnerische Welle“ erscheint also in der eigenen Partie — dafür reicht ein Trigger-Event.
- **Win/Lose:** Basis-Zerstörung → sofortiger Sieg. Sonst entscheidet nach Runden-Limit der Punkte-Stand (ggf. Sudden Death).

## Architektur

Zwei parallele Spiel-Instanzen, gekoppelt über einen kleinen Relay-Server:

```
┌─────────────────────────────┐
│  Rift Breaker + Lua-Mod (A) │
└──────────────┬──────────────┘
               │  Named Pipe / Memory-R/W
┌──────────────┴──────────────┐
│  Trainer / Sidecar (A)      │
└──────────────┬──────────────┘
               │  HTTP
┌──────────────┴──────────────┐
│  Relay-Server (Node)        │
│  Matchmaking + Event-Routing│
└──────────────┬──────────────┘
               │  HTTP
┌──────────────┴──────────────┐
│  Trainer / Sidecar (B)      │
└──────────────┬──────────────┘
               │  Named Pipe / Memory-R/W
┌──────────────┴──────────────┐
│  Rift Breaker + Lua-Mod (B) │
└─────────────────────────────┘
```

Das Spiel ist **rundenbasiert** — es gibt keine Echtzeit-Anforderungen. Ein simples **Event-/Pull-Modell** (Zustand abfragen, Events durchreichen) reicht vollkommen; Latenz ist unkritisch.

## Komponenten im Detail

### Lua-Mod (`mod/`)

Enthält die **gesamte Spiellogik**: Wellen spawnen, Punkte verwalten, Defense bauen, HUD-Anzeige. Läuft komplett auf der offiziellen Mod-API und bleibt damit **update-fest** — Game-Updates können dem Mod nur dann schaden, wenn sie die API selbst ändern (unwahrscheinlich, da offiziell).

### Trainer / Sidecar (`trainer/`, Windows-first)

Die I/O-Schicht zwischen Spiel und Netz. Umsetzung als **DLL-Injection** oder **externer Memory-Read/Write** (pymem-/Cheat-Engine-Stil). Zwei Aufgaben:

1. **Outbound:** Spiel-State auslesen — Score, Ressourcen, Wave-Events — und an den Relay schicken.
2. **Inbound:** Events ins Spiel hineinschreiben (z. B. Wave-Trigger), **ohne dass der Spieler in eine Konsole tippt**.

macOS kommt später: SIP + Hardened Runtime machen Memory-I/O dort deutlich schwerer.

### Relay-Server (`server/`, Node)

Kleiner zentraler Dienst: **Matchmaking** (zwei Spieler zusammenführen) und **Event-Routing** zwischen den Trainern beider Partien. Er kennt keine Spiellogik, er leitet nur Nachrichten weiter.

## Design-Entscheidungen

### Warum Trainer als I/O-Schicht statt Log-Bridge oder Konsole?

Drei Kandidaten für die Kopplung von Lua-Mod und Außenwelt, bewertet:

| Option | Outbound | Inbound | Probleme |
|---|---|---|---|
| **Log-Bridge** (Lua → `LogService:Log` → Log-Datei extern parsen) | ✅ | ❌ | Nur eine Richtung; Log-Spam; Timing fragil; an Spiel-Log-Format gekoppelt |
| **Konsole** (In-Game-Konsole bedienen) | ~ | ~ | Nur manuell; erfordert Tippen; nicht automatisierbar; Existenz der Konsole unklar |
| **Trainer / Sidecar** (Memory-I/O) | ✅ | ✅ | Ein Kanal für beide Richtungen; kein User-Zutun; State-Reads direkt am Prozess |

→ Der Trainer ist die **einzige Option mit echtem Inbound ohne Spieler-Interaktion** und liest den State außerdem direkter und robuster, als jede Log- oder Konsolen-Brücke es könnte. Dafür ist er update-empfindlicher — abgefangen durch die update-feste Mod-Logik (siehe Risiken).

### Weitere Entscheidungen

- **Rundenbasiert statt Echtzeit:** passt zum langsamen, aufbauenden Gameplay von Rift Breaker und eliminiert Netz-/Timing-Probleme komplett.
- **Eigener Relay statt P2P/Direktverbindung:** trivial umsetzbar, reicht für 1v1, später leicht erweiterbar (Ladder, Spectate).
- **Spiellogik komplett im Lua-Mod:** Der Trainer bleibt ein „dummer“ Kanal. Logik-Änderungen sind damit reine Mod-Änderungen ohne Neukompilieren des Trainers.

## Roadmap

1. **Spike** — Mod-Skeleton + Wave-Spawn-Experimente + Custom-UI *(gebaut; In-Game-Test offen, Branch `feature/spike-mod-skeleton`)*
2. **Trainer-PoC „read“** — Score/Ressourcen aus dem Prozess auslesen
3. **Trainer „write“** — Wave-Trigger ins Spiel schreiben
4. **Loop schließen** — Event-Pfad Partie A → Relay → Partie B (und zurück)
5. **Alpha 1v1** — erster spielbarer Duell-Lauf

## Risiken

- **Game-Updates brechen Trainer-Signaturen:** Mod-Logik bleibt davon unberührt; Offsets/Signaturen des Trainers müssen nach jedem Patch geprüft werden. Der Trainer sollte AOB-Signaturen statt fester Adressen nutzen.
- **macOS-Trainer deutlich aufwendiger:** SIP + Hardened Runtime — bewusst vertagt (Windows-first).
- **Kein Workshop-Release:** Der Trainer-Anteil verstößt gegen die Plattform-Regeln — bewusst ein „für uns“-Projekt, Distribution nur privat.
