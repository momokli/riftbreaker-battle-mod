# ARCHITECTURE — Ziel-Modell (RIFT BATTLE)

> **Status:** Entwurf zur Freigabe. Dieses Dokument ist die verbindliche Referenz
> für Game-State, Routing und IO. Issues/PRs referenzieren hierauf, damit der
> issue→PR-Flow kohärent bleibt. Iteration gegen dieses Doc ist gewollt.

## 1. Kernprinzip

- Der **Tournament-Server** ist der Backend: **EIN Match-State mit ZWEI Welten (A, B)**.
- Das **Spiel (Dedicated Server)** ist ein *dummer Executor*: führt Commands aus
  (`rb_wave N`, `rb_reset`) und meldet Events nach oben — **keine eigene
  Match-/Runden-/Routing-Logik**.
- Alle Business-Logik (Routing, Taktung, Match-Lifecycle) liegt im Backend.

## 2. Zwei Welten (A, B)

Ein Match hat **immer** zwei Welten:

| | `vs` (1v1) | `solo` (dev) |
|---|---|---|
| Physische Server | 2 (A und B) | 1 (nur A) |
| Welt B | echter Gegner | serverseitiger **MIRROR** (COPY von A) |

`solo` ist **kein** eigenes Single-World-Modell, sondern dasselbe Zwei-Welten-
Modell mit Welt B als serverseitigem Spiegel.

## 3. Routing (`route_send`)

- `route_send(from, units, value)` erzeugt eine **Send-Batch (Welle)** und
  routet sie an die **Gegenwelt** (`from.opponent()`).
- Die Send landet in der Queue der Ziel-Welt; beim `wave_start` der Ziel-Welt
  wird sie **gedraint und „revealt"** (die Welle erscheint dort).
- Melden **beide** Welten ihren Wellenstart der Runde R, ist der Reveal
  vollständig → **Runde +1**.

## 4. Modus-Switch: vs vs. solo — DER entscheidende Punkt

**Selbe Business-Logik, nur der Routing-Target unterscheidet sich.**

| | `Mode::Duel` (vs) | `Mode::Sp` (solo) |
|---|---|---|
| Send von A | → B (echter Gegner) | → B (MIRROR) |
| Was passiert | B sieht die Welle | B ist ein **COPY**: der Backend tut so, **als hätte B gesendet, was A gesendet hat**, und routet es zurück an A |
| Effekt | Du besiegst den Gegner | Du „duellierst dich gegen dich selbst" (eigene Welle kommt zurück) |

**Invariante:** `route_send` bleibt identisch. Nur das Ziel „B" ist im solo-Fall
eine **COPY-Rückgabe** statt eines echten Gegners. Der `Mode`-Switch ist genau
dieser eine Unterschied — kein zweiter Codepfad für die Business-Logik.

## 5. IO-Kanal (bidirektional)

```
Spiel (dummer Executor)
   ├─ INGRESS:  rb_wave N / rb_reset   ←  pipe_bridge (Named Pipe → ExecuteCommand)
   └─ EGRESS:   [RBBATTLE]-Log          →  Tail → Parse → POST (world-getaggt)
                                                        ↓
                                              EIN Match-State (Backend)
```

- **Ingress** (Backend → Spiel): `pipe_bridge` (`HTTP :9001` → Named Pipe →
  `ConsoleService::ExecuteCommand`). Commands: `rb_wave N`, `rb_reset`.
- **Egress** (Spiel → Backend): `[RBBATTLE]`-Zeilen im `exor_logs.txt` →
  Feeder tailt + parst → POST **world-getaggt** an den Backend.

## 6. EIN Match-State (nicht zwei)

Ein `MatchState` vereint (statt paralleler `MatchState` + `Referee`):

- **Lifecycle:** `lobby → ready → running → finished`.
- **Taktung** (server-autoritativ): `ready → rb_wave 1`, `wave_done → rb_wave N+1`,
  `hq_dead → rb_reset` (Runde +1).
- **Routing:** `route_send` (mit Mirror-Switch aus Abschnitt 4).
- **Pro-Welt:** `player`, `ready`, `hq_hp`, `score`, `resources`, `wave`,
  `pending`, `broadcast`.

Der frühere separate **Referee** (#268) wird hier **eingefaltet**, nicht parallel
geführt.

## 7. Event-/Command-Kontrakt

### Egress (Spiel → Backend), world-getaggt

| `[RBBATTLE]`-Zeile | Event | Wirkung |
|---|---|---|
| `event=mod_load` / `event=setup` | `ready` | Welt bereit |
| `event=wave status=start` | `wave_start` | Wellenstart → Reveal |
| `event=wave level=N status=done` | `wave_done` | Welle fertig → nächste Welle |
| `event=hq_hp hp=N` | `hq_hp` | HQ-HP aktualisieren |
| `event=score_update …` | `score_update` | Score/Resources |
| `event=hq_dead` | `hq_dead` | HQ-Tod → restart |

### Ingress (Backend → Spiel)

| Command | Bedeutung |
|---|---|
| `rb_wave N` | Welle N spawnen |
| `rb_reset` | Runde zurücksetzen |

## 8. Offene Punkte (zur Iteration gegen dieses Doc)

- [ ] **Exakte Mirror-Mechanik:** wie „COPY von A → zurück an A" konkret
      implementiert wird — als eigener Schritt im `route_send`, als
      `start_sp`-Sonderfall, oder als Welt-B-Proxy.
- [ ] **Taktung vs. Routing:** gehören `Referee`-Taktung (`rb_wave N`-Vergabe)
      und `route_send`-Routing in eine State-Machine zusammengeführt, oder
      bleibt Taktung eine eigene Schicht **auf** dem einen `MatchState`?
- [ ] **Egress-Feeder:** world-getaggt auf den `MatchState` (`/report` bzw.
      einen vereinheitlichten `/state`-Endpoint) statt auf `/referee/event`.
- [ ] **`MatchState`-Feld-Erweiterung:** `next_wave`/`restart_pending` aus dem
      Referee in `TeamState`/`MatchState` übernehmen, damit `/state` sie zeigt.
