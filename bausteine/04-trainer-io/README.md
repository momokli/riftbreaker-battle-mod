# Baustein 04 — Trainer-I/O (Injector + rbbridge-DLL + pipe_client)

**Was es testet:** Den kompletten Trainer-I/O-Kanal der Architektur
(Trainer-only, s. `docs/concept.md`) — **ohne das Spiel**:
1. `injector.exe` lädt `rbbridge.dll` zur Laufzeit in einen **beliebigen
   x64-Prozess** (Test: `notepad.exe`),
2. die DLL startet den Named-Pipe-Server `\\.\pipe\rbbattle`,
3. `pipe_client.py` verbindet sich, sendet `ping`/`exec` und druckt die
   JSON-Antworten (`pong`, `exec_result`).

Damit ist der I/O-Kanal (Injection + Pipe + Protokoll v0) unabhängig von
RE-Arbeit am Spiel validierbar. `exec` ist **kein no-op mehr**: seit dem
RE-Stand (Build 2.0.58485) löst `dispatch_exec` `ConsoleService::ExecuteCommand`
per AOB-Signatur (`.text`) und die `ConsoleService`-Instanz per RTTI-Walk
(vftable) + Adressraum-Scan auf (**keine festen RVAs**) und ruft sie auf:
- Erfolg → `{"event":"exec_result","command":"…","ok":true}`
- Anbindung nicht auflösbar (kein Spielprozess/Modul, Signatur/RTTI/Instanz
  fehlt) → `ok:false` + `"reason":"console_service_not_found"` — **ohne
  Crash**, es wird nichts aufgerufen.

**Was ist belegt?** Host-seitig: die Auflösung, die Fehlerbehandlung und der
Cache sind statisch + im Host-Test `rbbridge_hosttest.c` (synthetischer
PE-Puffer, `tests/rbbridge-hosttest`) abgesichert. **Live-Beweis fehlt noch:** dass
der Aufruf im laufenden Spiel wirklich die Welle spawnt (siehe Status/OFFEN).

Seit dem Dual-Mode-Umbau (DLL + Standalone-EXE aus einer Quelle) gibt es
zwei Wege, den Kanal zu testen: **Test 0** startet dieselbe Pipe-Server-
Logik als normale `rbbridge_standalone.exe` — ganz **ohne Injection**;
**Test 1** ist der bisherige Injection-Test (notepad.exe + injector.exe).

**Richtung (kanonisch):** `bausteine/04-trainer-io/` ist die **einzige**
Build-/Distributions-Quelle — `scripts/package_bausteine.sh` und der
Build-Job in `.github/workflows/ci.yml` bauen **ausschließlich** hieraus.
Der frühere byte-identische Spiegel unter `trainer/injector/` +
`trainer/rbbridge/` wurde mit Issue #299 entfernt (`trainer/` enthält nur
noch Protokoll und RE-Tooling). `pipe_client.py` + dieses README gibt es
nur hier.

## Inhalt

```
injector/injector.c      <- kanonisch (injector.exe, x64, Windows)
rbbridge/rbbridge.c      <- kanonisch (baut rbbridge.dll UND
                            rbbridge_standalone.exe, x64, Windows)
bridge/pipe_bridge.c     <- NEU (Issue #265): baut pipe_bridge.exe — HTTP(9001)->Pipe-Bridge
                            (x64, Windows; Win32 + ws2_32; nur in dieser Quelle, kein Spiegel)
pipe_client.py           <- Test-Client (Python 3, Windows, nur Standardbibliothek)
```

## Hinweis zur Bridge (Issue #265)

`bridge/pipe_bridge.c` → `pipe_bridge.exe` ist der HTTP-Endpunkt, den der
Dedicated-Server-Deploy braucht: er laeuft als Wine-x64-Prozess im Container,
nimmt `GET /health` und `POST /exec` an und uebersetzt die Kommandos in
exec-Zeilen auf `\\.\pipe\rbbattle` (der Wine-Named-Pipe ist nur aus Wine
erreichbar). Details, Verdrahtung und Testanleitung: `docs/INGRESS_IO.md`.
Alle vier Binaries baut `scripts/build_rbbridge_tools.sh <outdir>`.

## Cockpit: Server-Control-Panel (Plane B, Issue #422)

Die Cockpit-UI liegt seit #474 (Schritt 1) als eigener Baustein in
`bausteine/08-control-ui/cockpit.html` — sie ist ein **Konsument** dieses
IO-Kanals, kein Teil davon (Details: `bausteine/08-control-ui/README.md`).

Ihr Panel `server control (plane B)` hat Status (`state`/`health`/`uptime`/
`started_at`), Logs (letzte N Zeilen) und die Buttons `Restart server`/`Start`/
`Stop`. Datenquellen sind die Plane-B-Host-Agent-Routen `GET /server/status`,
`GET /server/logs?tail=N` (N ≤ 5000) und `POST /server/{restart,start,stop}`
(Body `{}`), relative Pfade auf derselben Origin. Ist der Agent nicht erreichbar
(HTTP !ok, Parse-Fehler), zeigt das Panel nur "—", meldet den Fehler in einer
eigenen kleinen Statuszeile und **laedt die Seite nie neu**; Status pollt alle
~5 s, Logs nur auf Knopf.

**Live-Daten brauchen gemergtes #424** (Agent + Caddy-Route `handle /server/*`)
und die Bearer-Injektion in der Caddy-Route (der Browser hat keinen Token -> sonst
401 -> "—"). Der Node-Test `tests/server-control-panel` laeuft ohne Netzwerk
und Dependencies (`cd tests/server-control-panel && npm test`).

**Auslieferung:** `scripts/gen_cockpit_html.py` liest die UI aus Baustein 08 und
erzeugt `bridge/cockpit_html.inc` (Build-Artefakt, gitignored) fuer
`pipe_bridge.c`; die Bridge liefert sie unter `GET /` aus. Schritt 2 (#474)
entkoppelt das (Caddy `file_server` + API-Proxy).

## Build (Windows, x64)

Voraussetzung: 64-bit-Toolchain — **Injector UND DLL müssen x64 sein**
(notepad.exe unter Windows 10/11 x64 ist 64-bit).

Option A — MinGW-w64:
```bat
:: rbbridge.dll (Injection)
x86_64-w64-mingw32-gcc -O2 -Wall -Wextra -shared -o rbbridge.dll rbbridge\rbbridge.c
:: rbbridge_standalone.exe (Test 0, kein -lws2_32 noetig)
x86_64-w64-mingw32-gcc -O2 -Wall -Wextra -DRBBRIDGE_STANDALONE -o rbbridge_standalone.exe rbbridge\rbbridge.c
x86_64-w64-mingw32-gcc -O2 -Wall -Wextra -o injector.exe injector\injector.c
:: pipe_bridge.exe (HTTP-Bridge, Issue #265; braucht ws2_32)
x86_64-w64-mingw32-gcc -O2 -Wall -Wextra -o pipe_bridge.exe bridge\pipe_bridge.c -lws2_32
```

Alle vier zusammen (Linux-Cross-Build, kanonischer Weg):
`bash scripts/build_rbbridge_tools.sh /tmp/rbtools-build`.

Option B — MSVC (Developer Prompt):
```bat
:: rbbridge.dll
cl /nologo /O2 /W3 /LD rbbridge.c /Fe:rbbridge.dll
:: rbbridge_standalone.exe
cl /nologo /O2 /W3 /DRBBRIDGE_STANDALONE rbbridge.c /Fe:rbbridge_standalone.exe
cl /nologo /O2 /W3 injector.c shell32.lib /Fe:injector.exe
```

`pipe_client.py` braucht keinen Build (Python 3.7+, „Add python.exe to PATH“).

## Test 0 — Standalone (KEINE Injection nötig)

Die Pipe-Server-Logik aus `rbbridge.c` lässt sich mit `-DRBBRIDGE_STANDALONE`
als normales Konsolen-Programm bauen — damit ist der komplette I/O-Kanal
(Pipe + Protokoll v0) ohne Injector und ohne Zielprozess testbar. Das
Protokollverhalten ist identisch zur injizierten DLL (nur eine Hinweiszeile
beim Start).

1. **Standalone-EXE starten** (Terminal 1):
   ```bat
   rbbridge_standalone.exe
   ```
   Erwartet: Hinweiszeile `rbbridge_standalone: Pipe-Server aktiv auf
   \\.\pipe\rbbattle ...` — läuft bis Ctrl+C.
2. **Pipe-Client starten** (Terminal 2):
   ```bat
   python pipe_client.py
   ```
   → verbindet sich (kein notepad, kein injector), sendet `{"cmd":"ping"}`,
   druckt die Antwort.
3. **exec prüfen** (optional; ohne Spielprozess gibt es kein
   Modul → `ok:false`/`console_service_not_found`, der Kanal antwortet
   trotzdem):
   ```bat
   python pipe_client.py exec rb_wave 3
   ```
   → sendet `{"cmd":"exec","command":"rb_wave 3"}`, druckt die Antwort.
4. **Beenden**: Ctrl+C im Terminal 1 → `rbbridge_standalone: beendet`.

## Test 1 — Injection (wie bisher)

1. **notepad.exe starten** und PID ermitteln:
   ```bat
   tasklist | findstr /i notepad
   ```
2. **DLL injizieren** (Pfad zur gebauten DLL):
   ```bat
   injector.exe <pid> C:\pfad\zu\bausteine\04-trainer-io\rbbridge\rbbridge.dll
   ```
   Erwartet: `[+] rbbridge.dll geladen: HMODULE=0x...`
   (Alternativ Prozessname: `injector.exe notepad.exe <dll>`.)
3. **Pipe-Client starten** (zweites Terminal):
   ```bat
   python pipe_client.py
   ```
   → sendet `{"cmd":"ping"}`, druckt die Antwort.
4. **exec prüfen** (im notepad-Prozess gibt es kein Spielmodul → `ok:false`;
   im injizierten Spielprozess bei erfolgreicher Auflösung `ok:true`):
   ```bat
   python pipe_client.py exec rb_wave 3
   ```
   → sendet `{"cmd":"exec","command":"rb_wave 3"}`, druckt die Antwort.
5. Aufräumen: notepad.exe schließen (DLL lebt nur im Prozess).

## Argumente & Quoting (Issue #18)

Der `exec`-Kanal transportiert **ein** Kommando als String. `pipe_client.py`
verbindet die Kommando-Tokens ab `argv[2]` zu EINEM String (`build_exec_command`),
sodass unquotierte Argumente erhalten bleiben:

```bat
python pipe_client.py exec rb_wave 3   ->  {"cmd":"exec","command":"rb_wave 3"}
```

Der Produktiv-Command-Runner `exec_cmd_client.exe` verliert unquotierte
Argumente dagegen (Live-Befund 2026-09-09: `exec_cmd_client.exe rb_wave 3` ->
`command="rb_wave"` -> level 1). Dort daher als EIN String quoten:
`exec_cmd_client.exe "rb_wave 3"` -> level 3 (8 Spawns). Selbsttest ohne
Pipe: `python pipe_client.py --selftest`.

## Erwartetes Ergebnis

- Test 0, Schritt 2 bzw. Test 1, Schritt 3, ping:
  ```
  -> {"cmd": "ping"}
  <- {"event":"pong","t":<uptime-ms>}
  ```
- exec (führt `ConsoleService::ExecuteCommand` aus, AOB/RTTI-aufgelöst):
  - Erfolg — Spielprozess, Modul + ConsoleService gefunden:
    ```
    <- {"event":"exec_result","command":"rb_wave 3","ok":true}
    ```
  - Nicht-Fund — kein Spielprozess/Modul (Test 0 standalone, Test 1 notepad):
    ```
    <- {"event":"exec_result","command":"rb_wave 3","ok":false,"reason":"console_service_not_found"}
    ```
    (kein Crash — bei Nicht-Fund wird die `ExecuteCommand`-fn nie aufgerufen)
- Host-Test der Auflösung (ohne Windows/Spielprozess, synthetischer PE-Puffer):
  `cd tests/rbbridge-hosttest && npm ci && npm test`
  → `rbbridge host-test: scan_bytes + RTTI-Resolver`
- Logs zur Kontrolle (beide Varianten, gleiche Datei):
  - Datei: `%TEMP%\rbbridge.log` (abschaltbar: `RBBRIDGE_LOG=0`),
  - `OutputDebugString` → DebugView (Sysinternals), Filter `rbbridge`.
- Läuft der Client weiter (`--watch`), kommen alle ~5 s
  `{"event":"state","state":{...}}`-Heartbeats (Platzhalter).

**Fehlerbilder:** „Zielprozess nicht gefunden“ → PID/Name prüfen;
Injection-Fehler (ERROR_ACCESS_DENIED) → 64-bit-Build prüfen und notepad als
gleicher Benutzer; hängt `pipe_client.py` beim Verbinden → Server läuft
nicht: bei Test 0 `rbbridge_standalone.exe` starten, bei Test 1 die DLL
injizieren (os.open blockiert, bis der Pipe-Server existiert).

## Risiken & offene Punkte

- **Thread-Modell (Ist-Stand `main`):** alle Game-Calls der Bridge laufen
  **inline im Pipe-Thread** — es gibt **keinen** Marshal (`exec`/`lua_*` und der
  frühere `ConsoleService::Update`-Detour sind mit dem C++-direct-only-Umbau
  entfernt, #387/#446). Das ist ein **offenes Live-Risiko** (Crash #436,
  Readiness #479: `ok:false, reason:"world_not_ready"` bis die Welt fertig ist).
  Single Source of Truth: `docs/research/dedicated-io-thread-model.md`.
- **Fehl-Fund der Instanz (teilweise abgesichert, offen):** die „first hit =
  this“-Heuristik ist durch den vftable-Plausibilitätscheck
  (`looks_like_vftable`: vftable im Modul-Image, erste Referenz zeigt ins
  Image) entschärft; ein völlig falscher Kandidat ist damit nicht zu 100 %
  ausgeschlossen (der Nicht-Fund ist abgesichert, der Fehl-Fund nicht).
- **Signatur ist build-gebunden:** die rel32-CALL-Displacements (4+4 Bytes)
  sind per Byte-Maske als Wildcards behandelt, die `E8`-Opcodes bleiben Pflicht;
  die übrigen 20 Signatur-Bytes sind an Build 2.0.58485 kalibriert und müssen
  bei einem Engine-Update gegen die neue `.text`-Gegenprobe nachgezogen werden.
- **Live-Beweis fehlt (OFFEN):** „Welle spawnt sichtbar + korrekt“ — nur mit
  Player (Momo/Matheo) prüfbar, hängt an #252.

## Status

- [x] rbbridge.c/injector.c einzige Quelle in `bausteine/04-trainer-io/` (Spiegel unter `trainer/` mit Issue #299 entfernt)
- [x] rbbridge.c Dual-Mode-Umbau (DLL + Standalone-EXE aus einer Quelle)
- [x] exec-Dispatch per AOB-Signatur/RTTI (statt fester RVAs) + Cache
- [x] Host-Test `rbbridge_hosttest.c` (scan_bytes + RTTI-Resolver, synthetischer PE-Puffer)
- [x] Cross-Build (x86_64-w64-mingw32-gcc): rbbridge.dll + rbbridge_standalone.exe kompilieren
- [x] pipe_bridge.c (HTTP(9001)->Pipe-Bridge, Test 2 `--ping`/`--once`), Cross-Build via `scripts/build_rbbridge_tools.sh` (#265)
- [ ] Windows-Build-Test: DLL + Standalone-EXE + Injector (Matheo/Momo)
- [ ] Windows-Test 0: Standalone-EXE + pipe_client.py → ping/pong (Matheo/Momo)
- [ ] Windows-Test 1: Injection in notepad.exe + ping/pong (Matheo/Momo)
- [ ] Windows-Test: exec ohne Spielprozess → `ok:false`/`console_service_not_found` ohne Crash (Matheo/Momo)
- [ ] Windows-Test (LIVE, #252): exec im Spielprozess → `ok:true` + Welle sichtbar (Matheo/Momo)
