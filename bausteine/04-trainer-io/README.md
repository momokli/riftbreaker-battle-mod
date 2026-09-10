# Baustein 04 — Trainer-I/O (Injector + rbbridge-DLL + pipe_client)

**Was es testet:** Den kompletten Trainer-I/O-Kanal der Architektur
(Trainer-only, s. `docs/concept.md`) — **ohne das Spiel**:
1. `injector.exe` lädt `rbbridge.dll` zur Laufzeit in einen **beliebigen
   x64-Prozess** (Test: `notepad.exe`),
2. die DLL startet den Named-Pipe-Server `\\.\pipe\rbbattle`,
3. `pipe_client.py` verbindet sich, sendet `ping`/`exec` und druckt die
   JSON-Antworten (`pong`, `exec_result`).

Damit ist der I/O-Kanal (Injection + Pipe + Protokoll v0) unabhängig von
RE-Arbeit am Spiel validierbar. `exec` ist im Harness ein **bewusster
no-op** (`ok:false`, TODO(RE)) — getestet wird, dass der Kanal antwortet,
nicht dass er das Spiel steuert.

Seit dem Dual-Mode-Umbau (DLL + Standalone-EXE aus einer Quelle) gibt es
zwei Wege, den Kanal zu testen: **Test 0** startet dieselbe Pipe-Server-
Logik als normale `rbbridge_standalone.exe` — ganz **ohne Injection**;
**Test 1** ist der bisherige Injection-Test (notepad.exe + injector.exe).

Dateien **1:1 übernommen/synchron** aus `trainer/` (Harness v0, PR #2,
MD5-geprüft; Dual-Mode-Umbau ist in beiden Kopien identisch); neu ist nur
`pipe_client.py` + dieses README.

## Inhalt

```
injector/injector.c      <- Kopie aus trainer/injector/ (injector.exe, x64, Windows)
rbbridge/rbbridge.c      <- Kopie aus trainer/rbbridge/ (baut rbbridge.dll UND
                            rbbridge_standalone.exe, x64, Windows)
pipe_client.py           <- NEU: Test-Client (Python 3, Windows, nur Standardbibliothek)
```

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
```

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
3. **exec no-op prüfen** (optional):
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
4. **exec no-op prüfen**:
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
- exec (Harness-no-op — Kanal antwortet, Ausführung folgt in der
  RE-Phase):
  ```
  <- {"event":"exec_result","command":"rb_wave 3","ok":false,"reason":"..."}
  ```
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

## Status

- [x] injector.c / rbbridge.c 1:1 aus trainer/ übernommen (PR #2)
- [x] rbbridge.c Dual-Mode-Umbau (DLL + Standalone-EXE), Kopie gesynct
- [x] Cross-Build (x86_64-w64-mingw32-gcc): rbbridge.dll + rbbridge_standalone.exe kompilieren
- [ ] Windows-Build-Test: DLL + Standalone-EXE + Injector (Matheo/Momo)
- [ ] Windows-Test 0: Standalone-EXE + pipe_client.py → ping/pong (Matheo/Momo)
- [ ] Windows-Test 1: Injection in notepad.exe + ping/pong (Matheo/Momo)
- [ ] Windows-Test: exec antwortet ok:false ohne Crash (Matheo/Momo)
