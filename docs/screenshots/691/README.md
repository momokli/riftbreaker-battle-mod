# Screenshots — Issue #691 (Wave-Spawn-Mechanik Erklär-Seite)

Frontend-Nachweis für `docs/research/691-wave-mechanics-explainer.html` (PR #692) —
eine neue, eigenständige HTML-Seite (kein Server, kein Framework).
Gerendert mit headless Chromium (Playwright, `--force-device-scale-factor=2`), lokal von der Datei
(`file://`), kein Server nötig.

- `01-full-page-light.png` — komplette Seite, Viewport 1440x900, Light-Mode (Systemstandard).
- `02-full-page-dark.png` — komplette Seite, Viewport 1440x900, `prefers-color-scheme: dark`.
- `03-flowchart.png` — Abschnitt 01 (Flowchart der Wellen-Spawn-Logik).
- `04-pool-level9-expanded.png` — Abschnitt 05, Level-9-Pool-Tabelle mit allen `<details>`
  aufgeklappt (Cross-Level-Referenz-Zeilen sichtbar).
- `05-boss-pool-table.png` — Abschnitt 06, die volle 32-Eintrags-Boss-Tabelle.
- `06-l9-vs-l8-analysis.png` — Abschnitt 03 ("Warum Welle 9 härter ist"), inkl. Difficulty-Bonus-Chart.
