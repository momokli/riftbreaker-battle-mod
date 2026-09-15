# progress-476-freeplay-blank-slate

Issue #476 — Free Play als Blank Slate: Vanilla-Naturwellen aus, Takt nur aus
unserer State-Machine.

## RE-Ergebnis (planet, Build 2.0.58485, 2026-09-15)

- **Der "aus"-Schalter:** `DifficultyDef "sandbox"`
  (`wave_strength "sandbox"`, `mission_infinite 1`, `mission_duration 0`) →
  `dom_manager` setzt `pauseAttacks = true` → Spawner überspringt
  `PrepareWave`/`Streaming` → **0 Naturwellen** (kein Freeze).
  Deklarativ: `set difficulty "sandbox"`.
- **SetSuspended** (RVA 0x1BA6CB0, `mov [rcx+0xF1],dl; ret`) = **Freeze**
  (Update kehrt sofort zurück), NICHT "aus".
- **DifficultyService-Layout:** `+0x08` World\* → System (`0x221d7af2`):
  `+0x08` difficulty-name, `+0x118` wave_strength, `+0x1B9` mission_infinite,
  **`+0x1BA` waves_disabled** (`AreWavesDisabled`).

## Umsetzung

- `rbbridge.c`: `natural_waves` (status|off|on), RTTI-Resolver +
  AOB-Signatur `RBBRIDGE_DIFFSYS_GET_SIG`, safe_read/write_u8, graceful.
- `pipe_bridge.c`: `POST /natural_waves`.
- `cockpit.html`: Sektion *Natural Waves (Vanilla)* (Read + Buttons).
- Doku: `docs/SERVER_SETTINGS.md` neu, RE-Abschnitt in
  `docs/research/dedicated-io-re-findings.md`.

## Rework (PR #478, REQUEST_CHANGES)

- **Blocker 1 (Screenshots):** `docs/screenshots/476/` mit Ganzseiten- und
  Sektions-Aufnahme der neuen Cockpit-Sektion; PR-Body ergänzt.
- **Blocker 2 (Full-Chain):** Live-Default-Flip
  (`deploy/inventory/host_vars/planet/vars.yml` → `sandbox`) aus dem PR
  **de-scoped** (planet bleibt `coop_normal`); Player-/Operator-Test als
  Follow-up, nicht als erledigt markiert.
- **Review-Findings:** UI-Label `off/on (next load)` + Tooltip + Hinweiszeile;
  Getter-Aufruf aus dem Pipe-Thread als Annahme markiert (Code + Doku);
  Write-/Read-Fehlerpfad getrennt (`write_failed` vs. `readback:"failed"`);
  `dom_mananger` = Lua-Klasse / `dom_manager.lua` = Datei dokumentiert;
  Newline am Dateiende; Rollen-Default-Nebenwirkung dokumentiert (Default =
  bisheriges Ist-Verhalten).

## Test-Status

- Host-Test `tests/rbbridge-hosttest`: **PASS** (op-Parser, Signatur-
  Selbstkontrolle, Wildcard/E8, graceful).
- Tool-Build `scripts/build_rbbridge_tools.sh`: **PASS** (4 Binaries, mingw).
- WebUI: **Screenshot-Beleg** `docs/screenshots/476/` (Mock-Backend, headless
  Chromium), keine Live-Abhängigkeit.
- Live (Game-Wert): **OFFEN** — Player-/Operator-Test nötig: Log
  ` sandbox mode on - pausing attacks.` + ≥10 min 0 Naturwellen +
  `natural_waves status` → `wave_strength":"sandbox"`. Gehört zum separat
  gelandeten Live-Flip; der Crash `MapGenerator.cpp:976` ist laut Maintainer
  (#478) ein Readiness-Problem aus `get_state`-Polling, nicht diese DLL (#479).

## Offene Punkte

- Player-Test Momo/Matheo: sichtbare 0 Naturwellen über ≥10 min (DoD) — gilt für
  den **separat** zu landenden Live-Default-Flip.
- C++-only Identifikation des `dom_mananger`-Instanz (für Freeze-Fallback)
  weiterhin ohne luabind-Ref nicht eindeutig (#446-Kontext).
