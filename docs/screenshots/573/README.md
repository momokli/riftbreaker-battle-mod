# Screenshots — Issue #573 (HQ Health im Cockpit)

Neues Cockpit-Panel *HQ Health* (`cockpit/cockpit.html`, Fieldset `#panel-hq`,
zwischen *Creatures Base Difficulty* und *Natural Waves (Vanilla)*): drei
Readouts (`hp`, `hp_max`, `dead`), verdrahtet in `refresh()` an die
`get_state`-Antwort des nativen C++-Reads (`hq_hp`/`hq_hp_max`/`hq_dead`).

Der native Read liefert **kein HQ** als `null` je Feld — das Panel zeigt dann
`—`; erst nach dem HQ-Bau stehen Zahlen an.

- `01-cockpit-hq-health-empty-full.png` — gesamte Cockpit-Seite mit Panel im
  Zustand **kein HQ** (`hq_hp`/`hq_hp_max`/`hq_dead` = `null`), Readouts `—`.
- `02-hq-health-empty-section.png` — Ausschnitt des Panels: `HP — HP_MAX — DEAD —`.
- `03-cockpit-hq-health-filled-full.png` — gesamte Cockpit-Seite mit Panel im
  Zustand **HQ gebaut** (`hq_hp: 4820`, `hq_hp_max: 5000`, `hq_dead: false`).
- `04-hq-health-filled-section.png` — Ausschnitt: `HP 4,820 HP_MAX 5,000 DEAD false`.

## Erzeugung

Erzeugt mit **headless Chromium** (`--headless=new --no-sandbox --disable-gpu
--hide-scrollbars --force-device-scale-factor=2`, chromium-1223) gegen die
**unveränderte** `cockpit/cockpit.html` (Viewport `1300x1935` → PNG `2600x3870`,
Layout bei ≥ 980 px Breite wie im Betrieb; Ausschnitt-Crops `1030x162`).

Als Backend lief lokal ein Mock (kein Dedicated Server nötig), der
`cockpit/cockpit.html` unverändert ausliefert und die vom Load gefeuerten
Endpunkte bedient:

- `POST get_state` mit der dokumentierten Bridge-Antwort (Ressourcen,
  `mission_flow*`, `players`, `creatures_base_difficulty`), je Aufnahme mit
  `hq_hp`/`hq_hp_max`/`hq_dead` = `null` (kein HQ) bzw. `4820`/`5000`/`false`
  (HQ gebaut),
- `GET /server/status` + `GET /server/logs`.

Die Werte stammen aus der C++-Read-Antwort (`get_state`), nicht aus einer
UI-seitigen Konstante. Kein Lua/DOM im Backend, keine Secrets.

Hinweis: Die beiden Zustände bilden das **defensive Gate** aus #573 ab — der
Read liefert bei fehlendem HQ `null` (kein falsches `dead`), erst mit HQ Zahlen.
