# Vollketten-E2E (Issue #11)

Testet die Kette `rb_wave 3` via Web-UI → Spawn am headless Client **so weit wie
möglich statisch/deterministisch** — ohne Windows-Spielprozess:

| Schritt | Prüfung | Ebene |
|---|---|---|
| Web-UI-Trigger | Button `data-cmd="rb_wave 3"` + `app.js` `POST /event` (`type:"exec_command"`) | statisch |
| Tournament-Server | `/event` → Outbox → `cmd_id` | dynamisch (echter `server.js`) |
| Relay | `GET /poll` → `dispatch pending: rb_wave 3 (cmd_id=…)` | dynamisch (echter `relay.py`) |
| rbbridge | `{"cmd":"exec","command":"rb_wave 3"}` → `ExecuteCommand` | statisch (`trainer/rbbridge/rbbridge.c`) |
| Mod | `rb_wave 3` → `SpawnWave(3)` → 8 Kreaturen (baxmoth×5, artigian×2, canceroth×1) | fengari + Stub-Services |

## Relay → rbbridge-Pipe-Dispatch (Issue #60)

`relay.py` `dispatch_exec` ist kein v0-TODO mehr: Es schreibt
`{"cmd":"exec","command":"...","cmd_id":"..."}` auf die konfigurierte Named
Pipe (`RBB_PIPE_PATH`, Default `\\.\pipe\rbbattle`). Geprüft wird das hier
**dynamisch gegen einen Named-Pipe-Ersatz** (Linux-FIFO, `mkfifo`) — die
`rbbridge`-Seite selbst ist nur statisch geprüft (Code-Grep, s. o.), echte
Windows-Named-Pipe + injizierte DLL bleiben offen (s. u.).

## OFFEN (bewusst als skip markiert)

Diese Schritte brauchen einen laufenden Windows-Spielprozess (headless Dedi +
injizierte `rbbridge.dll`) und sind in CI/Linux nicht ausführbar:

1. **ExecuteCommand im Spielprozess**: DLL-Injection + `ConsoleService::ExecuteCommand`.
2. **Spawn am headless Client sichtbar (Screenshot)**: Live-Beweis durch den Operator.

## Aufruf

```bash
cd tests/e2e-vollkette
npm install   # einmalig (fengari + luaparse)
npm test      # = node --test
```

Voraussetzungen: `node` + `python3` im PATH (für `server.js` + `relay.py`).
