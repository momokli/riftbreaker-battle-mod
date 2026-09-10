# Baustein 07 — Relay (Log-Tail → Tournament-Server → Dispatch-Bridge)

**Was es testet:** Die Prototyp-Strecke Spiel → Trainer → Relay → Server → Web-UI.
Das Spiel schreibt `[RBBATTLE] key=value`-Zeilen in `exor_logs.txt` (Baustein 03);
der Relay **tailt** diese Datei, parst die Zeilen und **liefert sie per
`POST /event`** an den Tournament-Server (Baustein 06) ein. Im Poll-Loop holt er
seine Outbox-Events (`GET /poll/:player_id`) ab — `exec_command` ist der
Control-Kanal von der Web-UI (Baustein 08) und wird per `dispatch_exec` als
`{"cmd":"exec","command":"...","cmd_id":"..."}` auf die Named Pipe
`\\.\pipe\rbbattle` geschrieben (Format wie von `trainer/rbbridge/rbbridge.c`
erwartet, s. `trainer/protocol.md`). Ist die Pipe nicht erreichbar, wird das
Kommando **nicht** ack-markiert, sondern mit Backoff (1–30 s) erneut versucht
(Issue #60).

Nur Standardbibliothek (Python 3.7+), kein pip-Paket.

## Konfiguration (Umgebungsvariablen)

| Variable | Bedeutung | Default |
|---|---|---|
| `RBB_LOG_PATH` | Pfad zur `exor_logs.txt` (oder `--log <pfad>`) | `%USERPROFILE%\Documents\The Riftbreaker\exor_logs.txt` |
| `RBB_PLAYER_ID` | Spieler-/Instanz-ID (Register + Poll) | **Pflicht**, sonst Exit 2 |
| `RBB_MATCH_ID` | Match-ID für `POST /event` | leer → Events werden nicht gepostet (Log-Hinweis) |
| `RBB_SERVER` | Basis-URL des Tournament-Servers | `http://127.0.0.1:8080` |
| `RBB_POLL_S` | Poll-Intervall | `1.0` |
| `RBB_PIPE_PATH` | Pfad/Name der Named Pipe für `exec`-Dispatch | `\\.\pipe\rbbattle` (wie `rbbridge.c` `PIPE_NAME_A`) |

## Start

```bash
RBB_PLAYER_ID=player_a RBB_MATCH_ID=m1-xxxx RBB_LOG_PATH="D:\...\exor_logs.txt" \
RBB_SERVER=http://127.0.0.1:8080 python3 relay.py
```

Beim Start registriert sich der Relay am Server (Retry, bis der Server da ist);
dann laufen vier Threads: **tail** (Log → Queue), **post** (Queue → `/event`,
Retry mit Backoff 1–30 s bei Netzfehlern, nichts geht verloren), **poll**
(`/poll/:player_id`, `exec_command` → `dispatch_exec` auf die Pipe, andere
Typen nur loggen) und **dispatch** (Retry-Queue: Kommandos, deren Pipe-Write
fehlschlug, mit Backoff 1–30 s erneut versuchen, bis sie ankommen). Strg+C
beendet sauber; Log-Rotation wird erkannt.

## Wie testen ohne Spiel

1. Server starten: `node bausteine/06-tournament-server/server.js` (Port 8080).
2. Spieler + Match anlegen:
   ```bash
   curl -s -X POST localhost:8080/register -d '{"player_id":"player_a"}'
   curl -s -X POST localhost:8080/register -d '{"player_id":"player_b"}'
   curl -s -X POST localhost:8080/match/create \
        -d '{"players":["player_a","player_b"],"rounds":2}'   # -> match_id merken
   ```
3. Fake-Log erzeugen (simuliert die Lua-Mod-Zeilen):
   ```bash
   cd bausteine/07-relay
   bash fake-log.sh /tmp/fake.log          # schreibt [RBBATTLE]-Zeilen im 2s-Takt
   ```
4. Relay dagegen starten:
   ```bash
   RBB_PLAYER_ID=player_a RBB_MATCH_ID=<match_id> RBB_LOG_PATH=/tmp/fake.log \
   python3 relay.py
   # Log zeigt: tail: ... -> post: ok type=score_update/wave_sent ...
   ```
5. Kommando von der Web-UI (oder curl) schicken — der Relay loggt den Dispatch:
   ```bash
   curl -s -X POST localhost:8080/event \
        -d '{"match_id":"<match_id>","player_id":"player_a",\
             "event":{"type":"exec_command","command":"rb_wave 1"}}'
   # relay-stdout ohne erreichbare Pipe (Default \\.\pipe\rbbattle unter Linux):
   #   dispatch failed reason=pipe_unavailable cmd_id=1 command=rb_wave 1 err=...
   # mit RBB_PIPE_PATH auf eine erreichbare (Fake-)Pipe:
   #   dispatch sent cmd_id=1 len=52
   ```
6. Kompletter Durchstich inkl. Assertions (inkl. Named-Pipe-Ersatz/FIFO für
   `dispatch_exec`): `bash test_e2e_prototype.sh` (Baustein 07, s. u.). Isolierte
   Unit-Tests für `dispatch_exec` (Erfolg/Pipe-fehlt/Ack-Pfad):
   `python3 -m unittest test_dispatch_pipe -v`.

## Status

- [x] tail: Log-Polling, Rotation, UTF-8 `errors=replace`, unvollständige Zeilen werden zurückgehalten
- [x] post: `POST /event`, Backoff-Retry bei Netzfehler (Queue, kein Verlust), 4xx = Konfigurationsfehler (log + weiter)
- [x] poll: Outbox abholen, `exec_command` → `dispatch_exec`, andere Typen loggen
- [x] register beim Start + Re-Register bei 404 (Server-Neustart)
- [x] `dispatch_exec` ins Spiel (Pipe/rbbridge, Issue #60): schreibt
      `{"cmd":"exec","command":"...","cmd_id":"..."}` auf `RBB_PIPE_PATH`
      (Default `\\.\pipe\rbbattle`); Pipe nicht erreichbar → kein Ack, Retry
      mit Backoff (1–30 s) statt Verlust. Getestet gegen einen Named-Pipe-
      Ersatz (Linux-FIFO) in `test_dispatch_pipe.py` und `test_e2e_prototype.sh`.
      Offen bleibt die Verifikation gegen die echte Windows-Pipe + injizierte
      `rbbridge.dll` (braucht einen laufenden Spielprozess, RE-Phase).
