# Dedicated IO Interface — Write-PoC (carbonium direkt schreiben, KEIN Lua)

## Ziel (PoC-Scope)

carbonium **live lesen UND direkt schreiben** — rein über den DLL-Kanal, **ohne
Lua-Mod, ohne Log-Tailing**. Konkret: Oszillation ±10 Carbonium pro Sekunde.

```
Loop (Backend/Agent): jede Sekunde
  get_state            → carbonium lesen
  carbonium > 0        → add_resource -10   („klauen")
  carbonium == 0       → add_resource +10   (zurückgeben)
Spieler sieht in-game: carbonium pendelt ±10
```

## Architektur (clean, KEIN Hybrid)

```
Backend → rbbridge.dll → C++-Funktion DIREKT  (z. B. PlayerService::AddResourceAmount)
        └─ kein ConsoleService/Lua-Command, kein Log
```

Lua-Mod ist für den IO-Kern ein **Umweg** (DLL → ConsoleService → Lua → C++).
Wir rufen die C++-Funktion direkt — gleiche Funktion, ein Hop weniger, kein
Lua-State, kein Log.

## Assumptions, die zu prüfen sind

| # | Annahme | Status |
|---|---|---|
| **A** | `get_state` liest carbonium **live** | ✅ bewiesen (100) |
| **B** | carbonium **direkt schreiben** (C++, kein Lua) | ⚠️ **offen — das hier** |
| **C** | Write sofort in `get_state` sichtbar (gleicher Basket, kein Cache) | ⚠️ hängt an B |

**B zerfällt in:**

- **B1 — Funktion:** Kandidat `PlayerService::AddResourceAmount`
  (RVA `0xF1E3D0`, `(unsigned int, UtfString const&, float, bool)`).
  Alternativ einen `StringHash`-basierten Setter suchen (vermeidet `UtfString`).
- **B2 — negativ = abziehen:** `AddResourceAmount(..., -10.0f, ...)` subtrahiert.
- **B3 — Einheiten:** `float` in **Carbonium-Einheiten** (`10.0` = 10 Carbonium),
  intern ×10⁶ → `10000000` (Basket ist fixed-point int64).
- **B4 — `UtfString "carbonium"` konstruieren:** C++-ABI / SSO-Layout klären,
  Referenz übergeben.
- **B5 — `playerId`/`bool`:** `0` = Spieler 1; was macht das `bool`?
- **B6 — Thread-Safety:** Aufruf vom Pipe-Thread (wie `ExecuteCommand`).

## Plan

1. RE: `AddResourceAmount` disassemblieren (`tools/re/disasm.py`) + UtfString-Layout
   + `SetResourceAmount`/StringHash-Setter via `llvm-pdbutil dump -publics`.
2. `add_resource <amount>` in `rbbridge.c` bauen (analog `get_state`), `pipe_bridge`
   um `POST /add_resource` erweitern.
3. Loop live testen (curl-Loop: get_state + add_resource ±10/s).
4. Validieren: carbonium oszilliert in-game sichtbar.

## Tooling / Pfade

- PDB/DLL: planet `/srv/rbgame/bin/…` oder Mac-CrossOver (byte-identisch).
- `~/.venvs/rb-re` (capstone+pefile), `tools/re/disasm.py`, `llvm-pdbutil`
  (Mac: `/opt/homebrew/opt/llvm/bin/`).
- Build/Stage/Restart/Test: siehe `docs/research/io-re-session-handoff.md`.
- ⚠️ NIE `edit_file`/`write_file` auf `.c` (clang-format-Kollaps) — `str.replace`-Script.

## Referenz

- Milestone #363 · API-Routen #366 · read-Blöcker (gelöst) #365
- Read-Grundlage: Branch `feature/363-dedicated-io-interface` (`get_state`, `probe`,
  Account-Basket-Dump)
- Docs: `docs/research/dedicated-io-re-findings.md`, `io-re-session-handoff.md`
