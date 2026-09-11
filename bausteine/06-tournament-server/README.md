# Baustein 06 — Tournament-Server (zentraler Relay/Match-Server) + Mock-Client

**Was es testet:** Die Server-Seite der Turnier-Architektur aus
`docs/concept.md` — N Spielinstanzen (je Lua-Mod + Trainer-DLL) koppeln
über einen Relay-Client an **einen zentralen Tournament-Server**:

- Spieler-Registrierung, Match-Anlage, Event-Zustellung per **Polling/Outbox**
  (Pull-Modell, passend zum rundenbasierten Spiel — keine Echtzeit),
- Event-Routing: `wave_sent` von A → `incoming_wave` in Bs Outbox,
- Rundenzählung auf dem Server, automatisches `round_end` nach Timer,
  `match_end` nach konfigurierter Rundenzahl,
- Scoreboard im Match-State (`score_update` kumuliert).

**Reine Node-Standardbibliothek** (`node:http`) — keine npm-Dependencies,
keine Persistenz, kein Auth, kein Docker (bewusst, kein Feature-Creep).
Events folgen dem Vertrag in `trainer/protocol.md` (Mapping unten).

## Inhalt

```
server.js       <- Tournament-Server (In-Memory, nur node:http)
mock_client.js  <- Simulierte Spielinstanz (registrieren, joinen, Poll-Loop,
                   score_update periodisch, wave_received quittieren; seedbare Aktionen)
test_e2e.sh     <- E2E: Server + 2 Mock-Clients, skriptiertes Match, Assertions
README.md       <- dieses Dokument
```

## Start

```bash
node server.js            # PORT via env, Default 8080
PORT=9000 node server.js  # eigener Port
```

Der Server loggt Lifecycle-Ereignisse (`[server] ...`) auf stdout.

## API-Übersicht

Alle Antworten sind JSON. Fehler: `{"error": "<meldung>", "type"?: "<wert>"}`
mit passendem Statuscode (400 invalid event type / invalid payload, 404 match
bzw. player, 409 round already running / limit erreicht / match finished).

### Spieler & Matches

| Endpoint | Body | Antwort |
|---|---|---|
| `POST /register` | `{"player_id":"player_a"}` | `{"player_id":"player_a","existed":false}` (idempotent) |
| `POST /match/create` | `{"players":["player_a","player_b"],"rounds":2}` | `{"match_id":"m1-...","players":[...],"rounds_total":2,"status":"waiting"}` |
| `GET /match/:id/state` | — | Match-State inkl. `status`, `round`, `winner`, `scoreboard` |
| `GET /poll/:player_id` | — | `{"player_id":"...","events":[...]}` — **Outbox wird geleert** |

`rounds` ist optional (Default 3). Beide Spieler müssen vor `/match/create`
registriert sein. `/health` liefert `{"ok":true}`.

### Events einliefern (game → server, Typen aus protocol.md)

`POST /event` mit `{"match_id":"...","player_id":"...","event":{"type":"...", ...}}`

| `event.type` | Pflichtfelder | Server-Wirkung |
|---|---|---|
| `score_update` | `score` (number ≥ 0) | Scoreboard-Update für den Spieler (Kumulation im Match-State) |
| `wave_sent` | `level` (int ≥ 1) | Routing: `incoming_wave` in die **Outbox des Gegners** |
| `wave_received` | `level` | Quittung, wird protokolliert |
| `round_start` | `round` | Spielseitige Bestätigung, wird protokolliert |
| `round_end` | `round` | Spielseitige Auswertung, wird protokolliert |
| `match_end` | `winner` | Match sofort beenden; `match_end` in **beide** Outboxen |
| `exec_result` | `command`, `ok` (boolean) | Dispatch-Feedback des Relays (Issue #89): protokollieren + per SSE an die Web-UI broadcasten (kein eigener Server-State) |

### Runden-Lifecycle

`POST /round/start` mit `{"match_id":"...","duration_s":90}`:

1. Server erhöht den Rundenzähler, schickt `round_start` in **beide** Outboxen,
   startet einen Timer (`setTimeout`).
2. Timer abgelaufen → automatisch `round_end` (mit `summary` des aktuellen
   Scoreboards) in **beide** Outboxen.
3. Nach der **letzten** konfigurierten Runde (`rounds_total`) wartet der Server
   ein Settle-Fenster (3 s, Konstante `SETTLE_MS`), damit die Clients ihre
   finalen `score_update`/`round_end`-Meldungen einliefern können, und erzeugt
   dann `match_end` mit `winner`/`reason:"score"` (bzw. `"draw"` bei Gleichstand).
4. Spielseitig gemeldetes `match_end` (z. B. `base_destroyed`) beendet sofort.

## Event-Format (Zustellung via Poll)

Zugestellte Events entsprechen dem Server→Spiel-Vertrag aus `trainer/protocol.md`
(`t` = Server-Zeitstempel ms):

```json
{"event":"round_start","round":2,"duration_s":90,"phase":"planning","t":1720000000000}
{"event":"incoming_wave","level":3,"from":"player_a","delay_s":5,"t":1720000000000}
{"event":"round_end","round":2,"summary":{"score_player_a":1560,"score_player_b":1320},"t":1720000000000}
{"event":"match_end","winner":"player_a","reason":"score","t":1720000000000}
```

`incoming_wave.delay_s` = 5 (Default) bzw. `delay_s` aus `wave_sent`, falls dort
mitgegeben (unbekannte Felder werden laut protocol.md ignoriert → vorwärtskompatibel).

## Mapping auf trainer/protocol.md

| protocol.md (game → server) | Umsetzung | protocol.md (server → game) | Umsetzung |
|---|---|---|---|
| `score_update` | `POST /event` type `score_update` → Scoreboard | `round_start` | Outbox bei `/round/start` |
| `wave_sent` | `POST /event` type `wave_sent` → Routing | `incoming_wave` | Outbox des Gegners bei `wave_sent` |
| `wave_received` | `POST /event` type `wave_received` (Quittung) | `round_end` | Outbox bei Timer-Ablauf |
| `round_start` | `POST /event` type `round_start` (Bestätigung) | `match_end` | Outbox nach letzter Runde / bei Spiel-Meldung |
| `round_end` | `POST /event` type `round_end` (Auswertung) | | |
| `match_end` | `POST /event` type `match_end` → sofortiges Ende | | |
| `exec_result` | `POST /event` type `exec_result` (Dispatch-Feedback des Relays, Issue #89) | | |

Abweichung v0-Harness: Im Harness ist der Transport die Named Pipe
`\\.\pipe\rbbattle`; der Pipe-Client wird später zum Forwarder auf diese
HTTP-Endpoints (siehe protocol.md, Abschnitt „Transport v0“).

## Testablauf

```bash
node --version   # Voraussetzung: Node.js (keine npm-Installation nötig)
bash test_e2e.sh # Exit 0 = alle Assertions ok
```

Der Test startet Server + 2 Mock-Clients (player_a = Creator, player_b =
Gegner) in einem skriptierten Match über **2 Runden à 4 s**:

- A schickt pro Runde eine Welle (`wave_sent` level 1, level 2),
- B empfängt beide als `incoming_wave` und quittiert (`wave_received`),
- beide liefern periodisch `score_update` (A: 1000+1000, B: 1050+1100 —
  +50 je empfangenem Wellen-Level),
- `round_end`/`match_end` kommen bei beiden an, `winner=player_b` (höherer
  Score), Match-State `finished`.

Assertions decken ab: empfangene Events, kumulierte Scores, Sieger, Server-
State (`status/winner/reason/scoreboard`) und den Error-Kontrakt
(400 invalid event type, 404 match/player, 409 round start nach match_end).

### Mock-Client einzeln

```bash
node mock_client.js --player player_a --creator --opponent player_b \
  --rounds 2 --duration 5 --url http://127.0.0.1:8080 --state-dir /tmp/rb
node mock_client.js --player player_b --url http://127.0.0.1:8080 --state-dir /tmp/rb
```

Seedbare Aktionen: `--creator` startet Runden und schickt Wellen
(`--no-attack` deaktiviert); Koordinationsdateien liegen im `--state-dir`
(`match.json`, `<id>.ready`, `summary.<id>.json`).

## Status

- [x] server.js: nur node:http, In-Memory, Register/Match/Create/State/Event/Poll/round-start
- [x] Event-Typen gegen protocol.md validiert (400 invalid event type)
- [x] Routing wave_sent → incoming_wave (Outbox), score_update → Scoreboard
- [x] Runden-Lifecycle: Timer → round_end (beide), match_end nach letzter Runde
- [x] mock_client.js: Poll-Loop, incoming_wave loggen, wave_received-Quittung, seedbare Aktionen
- [x] test_e2e.sh: 2 Runden skriptiert, alle Assertions grün (lokal ausgeführt, node v24)
- [ ] Realer Anschluss: Relay-Client/Trainer-DLL als Forwarder auf diese Endpoints (Baustein folgt)
- [ ] Mehrere parallele Matches/Instanzen im echten Betrieb verifizieren
