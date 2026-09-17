# Crash-Symbolik — collector-seitig statt PDB im Image (Issue #480)

## Problem

Provozierte Crashes des Dedicated-Servers landen als Bundle unter
`/opt/rbmods/crashes/<ts>-<uuid>/` (Minidump `.dmp`, `.log`, `.trace`,
`context.log`, `meta.json` — siehe Issue #462). Der Breakpad-`.trace` enthält
**keine Adressen** (nur `(function-name not available):0`) und ist deshalb nicht
nachsymbolisierbar; brauchbare Adressen stehen ausschließlich im **Minidump**.

Akzeptanzkriterium aus #480: ein Trace mit mindestens **einem echten
Funktionsnamen** — ohne Größen-/Startzeit-Regress im Dedicated-Image.

## Belege (planet, 2026-09-15)

| Fakt | Wert |
|---|---|
| PDB | `/srv/rbgame/bin/riftbreaker_dll_win_release.pdb` — **252 334 080 B** |
| DLL | `/srv/rbgame/bin/riftbreaker_dll_win_release.dll` — 77 885 440 B |
| Tool | `/usr/lib/llvm-18/bin/llvm-symbolizer` (+ `llvm-pdbutil`), Paket `llvm-18` |
| RVA-Test (#436) | `llvm-symbolizer --obj=<dll> --relative-address 0x275895` → echter Funktionsname |
| `.trace` | adressfrei → nicht nachsymbolisierbar |

`llvm-symbolizer` findet die PDB über das **CodeView-Debug-Directory der DLL im
selben Ordner** — es gibt (llvm-18) **kein** `--pdb`-Flag, und die PDB muss
nicht mit übergeben werden.

## Entscheidung

**Die PDB kommt NICHT ins Dedicated-Image** (252 MB sprengen das
Größen-/Startzeit-Budget und damit das Akzeptanzkriterium). Gewählter Weg = die
im Issue genannte Alternative: **collector-seitige Symbolik**. Die DLL/PDB
liegen bereits auf dem Host; symbolisiert wird nach dem Sammeln. Das Image
bleibt byte-identisch → kein Regress. Kein `dedicated-server-image`-Change.

## Aufbau

```
deploy/crash-collector/symbolize.py     # Kern: Minidump -> RVAs -> Funktionsnamen (stdlib-only)
deploy/crash-collector/crash_symbolize.sh   # CLI/Seam: Skip-Regeln, atomares Schreiben
deploy/crash-collector/crash_collector.sh   # ruft die CLI nach write_meta (additiv)
deploy/roles/crash-collector # rollt CLI + Kern aus (KEINE PDB/DLL)
tests/shell/crash-symbolize.test.sh  # planetfrei (synthetischer Dump + Fake-Symbolizer)
```

### Kern (`deploy/crash-collector/symbolize.py`)

1. Minidump-Streams parsen: **ModuleList (4)** → Basis/-Größe des Game-Moduls,
   **Exception (6)** → Fault-Adresse + AMD64-`Rip`, **ThreadList (3)** /
   **MemoryList (5)** → Stack-Speicher.
2. Kandidaten = Fault-Adresse + `Rip` + gescannte Return-Adressen; behalten wird
   **nur** `module_base <= addr < module_base + module_size` (Fremdadressen
   werden gefiltert).
3. Je Frame: `RVA = addr - module_base`, dann
   `llvm-symbolizer --obj=<dll> --relative-address <RVA>`
   (subprocess mit Argument-Liste, **kein** `shell=True`).
4. Ausgabe `<bundle>/symbolized.txt`: `# key: value`-Header (uuid, module,
   module_base/-size, fault_address, dll, tool, frames) + je Frame
   `<RVA-hex>\t<Funktionsname>`.

Exit-Codes: `0` ok · `2` Dump nicht parsebar · `3` kein Modul-Frame ·
`4` Symbolizer-Aufruf fehlgeschlagen (kein Teilergebnis).

### CLI (`deploy/crash-collector/crash_symbolize.sh <bundle-dir>`)

Findet `<uuid>.dmp` selbst (oder `--dmp`), prüft die **Skip-Regeln** und schreibt
atomar (Temp-Datei + `mv`). Jeder Skip/Fehler endet mit **rc=0** und loggt
`symbolize: skip <grund>`; Idempotent (existiert `symbolized.txt`, kein Re-Run).

Skip-Gründe: `RB_CRASH_SYMBOLIZE=0` · PDB fehlt · DLL fehlt · Tool fehlt ·
Symbolizer-Kern fehlt · Python fehlt · kein Minidump · kein Modul-Frame.

### Collector-Kopplung

`crash_collector.sh` ruft die CLI **nach `write_meta`, vor `retention_prune`**.
Danach wird `meta.json` ergänzt: `symbolized.txt` in `files` plus Feld
`symbolized {status, frames, tool, dll, generated_at}` (`status ∈ ok|skipped`).
Fehler/Skips töten den Collector **nie** (max. ein WARN); fehlt das
Symbolizer-Skript ganz, verhält sich der Collector wie vor #480.

### Env-Vertrag

| Var | Default |
|---|---|
| `RB_CRASH_SYMBOLIZE` | `1` |
| `RB_CRASH_SYMBOLIZE_BIN` | `/usr/local/bin/rbmods-crash-symbolize.sh` |
| `RB_CRASH_DLL` | `/srv/rbgame/bin/riftbreaker_dll_win_release.dll` |
| `RB_CRASH_PDB` | `/srv/rbgame/bin/riftbreaker_dll_win_release.pdb` |
| `RB_CRASH_LLVM_SYMBOLIZER` | `/usr/lib/llvm-18/bin/llvm-symbolizer` |
| `RB_CRASH_SYMBOLIZE_TOOL` | `/usr/local/lib/rbmods/crash/symbolize.py` |
| `RB_CRASH_SYMBOLIZE_TIMEOUT` | `60` |
| `RB_CRASH_STACK_SCAN`        | `1` (0 = nur Fault-/Kontext-Frames) |

## Zweites Modul (rbbridge.dll) — Issue #559

Crashes in der injizierten DLL `rbbridge.dll` (mingw-Build, kein PDB) werden
ebenfalls symbolisiert: `rbbridge.dll` wird mit `-g` (DWARF) gebaut (Codegen
unverändert), und `symbolize.py` löst ein zweites Modul (`--dll2`/`--module2`)
gegen eine zweite DLL auf. Frames des zweiten Moduls erscheinen in
`symbolized.txt` als `0x<rva>\t[<modul>] <name>` annotiert. Der Pfad kommt über
`RB_CRASH_RBBRIDGE_DLL` (Default `{{ rbtools_dir }}/rbbridge.dll`, per Env z. B.
`/opt/rbmods/rbtools/dev/rbbridge.dll`); fehlt die
Datei, symbolisiert der Collector weiterhin nur die Game-DLL (graceful, kein
Hard-Fail).

## Betrieb

Manuell (read-only, z. B. für Evidenz an bestehenden Bundles):

```bash
RB_CRASH_PDB=/srv/rbgame/bin/riftbreaker_dll_win_release.pdb \
RB_CRASH_DLL=/srv/rbgame/bin/riftbreaker_dll_win_release.dll \
deploy/crash-collector/crash_symbolize.sh /opt/rbmods/crashes/<ts>-<uuid>
```

Automatisch: der Dienst `rbmods-crash-collector` symbolisiert jedes neue Bundle
mit den Env-Variablen der Rolle (`deploy/roles/crash-collector`).

## Grenzen

- **Nur `.dmp` hat Adressen** — der Breakpad-`.trace` bleibt unsymbolisiert.
- Stack-Scanning liefert zusätzlich Datenwerte, die zufällig in den
  Modulbereich zeigen (Falsch-Frames möglich); die Fault-/Kontext-Frames stehen
  vorn.
- Symbolik ist nur gültig, wenn **PDB und DLL zusammenpassen** (CodeView-GUID).
  Bei Mismatch warnt `llvm-symbolizer` / läuft rc≠0 → kein Ergebnis, kein
  stiller falscher Name.
- **Offener Punkt (nur mit Spieler prüfbar):** ein *frisch provozierter* Crash
  im laufenden Dedicated-Server, der automatisch durch den Dienst symbolisiert
  wird (Ende-zu-Ende Marker → Bundle → `symbolized.txt`). Ist in #480 als
  offener Punkt geführt.

## Test-Split

- **Ohne Player (CI + planet read-only):** `tests/shell/crash-symbolize.test.sh`
  (synthetischer Minidump + Fake-`llvm-symbolizer`) und ein Evidenz-Lauf gegen
  die bereits vorhandenen planet-Bundles.
- **Nur mit Player:** der frische, automatisch symbolisierte Crash (s. o.).
