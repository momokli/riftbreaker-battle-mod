# Screenshots — Issue #388 (CampaignService Creatures-Base-Difficulty)

Neue Cockpit-Sektion *creatures base difficulty* (`cockpit.html`).

- `01-cockpit-full.png` — ganze Cockpit-Seite inkl. der neuen Sektion.
- `02-creatures-base-difficulty-section.png` — Ausschnitt der neuen Sektion
  (Read-Anzeige `now 1.5` + `set` / `+ increase` / `− decrease`).

Erzeugt mit headless Chromium gegen die **unveränderte** `cockpit.html`; als
Backend lief lokal ein Mock (kein Dedicated Server nötig), der auf
`POST /get_state` die dokumentierte Bridge-Antwort inkl.
`"creatures_base_difficulty": 1.5` liefert.
