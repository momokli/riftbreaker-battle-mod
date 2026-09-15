# Screenshots — Issue #520 (natives `pause_dom`/`resume_dom`)

Cockpit-Erweiterung im Panel *Mission Flow (Wave)*
(`bausteine/08-control-ui/cockpit.html`): neue Readout-Zeile **`dom paused`**
(`get_state.dom_paused`, nativer Read `LuaGraphNode::+0xF1`) und eine neue
Button-Zeile **`pause dom`** / **`resume dom`** (`POST /pause_dom` bzw.
`/resume_dom` → `LuaGraphNode::SetSuspended`).

- `01-cockpit-dom-full.png` — ganze Cockpit-Seite. Sichtbar im Mission-Flow-Panel:
  Readout-Zeile mit `dom paused no` (DOM laeuft) direkt neben `flow` / `active` /
  `payload.spawn_point`, darunter (Row 4) die nativen Buttons `pause dom` /
  `resume dom`.
- `02-dom-pause-section.png` — Ausschnitt des Mission-Flow-Panels **nach** dem
  Klick auf `pause dom`: `dom paused yes`. Belegt den echten Button-Pfad
  (`dom_pause.onclick` → `domSuspend("pause_dom")` → `$("dom_paused")`).

## Erzeugung

Erzeugt mit **headless Chromium** (`--headless=new --no-sandbox --disable-gpu
--force-device-scale-factor=2`, chromium-1223) gegen die **unveraenderte**
`bausteine/08-control-ui/cockpit.html`. Als Backend lief lokal ein Mock
(kein Dedicated Server noetig), der

- `POST /get_state` mit der dokumentierten Bridge-Antwort bedient
  (Ressourcen, `mission_flow*`, `mission_flow_payload{spawn_point}`,
  `creatures_base_difficulty: 1.5`, `dom_paused: false`),
- `GET /server/status` + `GET /server/logs?tail=N` bedient,
- `POST /pause_dom` mit `{"event":"pause_dom_result","ok":true,"paused":true,
  "readback":"ok","node":"0x7e1a16fd3500"}` beantwortet (und den Mock-Zustand
  `dom_paused=true` setzt).

Die Aufnahme `02` klickt den echten `dom_pause`-Button (der Mock haengt fuer die
Aufnahme-Datei genau EINEN Script-Block an, der
`document.getElementById('dom_pause').click()` ausfuehrt); die Repo-Datei
`cockpit.html` bleibt **unveraendert**. Der Readout-Wert stammt aus der
`get_state`-Antwort bzw. der `pause_dom`-Antwort, nicht aus einer UI-Konstante.

Kein Lua/DOM im Backend, keine Secrets.
