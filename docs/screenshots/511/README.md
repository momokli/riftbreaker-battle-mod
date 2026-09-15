# Screenshots — Issue #511 (HQ Health im Cockpit)

Neue Cockpit-Sektion *HQ Health* (`bausteine/08-control-ui/cockpit.html`): drei
Readouts (`hq_hp`, `hq_hp_max`, `hq_dead`) aus dem nativen C++-Read
(`POST /get_state`, Felder `hq_hp`/`hq_hp_max`/`hq_dead`).

- `01-cockpit-hq-health-full.png` — ganze Cockpit-Seite inkl. der neuen
  Sektion (zwischen *Creatures Base Difficulty* und *Natural Waves (Vanilla)*).
- `02-hq-health-section.png` — Ausschnitt der neuen Sektion mit gefüllten
  Readouts: `hp 850.5`, `hp_max 1,000`, `dead false`.

## Erzeugung

Erzeugt mit **headless Chromium** (chromium-1223, DevTools-Protokoll,
`deviceScaleFactor=2`, Viewport 900 × Inhaltshöhe) gegen die **unveränderte**
`bausteine/08-control-ui/cockpit.html` auf dem PR-Branch.

Als Backend lief lokal ein **Mock** (kein Dedicated Server nötig), der
`POST /get_state` mit der dokumentierten Bridge-Antwort bedient (Ressourcen,
`mission_flow*`, `creatures_base_difficulty`, `hq_hp: 850.5`,
`hq_hp_max: 1000.0`, `hq_dead: false`) sowie `GET /server/status` und
`GET /server/logs`.

**Abgrenzung:** Die Bilder belegen die **UI-/Frontend-Änderung** (neue Sektion,
Layout, Feldverdrahtung) — es sind **keine** Live-Werte aus dem Spiel. Der
Wert-Proof der vollen Kette (echtes HQ → `hq_hp > 0`, `hq_hp_max` == HUD,
`hq_dead` bei Zerstörung) ist ein **offener Player-Test** (Momo/Matheo), siehe
PR/Issue #511 und `docs/FULL_CHAIN_RULE.md`.

Kein Lua/DOM im Backend, keine Secrets.
