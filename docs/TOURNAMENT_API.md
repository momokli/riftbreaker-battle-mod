# RIFT BATTLE — Tournament-API v1 (Referee-Protokoll)

Der Tournament-Server ist der **zentrale Referee** zwischen den zwei
Riftbreaker-Dedi-Welten (A und B). Er verwaltet Lobby, Ready-Check, den
synchronen GO-Start, das Wave-Routing (Sends A→B/B→A), den Reveal bei
Wellenstart und den Match-Zustand bis zum HQ-Tod. Implementierung:
Rust/axum in `tournament/` (Issue #29), Web-UI in `tournament/web/`
(Issue #30). In-Memory, ein Match (Rematch über denselben Match-State),
Deployment macht der Operator. Seit **#298** sind die **mutierenden** Routen
per Bearer-Token geschützt (siehe „Auth-Modell“), die **lesenden** bleiben offen.

- **Server**: `cargo run` (bzw. `cargo build --release`), siehe
  [tournament/README.md](../tournament/README.md)
- **Bridge-Seite**: `tournament/bridge/poller_example.py` (Referenz-Poller
  für rbbridge/tools auf jeder Dedi-Welt)

## Konfiguration (nur Env, keine Hardcodes)

| Env                              | Default            | Bedeutung                                                                                         |
| -------------------------------- | ------------------ | ------------------------------------------------------------------------------------------------- |
| `TOURNAMENT_HOST`                | `127.0.0.1`        | Bind-Adresse (Loopback; #298)                                                                     |
| `TOURNAMENT_PORT`                | `8080`             | HTTP-Port (API + Web-UI)                                                                          |
| `TOURNAMENT_AUTO_GO`             | `true`             | GO automatisch, sobald beide Welten ready                                                         |
| `RBBRIDGE_A_URL`                 | —                  | HTTP-Endpoint der Welt-A-Bridge (GO-Push)                                                         |
| `RBBRIDGE_B_URL`                 | —                  | HTTP-Endpoint der Welt-B-Bridge (GO-Push)                                                         |
| `TOURNAMENT_GO_TIMEOUT_MS`       | `3000`             | Timeout je Broadcast-Endpoint                                                                     |
| `TOURNAMENT_INCOMING_DELAY_S`    | `5`                | `delay_s` des Ingress-Pushes (`incoming_wave`) an die Ziel-Bridge beim Wellenstart (US4, #996)     |
| `TOURNAMENT_HQ_HP`               | `100`              | Start-HP jedes HQ                                                                                 |
| `TOURNAMENT_REFEREE_MAX_WAVE`    | `0`                | Wellen-Deckel des Referees (`0` = unbegrenzt, Issue #268)                                         |
| `TOURNAMENT_REFEREE_RESTART_CMD` | `rb_reset`         | In-game Command des Referees bei HQ-Tod (Issues #268/#281; Mod-Kommando `rb_reset`)               |
| `TOURNAMENT_WEB_DIR`             | `<crate>/web`      | Verzeichnis der statischen Web-UI                                                                 |
| `TOURNAMENT_DB_PATH`             | `./data/rbbattle.db` | SQLite-Datei für persistierte Match-Records (#999; WAL-Modus, Verzeichnis wird angelegt)        |
| `TOURNAMENT_TOKEN`               | — (leer)           | Bearer für die mutierenden Routen (#298); leer = **fail-closed** (mutierend 401)                  |
| `RUST_LOG`                       | `info`             | Log-Level                                                                                         |

`RBBRIDGE_*_URL` zeigen auf den HTTP-Adapter der jeweiligen Dedi-Bridge
(z. B. `http://10.0.0.5:9001/exec`). Fehlen sie, entfällt der Push und die
Bridges erkennen den Start ausschließlich über Polling von `GET /state`.

## Auth-Modell (Issue #298)

Die API trennt **lesende** von **mutierenden** Routen:

| Klasse         | Routen                                                                                          | Schutz |
| -------------- | ----------------------------------------------------------------------------------------------- | ------ |
| **mutierend**  | `POST /lobby /ready /go /pause /resume /send /report /rematch /sp /wave /referee/event`           | `Authorization: Bearer <TOURNAMENT_TOKEN>` — sonst **401** (+ `WWW-Authenticate: Bearer`) |
| **lesend**     | `GET /state /matches/{id} /events /referee/poll /health` + statische Web-UI (`ServeDir`)          | frei (Polling/Landing unverändert) |

- **Fail-closed:** ist `TOURNAMENT_TOKEN` leer/unset, weist die Middleware
  **jeden** mutierenden Request mit 401 ab — die API ist nie „offen“.
- **Token-Quelle:** ausschließlich der Vault (`vault_tournament_token`),
  read-once aus `TOURNAMENT_TOKEN`. Landet nie im Repo/Log; die Unit liest ihn
  aus einer 0600-`EnvironmentFile`.
- **Single-Source:** derselbe Vault-Key speist Rust, den rift-caddy
  (`header_up Authorization "Bearer …"` auf den Schreibpfaden) und den
  Queue-Dienst (`QUEUE_REFEREE_TOKEN`). Kein zweiter Secret-Kanal.
- **Bind:** `TOURNAMENT_HOST` ist Default `127.0.0.1`; der rift-caddy
  (`network_mode: host`) proxyt dorthin. Ein Expose nach außen ist bewusst
  nicht Teil dieses Schutzes (**Firewall out-of-scope**, eigener PR).

Ein Aufruf ohne/mit falschem Bearer:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8081/wave   # 401
curl -s -o /dev/null -w '%{http_code}\n' -H 'Authorization: Bearer <token>' \
  -X POST http://127.0.0.1:8081/wave                                          # 200/409 (je nach Bridge)
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8081/health          # 200 (frei)
```

## Match-Lebenszyklus

```text
LOBBY ── beide Spieler registriert ──► LOBBY
LOBBY ── beide Welten ready (AUTO_GO=off) ──► READY (GO steht aus)
LOBBY/READY ── POST /go (oder AUTO_GO beim 2. Ready) ──► RUNNING (Runde 1)
RUNNING ── Runden-Loop ── HQ einer Welt ≤ 0 (event=hq_hp) ──► FINISHED (winner)
FINISHED ── POST /rematch ──► LOBBY (Spieler bleiben, Rematch-Zähler +1)
         └ (Lobby-Rematch #1030: die QUEUE ruft /rematch intern, dann
            POST /lobby mit NEUER match_id + frische Kalt-Welten)
```

Beim Übergang nach `FINISHED` erzeugt der Referee **genau einen** persistenten
Match-Record (SQLite, `TOURNAMENT_DB_PATH`) und schreibt ihn idempotent —
Schreibfehler werden nur geloggt, die HTTP-Antwort bleibt unverändert (#999,
[Match-Records](#get-matchesid--persistierter-match-record-999)).

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
   (Spieler bleiben registriert, ready/HP/Queues werden zurückgesetzt; das
   Queue-`match_id`-Echo wird geleert).

> **Lobby-Rematch (#1030):** Der spielerseitige Rematch läuft **nicht** direkt
> gegen `POST /rematch`, sondern über die Queue (`POST /queue/rematch` im
> Relay/Queue-Dienst). Die Queue stoppt zuerst die alten Kalt-Welten, ruft
> **intern** `POST /rematch` (Reset) und provisioniert danach zwei **frische**
> Welten derselben Paarung; die neue `match_id` kommt per `POST /lobby`.
> `POST /referee/rematch` am Relay ist der **reine Reset** (Operator-Pfad,
> keine neuen Welten). Details: [docs/LOBBY.md](LOBBY.md) §2, [docs/VS_MATCH.md](VS_MATCH.md) §6.7.

## Endpoints

Alle Antworten sind JSON. Fehler:
`{"error": "<meldung>", "type": "invalid|not_found|conflict"}` mit
400/404/409. Unbekannte Felder in Bodies werden **ignoriert**
(vorwärtskompatibel). Unbekannte Pfade → statische Web-UI bzw. 404.

### POST /lobby — Spieler registrieren

```json
{ "player": "momo", "world": "A", "match_id": 7 }
```

Idempotent; Namenswechsel setzt den Ready-Status der Welt zurück. Nur in
Phase `lobby` (sonst 409). `match_id` ist **optional und additiv** (Issue #1028):
fehlt sie, wird nichts gesetzt; ein falscher Typ (z. B. String) wird mit **422**
abgewiesen. Der Queue-Dienst schickt hier seine Match-`match_id` mit, der
Referee gibt sie in `GET /state` als `teams.{A,B}.match_id` zurück (Grundlage des
Auto-Finish; die Queue zieht das Ergebnis selbst). Antwort:

```json
{
  "world": "A",
  "player": "momo",
  "created": true,
  "match_complete": false,
  "phase": "lobby"
}
```

### POST /ready — Welt meldet sich bereit

```json
{ "world": "A" }
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

Broadcast an jede Bridge: je Welt werden **beide** verifizierten Routen als
leerer POST (Body `{}`) gefächert — Basis ist `RBBRIDGE_*_URL` ohne
abschließendes `/exec`:

1. `POST <bridge-base>/resume_game` — native Server-Pause der kalt gebooteten
   Welt aufheben (Issue #880; live gemessen ≈0,12 s,
   `deploy/parked/MEASUREMENT.md`).
2. `POST <bridge-base>/start` — Wellen-Zyklus armieren (`start_epoch`); der
   attack-cycle vollzieht `PAUSED→WARMUP→RUNNING` (`attack_cycle.py`).

Die Reihenfolge ist die Ausführungsreihenfolge (erst Sim entfrieren, dann Zyklus
armieren); beide Bridge-Routen nehmen **keinen** Body. Der frühere
`commands`-Payload (`TOURNAMENT_GO_COMMANDS`, Default `debug_dom_resume`) ist mit
#1027 entfernt — es gibt **kein** Env mehr.

Der Server behandelt den Push als **nicht-kritisch**: der Zustell-Status landet
je Welt aggregiert (`ok` = alle Routen ok, `routes[]`) in
`teams.<W>.go_broadcast` von `GET /state`, der zuverlässige Kanal ist das
Polling der Bridges.
Antwort:

```json
{
  "started": true,
  "phase": "running",
  "round": 1,
  "broadcast": {
    "A": {
      "ok": true,
      "endpoint": "http://127.0.0.1:9001",
      "routes": [
        { "route": "resume_game", "ok": true, "status": 200, "error": null, "endpoint": "http://127.0.0.1:9001/resume_game" },
        { "route": "start", "ok": true, "status": 200, "error": null, "endpoint": "http://127.0.0.1:9001/start" }
      ]
    },
    "B": { "ok": null, "note": "kein Endpoint konfiguriert — Bridges pollten /state" }
  }
}
```

### POST /pause — Pause-Fan-out an beide Welten (#997)

```json
{}              // beide Welten pausieren (DOM-Freeze)
{"retry": true} // laufendes Match, bereits pausiert: Broadcast erneut senden
```

Guard: nur in Phase `running` (sonst **409** `conflict`). Der Referee fächert
`POST <bridge-base>/pause_dom` an **beide** Bridges (`RBBRIDGE_A_URL` /
`RBBRIDGE_B_URL`, analog `POST /go`); der Bridge-Endpoint-Pfad wird wie beim
Ingress abgeleitet (ein abschließendes `/exec` wird entfernt, `/pause_dom`
angehängt). Die Bridge-Route nimmt **keinen** Body (Body ist `{}`).

`paused` ist der neue match-weite Zustand; die **Phase bleibt `running`**
(Pause ist kein Phasen-Übergang). `already:true`, wenn das Match bereits
pausiert war — dann **kein** erneuter Fan-out, außer `{"retry":true}`.
Antwort (HTTP 200; Partial-Fehler einer Welt stehen je Welt als `ok:false`,
**kein** 5xx):

```json
{
  "paused": true,
  "phase": "running",
  "already": false,
  "broadcast": {
    "A": { "ok": true, "status": 200, "error": null, "endpoint": "http://…:9002/pause_dom" },
    "B": { "ok": null, "note": "kein Endpoint konfiguriert (RBBRIDGE_B_URL) — kein Pause-Push" }
  }
}
```

Der Zustell-Status je Welt landet in `teams.<W>.pause_broadcast` von
`GET /state` (sichtbar für die UI).

### POST /resume — Resume-Fan-out an beide Welten (#997)

```json
{}              // beide Welten fortsetzen (DOM-Freeze aufheben)
{"retry": true} // laufendes Match, bereits frei: Broadcast erneut senden
```

Guard: nur in Phase `running` (sonst **409** `conflict`). Fächert
`POST <bridge-base>/resume_dom` an **beide** Bridges. `already:true`, wenn das
Match gar nicht pausiert war (kein Doppel-Feed); mit `{"retry":true}` wird
trotzdem erneut gefächert. Antwort wie `POST /pause` mit `"paused": false`.

### POST /send — Wave-Routing

```json
{
  "world": "A",
  "units": [
    { "unit": "creeper", "count": 5 },
    { "unit": "brute", "count": 2 }
  ],
  "value": 1500
}
```

Nur in Phase `running` (sonst 409). Der Send wird in die Queue der
**Gegner-Welt** gelegt und bei deren nächstem Wellenstart in den Reveal
übernommen. Antwort: `{"queued_for": "B", "round": 1, "batch": {…}, "pending_sends": 1}`.

**Wellen-basierter Cross-World-Send (US2/US5, #996).** Der Attack-Cycle kennt
keine Unit-Komposition, nur ein Difficulty-Level; deshalb gibt es eine zweite,
additive Form mit `level` statt `units`:

```json
{ "world": "A", "level": 3, "value": 1400 }
```

`level` (≥ 1) wird beim Referee zum Wellen-Send gestempelt (`SendBatch.level`) und
in die `pending`-Queue der Gegner-Welt gelegt — identisch zum Unit-Send, nur ohne
Einheiten. Antwort zusätzlich mit `"level": 3`. Unit-Sends ohne `level` bleiben
unverändert (`level: null`). Ohne `level` **und** ohne `units` → 400 `invalid`.

### POST /report — Welt-Events (send_state-Egress, Issue #13 konzeptionell)

```json
{"world": "A", "event": "wave_start", "built_value": 8200}
{"world": "A", "event": "hq_hp", "hp": 70.0}
{"world": "A", "event": "score_update", "score": 1240, "resources": {"iron": 320, "carbon": 80}, "wave": 4}
{"world": "A", "event": "hq_dead"}
```

- `wave_start`: Wellenstart der Welt (Lock). `built_value` optional
  (Built-Value zum Reveal). Idempotent je Runde: Antwort
  `{"effect": "locked"|"duplicate", "round": …, "rounds_done": …, "phase": …, "ingress": [… ]}`.
  Beim echten Lock (`effect:locked`) pusht der Referee die gedrainten
  **level-basierten** Sends aus `reveal.incoming.<W>` als Ingress an die
  Bridge der Zielwelt (`POST <RBBRIDGE_<W>_URL>/incoming_send`,
  Body `{level, from, delay_s}`; `delay_s` aus `TOURNAMENT_INCOMING_DELAY_S`).
  Der `ingress`-Block nennt je Batch
  `{level, from, ok, http_status, error, endpoint}`; ohne konfigurierten
  Endpoint `{level, from, ok:null, note}` (kein Panic). Ein Retry
  (`effect:duplicate`) pusht **nicht** erneut. Unit-Sends ohne `level` werden
  nicht als Ingress gepusht (nur im Reveal geführt).
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

| `type`                    | Wirkung                                                                                         | Command                                                                        |
| ------------------------- | ----------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| `ready`                   | Executor oben (Map geladen; nach `rb_reset` **Mapping OFFEN**, s. `REFEREE.md` „Offene Punkte") | `rb_wave 1`                                                                    |
| `wave_done` (mit `level`) | Welle abgeschlossen                                                                             | `rb_wave <level+1>` (bis `TOURNAMENT_REFEREE_MAX_WAVE`)                        |
| `hq_destroyed`            | HQ zerstört                                                                                     | `rb_reset` (Mod: Runde auf 0, Setup-Phase), Runde +1, Wellen ruhen bis `ready` |

Duplikate/veraltete Level/mehrfaches `hq_destroyed` sind idempotent (kein
Doppel-Command). Antwort:

```json
{
  "world": "A",
  "type": "wave_done",
  "accepted": true,
  "commands": [
    { "world": "A", "command": "rb_wave 4", "cmd_id": 7, "reason": "wave_done" }
  ],
  "state": {
    "running": true,
    "restart_pending": false,
    "waves_in_flight": 4,
    "next_level": 4,
    "rounds": 0,
    "commands_sent": 4,
    "queued_commands": 1
  }
}
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
{ "player": "momo" }
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
  dem Hinweis „nächster Spieler kann joinen“. Der Queue-Dienst liest diesen
  Zustand (Auto-Finish, #1028) und traegt Ergebnis + kaltes Cleanup selbst nach.

### POST /wave — Operator-Wellen-Spawn (Issue #266)

```json
{ "world": "A", "n": 3 }
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
{
  "ok": true,
  "exec_ok": true,
  "world": "A",
  "command": "rb_wave 3",
  "endpoint": "http://127.0.0.1:9001/exec",
  "status": 200,
  "error": null,
  "exec_result": { "ok": true, "results": [{ "command": "rb_wave 3", "ok": true }] }
}
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
HTTP → `\\.\pipe\rbbattle`, #265). Der Live-Beweis, dass die
Welle im Spiel sichtbar spawnt (`[RBBATTLE] event=wave level=3 status=start`),
ist ein Player-Test (Momo/Matheo) und bleibt offen.

Client-Verhalten der `/solo`-UI (#266): der Transport bricht clientseitig nach
8 s ab (AbortController, `DEFAULT_CMD_TIMEOUT_MS` in `solo-cockpit.js`) und
stellt einen Haenger als eigenen Fehlerzustand dar (`timeout:true`, Statuszeile
`FEHLER — Zeitüberschreitung …`, `is-err`) — nicht als Erfolg. Der Spawn-Button
ist waehrend des laufenden Requests gesperrt (kein Doppel-POST). Eine 200-Antwort
mit unparsebarem Body wird als `UNKLARE ANTWORT — Welle nicht bestätigt`
(`is-err`) gezeigt, nie als „OK".

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
  "paused": false,
  "teams": {
    "A": {
      "player": "momo", "ready": true, "hq_hp": 100.0,
      "match_id": 7,
      "score": 1240, "resources": {"iron": 320, "carbon": 80}, "wave": 4,
      "pending_sends": [ {"from": "B", "units": […], "value": 900, "round": 2, "ts": …} ],
      "go_broadcast": {"at": …, "ok": true, "error": null, "endpoint": "http://…"},
      "pause_broadcast": {"at": …, "ok": true, "error": null, "endpoint": "http://…:9002/pause_dom"}
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
Terminal-Feed der UI. Seit #996 (US1) trägt jeder Feed-Eintrag ein optionales
`"world": "A"|"B"` (fehlt bei globalen Einträgen wie `go`/`match_end`);
level-basierte Sends erscheinen mit `"level"` im `batch`/`pending_sends`.
Seit #997 liefert `/state` zusätzlich `paused` (match-weit, top-level) und
`teams.<W>.pause_broadcast` (Zustell-Status des letzten Pause-/Resume-Fan-outs).

### GET /health

`{"ok": true, "phase": "lobby"}`

### GET /matches/{id} — persistierter Match-Record (#999)

```
GET /matches/{id}?rematch=<n>
```

Additiver **Read-Beleg**: liefert genau den Record, den das Match-Ende
(`event=hq_hp` mit `hp ≤ 0`) persistiert hat. `{id}` ist die `match_id`
(aktuell `rift-1`), `rematch` wählt die Match-Instanz (Default `0`; jedes
`POST /rematch` erhöht den Zähler). Unbekannt → **404**
`{"error": "kein Match-Record …", "type": "not_found"}`.

```json
{
  "match_id": "rift-1",
  "rematch": 0,
  "mode": "duel",
  "rounds_done": 3,
  "winner_player": "matheo",
  "finished_at": "2026-09-29T21:11:00Z",
  "participants": [
    {"player_id": "momo",   "display_name": "momo",   "identity_source": "name", "opponent_id": "B", "result": "loss"},
    {"player_id": "matheo", "display_name": "matheo", "identity_source": "name", "opponent_id": "A", "result": "win"}
  ]
}
```

Der Record ist bewusst **ELO-frei** (Elo/MMR gehören zu #131): er bildet nur
Teilnehmer, Modus, Rundenzahl, Sieger und Zeitstempel (RFC3339 UTC) ab — die
Grundlage für ein späteres Ranking. `identity_source` ist heute `name`
(freier Textname, `POST /lobby`); die Client-Identität (#992,
`str:`/`steamid:`/`account:`) dockt additiv an. Persistenz: SQLite-Tabelle
`match_record`, Key (`match_id`, `rematch`, `player_id`), `journal_mode=WAL`
(Datei `TOURNAMENT_DB_PATH`); ein zweiter `/report` derselben Match-Instanz
legt kein Duplikat an.

## Bridge-Anbindung (v1, dokumentiertes Protokoll)

Jede Dedi-Welt betreibt eine Bridge (rbbridge + Python/tools). Sie **pollt
`GET /state`** (Default 2 s) und führt Game-Commands über den lokalen
exec-Kanal aus (`exec_cmd_client`/rbbridge-exec-Dispatch):

| Beobachtung in `/state` | Bridge-Kommando                                    | Wirkung                                                                                                      |
| ----------------------- | -------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `phase` wird `running`  | `POST /start` (Wellen-Zyklus) · `POST /resume_game` (Server-Pause aufheben) | GO-Push des Servers an beide Welten (#1027); Fallback: Bridge erkennt `running`/`start_epoch` beim Polling von `GET /state` (Idempotenz vorausgesetzt) |
| `round` steigt          | `round_start <n>`                                  | Neue Build-Phase, HUD-Updates                                                                                |
| `reveal.round` neu      | `reveal`                                           | HUD-Aufdeckung: Built-Values + eingehende Komposition                                                        |
| `phase` wird `finished` | `match_over`                                       | Sieg-/Verlierer-Screen                                                                                       |
| —                       | `POST /report wave_start`                          | Welt meldet Lock + Built-Value (vom Mod/RE-Layer ausgelöst)                                                  |
| —                       | `POST /report hq_hp`                               | Welt meldet HQ-HP (send_state-Egress, Issue #13)                                                             |
| —                       | `POST /report hq_dead`                             | Welt meldet HQ-Tod (Mod-Log, #267) → Referee-`rb_reset`-Push an `RBBRIDGE_*_URL` (#281: In-game-Round-Reset) |

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
Kein Echtzeit-Zwang: Polling-Pull-Modell (wie im Server-Protokoll).

## Bekannte v1-Grenzen

- Ein Match global, In-Memory (kein Persistenz) — Multi-Match folgt. Auth:
siehe „Auth-Modell“ (mutierend per Bearer, lesend frei, #298).
- Sends, die nach dem Wellenstart einer Welt eintreffen, laufen in deren
  nächste Welle (Rundenzuteilung beim Referee).
- Sehr späte `wave_start`-Retries nach abgeschlossenem Reveal werden als
  Wellenstart der Folgerunde gewertet (kein doppelter Drain, s. State-Machine).
- Broadcast nur `http://` (kein TLS/Redirect) — Endpoints liegen im selben
  Netz wie der Referee.
