# PDB-Symbol-Validierung — Lokalbefund (`riftbreaker_dll_win_release.pdb`)

Koordinations-Notiz zur Frage „wie nützlich sind die Debug-Symbols?". Von einem
Agent **lokal** validiert (macOS/CrossOver), **nicht** gegen planet
(`/srv/rbgame/bin/...`) geprüft.

Verfolgt in: **Issue #364**
(https://github.com/momokli/riftbreaker-battle-mod/issues/364).

## Quelle

- PDB: `~/Library/Application Support/CrossOver/Bottles/gams/drive_c/Program Files (x86)/The Riftbreaker/bin/riftbreaker_dll_win_release.pdb` (252 MB)
- DLL: gleicher Ordner, `riftbreaker_dll_win_release.dll` (77 MB)
- Tool: `llvm-pdbutil` / `llvm-readobj` (LLVM 23.1.1, via `brew install llvm`)

## Versionsabgleich (verifiziert)

PDB-GUID == DLL-CodeView-GUID — lokale PDB und DLL passen exakt zusammen:

- GUID `{843EFDEE-A997-4C52-BC9A-251E357DB35F}`, Age `1`
- Build-Zeitstempel `2026-04-02 16:18:32`
- PDB-Pfad im DLL-Debug-Verzeichnis:
  `C:\BuildAgent\work\53046f5bad88f7bd\riftbreaker\dev\build\win_vc17\bin\riftbreaker_dll_win_release.pdb`

## Kernbefund: KEIN volles PDB — Typinfo fehlt

`dump -summary` meldet zwar `Has Types: true`, aber:

- `dump -types` → **0 Typ-Records**
- Streams: TPI (2) und IPI (4) je **56 Byte = nur Header** (leer)
- `Is stripped: true`

→ **Keine Klassen-/Struct-Layouts, keine Member-Offsets, keine Enum-Werte.**

Konsequenz: die Beschreibung „volles privates PDB“ in `AGENTS.md` §9 ist für
diesen Build **nicht zutreffend**. Member-Offsets/Struct-Layouts müssen weiter
per RE ermittelt werden.

## Was das PDB trotzdem liefert (reichlich)

- Symbolnamen (mangled + demangled): Funktionen, Lambdas, Template-Instanzen,
  luabind-Registrierungen.
- Global-/Local-Proc-Refs + Data-Symbole (Adressen/RVAs ablesbar).
- Modul-/Quell-Info: unity-`*.obj`-Namen + voller BuildAgent-Quellpfad
  (Achtung: Unity-Build → Mapping auf einzelne `.cpp` fehlt; ebenso Zeilennummern).
- Section-Headers (RVA ↔ Section-Mapping).

## Publics / Globals / IDs (gemessen)

- **`dump -ids` → 0 Records** — der IPI-Stream ist ebenfalls leer (nicht „IDs
  vorhanden"). Ebenso `dump -types` → 0 Records. Beide Flags (`Has Types`/
  `Has IDs`) sind irreführend.
- **`dump -publics` ist reichhaltig** — massenhaft `S_PUB32` **mit Adressen**
  (nicht nur Export-Namen): `EntityService`/`MissionService`-luabind-Regs,
  RTTI `??_R0?AV...`, vtables `??_7...`, z. B. `FindEntityByGroup@FindService@Exor@@`.
  → Primärquelle für Symbole + RVAs.
- **Keine benannten Service-Singletons** (`g_EntityService`, `g_GameFramework`,
  `g_ConsoleService`) im Globals-Stream (nur `cfg_resourceManager*`). Services
  werden über `TypeRegistry`/RTTI aufgelöst (`LuaSystem::CreateService<T>()`,
  `TypeRegistry::GetType<T>()`) → bestätigt den RTTI-Walk-Ansatz in `rbbridge.c`;
  ein fester `g_ConsoleService`-Pointer existiert nicht.

## ConsoleService-Befund

- `ConsoleService`-Symbole in `dump -globals` sind **ausschließlich
  `S_LPROCREF`** — vtable `??_7ConsoleService@Exor@@6B@` und die echte
  `ConsoleService::ExecuteCommand` liegen als **modul-lokale** Symbole
  (`S_LDATA32`/`S_LPROC32`) in den Modul-Streams, **nicht** im Globals/Publics-
  Stream. RVA direkt aus dem PDB ziehen = per-Modul-`dump -symbols` nötig.
- Bekannte RVAs sind mit dem Section-Layout konsistent:
  - vtable `0x2F23C80` → `.rdata` (0x2DA2000–0x3EE7880) ✓
  - `ExecuteCommand` `0x1C0BEF0` → `.text` (0x1000–0x2DB0A93) ✓
- luabind-Registrierungen geben exakte Signaturen preis, u. a.:
  - `ConsoleService::Write(char const*)` → `void`
  - `ConsoleService::Write(char const*, luabind::adl::object)` → `void`
  - eine Methode `(char const*)` → `Exor::UtfString<char, ...>`
  - `RegisterCommand`, `Update`, `OnInit`, Ctor

## Befehle (reproduzierbar)

```sh
PDB="<...>/The Riftbreaker/bin/riftbreaker_dll_win_release.pdb"
llvm-pdbutil dump -summary "$PDB"
llvm-pdbutil dump -types "$PDB"            # 0 records
llvm-pdbutil dump -streams "$PDB"          # TPI/IPI je 56 Byte
llvm-pdbutil dump -globals "$PDB" | grep -i ConsoleService
llvm-pdbutil dump -section-headers "$PDB"
llvm-readobj --coff-debug-directory "<...>/riftbreaker_dll_win_release.dll"
```

## Offen / an RE-Agent

- planet-PDB (`/srv/rbgame/bin/...`) gegen diesen Lokalbefund per GUID prüfen —
  falls abweichend, ist der planet-Build ein anderer (RVAs dann neu).
- RVA von vtable + `ExecuteCommand` sauber aus den Modul-Streams extrahieren
  (per-Modul-`dump -symbols`), statt blind auf die gemerkten RVAs zu vertrauen.
