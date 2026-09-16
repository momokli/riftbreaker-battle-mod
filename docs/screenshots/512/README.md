# Screenshots — Issue #512 (Spielerzahl `players` im Cockpit)

Frontend-Nachweis für die neue sichtbare Property-Zeile `players` im Panel
**Mission Flow (Wave)** (`bausteine/08-control-ui/cockpit.html`, PR #524).
Ohne laufenden Dedicated Server gerendert (Mock-Backend + headless Chromium,
`--force-device-scale-factor=2`), siehe Skill `riftbreaker-cockpit-screenshot`.

Aufgenommen am 2026-09-16, Branch `feat/512-player-count`.

- `01-mission-flow-before.png` — Ausschnitt (1120x230 px) der Readout-Zeile
  **vor** der Änderung (`93ff326^2`, d. h. `main`-Stand): `FLOW …` / `ACTIVE
  true` / `PAYLOAD.SPAWN_POINT base`. **Kein `PLAYERS`-Feld.**
- `02-mission-flow-after.png` — gleicher Ausschnitt **nach** der Änderung:
  zusätzlich `PLAYERS 1` (Mock `get_state` → `"players":1`). Der Wert stammt
  aus dem neuen nativen Read; im Mock wird er gesetzt, damit die Zeile belegt
  ist. **Der Live-Wert mit verbundenem Spieler ist noch offen** (siehe
  Offener Punkt im PR / Issue #512) — hier nur die Frontend-Darstellung.
- `03-cockpit-before-1440.png` — ganze Seite (Viewport 1440x900,
  2×-Skalierung → 2880x2400), **vor** der Änderung.
- `04-cockpit-after-1440.png` — ganze Seite, **nach** der Änderung.

Mock-Werte (nur Darstellung, keine echten Serverdaten):
`carbonium 12,480/20,000`, `ironium 3,160/5,000`,
`mission_flow logic/dom/attack_level_1_entry.logic`, `active true`,
`payload.spawn_point base`, `players 1`, `creatures_base_difficulty 1`.

Hinweis: Die ganze Seite füllt im 1440-Preset drei Spalten (Resources ·
Mission Flow · Server Control); `players` liegt in der Readout-Zeile des
mittleren Panels (Detail-Crops 01/02).
