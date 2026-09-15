# Screenshots — Issue #476 (Natural Waves (Vanilla) im Cockpit)

Neue Cockpit-Sektion *Natural Waves (Vanilla)*
(`bausteine/08-control-ui/cockpit.html`): zwei Readouts (`waves`, `strength`),
drei Buttons (`off (next load)` / `on (next load)` / `status`) und eine
Hinweiszeile zur Runtime-Write-Semantik.

- `01-cockpit-natural-waves-full.png` — ganze Cockpit-Seite inkl. der neuen
  Sektion (zwischen *Creatures Base Difficulty* und *Server Control (Plane B)*).
- `02-natural-waves-section.png` — Ausschnitt der neuen Sektion mit belegtem
  Readout: `waves off`, `strength sandbox` (Antwort von
  `POST /natural_waves {"op":"status"}`).

## Erzeugung

Erzeugt mit **headless Chromium** (`--headless=new --no-sandbox --disable-gpu
--force-device-scale-factor=2`, chromium-1223) gegen die **unveränderte**
`bausteine/08-control-ui/cockpit.html`. Als Backend lief lokal ein Mock
(kein Dedicated Server nötig), der

- `POST /get_state` mit der dokumentierten Bridge-Antwort bedient
  (Ressourcen, `mission_flow*`, `creatures_base_difficulty: 1.5`),
- `GET /server/status` + `GET /server/logs` bedient,
- `POST /natural_waves {"op":"status"}` mit
  `{"ok":true,"readback":"ok","waves_disabled":true,"wave_strength":"sandbox",
  "mission_infinite":true,"difficulty":"sandbox"}` beantwortet.

Die Readouts der Sektion füllen sich erst beim Klick auf `status` (das ist die
reale Codepfad-Verdrahtung `natural_waves()` → `POST /natural_waves`). Da der
Chromium-CLI kein Klick zur Verfügung steht, injiziert der Mock für die
Aufnahme-Datei eine **einzige** zusätzliche Zeile, die genau diesen echten
Button klickt (`document.getElementById('nw_status').click()`). Die Repo-Datei
`cockpit.html` selbst wird **nicht** verändert; die Aufnahme zeigt also den
echten Rendering- und Request-Pfad.

Kein Lua/DOM im Backend, keine Secrets. Der Wert stammt aus der C++-Read-Antwort
(`wave_strength`), nicht aus einer UI-seitigen Konstante.
