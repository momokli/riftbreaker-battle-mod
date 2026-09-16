# Screenshots — Issue #516 (nativer Round-Reset im Cockpit)

Neues Cockpit-Panel *Round Reset (native)* (`cockpit/cockpit.html`,
Fieldset `#panel-round`): zwei Readouts (`pending flag`, `flag offset`), zwei
Buttons (`reset round` / `status`) und die Hinweiszeile zur nativen
Write-Semantik (`RequestRestart()` → Pending-Flag → Map-Restart auf dem
Game-Thread, Slot `0x90`).

- `01-cockpit-round-reset-full.png` — gesamte Cockpit-Seite inkl. des neuen
  Panels (zwischen *Natural Waves (Vanilla)* und dem Server-Control-Bereich).
- `02-round-reset-section.png` — Ausschnitt des neuen Panels mit belegten
  Readouts: `pending flag yes`, `flag offset 0x52a` (Antwort von
  `POST /restart_map {"op":"status"}`).

## Erzeugung

Erzeugt mit **headless Chromium** (`--headless=new --no-sandbox --disable-gpu
--hide-scrollbars --force-device-scale-factor=2`, chromium-1223) gegen die
**unveränderte** `cockpit/cockpit.html` (Viewport
`1300x1935` → PNG `2600x3870`; Layout bei ≥ 980 px Breite, wie im Betrieb).
Als Backend lief lokal ein Mock (kein Dedicated Server nötig), der

- `GET /` mit dem unveränderten Cockpit-HTML bedient,
- `POST /get_state` mit der dokumentierten Bridge-Antwort
  (`carbonium`/`ironium` + Max, `mission_flow*`,
  `creatures_base_difficulty: 1.5`),
- `GET /server/status` + `GET /server/logs` bedient,
- `POST /restart_map` mit der echten Read/Write-Antwort des nativen Panels
  (`{"event":"restart_map_result","ok":true,"op":"status",
  "flag_offset":"0x52a","restart_pending":true,"vtable":"0x…",
  "instance":"0x…"}`) beantwortet.

Die Readouts der Sektion füllen sich erst beim Klick auf `status` (das ist die
reale Codepfad-Verdrahtung `roundReset("status")` → `POST /restart_map`). Da
der Chromium-CLI kein Klick zur Verfügung steht, injiziert der Mock für die
Aufnahme-Datei eine **einzige** zusätzliche Zeile, die genau diesen echten
Button klickt (`document.getElementById('rr_status').click()`). Die Repo-Datei
`cockpit.html` selbst wird **nicht** verändert; die Aufnahme zeigt also den
echten Rendering- und Request-Pfad.

Kein Lua/DOM im Backend, keine Secrets. Die Werte stammen aus der C++-Read-
Antwort (`flag_offset`/`restart_pending`), nicht aus einer UI-seitigen
Konstante. Hinweis: `restart_pending` ist eine Momentaufnahme (Race mit dem
Game-Thread) — siehe `dispatch_restart_map` in
`server/dll/rbbridge.c` und
`docs/research/native-round-reset.md`.
