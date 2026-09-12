# Referee — der Tournament-Server als autoritative Event-/State-Quelle (Issue #268)

„Solo online“: **Der Tournament-Server ist das Gehirn, das Spiel ist ein dummer
Executor.** Der Server entscheidet, *wann* eine Welle kommt und *wann* neu
gestartet wird; die in-game Lua führt nur noch Commands aus und meldet
Ereignisse nach oben. Das ist die Ablösung des bisherigen Modells, in dem die
Lua-Mod die Runden-/Wellen-Logik selbst hielt und die Events nur „nebenbei“
ins Log schrieb.

```
            Events (Rückkanal)                 Commands
  in-game Lua  ───────────────►  Referee  ───────────────►  in-game Lua
  (Executor)   Relay/Pipe #265  (Server)   rb_wave N / rb_reset   (Executor)
```

Transport ist der IO-Kanal aus #265: `Tournament-Server → relay.py
(07-relay) → Named Pipe \\.\pipe\rbbattle → rbbridge.dll →
ConsoleService::ExecuteCommand`. Der Referee selbst ist transport-neutral und
kennt nur Events rein / Commands raus.

## Rollen

| Seite | Verantwortung |
|---|---|
| **Referee** (`tournament/`, `src/referee.rs`) | Wellen-Takt (Level-Vergabe), HQ-Tod → Restart, Runden-Zähler, Dedup/Idempotenz. Deterministisch, kein I/O, keine Uhr. |
| **Executor** (in-game Lua) | Führt `rb_wave N` / `rb_reset` aus, meldet `ready` / `wave_done` / `hq_destroyed` nach oben. **Keine eigene Runden-/Match-Logik mehr** (Stufe 2, s. u.). |
| **Relay/Bridge** (#265) | Transport beider Richtungen (Log/Pipe → Event, Command → Pipe). |

## Event-Schema (Executor → Referee)

`POST /referee/event`

```json
{"world": "A", "type": "ready"}
{"world": "A", "type": "wave_done", "level": 3}
{"world": "A", "type": "hq_destroyed"}
```

| `type` | Bedeutung | Wirkung im Referee |
|---|---|---|
| `ready` | Executor oben (Map geladen / nach `rb_reset`) | Welle 1 der (neuen) Runde wird ausgegeben |
| `wave_done` | `event=wave level=N status=done` aus dem Game-Log | nächste Welle (`level+1`), sofern unter dem Deckel |
| `hq_destroyed` | `event=hq_dead` aus dem Game-Log | Restart-Command + Runde +1, Wellen ruhen bis `ready` |

**Idempotenz:** doppeltes `ready` im laufenden Zustand, `wave_done` mit
veraltetem/fremdem Level und mehrfaches `hq_destroyed` erzeugen **keinen**
zusätzlichen Command — ein doppeltes Log-Event löst nie eine Doppel-Welle oder
einen Doppel-Restart aus.

## Command-Schema (Referee → Executor)

```json
{"world": "A", "command": "rb_wave 3", "cmd_id": 7, "reason": "wave_done"}
{"world": "A", "command": "rb_reset",  "cmd_id": 8, "reason": "hq_destroyed round=1"}
```

`cmd_id` ist der monotone Dedup-Schlüssel, den der Relay bereits aus dem
Pipe-Vertrag kennt (`docs/relay-pipe-contract.md`).

Zustellung (drei Wege):

1. **Direkt in der Antwort** von `POST /referee/event` (`commands: [...]`).
2. **Abholen** über `GET /referee/poll?world=A` (leert die Outbox der Welt).
3. **Push** an die Bridge (`RBBRIDGE_*_URL`) beim echten HQ-Tod über
   `POST /report` `hq_dead` (#267) — server-seitig verdrahtet, Live-Zustellung
   über die Pipe offen (#265).

**Genau eine Zustellung pro Command (B1, #267).** Der `/report`-`hq_dead`-Pfad
pusht inklusive `cmd_id` und **ackt** ein erfolgreich zugestelltes Command
(entfernt es aus der Outbox). Der Poll liefert es dann nicht erneut: Push
**oder** Poll, nie beides — auch wenn der #265-Poll-Executor live ist. Schlägt
der Push fehl (Endpoint down/Timeout/nicht-2xx) bzw. ist kein Endpoint
konfiguriert, bleibt das Command in der Outbox und wird über den Poll
zugestellt (Retry). Ein gepushter Payload trägt `cmd_id`/`world`/`reason`
zusätzlich zum `command`; der Dedup-Schlüssel ist `cmd_id`
(`docs/relay-pipe-contract.md`). Der Push läuft **synchron im Request** (wie der
manuelle GO-Broadcast, nicht wie `AUTO_GO`/`spawn_go_broadcast`) und wird im
Antwortfeld `broadcast` gemeldet; er schreibt **nicht** in den
GO-spezifischen `/state`-Broadcast-Status (bewusst, R3).

## Zustandsmaschine (pro Welt)

```text
        ready                 wave_done(level == offen)        hq_destroyed
  ──────────────► RUNNING ──────────────────────────► RUNNING ──────────────► RESTART
   (rb_wave 1)    Wellen-Puls (rb_wave N+1)                        (rb_reset, Runde→0)
                       ▲                                                 │
                       └──────────────────── ready ──────────────────────┘
```

* Der Wellen-Level wird **serverseitig** vergeben (1-basiert, monoton pro
  Welt), nicht mehr in der Lua gezählt.
* `TOURNAMENT_REFEREE_MAX_WAVE` deckelt den Level (`0` = unbegrenzt); ab dem
  Deckel gibt der Referee keine weitere Welle mehr aus.
* `TOURNAMENT_REFEREE_RESTART_CMD` legt den Restart-Command fest (Default
  `rb_reset` — das in-game Mod-Kommando aus #281).

## Test-Split (Pflicht, Issue #268)

* **OHNE Player (erledigt, automatisiert):** `cargo test` in `tournament/`.
  Der Referee-Kern ist deterministisch (Event-In → Command-Out): `ready` →
  `rb_wave 1`, `wave_done` → nächste Welle, `hq_destroyed` → `rb_reset` +
  Runde, Duplikate/Deckel/Welt-Isolation. Zusätzlich HTTP-Level-Tests für
  `POST /referee/event` und `GET /referee/poll`.
* **NUR mit Player (OFFEN — Player-Test Momo/Matheo):** der volle Loop
  *Welle spawnt sichtbar → HQ zerstört → Restart* über die echte Pipe. Das
  ist **nicht** automatisiert prüfbar und bleibt offen, bis #265 deployt ist.

## Offene Punkte / Design-Entscheidungen

* **Wellen-Takt ist Event-getaktet, nicht zeitgetaktet (v1).** Der Referee gibt
  die nächste Welle erst nach dem `wave_done` der vorigen aus — kein
  Server-Timer, keine Uhr. Das hält die Logik deterministisch und ohne Player
  testbar. Ein zeitbasierter Takt (z. B. Planungsphase mit Deadline) ist ein
  möglicher Folgeschritt.
* **`rb_reset`-Command ist konfigurierbar (`TOURNAMENT_REFEREE_RESTART_CMD`).**
  Der Server pusht nach `hq_destroyed` den in-game Round-Reset des Mods
  (Default `rb_reset`): der Mod setzt Runde/Wave-Timer auf 0, leert die Economy
  und geht in die HQ-Placement-Phase (in-game Lua, #281). Der frühere grobe
  Fallback (`docker restart` in `tools/solo-feed/`) bleibt für Umgebungen ohne
  den in-game Reset dokumentiert. Die **Live-Zustellung** über die echte Pipe
  (Restart-Command kommt im laufenden Spiel an) klärt der Player-Test.
* **Kein MatchState-`FINISHED` beim HQ-Tod, Antwort ohne `match_over` (B2, #267).**
  `POST /report event=hq_dead` fasst **nur** den Referee an (`rb_reset`, Runde +1
  im Referee; der Mod setzt die Runde auf 0, #281);
  der Match-Zustand bleibt unverändert. Die Antwort meldet darum den realen
  Referee-Zustand (`restart`/`rounds`/`referee_running`/`restart_pending`) und die
  aktuelle `phase` — kein `match_over:true` (das wäre nur über
  `event=hq_hp` mit `hp ≤ 0` wahr, das den Übergang nach `FINISHED` wirklich
  ausführt).
* **`ignored` statt `duplicate` (R1, #267).** Ein verworfenes `hq_dead` liefert
  `ignored:true`; `referee_running`/`restart_pending` unterscheiden „kein
  laufendes Match“ (beide `false`) von „Repeat nach Restart“ (`restart_pending`
  `true`). `restart` ist an das konfigurierte Restart-Command gebunden, nicht an
  „irgendein Command“ (R2).
* **Koexistenz mit der bestehenden Match-State-Machine.** `MatchState`
  (`state.rs`, Duel-Flow aus #29/#30/#44) verwaltet Lobby/Ready/GO/Reveal; der
  Referee ist die **neue autoritative Wellen-/HQ-/Runden-Quelle** für den
  Solo-online-Fall. Eine spätere Zusammenführung beider Zähler ist möglich,
  aber bewusst nicht in diesem Schritt (kleiner, testbarer Kern).
* **Lua-Reduktion ist Stufe 2 (OFFEN).** Die Lua emittiert die nötigen
  Signale bereits (`event=wave level=N status=done`, `event=hq_dead`) und
  registriert `rb_wave`. Das *Entfernen* der in-game Runden-/Match-Logik
  (natürlicher Wellen-Timer als Rundentakt) ist ein eigener, größerer Schnitt,
  der nur mit laufendem Spiel sicher verifizierbar ist — daher hier noch nicht
  umgesetzt.
* **Deploy-Dependency #265.** Ohne den deployten IO-Kanal (Relay/Bridge im
  Dedicated-Container) ist der Loop nur halb verdrahtet; der Server-Teil ist
  fertig und getestet, der Transport folgt mit #265. Der Restart-Push ist
  **server-seitig verdrahtet** (`POST /report` `hq_dead` → `rb_reset` an
  `RBBRIDGE_<W>_URL`); die Live-Zustellung über die echte Pipe bleibt offen.
