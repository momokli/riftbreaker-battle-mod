# RIFT BATTLE — Tournament-API v1 (Referee-Protokoll)

Der Tournament-Server ist der **zentrale Referee** zwischen den zwei
Riftbreaker-Dedi-Welten (A und B). Er verwaltet Lobby, Ready-Check, den
synchronen GO-Start, das Wave-Routing (Sends A→B/B→A), den Reveal bei
Wellenstart und den Match-Zustand bis zum HQ-Tod. Implementierung:
Rust/axum in `tournament/` (Issue #29), Web-UI in `tournament/web/`
(Issue #30). v1 bewusst ohne Auth/Persistenz — In-Memory, ein Match
(Rematch über denselben Match-State), Deployment macht der Operator.

- **Server**: `cargo run` (bzw. `cargo build --release`), siehe
  [tournament/README.md](../tournament/README.md)
- **Bridge-Seite**: `tournament/bridge/poller_example.py` (Referenz-Poller
  für rbbridge/tools auf jeder Dedi-Welt)

## Konfiguration (nur Env, keine Hardcodes)

| Env | Default | Bedeutung |
|---|---|---|
| `TOURNAMENT_HOST` | `0.0.0.0` | Bind-Adresse |
| `TOURNAMENT_PORT` | `8080` | HTTP-Port (API + Web-UI) |
| `TOURNAMENT_AUTO_GO` | `true` | GO automatisch, sobald beide Welten ready |
| `RBBRIDGE_A_URL` | — | HTTP-Endpoint der Welt-A-Bridge (GO-Push) |
| `RBBRIDGE_B_URL` | — | HTTP-Endpoint der Welt-B-Bridge (GO-Push) |
| `TOURNAMENT_GO_COMMANDS` | `debug_dom_resume` | Komma-separierte Unpause-/Start-Kommandos je Welt beim GO (je EIN gequotetes Argument, Issue #18) |
| `TOURNAMENT_GO_TIMEOUT_MS` | `3000` | Timeout je Broadcast-Endpoint |
| `TOURNAMENT_HQ_HP` | `100` | Start-HP jedes HQ |
| `TOURNAMENT_REFEREE_MAX_WAVE` | `0` | Wellen-Deckel des Referees (`0` = unbegrenzt, Issue #268) |
| `TOURNAMENT_REFEREE_RESTART_CMD` | `rb_reset` | In-game Command des Referees bei HQ-Tod (Issues #268/#281; Mod-Kommando `rb_reset`) |
| `TOURNAMENT_WEB_DIR` | `<crate>/web` | Verzeichnis der statischen Web-UI |
| `RUST_LOG` | `info` | Log-Level |

`RBBRIDGE_*_URL` zeigen auf den HTTP-Adapter der jeweiligen Dedi-Bridge
(z. B. `http://10.0.0.5:9001/exec`). Fehlen sie, entfällt der Push und die
Bridges erkennen den Start ausschließlich über Polling von `GET /state`.

## Match-Lebenszyklus

```text
LOBBY ── beide Spieler registriert ──► LOBBY
LOBBY ── beide Welten ready (AUTO_GO=off) ──► READY (GO steht aus)
LOBBY/READY ── POST /go (oder AUTO_GO beim 2. Ready) ──► RUNNING (Runde 1)
RUNNING ── Runden-Loop ── HQ einer Welt ≤ 0 (event=hq_hp) ──► FINISHED (winner)
FINISHED ── POST /rematch ──► LOBBY (Spieler bleiben, Rematch-Zähler +1)
```

> **`POST /report event=hq_dead` beendet kein Match** und wechselt den
> MatchState **nicht** nach `FINISHED`: Das Event fasst ausschließlich den
> Referee an (`rb_reset` + Runde +1; der Mod setzt die Runde dann auf 0, #267/#281).
> Match-Ende läuft weiterhin über `event=hq_hp` mit `hp ≤ 0` → `FINISHED`.

### Runden-Loop (RUNNING)

1. **Build-Phase (Runde R):** Beide Welten bauen; Sends gehen jederzeit an den
   Referee (`POST /send`), werden der aktuellen Runde zugestempelt und landen
   in der Queue der **Gegner-Welt** (dort spawnen sie in der nächsten Welle —
   GDD: „Sends boosten die NÄCHSTE Naturwelle“).
2. **Wellenstart (Lock + Reveal):** Jede Welt meldet ihren Wellenstart
   (`POST /report event=wave_start`, inkl. Built-Value zum Lock). Dabei wird
   die eingehende Send-Queue dieser Welt **gedraint** und in den Reveal
   übernommen. Der Reveal wird erst **vollständig**, wenn beide Welten ihren
   Wellenstart derselben Runde gemeldet haben — dann zählt der Referee auf
   Runde R+1 hoch.
3. **HQ-Schaden:** Die Welt meldet ihren HQ-HP (absolut, `event=hq_hp`).
   Bei ≤ 0 → Phase `finished`, `winner` = Gegner-Welt.
4. **Rematch:** Nach Match-Ende setzt `POST /rematch` in die Lobby zurück
   (Spieler bleiben registriert, ready/HP/Queues werden zurückgesetzt).

## Endpoints

Alle Antworten sind JSON. Fehler:
`{"error": "<meldung>", "type": "invalid|not_found|conflict"}` mit
400/404/409. Unbekannte Felder in Bodies werden **ignoriert**
(vorwärtskompatibel). Unbekannte Pfade → statische Web-UI bzw. 404.

### POST /lobby — Spieler registrieren

```json
{"player": "momo", "world": "A"}
```

Idempotent; Namenswechsel setzt den Ready-Status der Welt zurück. Nur in
Phase `lobby` (sonst 409). Antwort:

```json
{"world": "A", "player": "momo", "created": true,
 "match_complete": false, "phase": "lobby"}
```

### POST /ready — Welt meldet sich bereit

```json
{"world": "A"}
```

Voraussetzung: Welt registriert. Antwort:

```json
{"world": "A", "phase": "ready", "match_started": false,
 "round": 0, "teams": { … je Welt ready/hq_hp/… }}
```

Sind **beide** Welten ready und ist `TOURNAMENT_AUTO_GO=true`, startet der
Referee sofort (`match_started: true`, Phase `running`, Runde 1) und
broadcastet GO an beide `RBBRIDGE_*_URL`-Endpoints (async). Bei
`AUTO_GO=false` geht es in Phase `ready`, bis `POST /go` kommt.

### POST /go — GO-Broadcast + Start

```json
{}              // Start aus lobby/ready (beide Welten müssen registriert sein)
{"retry": true} // laufendes Match: Broadcast erneut senden (z. B. nach Endpoint-Fehler)
```

Broadcast-Payload an jede Bridge (`POST` auf `RBBRIDGE_*_URL`):

```json
{"cmd": "go", "match_id": "rift-1", "round": 1,
 "commands": ["debug_dom_resume"]}
```

`commands` ist die **geordnete** Liste der Unpause-/Start-Kommandos, die die
Bridge je Welt ausführen muss (Sync-Start, Issue #22): `exec_cmd_client
"<cmd>"` als EIN gequotetes Argument (Issue #18). Default ist
`debug_dom_resume` (DOM-Ebene, verifiziert — SYNC_START.md); die native
Server-Pause (`resume_game`, unverifiziert) wird per `TOURNAMENT_GO_COMMANDS`
ergänzt, ihr Fallback ist das automatische `ResumeGame` beim Client-Join
(`server_pause_game_when_empty`).

Die Bridge führt daraus ihr GO aus (Unpause der pausierten Welt) — über
`exec_cmd_client`/den rbbridge-exec-Dispatch; der Server behandelt den Push
als **nicht-kritisch**: Zustell-Status landet in `teams.<W>.go_broadcast`
von `GET /state`, der zuverlässige Kanal ist das Polling der Bridges.
Antwort:

```json
{"started": true, "phase": "running", "round": 1,
 "broadcast": {"A": {"ok": true, "status": 200, "error": null, "endpoint": "http://…"},
               "B": {"ok": null, "note": "kein Endpoint konfiguriert — Bridges pollten /state"}}}
```

### POST /send — Wave-Routing

```json
{"world": "A",
 "units": [{"unit": "creeper", "count": 5}, {"unit": "brute", "count": 2}],
 "value": 1500}
```

Nur in Phase `running` (sonst 409). Der Send wird in die Queue der
**Gegner-Welt** gelegt und bei deren nächstem Wellenstart in den Reveal
übernommen. Antwort: `{"queued_for": "B", "round": 1, "batch": {…}, "pending_sends": 1}`.

### POST /report — Welt-Events (send_state-Egress, Issue #13 konzeptionell)

```json
{"world": "A", "event": "wave_start", "built_value": 8200}
{"world": "A", "event": "hq_hp", "hp": 70.0}
{"world": "A", "event": "score_update", "score": 1240, "resources": {"iron": 320, "carbon": 80}, "wave": 4}
{"world": "A", "event": "hq_dead"}
```

- `wave_start`: Wellenstart der Welt (Lock). `built_value` optional
  (Built-Value zum Reveal). Idempotent je Runde: Antwort
  `{"effect": "locked"|"duplicate", "round": …, "rounds_done": …, "phase": …}`.
- `hq_hp`: aktueller HQ-HP (absolut, 0 = HQ-Tod → Match-Ende). Antwort
  `{"match_over": bool, "winner": …, "hq_hp": …}`.
- `score_update`: periodischer State-Snapshot (send_state-Egress, Issue #13) —
  Score, Ressourcen und aktuelle Wave einer Welt. Idempotent; der Feed wird nur
  bei Score-/Wave-Änderung belastet. Antwort
  `{"event": "score_update", "score": …, "wave": …, "changed": bool, "phase": …}`.
- `hq_dead` (Aliase `hq_destroy`/`hq_destroyed`, Issue #267): HQ-Tod aus dem
  echten Spiel (Mod-Log `event=hq_dead status=match_end hp=0`). Wird als
  `HqDestroyed` in den Referee gespeist; der Referee entscheidet genau EIN
  `rb_reset` (aus `TOURNAMENT_REFEREE_RESTART_CMD`) und der Server **pusht** es an
  die Bridge der Welt
  (`POST <RBBRIDGE_<W>_URL> {"command": "rb_reset", "cmd_id": …, "world": "A", "reason": …}`,
  analog GO-Broadcast). Antwort
  `{"event": "hq_dead", "phase": …, "rounds": …, "restart": bool, "ignored": bool, "referee_running": bool, "restart_pending": bool, "acked": n, "commands": […], "broadcast": […]}`
  (`broadcast[i] = {command, cmd_id, ok, status, error, endpoint}`).
  **Kein `match_over`/`winner`:** #267 startet nur die Runde neu, kein Match-Ende
  (s. o.).
  **Idempotent + genau einmal zugestellt:** ein zweites `hq_dead` liefert aus dem
  Referee keine Commands → `restart:false`, `ignored:true`, **kein** zweiter Push.
  Erfolgreich gepushte Commands werden aus der Referee-Outbox entfernt (`acked`)
  — der Poll-Pfad (`GET /referee/poll`) liefert sie daher **nicht** doppelt
  („Push **oder** Poll“). Schlägt der Push fehl oder ist kein Endpoint
  konfiguriert (`RBBRIDGE_<W>_URL`), bleibt das Command in der Outbox und wird
  über den Poll zugestellt (`ok:null`/Fehler, kein Panic/Crash). `ignored:true`
  heißt „vom Referee-Guard verworfen (kein laufendes Match bzw. Repeat nach
  Restart)“; `referee_running`/`restart_pending` unterscheiden die beiden Fälle.

> Der Live-Player-Test (echtes HQ zerstören → Runde startet sichtbar neu) bleibt
> **offen** und braucht den deployten IO-Kanal (#265).

### POST /referee/event — Spiel-Event an den Referee (Issue #268)

Der Referee ist die autoritative Event-/State-Quelle; die in-game Lua ist
reiner Executor. Spiel-Events kommen über den Rückkanal (Relay/Pipe, #265),
Commands gehen in der Antwort und/oder über `GET /referee/poll` zurück.

```json
{"world": "A", "type": "ready"}
{"world": "A", "type": "wave_done", "level": 3}
{"world": "A", "type": "hq_destroyed"}
```

| `type` | Wirkung | Command |
|---|---|---|
| `ready` | Executor oben (Map geladen / nach `rb_reset`) | `rb_wave 1` |
| `wave_done` (mit `level`) | Welle abgeschlossen | `rb_wave <level+1>` (bis `TOURNAMENT_REFEREE_MAX_WAVE`) |
| `hq_destroyed` | HQ zerstört | `rb_reset` (Mod: Runde auf 0, Setup-Phase), Runde +1, Wellen ruhen bis `ready` |

Duplikate/veraltete Level/mehrfaches `hq_destroyed` sind idempotent (kein
Doppel-Command). Antwort:

```json
{"world": "A", "type": "wave_done", "accepted": true,
 "commands": [{"world": "A", "command": "rb_wave 4", "cmd_id": 7, "reason": "wave_done"}],
 "state": {"running": true, "restart_pending": false, "waves_in_flight": 4,
           "next_level": 4, "rounds": 0, "commands_sent": 4, "queued_commands": 1}}
```

Fehler: unbekannte Welt → 400 `invalid`; `wave_done` ohne `level` → 400;
unbekannter `type` → 422 (serde).

### GET /referee/poll — offene Referee-Commands (Issue #268)

`?world=A` — holt alle noch nicht abgeholten Commands der Welt (leert die
Outbox):

```json
{"world": "A", "commands": [{"world": "A", "command": "rb_wave 4", "cmd_id": 7, "reason": "wave_done"}],
 "state": { … }}
```

Konzept, Zustandsmaschine, Test-Split und offene Punkte (Player-Test,
Lua-Reduktion, #265): [`docs/REFEREE.md`](REFEREE.md).

### POST /rematch

`{}` — Reset in die Lobby (nur nicht in `running`, sonst 409). Antwort:
`{"phase": "lobby", "rematches": 1}`.

### POST /sp — SP-Mode starten (Issue #44, Server-only)

```json
{"player": "momo"}
```

Startet ein Solo-/SP-Match: P1 (`player`) wird für Welt A registriert, die
Gegner-Seite ist die serverseitig erzeugte **MIRROR**-Seite (Welt B) — man
duelliert sich gegen sich selbst. Kein zweiter Client nötig. Antwort:

```json
{"started": true, "phase": "running", "round": 1, "mode": "sp",
 "teams": {"A": {"player": "momo", …}, "B": {"player": "MIRROR", …}}}
```

SP-Mode-Semantik (Mirror-Konzept):
- **Sends gespiegelt:** `POST /send` von A routet normal zu B **und** legt einen
  identischen Spiegel-Batch (von B) zurück in die Queue von A — die eigenen
  Sends kommen als Gegner-Seite zurück.
- **Wellenstart:** `POST /report wave_start` von A lockt **beide** Seiten
  (A + MIRROR B) und spiegelt den Built-Value auf B. Ein expliziter
  `wave_start` von B wird mit 409 abgewiesen.
- **HQ-HP gespiegelt:** `hq_hp` von A setzt auch die MIRROR-HP (es gibt nur
  EIN reales HQ).
- **Match-Ende:** Bei HQ ≤ 0 → Phase `finished` + Feed-Event `match_end` mit
  dem Hinweis „nächster Spieler kann joinen“.

### POST /wave — Operator-Wellen-Spawn (Issue #266)

```json
{"world": "A", "n": 3}
```

Leitet `exec rb_wave <n>` an den Bridge-/Relay-HTTP-Endpoint der Welt weiter
(`RBBRIDGE_A_URL`/`RBBRIDGE_B_URL`, `POST <url> {"command":"rb_wave <n>"}`) und
gibt dessen `exec_result` an die UI zurueck — der Weg fuer den „Spawn Wave"-
Button der `/solo`-Match-Page. Defaults: `world="A"` (Solo/SP hat nur ein reales
HQ in A), `n=3`. `n` muss 1..100 sein: Werte ausserhalb → 400 `invalid`;
falscher Typ (z. B. `n:1.5`, `n:-1`, `n:"x"`) wird schon von Serde abgewiesen
→ **422**, nicht 400. Ohne konfigurierten Bridge-Endpoint → 409 `conflict`
(kein Transport).

Antwort:

```json
{"ok": true, "exec_ok": true, "world": "A", "command": "rb_wave 3",
 "endpoint": "http://127.0.0.1:9001/exec", "status": 200, "error": null,
 "exec_result": {"ok": true, "results": [{"command": "rb_wave 3", "ok": true}]}}
```

Zwei getrennte Erfolgsflags (Review #271, Finding 2/3):

- `ok` = **Zustell-Erfolg** — die Bridge/Relay antwortete HTTP 2xx ohne
  Transportfehler.
- `exec_ok` = **Ausfuehr-Erfolg** aus dem durchgereichten `exec_result`
  (`ok:true` bzw. alle `results[].ok`); `null`, wenn kein JSON-Body kam. Die
  Web-UI leitet dasselbe in `waveResult()` ab.

Eine Bridge, die mit **200/`exec_result.ok=false`** antwortet (z. B.
`status=timeout` → `reason=no_response` laut
[relay-pipe-contract.md](relay-pipe-contract.md)), liefert HTTP **200** mit
`ok:true` und `exec_ok:false` plus `error`/`exec_result`; ein Zustellfehler
einer 502/503 wird durchgereicht und ergibt `ok:false`. Nur ein fehlender
Bridge-Endpoint ist ein 409. Der Versuch wird als Feed-Event `kind=wave`
protokolliert (sichtbar im Live-Dev-Log der `/solo`-Seite). Der Endpoint
veraendert den Match-Zustand nicht.

Transport: auf dem Dedicated-Server ist der Endpoint die `pipe_bridge` (Wine,
HTTP → `\\.\pipe\rbbattle`, #265); fuer native Windows-Welten der
`relay.py`-Pfad (gleiches `exec`/`exec_result`-Protokoll, s.
[relay-pipe-contract.md](relay-pipe-contract.md)). Der Live-Beweis, dass die
Welle im Spiel sichtbar spawnt (`[RBBATTLE] event=wave level=3 status=start`),
ist ein Player-Test (Momo/Matheo) und bleibt offen.

### GET /events — Feed-Cursor für Poll-Bridges (Telegram-Feed u. a.)

```
GET /events?since=<seq>
```

Liefert `{"events": [...], "last_seq": <n>}`. `since` filtert auf Feed-Einträge
mit `seq > since`; `last_seq` ist die höchste vergebene Sequenz (Cursor-Stand).
Jeder Eintrag trägt ein monotones `seq`-Feld (Cursor ohne Event-Verlust).

### GET /state — Match-Zustand (Poll-Kanal für Bridges + Web-UI)

```json
{
  "match_id": "rift-1",
  "mode": "duel|sp",
  "phase": "lobby|ready|running|finished",
  "round": 2, "rounds_done": 1, "rematches": 0,
  "winner": null | "A" | "B",
  "started_at": 1788971651325, "hq_hp_start": 100.0,
  "teams": {
    "A": {
      "player": "momo", "ready": true, "hq_hp": 100.0,
      "score": 1240, "resources": {"iron": 320, "carbon": 80}, "wave": 4,
      "pending_sends": [ {"from": "B", "units": […], "value": 900, "round": 2, "ts": …} ],
      "go_broadcast": {"at": …, "ok": true, "error": null, "endpoint": "http://…"}
    },
    "B": { … }
  },
  "reveal": {
    "round": 1,
    "built": {"A": 8200, "B": 6400},
    "incoming": {"A": [ …Sends von B… ], "B": [ …Sends von A… ]}
  } | null,
  "feed": [ {"seq": 3, "t": …, "kind": "go|send|wave|reveal|hq|finish|match_end|…", "msg": "…"} ]
}
```

`reveal` = Daten des letzten Wellenstarts (Built-Werte beider Teams +
eingehende Send-Komposition je Welt — was bei diesem Wellenstart gespawnt
ist). `teams.<W>.pending_sends` = Sends, die in die **nächste** Welle dieser
Welt laufen. `feed` = letzte Ereignisse (neueste zuerst, max. 30) für das
Terminal-Feed der UI.

### GET /health

`{"ok": true, "phase": "lobby"}`

## Bridge-Anbindung (v1, dokumentiertes Protokoll)

Jede Dedi-Welt betreibt eine Bridge (rbbridge + Python/tools). Sie **pollt
`GET /state`** (Default 2 s) und führt Game-Commands über den lokalen
exec-Kanal aus (`exec_cmd_client`/rbbridge-exec-Dispatch):

| Beobachtung in `/state` | Bridge-Kommando | Wirkung |
|---|---|---|
| `phase` wird `running` | `debug_dom_resume` (bzw. `TOURNAMENT_GO_COMMANDS`) | Unpause/Start des Runden-Loops (Fallback, falls der GO-Push nicht ankam; Idempotenz vorausgesetzt) |
| `round` steigt | `round_start <n>` | Neue Build-Phase, HUD-Updates |
| `reveal.round` neu | `reveal` | HUD-Aufdeckung: Built-Values + eingehende Komposition |
| `phase` wird `finished` | `match_over` | Sieg-/Verlierer-Screen |
| — | `POST /report wave_start` | Welt meldet Lock + Built-Value (vom Mod/RE-Layer ausgelöst) |
| — | `POST /report hq_hp` | Welt meldet HQ-HP (send_state-Egress, Issue #13) |
| — | `POST /report hq_dead` | Welt meldet HQ-Tod (Mod-Log, #267) → Referee-`rb_reset`-Push an `RBBRIDGE_*_URL` (#281: In-game-Round-Reset) |

Der GO-Push des Servers (`RBBRIDGE_*_URL`) und das Poll-Fallback sind
**redundant aber idempotent**: Kommandos dürfen doppelt ankommen
(GO/Unpause doppelt ist unkritisch; `round_start`/`reveal` werden anhand
der lokalen Runde dedupliziert). Referenz-Implementierung:
`tournament/bridge/poller_example.py`.

### Send-Pfad (Welt → Referee → Gegner-Welt)

Der Lua-Mod/RE-Layer einer Welt liefert Sends als
`POST /send {"world": "A", "units": […], "value": …}` an den Referee
(direkt oder über die Bridge). Der Referee routet in die Queue von B;
beim nächsten `wave_start` von B erscheint der Send in `reveal.incoming.B`.
Kein Echtzeit-Zwang: Polling-Pull-Modell (wie im Trainer-Protokoll).

## Bekannte v1-Grenzen

- Ein Match global, In-Memory (kein Persistenz/Auth) — Multi-Match folgt.
- Sends, die nach dem Wellenstart einer Welt eintreffen, laufen in deren
  nächste Welle (Rundenzuteilung beim Referee).
- Sehr späte `wave_start`-Retries nach abgeschlossenem Reveal werden als
  Wellenstart der Folgerunde gewertet (kein doppelter Drain, s. State-Machine).
- Broadcast nur `http://` (kein TLS/Redirect) — Endpoints liegen im selben
  Netz wie der Referee.
