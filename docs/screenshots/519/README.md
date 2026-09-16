# Screenshots — Issue #519 (end_game nativ: Win/Lose im Cockpit)

Neue Cockpit-Sektion *Match End (Win/Lose)* (`bausteine/08-control-ui/cockpit.html`):
Readout `last end` (aus `get_state`-Feld `end_game`) und die beiden Buttons
`win` / `lose`, die `POST /end_game {"result":"win"|"lose"}` auslösen — nativ via
`MissionService::FinishCurrentMission` (MISSION_STATUS_WIN/LOSE), kein Lua/Console.

- `01-cockpit-end-game-full.png` — ganze Cockpit-Seite mit der neuen Sektion
  zwischen *Mission Flow (Wave)* und *Creatures Base Difficulty*.
- `02-end-game-section.png` — Ausschnitt der neuen Sektion mit belegtem Readout:
  `last end lose` (Antwort von `POST /end_game {"result":"lose"}` →
  `{"event":"end_game_result","ok":true,"result":"lose","status":1}`, Readback in
  `get_state.end_game`).

## Erzeugung

Erzeugt mit **headless Chromium** (`--headless=new --no-sandbox --disable-gpu
--hide-scrollbars --force-device-scale-factor=2 --window-size=900,880`,
chromium-1223) gegen die **unveränderte** `bausteine/08-control-ui/cockpit.html`.
Als Backend lief lokal ein Mock (kein Dedicated Server nötig), der

- `GET /` mit der Cockpit-Seite bedient,
- `GET /server/status` + `GET /server/logs` bedient,
- `POST /get_state` mit der dokumentierten Bridge-Antwort bedient (Ressourcen,
  `mission_flow*`, `creatures_base_difficulty: 1.5`, `end_game: null`),
- `POST /end_game {"result":"win"|"lose"}` mit
  `{"event":"end_game_result","ok":true,"result":...,"status":0|1}` beantwortet und
  den Wert danach in `get_state.end_game` zurückliefert.

Da der Chromium-CLI kein Klick zur Verfügung steht, injiziert der Mock in die
**Aufnahme-HTML** eine einzige zusätzliche Zeile, die den **echten** Button klickt
(`document.getElementById('end_lose').click()`). Die Repo-Datei `cockpit.html`
selbst wird **nicht** verändert; die Aufnahme zeigt den echten Rendering- und
Request-Pfad.

Kein Lua/DOM im Backend, keine Secrets. Der Wert stammt aus der C++-Read-Antwort
(`end_game.result`), nicht aus einer UI-seitigen Konstante.

> Hinweis: Die sichtbare Wirkung (Match endet tatsächlich mit Win/Lose, End-Screen)
> ist nur mit verbundenem Player prüfbar und bleibt offener Punkt (#519 DoD).
