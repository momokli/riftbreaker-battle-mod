# Dedicated IO Interface — Session-Handoff (RE READ-OUT)

Stand: 2026-09-13, Build 2.0.58485 (GOG == Dedi). Ziel: Issue **#363** — READ OUT
(voller Game-State) + PUSH IN über **EINEN** bidirektionalen DLL-Kanal
(`rbbridge.dll` + Named Pipe `\\.\pipe\rbbattle` + `pipe_bridge.exe`), kein
Log-Tailing.

## Wo steht der Code

| Branch | Inhalt |
|---|---|
| `feature/363-dedicated-io-interface` | 3 Commits: Probe v2, `POST /probe`, carbonium-Scan |
| `docs/361-architecture` | `docs/ARCHITECTURE.md` (Zwei-Welten-Mirror, #361) |

Relevante Dateien:
- `bausteine/04-trainer-io/rbbridge/rbbridge.c` — `probe_resources()`,
  `safe_read_u64()`, `dump_qwords()`, `scan_hash()`
- `bausteine/04-trainer-io/bridge/pipe_bridge.c` — `handle_probe()` (`POST /probe`)
- `docs/research/dedicated-io-re-findings.md` — RE-Offsets/Symbole
- `docs/research/pdb-symbol-validation.md` — PDB stripped (#364)

## Live-verifiziert (planet, 6321-Dedi, über `POST /probe`)

Der Probe-Roundtrip **funktioniert** (injizierte DLL liest Spielprozess und
antwortet als JSON über die Pipe/HTTP-Bridge). Kette bestätigt:

1. `PlayerService`-vftable **RVA `0x2E8E910`** — Instanz wird per vftable-Scan
   gefunden (`instance[0] == base + 0x2E8E910`). ASLR-safe (Basis aus
   `resolve_module`).
2. `PlayerService → [+8]` = **Resource-System** (Pointer).
3. Container ist **EMBEDDED** bei `resource_system + 0x30` (`lea`, **kein**
   Deref). ⚠️ Der erste Probe-Entwurf dereferenzierte fälschlich `*(rs+0x30)`
   — korrigiert.

## carbonium-Scan — aktueller Befund (wichtigster offener Punkt)

`scan_hash(hPipe, 0x659cc791, 24)` scannt lesbare Regionen nach dem
FNV-1a-Hash von `carbonium` (`0x659cc791`, verifiziert). Ergebnis live:

- **2 Treffer** in der **Ressourcen-DEFINITION-Tabelle** (Adressen ~100 Byte
  auseinander), `value` = große Pointer/Struct-Daten — **KEINE Beträge**.
- **KEIN Basket-Entry** (`hash, kleiner Betrag`) gefunden — auch nicht mit
  verbundenem Spieler bei **100/350 carbonium**.

### Hypothesen (zu klären)

1. `ResourceBasket` ist intern evtl. **keyed per `GameplayResourceType` (enum)**
   statt `StringHash` — Symbol `FlatMap<GameplayResourceType, ResourceBasket>`
   existiert. `GetResourceAmount(StringHash)` (0x2D3520) nutzt aber klar eine
   StringHash-keyed sortierte 16-B-Array → ggf. zwei verschiedene Strukturen.
2. `ResourceValue` ist kein einfacher `int64` (float / fixed-point / current+max).
3. Der per-Player-`ResourceAccount` (→ Basket) muss erst **über den Container
   gefunden** werden; der Container-Lookup ist `0x180C26E50` / `0x181DD06F0`
   (templatisierter FlatMap/HashMap-Code, noch nicht vollständig reverse).

## Bugs (bekannt, NICHT gefixt — nächster Agent)

1. **pipe_bridge `handle_probe` RACE:** bricht bei `got_probe && avail==0` sofort
   ab — aber `scan_hash` läuft **nach** den Dumps (synchron, ~100–500 ms) und
   schreibt `scan_hit` erst später. Folge: `scan_hit`-Events gehen **manchmal
   verloren** (1. Scan = 2 Treffer, 2. Scan = 0 Treffer, gleiche Speicherlage).
   **FIX:** `scan_hash` emittiert `scan_done`-Event (hits/regions/bytes), und
   `handle_probe` wartet auf `scan_done` statt sofort zu brechen.
2. **`scan_hash` Adress-Bug:** `%08x` + `(unsigned)` kürzt 64-bit-Adresse auf
   32-bit. **FIX:** `%llx` + `(unsigned long long)`.
3. **Stil:** `probe_resources`/`safe_read_u64`/`dump_qwords`/`scan_hash` sind in
   2-Space/K&R eingerückt (write_file hat den Fragment clang-formatiert), Rest
   der Datei ist 4-Space. Vor einem PR einmal angleichen.

## Konkrete nächste Schritte (Reihenfolge empfohlen)

1. **Bugs 1+2 fixen** (scan_done + `%llx`), neu bauen/stagen/restarten, erneut
   `POST /probe` → zuverlässige `scan_hit`/`scan_done`.
2. **Basket-Key klären:** Disasm `SetResourceAmount`/`AddResourceAmount`
   (RVA `0xF1E3D0`), `GetGlobalResourcesList` (`0xF28060`), Container-Lookup
   (`0x180C26E50`, `0x181DD06F0`) → Account→Basket-Offset + Key-Format.
3. **Wert-Format klären:** mit Spieler live `scan` + Dump des Basket-Arrays um
   den carbonium-Hash → aktueller Betrag (100) ablesen.
4. **A (sauber):** `get_state` als Request/Response in `rbbridge.c` (wie
   `exec`), `pipe_bridge`-Forward (`POST /get_state`), Tournament-Konsument.

## Tooling / Pfade / Deploy

- PDB/DLL byte-identisch auf **planet** `/srv/rbgame/bin/…` und **mac**
  `~/Library/Application Support/CrossOver/Bottles/gams/drive_c/Program Files (x86)/The Riftbreaker/bin/…`
  (77.885.440 / 252.334.080 Byte).
- `llvm-pdbutil`/`llvm-readobj` (mac + planet), `/opt/rb-re/venv` (pefile+capstone),
  `/tmp/disasm.py` (planet; Aufruf `disasm.py <RVA-0x1000 dez> <count>`).
- Build (planet, mingw): `x86_64-w64-mingw32-gcc -O2 -Wall -Wextra -Wl,--no-insert-timestamp -shared -o rbbridge.dll rbbridge.c`
  und `… -o pipe_bridge.exe pipe_bridge.c -lws2_32`.
- Stage: `sudo cp /tmp/rbbridge.dll /tmp/pipe_bridge.exe /opt/rbmods/rbtools/`
  → Restart: `sudo docker restart riftbreaker-dedicated` (Injection läuft beim
  Boot automatisch, ~2–3 min).
- Test: `curl -s -m 20 -X POST http://127.0.0.1:9001/probe` (vom planet-Host).

## ⚠️ WICHTIG — edit_file/write_file auf `.c` → clang-format-Kollaps

NIEMALS `edit_file`/`write_file` direkt auf `rbbridge.c`/`pipe_bridge.c` anwenden
(der Zed-Editor formatiert beim Schreiben um → Whitespace-Kollaps + Include-Reorder
→ kaputter Build, beobachtet bei rbbridge.c: 2167 Zeilen umformatiert, `#include
<psapi.h>` vor `<windows.h>` verschoben). **Immer:**
1. `git checkout -- <datei>` (zurück auf sauberen Stand),
2. Python-`str.replace`-Script (C-Code als Raw-String eingebettet, dann
   `io.open(path,"w").write()`) — kein clang-format,
3. auf planet mit mingw compilen, erst dann stagen/restarten.
