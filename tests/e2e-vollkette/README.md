# Vollketten-E2E (Issue #11)

Testet die Kette `rb_wave 3` via Web-UI → Spawn am headless Client **so weit wie
möglich statisch/deterministisch** — ohne Windows-Spielprozess:

| Schritt | Prüfung | Ebene |
|---|---|---|
| Web-UI-Trigger | Button `data-cmd="rb_wave 3"` + `app.js` `POST /event` (`type:"exec_command"`) | statisch |
| Tournament-Server | `/event` → Outbox → `cmd_id` | dynamisch (echter `server.js`) |
| Relay | `GET /poll` → `dispatch_exec` schreibt `{"cmd":"exec",…}` auf die Pipe | dynamisch (echter `relay.py`, FIFO-Fake) |
| rbbridge | `{"cmd":"exec","command":"rb_wave 3"}` → `ExecuteCommand` (AOB/RTTI, keine festen RVAs) | statisch (`bausteine/04-trainer-io/rbbridge/rbbridge.c`) + Host-Test |
| Mod | `rb_wave 3` → `SpawnWave(3)` → 8 Kreaturen (baxmoth×5, artigian×2, canceroth×1) | fengari + Stub-Services |

## Host-Test der Auflösung (`rbbridge-hosttest.test.js`)

Kompiliert zur Laufzeit die **reinen** Scan-/RTTI-Funktionen aus `rbbridge.c`
(Host-CC `cc`, dann `gcc` — **kein mingw**) gegen einen **synthetischen,
PE-artigen Puffer** (`hosttest/rbbridge_hosttest.c`) und führt sie aus: kein
Windows, kein Spielprozess, kein Netz. Geprüft werden `scan_bytes`/
`scan_bytes_mask` (inkl. rel32-Wildcards und E8-Pflicht), der
RTTI-Walk (`resolve_console_vftable`), der Resolver inkl. Cache
(`resolve_console_service`) und die Fehlerpfade (Modul/RTTI/Signatur/Instanz
fehlt → `0`/`NULL`, kein Crash). Ist kein Host-CC vorhanden, wird der Test
**sichtbar übersprungen** (skip mit Begründung, nie stillschweigend grün).

## OFFEN (bewusst als skip markiert)

Diese Schritte brauchen einen laufenden Windows-Spielprozess (headless Dedi +
injizierte `rbbridge.dll`) und sind in CI/Linux nicht ausführbar:

1. **ExecuteCommand im Spielprozess**: DLL-Injection +
   `ConsoleService::ExecuteCommand` — plus Live-Beweis, dass die Welle
   wirklich spawnt (Thread-Marshalling offen), siehe #252.
2. **Spawn am headless Client sichtbar (Screenshot)**: Live-Beweis durch den
   Operator (Momo/Matheo).

## Aufruf

```bash
cd tests/e2e-vollkette
npm ci        # einmalig (fengari + luaparse)
npm test      # = node --test
```

Voraussetzungen: `node` + `python3` im PATH (für `server.js` + `relay.py`);
für den Host-Test zusätzlich ein Host-C-Compiler (`cc`/`gcc`) — fehlt er,
wird nur dieser Test übersprungen.
