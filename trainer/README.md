# Trainer — Harness v0 (Grundgerüst)

> **Status: Grundgerüst (Harness).** Kompiliert (auf Windows) und injizierbar;
> die eigentliche Spiellogik-Anbindung (Reverse Engineering) folgt in der
> RE-Phase. Alles RE-abhängige ist im Code als `TODO(RE)` / `FIXME(RE)`
> markiert — siehe [Offene RE-Punkte](#offene-re-punkte).

## Architektur (Trainer-only)

**Die Trainer-DLL ist das einzige I/O-Gateway zwischen Spielprozess und
Außenwelt.** Der Lua-Mod bleibt reine Spiellogik — er darf (Findings:
`docs/findings.md`) **kein** Datei-I/O machen (`io.open` crasht das Spiel!).

```
┌──────────────────────────────────────┐
│  The Rift Breaker (Spielprozess)     │
│                                      │
│  ┌────────────┐   rb_wave <level>    │
│  │  Lua-Mod   │ ◄── registriert via  │
│  │ (Spiellogik)│     ConsoleService   │
│  └─────▲──────┘                      │
│        │ (später: RE-Aufrufstelle /  │
│        │  exec-Kanal)                │
│  ┌─────┴──────────────────────────┐  │
│  │  rbbridge.dll (injiziert)      │  │
│  │  = I/O-Gateway: Named-Pipe-    │  │
│  │    Server \\.\pipe\rbbattle    │  │
│  └─────▲──────────────────────────┘  │
└────────┼─────────────────────────────┘
         │ Named Pipe (JSON, line-delimited)
┌────────┴─────────────────────────────┐
│  Pipe-Client (extern, später         │
│  Tournament-Server-Forwarder)        │
└──────────────────────────────────────┘
```

- **Steam-kompatibel:** Runtime-only-DLL-Injection — es werden **keinerlei
  Dateien im Spielverzeichnis verändert**. Nur die `rbbridge.dll` muss als
  Datei existieren (irgendwo lokal) und wird zur Laufzeit geladen.
- **Rundenbasiert:** Keine Echtzeit-Garantien nötig; Protokoll-Events mit
  Client-Retry/Pull, siehe [protocol.md](protocol.md).
- Der Lua-Mod (Spiellogik) entsteht im Spike-Zweig
  (`feature/spike-mod-skeleton`, `mod/lua/rbbattle_autoexec.lua`, registriert
  u. a. `rb_wave <level>`) und wird später nach `mod/` gemerged.

## Komponenten

| Komponente | Inhalt | Aufgabe |
|---|---|---|
| `injector/injector.c` | `injector.exe` (x64, Windows) | DLL zur Laufzeit in den Spielprozess laden (Remote-`LoadLibraryW`); Ziel per PID oder Prozessname |
| `rbbridge/rbbridge.c` | `rbbridge.dll` + `rbbridge_standalone.exe` (x64, Windows) | In-Game-Gateway: Named-Pipe-Server `\\.\pipe\rbbattle`, line-delimited JSON v0; `exec`-Dispatch mit `TODO(RE)`; State-Heartbeat-Platzhalter. **Dual-Mode:** eine Quelle baut per `-DRBBRIDGE_STANDALONE` zusätzlich eine Standalone-EXE mit identischem Protokoll (Test ohne Injection, Baustein 04 Test 0) |
| `scan/` | Python + pymem | RE-Phase: Prozess-/Modul-Info (`scan_find.py`), interaktiver Wert-Scan (`scan_values.py`) → `offsets.json` |
| `protocol.md` | Spezifikation | Event-Schema v0 (Spiel ⇄ Server) |

## Build (Windows, x64)

Voraussetzung: 64-bit-Toolchain. Das Spiel ist 64-bit — **Injector UND DLL
müssen x64 sein**.

### Option A — MinGW-w64

```bat
:: rbbridge.dll (Injection)
x86_64-w64-mingw32-gcc -O2 -Wall -Wextra -shared -o rbbridge.dll rbbridge.c

:: rbbridge_standalone.exe (gleiche Quelle, kein Injection noetig)
x86_64-w64-mingw32-gcc -O2 -Wall -Wextra -DRBBRIDGE_STANDALONE -o rbbridge_standalone.exe rbbridge.c

:: injector.exe
x86_64-w64-mingw32-gcc -O2 -Wall -Wextra -o injector.exe injector.c
```

### Option B — MSVC (Visual Studio Build Tools / Developer Prompt)

```bat
:: rbbridge.dll
cl /nologo /O2 /W3 /LD rbbridge.c /Fe:rbbridge.dll

:: rbbridge_standalone.exe
cl /nologo /O2 /W3 /DRBBRIDGE_STANDALONE rbbridge.c /Fe:rbbridge_standalone.exe

:: injector.exe  (shell32.lib nur wegen CommandLineToArgvW)
cl /nologo /O2 /W3 injector.c shell32.lib /Fe:injector.exe
```

## Nutzung

### 0. Ohne Spiel testen — Standalone-EXE (kein Injection nötig)

`rbbridge_standalone.exe` (Build s. oben, `-DRBBRIDGE_STANDALONE`) startet
denselben Pipe-Server als normales Konsolen-Programm — Protokollverhalten
identisch zur injizierten DLL. Damit ist die Trainer-IO auf jedem
Windows-Rechner ohne Spiel/Injection testbar (ausführlich: Baustein 04,
Test 0):

```bat
rbbridge_standalone.exe        :: Terminal 1, laeuft bis Ctrl+C
python pipe_client.py          :: Terminal 2 -> ping/pong (s. Baustein 04)
```

### 1. Spiel starten

The Rift Breaker starten und eine Karte laden (das Lua-Mod-Registrieren ist
für den Pipe-Server nicht nötig — der läuft ab DLL-Load; für echte
`rb_wave`-Ausführung später schon).

### 2. Injizieren

```bat
injector.exe riftbreaker.exe C:\pfad\zu\rbbridge.dll
:: oder per PID:
injector.exe 4821 C:\pfad\zu\rbbridge.dll
```

Erfolgsmeldung: `[+] rbbridge.dll geladen: HMODULE=0x...`

### 3. Pipe-Client (Test)

Der Pipe-Server läuft ab sofort unter `\\.\pipe\rbbattle` und wartet auf
einen Client. Testen z. B. mit PowerShell:

```powershell
$p = New-Object System.IO.Pipes.NamedPipeClientStream('.', 'rbbattle', [System.IO.Pipes.PipeDirection]::InOut)
$p.Connect()
$w = New-Object System.IO.StreamWriter($p); $w.NewLine = "`n"
$r = New-Object System.IO.StreamReader($p)
$w.WriteLine('{"cmd":"ping"}'); $w.Flush()
$r.ReadLine()   # -> {"event":"pong","t":...}
```

`{"cmd":"exec","command":"rb_wave 3"}` antwortet im Harness mit
`exec_result ... "ok":false` (TODO(RE)) — Protokoll-Details:
[protocol.md](protocol.md).

### 4. Logs

- `OutputDebugString` → DebugView (Sysinternals), Filter `rbbridge`.
- Datei-Log: `%TEMP%\rbbridge.log` (abschaltbar: `RBBRIDGE_LOG=0`).

## Dateien, die nie angefasst werden

Spielinstallation/Steam-Verzeichnisse bleiben unverändert (runtime-only).
In den Prozess geht nur die injizierte DLL; geschrieben wird ausschließlich
in die eigene Pipe und das Temp-Log.

## Offene RE-Punkte

Alles, was den Spielprozess von innen versteht, ist Phase 2
(Reverse Engineering) und im Code markiert:

1. **`dispatch_exec()`** (`rbbridge.c`): `{"cmd":"exec","command":"rb_wave 3"}`
   wirklich im Spiel ausführen. Gesucht: ConsoleService-Instanz / Lua-State
   bzw. die Engine-Funktion hinter `ExecuteCommand` (Findings Punkt 8 —
   `ConsoleService:ExecuteCommand` spawnt nachweislich), bevorzugt per
   AOB-Signatur statt fester Adresse. Anhaltspunkt für die Verdrahtung:
   Experiment C im Spike (Log-Bridge `[RBBATTLE] event=...`).
2. **`send_state_placeholder()`** (`rbbridge.c`): echte State-Werte (Score,
   Ressourcen, Wave) aus dem Prozess lesen → `score_update`-Events.
3. **Offsets finden:** `scan/scan_values.py` + Cheat-Engine-Workflow
   (Anleitung: `scan/README.md`), Ergebnisse → `trainer/scan/offsets.json`.
4. **Event-Zustellung Server→Spiel** strukturiert verdrahten
   (`incoming_wave`, `round_*`, ... — Schema: `protocol.md`).

## Verwandte Doku

- `../docs/concept.md` — Spiel-/Rundenkonzept (Trainer-Abschnitt wird durch
  diese trainer-only-Architektur konkretisiert).
- `../docs/findings.md` — verifizierte Mod-/Engine-Findings.
- `protocol.md` — Event-Schema v0.
