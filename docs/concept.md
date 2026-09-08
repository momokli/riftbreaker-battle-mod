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

Enthält **nur die Spiellogik** (Wellen spawnen, Punkte verwalten, Defense bauen, HUD) — **kein eigener I/O-Kanal** (Trainer-only, s. u.). Registriert Console-Commands (z. B. `rb_wave`) als Eingabe-API für die Trainer-DLL. Läuft komplett auf der offiziellen Mod-API ohne externe Zugriffe und bleibt damit **update-fest** und **Steam-Workshop-tauglich** — Game-Updates können dem Mod nur dann schaden, wenn sie die API selbst ändern (unwahrscheinlich, da offiziell).

### Trainer / Sidecar (`trainer/`, Windows-first)

Die **einzige I/O-Schicht** zwischen Spiel und Netz (Trainer-only, Entscheidung 08.09.2026). Ein externer **Injector** hängt die **Trainer-DLL** zur Laufzeit in den Spielprozess (runtime-only Injection); die DLL öffnet eine **Named Pipe** (z. B. `\\.\pipe\rbbattle`) zum lokalen Relay-Client. Zwei Aufgaben:

1. **Egress:** Spiel-State auslesen (Memory) bzw. Events abfangen — Score, Ressourcen, Wave-Events — und über die Pipe an den Relay schicken.
2. **Ingress:** Mod-Commands ausführen bzw. Spielfunktionen aufrufen (z. B. Wave-Trigger), **ohne dass der Spieler in eine Konsole tippt**.

macOS kommt später: SIP + Hardened Runtime machen In-Process-I/O dort deutlich schwerer.

### Relay-Server (`server/`, Node)

Kleiner zentraler Dienst: **Matchmaking** (zwei Spieler zusammenführen) und **Event-Routing** zwischen den Relay-Clients beider Partien. Er kennt keine Spiellogik, er leitet nur Nachrichten weiter.

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

## Tournament-Architektur (Trainer-only)

Das 1v1-Modell aus den vorherigen Abschnitten skaliert direkt auf **Turniere mit N Spielern**: Jeder Teilnehmer spielt eine eigene, unabhängige Partie; ein zentraler **Tournament-Server** übernimmt Lobby/Matchmaking, Runden-Timer, Event-Routing und Scoreboard — 1v1 ist nur der Spezialfall *N = 2*.

**Leitprinzip (Entscheidung 08.09.2026): Trainer-only.** Die Lua-Mod hat **keinen eigenen I/O-Kanal** — Ingress UND Egress laufen ausschließlich über die **Trainer-DLL**:

```
┌────────────────────────────────┐
│  Rift Breaker + Lua-Mod        │
│  + Trainer-DLL (injiziert)     │
└────────────────┴───────────────┘
                 │  Named Pipe (z. B. \\.\pipe\rbbattle)
┌────────────────┬───────────────┐
│  Relay-Client / Sidecar        │
└────────────────┴───────────────┘
                 │  HTTP/WebSocket (JSON)
                 ▼
┌────────────────┬───────────────┐
│  Tournament-Server             │
│  Lobby / Matchmaking           │
│  Runden-Timer / Event-Routing  │
│  Scoreboard                    │
└────────────────────────────────┘
```

Pro Spieler läuft genau eine solche Instanz (A, B, … N); alle Relay-Clients hängen am selben Tournament-Server. `…` = weitere Instanzen bis N.

### Bausteine

- **Lua-Mod (`mod/`):** **Nur** Spiellogik — Wellen, Punkte, Defense, HUD. Registriert Console-Commands (z. B. `rb_wave`) als Eingabe-API für die DLL. Keine externen Zugriffe → bleibt ein normaler, **Steam-Workshop-tauglicher** Mod.
- **Trainer-DLL (`trainer/`, Windows-first):** das **einzige I/O-Gateway** zwischen Spiel und Außenwelt. Ein **Injector** (externes Tool) hängt die DLL zur Laufzeit in den Spielprozess; die DLL öffnet eine **Named Pipe** (z. B. `\\.\pipe\rbbattle`) zum lokalen Relay-Client.
  - **Ingress:** Server → Relay-Client → Pipe → DLL führt Mod-Command aus bzw. ruft eine Spielfunktion auf (z. B. Wave-Trigger).
  - **Egress:** DLL liest Game-State (Memory) bzw. fängt Events ab → Pipe → Relay-Client → Server.
- **Relay-Client / Sidecar:** lokaler Prozess neben der Spiel-Instanz; verbindet Named Pipe und Server.
- **Tournament-Server (`server/`):** Lobby/Matchmaking, Runden-Timer, Event-Routing, Scoreboard. Er kennt keine Spiellogik — er leitet nur Events weiter.

### Datenfluss

**Ingress (Server → Spiel):** `Tournament-Server → Relay-Client → Named Pipe → Trainer-DLL → Mod-Command / Spielfunktion`

**Egress (Spiel → Server):** `Trainer-DLL (Game-State-Read / Event-Hook) → Named Pipe → Relay-Client → Tournament-Server`

### Verworfen (08.09.2026)

| Ansatz | Grund |
|---|---|
| **Konsole-Buffer-Injektion** (Input programmatisch in den Konsolen-Buffer schreiben) | fragil — hängt an undokumentierten Buffer-/Fokus-Zuständen der In-Game-Konsole |
| **UI-Automation / SendInput** (Tastendrücke simulieren) | kein echtes Ingress; Fokus-Probleme; bricht bei Fensterwechsel/Fullscreen ab |
| **Log-File-Tailing** (`exor_logs.txt` parsen) | nur Outbound; Log-Spam; Timing fragil — höchstens Notnagel zur Verifikation |

### Steam-Kompatibilität

- **Runtime-only Injection:** Es werden keine Game-Dateien modifiziert (weder Installation noch Workshop-Content) — der Mod bleibt ein normaler Workshop-Mod.
- Die **Trainer-DLL ist bewusst ein externes Tool** („für uns“-Projekt): separate, private Distribution, kein Teil des Workshop-Uploads.

### Protokoll

Kleine JSON-Events (Typ, Payload, Runde). Der Modus ist **rundenbasiert** — keine Echtzeit-Anforderungen: Retries, gelegentliche Latenz oder einzelne verpasste Events sind unkritisch; HTTP/WebSocket reicht völlig.

## Roadmap

1. **Spike** — Mod-Skeleton + Wave-Spawn-Experimente + Custom-UI *(gebaut; In-Game-Test offen)*
2. **Trainer-Harness** — Injector + DLL-Grundgerüst + Named Pipe *(läuft)*
3. **RE-Phase** — Live-Session (Windows + Spiel): Scan-Skripte; Offsets/Signaturen für Game-State & Command-Aufrufe sammeln
4. **Read-PoC** — Score/State per DLL über die Pipe auslesen (Egress)
5. **Write-PoC** — Command-Ausführung über die DLL (Ingress, z. B. Wave-Trigger)
6. **Relay-Client + Server-Skeleton** — Event-Pfad Partie A → Server → Partie B schließen
7. **Alpha 1v1** — erster spielbarer Duell-Lauf

## Risiken

- **Game-Updates brechen Trainer-Signaturen:** Mod-Logik bleibt davon unberührt; Offsets/Signaturen des Trainers müssen nach jedem Patch geprüft werden. Der Trainer sollte AOB-Signaturen statt fester Adressen nutzen.
- **macOS-Trainer deutlich aufwendiger:** SIP + Hardened Runtime — bewusst vertagt (Windows-first).
- **Trainer nicht Workshop-fähig:** Die Trainer-DLL (In-Process-Injection) verstößt gegen die Plattform-Regeln — bewusst nur private Distribution („für uns“-Projekt). Die Mod selbst bleibt ein normaler Workshop-Mod; runtime-only Injection modifiziert keine Game-Dateien.
