# Pipe-Vertrag Relay → rbbridge (exec-Kanal)

Notiz zum Transportvertrag zwischen dem Relay (`bausteine/07-relay/relay.py`,
`dispatch_exec`/`PipeClient`) und der In-Game-Bridge
(`trainer/rbbridge/rbbridge.c`, Pipe-Server). Das rbbridge-Gegenstück ist in
[trainer/protocol.md](../trainer/protocol.md) dokumentiert; dieser Abschnitt
fixiert die Parameter, die der Relay einhält.

## Transport

| Aspekt | Wert |
|---|---|
| Pipe-Name | `\\.\pipe\rbbattle` (`rbbridge.c` `PIPE_NAME_A`; Relay via `RBB_PIPE_PATH` konfigurierbar) |
| Richtung (Relay) | Nur Schreiben (fire-and-forget); die Antwort `exec_result` liest der v0-Relay nicht |
| Encoding | UTF-8 |
| Framing | line-delimited JSON — eine Nachricht = eine Zeile, mit `\n` abgeschlossen |
| Nachricht | `{"cmd":"exec","command":"<command>","cmd_id":<id>}` (kompaktes JSON, keine Leerzeichen) |
| Max. Zeilenlänge | 8 KiB — längere Zeilen verwirft der Empfänger (`rbbridge.c` `PIPE_LINE_MAX`) |
| Timeout | Client-seitig `RBB_PIPE_TIMEOUT_S` (Default 5,0 s) für Connect+Write; `rbbridge.c` kennt selbst keinen Client-Timeout (`POLL_MS` 100 ist nur der Serviceloop-Takt) |

## Verhalten des Relay

- `exec_command` aus der Outbox → `dispatch_exec` schreibt die Zeile auf die
  Pipe und markiert erst **nach erfolgreichem Write** als ack
  (`dispatch sent cmd_id=... len=...`).
- Pipe nicht erreichbar (Spiel läuft nicht / rbbridge nicht injiziert / Pipe
  belegt): `dispatch failed reason=pipe_unavailable` — das Kommando wird
  **nicht** verworfen, sondern mit exponentiellem Backoff (1–30 s) erneut
  versucht.
- `cmd_id` wird vom Tournament-Server vergeben (Ganzzahl) und dient dem Relay
  als Dedup-Schlüssel; die rbbridge ignoriert `cmd_id` (sie liest nur `cmd`/
  `command`).

## Test ohne Windows

Linux: FIFO als Named-Pipe-Ersatz (`RBB_PIPE_PATH` auf einen FIFO-Pfad).
Siehe `bausteine/07-relay/test_dispatch.py` (Erfolg/Pipe-fehlt/Ack) und
`tests/e2e-vollkette/vollkette.test.js`.
