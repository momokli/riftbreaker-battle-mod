# rbbridge unter MinGW-w64: kein SEH — jeder DLL-Zugriff muss plausibilisiert werden

Bezug: Issue #902 (Pipe-Ausfall ehrlich melden + selbstheilen). Kontext:
Issue #623 / PR #904 (VEH-Guard gegen Pipe-Crash) — hier **nur referenziert**,
nicht vorausgesetzt. Verwandte Doku: `docs/research/dedicated-io-re-findings.md`,
`docs/research/dedicated-io-thread-model.md`, `docs/research/dedicated-io-direct-reads.md`,
`docs/crash-symbolication.md`.

## Kernaussage

`rbbridge.dll` wird mit **MinGW-w64** gebaut (`scripts/build_rbbridge_tools.sh`,
`x86_64-w64-mingw32-gcc -shared`), nicht mit MSVC. MinGW-w64 liefert damit
**kein strukturiertes Exception Handling** (`__try`/`__except`, `__finally`) —
das ist MSVC-only. Konkret im Code dokumentiert (Stand `main` @ `9a87f34`):

- `server/dll/rbbridge.c` Z.1104: „MinGW-x64 stellt kein SEH (`__try/__except`,
  MSVC-only) bereit.“
- `server/dll/rbbridge.c` Z.3677: dito beim Instanz-Scan.

Die injizierte DLL lebt **im Adressraum des Dedicated-Servers** (C++; läuft
unter Wine). Faultet dort ein Zugriff auf eine abweichende bzw. nicht
garantierte Adresse, ist der Prozess ohne SEH-/C++-Exception-Netz — es gibt
keinen Catch, der noch greifen könnte. Anders gesagt: **unter MSVC wäre ein
`__try/__except` ein Sicherheitsnetz; unter MinGW gibt es dieses Netz nicht.**
Deshalb gilt für die DLL: *jeder* Pointer-/Modul-/Vtable-Zugriff wird **vor**
dem Dereferenzieren plausibilisiert (Guard-/Bereichs-/Null-Checks) — man
vertraut nicht auf SEH.

## Konsequenzen am Code

### 1. Crash-sichere Lese-/Scan-Pfade (statt roher Deref)

Die Instanz-/Signatursuche liest nie roh. Statt `q[i]`-Deref wird die Region
über `VirtualQuery` (nur `MEM_COMMIT` + lesbar, kein `PAGE_GUARD`,
`is_readable_region`) ermittelt und chunkweise per `ReadProcessMemory` in einen
**lokalen** Puffer kopiert und dort gescannt (`scan_qword_instance`,
`scan_qword_instance_writable`, Z.~3655 ff.). Liefert die Region zwischen
`VirtualQuery` und `ReadProcessMemory` FALSE (Heap-Churn/TOCTOU beim
Player-Join), wird der Chunk übersprungen — **kein Page-Fault, kein Crash**.

Das ist der Ersatz für SEH: Validität wird durch die Win32-API-Rückgabe
(`ReadProcessMemory == FALSE`) plus Bereichs-Check geprüft, nicht durch einen
Exception-Handler.

### 2. Plausibilisierung vor jedem Aufruf

Der eigentliche `dispatch_exec`-Aufruf auf eine aufgelöste Funktion findet
**nur** statt, wenn Instanz- **und** Signatur-Check davor bestanden wurden;
jeder Nicht-Fund liefert `{"event":"exec_result","ok":false,...,"reason":"..."}`
plus `dbg()` und ruft **niemals** auf (Kopfkommentar Z.~1092 ff.). Null-Checks
und Bereichsprüfungen sind die Guard-Kette, die unter MSVC ein `__try` wäre.

### 3. Pipe-Server-Thread: kein Absturzpfad darf den Prozess mitnehmen

`pipe_server_main` (Z.6359) ist der DLL-seitige Pipe-Server: `CreateNamedPipeA`
→ `ConnectNamedPipe` → `serve_client` → `DisconnectNamedPipe`/`CloseHandle` in
einer Endlosschleife bis `g_stop`. Jeder Win32-Fehler wird **explizit**
abgefangen und führt nur zu `Sleep`+`continue` (bzw. Cleanup), nie zu einem
unbehandelten Fault — auch hier gilt: kein SEH, also muss der Fehlerpfad selbst
sauber sein. `rbbridge_start` (Z.6597) schützt den Doppelstart per
`InterlockedCompareExchange(&g_thread_started,1,0)`.

Konsequenz für #902: Weil ein evtl. toter Pipe-Thread unter MinGW keinen
SEH-Rettungsanker hat, wird sein Zustand **aktiv geprüft** (`WaitForSingleObject`
auf `g_thread`) und der Server ohne manuellen Eingriff neu aufgesetzt
(`rbbridge_ensure_server` / `entrypoint`-Watchdog, US3/US4) statt auf ein
Exception-Netz zu hoffen.

### 4. VEH-Guard (Kontext aus #623 / PR #904)

PR #904 ergänzt einen **Vectored-Exception-Handler** (`rbbridge_guard_push`/
`rbbridge_guard_pop`, `rbbridge_veh_note`) als zusätzliche Absicherung. Das ist
**Zusatzkontext** und keine Voraussetzung für #902: Auf `main` existiert diese
Infrastruktur noch nicht (grep über `server/dll/rbbridge.c` findet weder
`rbbridge_guard_*` noch `rbbridge_veh_note`). Der VEH fängt *nur* die
Exception-Wege ab, die MinGW ohne SEH offenlässt; er **ersetzt die Guards
nicht**, sondern ergänzt sie. Plausibilisierung bleibt die erste Verteidigung.

## Merksatz

> MinGW-w64 kennt kein SEH: Was ein `__try/__except` unter MSVC abfangen würde,
> muss hier **vor** dem Zugriff durch einen Check verhindert werden. Jeder
> DLL-seitige Pointer-/Modul-/Thread-Zugriff ist damit plausibilisierungs-
> pflichtig — Guard-/Bereichs-/Null-Check zuerst, Operation danach.
