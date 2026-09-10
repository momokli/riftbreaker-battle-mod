# Protokoll v0 — Rift-Breaker-Battle-Events (JSON, line-delimited)

Status: **v0-Entwurf (Harness)**. Transport heute = Named Pipe
`\\.\pipe\rbbattle` zwischen In-Game-DLL (`rbbridge.dll`) und Pipe-Client;
derselbe Event-Satz ist später auch die Semantik zwischen Spiel-Seite und
Tournament-/Relay-Server über Netz (der Pipe-Client wird dann zum Forwarder).

Rundenbasiertes Spiel ⇒ **keine Echtzeit-Garantien**. Sender dürfen
wiederholen (Retry), Empfänger dürfen Zustand jederzeit aktiv ziehen (Pull).
Ein verlorenes Event ist kein Desync: Die Runde/Match-Instanz ist die
Wahrheitsquelle, Events sind nur Benachrichtigungen.

## Transport (v0)

- Eine Nachricht = **eine Zeile**, UTF-8, mit `\n` abgeschlossen (`\r` wird
  toleriert und ignoriert).
- Zeilen > 8 KiB werden vom Empfänger verworfen (Harness-Limit).
- Pipe-Name: `\\.\pipe\rbbattle`, ein Client gleichzeitig (v0).
- Kein Client verbunden / Client weg: In-Game-DLL bleibt ruhig und wartet
  (Reconnect). **Fehlt die Pipe, crasht das Spiel nie.**
- Jede Zeile ist ein flaches JSON-Objekt (minimaler Eigen-Parser in der DLL).

### Nachrichten der DLL an den Client (Beispiele, im Harness implementiert)

```json
{"event":"pong","t":12345678}
{"event":"exec_result","command":"rb_wave 3","ok":false,"reason":"not_implemented (RE: ConsoleService/Lua-State finden)"}
{"event":"score_update","t":12345678,"score":0,"resources":{"iron":0,"carbon":0},"wave":0}
{"event":"error","error":"unknown_cmd"}
```

### Nachrichten des Clients an die DLL (im Harness implementiert)

```json
{"cmd":"ping"}
{"cmd":"exec","command":"rb_wave 3"}
```

- `exec` ist der **v0-Einheitskanal**: Beliebiges Spiel-Kommando als String.
  In der RE-Phase wird `dispatch_exec` an den echten Spiel-Console-Dienst
  angeschlossen (dann gilt `"ok":true`). Bis dahin antwortet die DLL mit
  `exec_result ... "ok":false`.
- Strukturierte Server→Spiel-Events (unten) werden später entweder über
  `exec`-Wrapper (`command="rbbattle_event <json>"`, vom Lua-Mod registriert)
  oder direkt über eine RE-gefundene Aufrufstelle zugestellt — Entscheidung
  folgt in der RE-Phase.

## Semantische Events (Zielvertrag Spiel ⇄ Server)

Feldkonventionen v0: `event` = Eventname; `t` = Spiel-Uptime in ms
(`GetTickCount64`); optionale Zusatzfelder pro Event. Unbekannte Felder
werden ignoriert (vorwärtskompatibel). Alle Events sind benachrichtigend
(siehe Retry/Pull oben).

### game → server (Spiel meldet an Server)

| Event | Bedeutung | Kernfelder | Beispiel |
|---|---|---|---|
| `score_update` | Punktestand/Ressourcen geändert (oder periodisch) | `score`, `resources{}`, `wave` | siehe unten |
| `wave_sent` | Spieler hat eine Welle zum Gegner geschickt (Punkte ausgegeben) | `level`, `cost`, `score_left` | siehe unten |
| `wave_received` | Gegner-Welle ist in der eigenen Partie angekommen & gespawnt | `level`, `from` | siehe unten |
| `round_start` | Eigene Runde beginnt (Spiel-Seite bestätigt / startet Phase) | `round`, `phase` | siehe unten |
| `round_end` | Eigene Runde ist vorbei (ausgewertet) | `round`, `score`, `survived` | siehe unten |
| `match_end` | Partie entschieden | `winner`, `reason`, `final_score` | siehe unten |

```json
{"event":"score_update","t":882341,"score":1240,"resources":{"iron":320,"carbon":80},"wave":4}
{"event":"wave_sent","t":912004,"level":3,"cost":400,"score_left":840}
{"event":"wave_received","t":935118,"level":3,"from":"player_b"}
{"event":"round_start","t":900000,"round":2,"phase":"planning"}
{"event":"round_end","t":990000,"round":2,"score":1560,"survived":true}
{"event":"match_end","t":1200000,"winner":"player_a","reason":"base_destroyed","final_score":3120}
```

### server → game (Server steuert Spiel)

| Event | Bedeutung | Kernfelder | Beispiel |
|---|---|---|---|
| `round_start` | Runde N beginnt jetzt (Planungsphase) | `round`, `duration_s`, `phase` | siehe unten |
| `incoming_wave` | Gegner hat Welle geschickt — im Spiel spawnen | `level`, `from`, `delay_s` | siehe unten |
| `round_end` | Runde N ist abgeschlossen, Server wertet aus | `round`, `summary{}` | siehe unten |
| `match_end` | Partie beendet (Sieg/Niederlage) | `winner`, `reason` | siehe unten |

```json
{"event":"round_start","t":900000,"round":2,"duration_s":90,"phase":"planning"}
{"event":"incoming_wave","t":940000,"level":3,"from":"player_b","delay_s":5}
{"event":"round_end","t":990000,"round":2,"summary":{"score_player_a":1560,"score_player_b":1320}}
{"event":"match_end","t":1200000,"winner":"player_a","reason":"base_destroyed"}
```

## Wer erzeugt was im Spiel (Verdrahtung, teils RE)

| Event | Erzeuger im Spiel | Stand im Harness |
|---|---|---|
| `pong`, `exec_result`, `score_update`, `error` | `rbbridge.c` (Pipe-Server) | ✅ implementiert |
| `score_update`, `wave_received`, `round_*`, `match_end` | **TODO(RE):** Werte/Adressen per `scan/` finden bzw. Events aus Lua-Signalen (`[RBBATTLE] event=...` Log-Prefix im Mod, Experiment C) ableiten | offen (Struktur in `send_state()` verdrahtet, Werte Default bis RE) |
| `wave_sent` | Lua-Mod beim Kauf der Welle (meldet über `exec`-Kanal / künftigen Event-Pfad) | offen (Mod folgt aus Spike) |
| `round_start`, `incoming_wave`, `round_end`, `match_end` (Server→Spiel) | Empfang in DLL → Zustellung an Spiel/Lua | **TODO(RE):** dispatch_exec-Anschluss; Lua-seitig registriert der Mod `rb_wave <level>` bereits (Spike) |

## Client-Verhalten (Empfehlung für späteren Pipe-Client/Server-Bridge)

- Verbindungsaufbau mit Retry (z. B. alle 1 s, unbegrenzt) — die DLL wartet
  ruhig auf Client-Verbindungen.
- Pro Zeile eine Nachricht; Antworten (`pong`, `exec_result`, ...) über eine
  korrelierbare ID zuordnen, sobald mehr als fire-and-forget gebraucht wird
  (v0: Reihenfolge + `command`-Echo reichen).
- Regelmäßig `{"cmd":"ping"}` als Liveness-Check; der `score_update`-Snapshot
  kommt ohnehin alle 5 s von der DLL (send_state-Egress, Issue #13).
- Keine Annahme über garantierten Empfang — bei Lücke einfach nächsten
  Zustand per Pull (z. B. `exec`-Kommando `rbbattle_state` im Lua-Mod) holen.
