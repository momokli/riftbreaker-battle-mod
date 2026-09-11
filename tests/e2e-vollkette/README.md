# Vollketten-E2E (Issue #11)

Testet die Kette `rb_wave 3` via Web-UI → Spawn am headless Client **so weit wie
möglich statisch/deterministisch** — ohne Windows-Spielprozess:

| Schritt | Prüfung | Ebene |
|---|---|---|
| Web-UI-Trigger | Button `data-cmd="rb_wave 3"` + `app.js` `POST /event` (`type:"exec_command"`) | statisch |
| Tournament-Server | `/event` → Outbox → `cmd_id` | dynamisch (echter `server.js`) |
| Relay | `GET /poll` → `dispatch_exec` schreibt `{"cmd":"exec",…}` auf die Pipe | dynamisch (echter `relay.py`, FIFO-Fake) |
| rbbridge | `{"cmd":"exec","command":"rb_wave 3"}` → `ExecuteCommand` (AOB/RTTI, keine festen RVAs) | statisch (`bausteine/04-trainer-io/rbbridge/rbbridge.c`) + Host-Test |
| rbbridge Modul-Resolution | Wine-robust: `GetModuleHandleA/W`, `EnumProcessModules`, Toolhelp32, Signatur-Scan → `VirtualQuery()->AllocationBase`, `LoadLibraryA`-Fallback, graceful `ok:false` (Issue #252) | statisch (`rbbridge.c`) + opt-in live |
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

## Geprüft (Issue #252)

- **Relay → rbbridge-Pipe-Dispatch**: `relay.py` `dispatch_exec` ist implementiert
  (nicht mehr v0-TODO) und wird im Test dynamisch gegen den echten `relay.py`
  geprüft.
- **ExecuteCommand im Spielprozess / Modul-Resolution**: Wine-robuste Auflösung
  (`resolve_module`: GetModuleHandle A/W, EnumProcessModules, Toolhelp32,
  Signatur-Scan → `VirtualQuery()->AllocationBase`, LoadLibraryA-Fallback) wird
  per **statischer Resolution-Prüfung** (Issue #252) abgedeckt; Host-Test siehe
  oben.
- **Opt-in Live-Test**: `RBB_LIVE_PIPE=1 npm test` verbindet die echte
  rbbridge-Pipe und prüft `ExecuteCommand` live. Voraussetzung: Wine/Windows +
  laufender Spielprozess + injizierte `rbbridge.dll` (`\\.\pipe\rbbattle`).
  Ohne diese Env-Var wird der Test übersprungen.

## OFFEN (bewusst als skip markiert)

1. **Spawn am headless Client sichtbar (Screenshot)**: Live-Beweis durch den
   Operator — **Player-Test Momo/Matheo** (explizit NICHT erledigt; braucht
   beigetretenen Client + Screenshot). Schließt den Live-Beweis ein, dass die
   Welle wirklich spawnt (Thread-Marshalling offen), siehe #252.

## Aufruf

```bash
cd tests/e2e-vollkette
npm ci        # einmalig (fengari + luaparse)
npm test      # = node --test
```

Voraussetzungen: `node` + `python3` im PATH (für `server.js` + `relay.py`);
für den Host-Test zusätzlich ein Host-C-Compiler (`cc`/`gcc`) — fehlt er,
wird nur dieser Test übersprungen.

Opt-in Live-Test (nur mit Wine/Windows + Spielprozess):

```bash
RBB_LIVE_PIPE=1 npm test
```
