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

## Assumptions — Ergebnis (alle bestätigt, LIVE am 6321-Dedi)

| #     | Annahme                                                            | Status                   |
| ----- | ------------------------------------------------------------------ | ------------------------ |
| **A** | `get_state` liest carbonium **live**                               | ✅ bewiesen              |
| **B** | carbonium **direkt schreiben** (C++, kein Lua)                     | ✅ **LIVE bewiesen**     |
| **C** | Write sofort in `get_state` sichtbar (gleicher Basket, kein Cache) | ✅ bewiesen (curl + HUD) |

**B im Detail (alle ✅, Stand 2026-09-13, Build 2.0.58485):**

- **B1 — Funktion:** `PlayerService::AddResourceAmount` RVA `0xF1E3D0`
  (`bool (unsigned int, UtfString const&, float, bool)`) — Disasm + Live-Call
  bestätigt. Der StringHash-Setter `ResourceBasket::SetResourceAmount(StringHash
const&, ResourceValue)` existiert ebenfalls (PDB `-publics`), wurde aber NICHT
  gebraucht — `AddResourceAmount` läuft sauber.
- **B2 — negativ = abziehen:** ✅. `entry.value += amount`; negatives Ergebnis
  wird auf 0 geklemmt (Disasm `0x1802d6890`). Live: `-10` → 110 → 100, `ret=true`.
- **B3 — Einheiten:** ✅ `float` in Carbonium-Einheiten (`10.0` = 10 Carbonium).
  Intern skaliert `AddResourceAmount` über eine `.data`-Globale:
  `basket_delta = (int64)((float)scale * amount)`. `scale` = Laufzeitwert bei
  RVA `0x4794210` (statischer Initialwert `0x03bde828`, zur Laufzeit `1000000`).
  Die Bridge liest `scale` zur Laufzeit und rechnet `amount = raw/scale` —
  damit ist die Einheiten-Frage robust gegen die Skala.
- **B4 — `UtfString "carbonium"`:** ✅. Layout (aus Disasm): `data@+8`
  (SSO wenn `capacity@+0x20 <= 15`), `size@+0x18`, `capacity@+0x20`; Offset `+0`
  wird nie gelesen. SSO-Instanz (40 Byte) auf dem Stack gebaut, Referenz übergeben.
- **B5 — `playerId`/`bool`:** ✅ `playerId = 0` (Spieler 1); `bool = true`
  wird an den Broadcast/HUD-Sync durchgereicht (macht die Änderung sichtbar).
- **B6 — Thread-Safety:** ✅ (empirisch). Aufruf im Pipe-Thread (wie
  `ExecuteCommand`); positiver + negativer Call + HUD-Sync liefen stabil, kein
  Crash.

## LIVE-BEWEIS (2026-09-13)

```
POST /add_resource {"amount":"10"}   -> {"ok":true,"raw":10000000,"scale":1000000,"ret":true}
get_state                            -> carbonium 100000000 -> 110000000 (+10)
POST /add_resource {"amount":"-10"}  -> carbonium 110000000 -> 100000000 (-10)
```

Oszillations-Loop (1/s): carbonium 100 → 90 → … → 10 → 0 → 10 → 0 → …
**in-game sichtbar bestätigt** (HUD pendelt 0 ↔ 10). Damit ist der bidirektionale
Kanal vollständig: READ (`get_state`) + WRITE (`add_resource`) über denselben
DLL-Kanal, ohne Lua/Log.

## Plan

1. RE: `AddResourceAmount` disassemblieren (`tools/re/disasm.py`) + UtfString-Layout
   - `SetResourceAmount`/StringHash-Setter via `llvm-pdbutil dump -publics`.
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
