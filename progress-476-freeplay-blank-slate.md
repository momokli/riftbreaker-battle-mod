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
- Deploy: `riftbreaker_server_difficulty: sandbox` (defaults + host_vars planet).
- Doku: `docs/SERVER_SETTINGS.md` neu, RE-Abschnitt in
  `docs/research/dedicated-io-re-findings.md`.

## Test-Status

- Host-Test `tests/rbbridge-hosttest`: **PASS** (op-Parser, Signatur-
  Selbstkontrolle, Wildcard/E8, graceful).
- Tool-Build `scripts/build_rbbridge_tools.sh`: **PASS** (4 Binaries, mingw).
- Live (Game-Wert): **OFFEN** — dev-Stack crasht während Map-Gen nach der
  Injection (schon vorher `winedbg`-Crashes). Player-/Operator-Test nötig:
  Log ` sandbox mode on - pausing attacks.` + ≥10 min 0 Naturwellen +
  `natural_waves status` → `wave_strength":"sandbox"`.

## Offene Punkte

- Player-Test Momo/Matheo: sichtbare 0 Naturwellen über ≥10 min (DoD).
- C++-only Identifikation des `dom_mananger`-Instanz (für Freeze-Fallback)
  weiterhin ohne luabind-Ref nicht eindeutig (#446-Kontext).