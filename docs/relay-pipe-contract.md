# Pipe-Vertrag Relay → rbbridge (exec-Kanal)

Notiz zum Transportvertrag zwischen dem Relay (`bausteine/07-relay/relay.py`,
`dispatch_exec`/`PipeClient`) und der In-Game-Bridge
(`bausteine/04-trainer-io/rbbridge/rbbridge.c`, kanonisch; byte-identischer
Spiegel: `trainer/rbbridge/rbbridge.c`, Pipe-Server). Das rbbridge-Gegenstück ist in
[trainer/protocol.md](../trainer/protocol.md) dokumentiert; dieser Abschnitt
fixiert die Parameter, die der Relay einhält.

## Transport

| Aspekt | Wert |
|---|---|
| Pipe-Name | `\\.\pipe\rbbattle` (`rbbridge.c` `PIPE_NAME_A`; Relay via `RBB_PIPE_PATH` konfigurierbar) |
| Richtung | Duplex über **dieselbe Verbindung** (`PIPE_ACCESS_DUPLEX`): Relay schreibt `exec`, liest auf demselben Handle die `exec_result`-Antwort (Issue #73) |
| Encoding | UTF-8 |
| Framing | line-delimited JSON — eine Nachricht = eine Zeile, mit `\n` abgeschlossen |
| Nachricht (Relay → rbbridge) | `{"cmd":"exec","command":"<command>","cmd_id":<id>}` (kompaktes JSON, keine Leerzeichen) |
| Nachricht (rbbridge → Relay) | `{"event":"exec_result","command":"<command>","ok":true\|false,"reason":"..."}` (kein `cmd_id`-Echo — rbbridge kennt das Feld nicht, Matching läuft über `command`, s. u.) |
| Max. Zeilenlänge | 8 KiB — längere Zeilen verwirft der Empfänger (`rbbridge.c` `PIPE_LINE_MAX`) |
| Timeout | Client-seitig `RBB_PIPE_TIMEOUT_S` (Default 5,0 s), zweiphasig: (1) Connect+Write — Ablauf hier ist ein Fehler; (2) danach dieselbe Budgetzeit für die `exec_result`-Antwort — Ablauf hier ist **kein** Fehler, nur „keine Antwort“. `rbbridge.c` kennt selbst keinen Client-Timeout (`POLL_MS` 100 ist nur der Serviceloop-Takt) |

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
- **Push und Poll teilen denselben `cmd_id` (Issue #267).** Der Tournament-Server
  stellt ein Referee-Command entweder per Push (`POST <RBBRIDGE_<W>_URL>`, jetzt
  inkl. `cmd_id`/`world`/`reason`) **oder** über die Poll-Outbox
  (`GET /referee/poll`) zu — nicht über beide: Ein erfolgreich gepushter Command
  wird serverseitig „geackt“ und aus der Outbox entfernt, der Poll liefert ihn
  also nicht erneut. Geht die Push-Antwort verloren (Command bleibt in der
  Outbox), dedupliziert der Relay weiterhin über `cmd_id`; der Push-Payload
  trägt `cmd_id` deshalb mit. Ein `restart`-Command ist **nicht** idempotent —
  die Dedup-Zusage ist also verpflichtend, nicht optional.
- **Antwortrichtung (Issue #73):** Nach erfolgreichem Write liest der Relay
  auf demselben Pipe-Handle weiter, bis eine `exec_result`-Zeile mit
  passendem `command`-Feld kommt oder `RBB_PIPE_TIMEOUT_S` abläuft. Andere
  Nachrichten der DLL (`pong`, `score_update`, `error`, …) auf derselben
  Verbindung werden dabei übersprungen, nicht als Fehler gewertet. Ergebnis
  wird strukturiert geloggt: `dispatch result cmd_id=... status=ok`,
  `status=error reason=...` oder `status=timeout` (keine Antwort — blockiert
  keine weiteren Dispatches, s. u.).
- Ein Antwort-Timeout ist **kein** Dispatch-Fehler: Das Kommando wurde
  bereits erfolgreich geschrieben und bleibt ack-markiert. Nur ein
  Connect/Write-Fehler (Pipe nicht erreichbar) landet in der Retry-Queue.
- **Meldung an den Server (Issue #89, AC aus #73):** Das Ergebnis wird
  best-effort als eigenen `POST /event` an den Tournament-Server gemeldet
  (`event.type=exec_result`, Felder `command`, `cmd_id`, `ok`, `status`,
  optional `reason`). Der Server protokolliert es und broadcastet es per SSE
  an die Web-UI; damit ist das Dispatch-Feedback nicht mehr nur ein lokales
  Relay-Log. `status=timeout` / `ok=false,reason="no_response"` heisst dabei
  weiterhin "keine Antwort", nicht "Fehlschlag". Ein Meldefehler (Server
  weg, 4xx/5xx) aendert nichts am Dispatch: das Kommando bleibt ack-markiert,
  es wird nur geloggt (`dispatch result report failed ...`). Ohne
  `RBB_MATCH_ID` wird nicht gemeldet (wie beim Event-Post).

## Test ohne Windows

Linux: FIFO als Named-Pipe-Ersatz (`RBB_PIPE_PATH` auf einen FIFO-Pfad) für
den Schreibpfad. **Wichtig:** Eine FIFO ist eine einzelne Queue — schreibt
der Relay auf einem `O_RDWR`-Handle und liest sofort danach auf demselben
fd (wie beim echten Duplex-Pipe-Kontrakt), bekommt er deterministisch seine
eigene gerade geschriebene Zeile zurück; ein externer Prozess/Reader (z. B.
`cat`) sieht dabei nie etwas — kein Scheduling-Zufall, sondern reine
FIFO-Queue-Semantik (bei einer echten Windows-Named-Pipe mit getrennten
Puffern je Richtung tritt das nicht auf). Deshalb:
- Schreibpfad (Inhalt der exec-Zeile): FIFO-Fake in
  `bausteine/07-relay/test_dispatch.py` (`PipeClientTest`, nutzt nur
  `send_exec`, das nicht zurückliest).
- Antwortpfad (`exec_result` parsen/matchen): `os.pipe()` in
  `bausteine/07-relay/test_dispatch.py` (`ReadResultTest`) — echte getrennte
  Enden, keine Selbst-Lese-Falle.
- End-to-End (Timeout-Verhalten, kein Haenger): FIFO in
  `bausteine/07-relay/test_e2e_prototype.sh` und
  `tests/e2e-vollkette/vollkette.test.js` — dort ohne Responder, geprüft wird
  `dispatch result cmd_id=... status=timeout`.
