# Baustein 07 — Relay (Log-Tail → Tournament-Server → Dispatch-Bridge)

**Was es testet:** Die Prototyp-Strecke Spiel → Trainer → Relay → Server → Web-UI.
Das Spiel schreibt `[RBBATTLE] key=value`-Zeilen in `exor_logs.txt` (Baustein 03);
der Relay **tailt** diese Datei, parst die Zeilen und **liefert sie per
`POST /event`** an den Tournament-Server (Baustein 06) ein. Im Poll-Loop holt er
seine Outbox-Events (`GET /poll/:player_id`) ab — `exec_command` ist der
Control-Kanal von der Web-UI (Baustein 08) und wird als
`{"cmd":"exec",...}` auf die rbbridge-Named-Pipe dispatcht. Ist die Pipe
nicht erreichbar, wird das Kommando nicht verworfen, sondern mit Backoff
erneut versucht (erst nach erfolgreichem Schreiben ack-markiert). Danach
liest der Relay auf derselben Verbindung die `exec_result`-Antwort
(Issue #73) und loggt das Ergebnis — eine ausbleibende Antwort ist kein
Fehler, nur ein Hinweis.

Nur Standardbibliothek (Python 3.7+), kein pip-Paket.

## Konfiguration (Umgebungsvariablen)

| Variable | Bedeutung | Default |
|---|---|---|
| `RBB_LOG_PATH` | Pfad zur `exor_logs.txt` (oder `--log <pfad>`) | `%USERPROFILE%\Documents\The Riftbreaker\exor_logs.txt` |
| `RBB_PLAYER_ID` | Spieler-/Instanz-ID (Register + Poll) | **Pflicht**, sonst Exit 2 |
| `RBB_MATCH_ID` | Match-ID für `POST /event` | leer → Events werden nicht gepostet (Log-Hinweis) |
| `RBB_SERVER` | Basis-URL des Tournament-Servers | `http://127.0.0.1:8080` |
| `RBB_POLL_S` | Poll-Intervall | `1.0` |
| `RBB_PIPE_PATH` | rbbridge-Named-Pipe | `\\.\pipe\rbbattle` (wie `rbbridge.c` `PIPE_NAME_A`) |
| `RBB_PIPE_TIMEOUT_S` | Timeout Pipe-Connect/Write (s) | `5.0` |

## Start

```bash
RBB_PLAYER_ID=player_a RBB_MATCH_ID=m1-xxxx RBB_LOG_PATH="D:\...\exor_logs.txt" \
RBB_SERVER=http://127.0.0.1:8080 python3 relay.py
```

Beim Start registriert sich der Relay am Server (Retry, bis der Server da ist);
dann laufen vier Threads: **tail** (Log → Queue), **post** (Queue → `/event`,
Retry mit Backoff 1–30 s bei Netzfehlern, nichts geht verloren), **poll**
(`/poll/:player_id`, `exec_command` → Dispatch-Queue), **dispatch** (schreibt
`{"cmd":"exec",...}` auf die rbbridge-Pipe; bei Pipe-Fehler Retry mit Backoff).
Strg+C beendet sauber; Log-Rotation wird erkannt.

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
5. rbbridge-Pipe-Fake anlegen (Named-Pipe-Ersatz unter Linux) und Kommando schicken:
   ```bash
   mkfifo /tmp/fake_pipe   # kein "cat"-Leser hier - s. Hinweis unten
   # Relay mit RBB_PIPE_PATH=/tmp/fake_pipe neu starten
   curl -s -X POST localhost:8080/event \
        -d '{"match_id":"<match_id>","player_id":"player_a",\
             "event":{"type":"exec_command","command":"rb_wave 1"}}'
   # relay-stdout: dispatch sent cmd_id=1 len=...
   #               dispatch result cmd_id=1 status=timeout command='rb_wave 1'
   #               (kein Responder auf der Fake-Pipe -> Timeout, kein Fehler)
   ```
   **Hinweis:** Ein externer `cat`-Leser auf derselben FIFO sieht die
   exec-Zeile **nicht** — der Relay liest nach dem Schreiben auf demselben
   Handle selbst weiter (Antwortrichtung, Issue #73), und eine FIFO ist nur
   eine einzelne Queue: Wer als Erster nach dem Schreiben liest, bekommt die
   Daten — das ist bei einem Prozess, der write() direkt von read() gefolgt
   ausführt, deterministisch er selbst, nie ein externer Reader (bei einer
   echten Windows-Named-Pipe mit getrennten Puffern je Richtung tritt das
   nicht auf). Details: `docs/relay-pipe-contract.md`.
6. Kompletter Durchstich inkl. Assertions: `bash test_e2e_prototype.sh` (Baustein 07, s. u.)

Unit-Tests des Pipe-Dispatchs (Erfolg / Pipe-fehlt / Ack-Pfad / exec_result-
Antwort, ohne Spiel, FIFO bzw. `os.pipe()` als Named-Pipe-Ersatz):
`python3 -m unittest test_dispatch -v`.

## Status

- [x] tail: Log-Polling, Rotation, UTF-8 `errors=replace`, unvollständige Zeilen werden zurückgehalten
- [x] post: `POST /event`, Backoff-Retry bei Netzfehler (Queue, kein Verlust), 4xx = Konfigurationsfehler (log + weiter)
- [x] poll: Outbox abholen, `exec_command` → Dispatch-Queue, andere Typen loggen
- [x] dispatch: `{"cmd":"exec",...}` auf die rbbridge-Pipe, Retry/Backoff bei Pipe-Fehler, ack erst nach Erfolg (Issue #60)
- [x] dispatch-Antwort: `exec_result` auf derselben Verbindung lesen und
      klassifizieren (`status=ok|error|timeout`), Timeout blockiert keine
      weiteren Dispatches (Issue #73)
- [x] register beim Start + Re-Register bei 404 (Server-Neustart)
