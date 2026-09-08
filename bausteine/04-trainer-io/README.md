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

Dateien **1:1 übernommen** aus `trainer/` (Harness v0, PR #2, MD5-geprüft);
neu ist nur `pipe_client.py` + dieses README.

## Inhalt

```
injector/injector.c      <- Kopie aus trainer/injector/ (injector.exe, x64, Windows)
rbbridge/rbbridge.c      <- Kopie aus trainer/rbbridge/ (rbbridge.dll, x64, Windows)
pipe_client.py           <- NEU: Test-Client (Python 3, Windows, nur Standardbibliothek)
```

## Build (Windows, x64)

Voraussetzung: 64-bit-Toolchain — **Injector UND DLL müssen x64 sein**
(notepad.exe unter Windows 10/11 x64 ist 64-bit).

Option A — MinGW-w64:
```bat
x86_64-w64-mingw32-gcc -O2 -Wall -Wextra -shared -o rbbridge.dll rbbridge\rbbridge.c
x86_64-w64-mingw32-gcc -O2 -Wall -Wextra -o injector.exe injector\injector.c
```

Option B — MSVC (Developer Prompt):
```bat
cl /nologo /O2 /W3 /LD rbbridge.c /Fe:rbbridge.dll
cl /nologo /O2 /W3 injector.c shell32.lib /Fe:injector.exe
```

`pipe_client.py` braucht keinen Build (Python 3.7+, „Add python.exe to PATH“).

## Testablauf — KEIN Spiel nötig

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

## Erwartetes Ergebnis

- Schritt 3, ping:
  ```
  -> {"cmd": "ping"}
  <- {"event":"pong","t":<uptime-ms>}
  ```
- Schritt 4, exec (Harness-no-op — Kanal antwortet, Ausführung folgt in der
  RE-Phase):
  ```
  <- {"event":"exec_result","command":"rb_wave 3","ok":false,"reason":"..."}
  ```
- DLL-Logs zur Kontrolle:
  - Datei: `%TEMP%\rbbridge.log` (abschaltbar: `RBBRIDGE_LOG=0`),
  - `OutputDebugString` → DebugView (Sysinternals), Filter `rbbridge`.
- Läuft der Client weiter (`--watch`), kommen alle ~5 s
  `{"event":"state","state":{...}}`-Heartbeats (Platzhalter).

**Fehlerbilder:** „Zielprozess nicht gefunden“ → PID/Name prüfen;
Injection-Fehler (ERROR_ACCESS_DENIED) → 64-bit-Build prüfen und notepad als
gleicher Benutzer; hängt `pipe_client.py` beim Verbinden → DLL wurde noch
nicht injiziert (os.open blockiert, bis der Pipe-Server existiert).

## Status

- [x] injector.c / rbbridge.c 1:1 aus trainer/ übernommen (PR #2)
- [x] pipe_client.py erstellt (plain os.open, ping/exec, JSON-Ausgabe)
- [ ] Build-Test: DLL + Injector kompilieren (Windows x64) (Matheo)
- [ ] Pipe-Test: Injection in notepad.exe + ping/pong (Matheo/Momo)
- [ ] Pipe-Test: exec antwortet ok:false ohne Crash (Matheo/Momo)
