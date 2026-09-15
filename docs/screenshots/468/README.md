# Screenshots — Issue #468 (Cockpit-Redesign: Qt-Style-Chrome, responsive 1/2/3 Spalten)

Redesign des Operator-Cockpits (`bausteine/08-control-ui/cockpit.html`):
dunkles Grau-Chrome, ein Teal-Akzent (`--accent #3fd0d4`), vier
`fieldset`-Gruppen (_Resources_, _Mission Flow (Wave)_,
_Creatures Base Difficulty_, _Server Control (Plane B)_ inkl. Container-Log)
und eine vollbreite Statusbar am unteren Rand. Responsiv: **eine** Spalte unter
980px, **zwei** Spalten ab 980px (links die gestapelten Gruppen, rechts Server
Control + Log), **drei** Spalten ab 1400px.

- `01-cockpit-3col-1440.png` — ganze Seite, Viewport 1440x900. Drei Spalten:
  Spalte 1 `Resources` über `Creatures Base Difficulty`, Spalte 2
  `Mission Flow (Wave)`, Spalte 3 `Server Control (Plane B)` mit dem Log über die
  volle Höhe. Ressourcen-Zeilen `carbonium 12,480 / 20,000` und
  `ironium 3,160 / 5,000` aus dem Mock; Statusstrip `state running`,
  `health healthy`, `uptime 2h 14m 07s`, `started 2026-09-15T09:12:03Z`.
  Die Readout-Reihe der Mission-Flow-Gruppe bricht hier um: `flow` und `active`
  in Zeile 1, `payload.spawn_point base` in Zeile 2 — vollständig **innerhalb**
  des Panels (siehe „Hinweise zur Aufnahme").
- `02-cockpit-2col-1200.png` — ganze Seite, Viewport 1200x900. Zwei Spalten:
  links die drei Gruppen gestapelt, rechts `Server Control (Plane B)` mit dem
  Log-Pane. Zeigt das horizontale Clipping der Log-Zeilen (`white-space: pre`):
  die lange Telemetrie-Zeile wird am Pane-Rand abgeschnitten.
- `03-cockpit-1col-760.png` — ganze Seite, Viewport 760x1100. Eine Spalte: alle
  vier Gruppen untereinander. Das Log-Pane ist gedeckelt (`max-height: 40vh`) und
  scrollt intern — sichtbar daran, dass die Log-Zeilen am unteren Pane-Rand
  abgeschnitten werden, während die Controls darüber (`Restart server`/`Start`/
  `Stop`, `logs (tail)` + `refresh logs`) unverdrängt stehen. Hier passen alle
  drei Readouts in eine Zeile.
- `04-log-pane-statusbar.png` — Ausschnitt (740x710, `sips`-Crop ab Offset
  x=700/y=190) aus `01-cockpit-3col-1440.png`: das rechte Log-Pane mit den
  Mock-Zeilen (Docker-/Wine-Zeilen plus eine lange Telemetrie-Zeile) und die
  komplette Statusbar mit **beiden leeren Message-Slots** `#out` und
  `#server_control_msg` — die Leiste kollabiert nicht.

## Hinweise zur Aufnahme

- **Die Readout-Reihe der Mission-Flow-Gruppe bricht um.** Sie trägt seit #386
  drei Paare (`flow`, `active`, `payload.spawn_point`); ohne `flex-wrap` lief das
  dritte Paar bei 1440px und 1200px aus dem Panel heraus (Label mitten im Wort
  abgeschnitten, Wert hinter dem `Server Control`-Panel unsichtbar). Diese
  Aufnahmen zeigen den Stand **nach** dem Fix (`flex-wrap: wrap` auf
  `.flow-readouts`, `min-width: 0` auf `.flow-prop`, `overflow-wrap: anywhere`
  auf `.prop-val`) — der Wert steht jetzt in Zeile 2 innerhalb des Panels.
- **Die gestylten Thin-Scrollbars sind in den Aufnahmen nicht sichtbar.** Die
  Seite setzt `scrollbar-width: thin` und `::-webkit-scrollbar`; Chrome ignoriert
  bei gesetztem `scrollbar-width` die `::-webkit-scrollbar`-Regeln und nutzt
  native Overlay-Scrollbars (macOS), die erst beim Scrollen erscheinen. Gescrollt
  wird trotzdem (siehe `03-…`: der Log-Inhalt wird abgeschnitten).
- **Ein geteiltes Chrome-Profil verfälscht die Spaltenzahl.** Ein erster
  Aufnahme-Lauf mit _einem_ `--user-data-dir` für alle Viewports lieferte falsche
  Layouts (das Profil trug einen Zoom-Level zwischen den Läufen mit, sodass die
  CSS-Viewport-Breite nicht mehr zur Bildgröße passte: 1200px-Bild mit
  Ein-Spalten-Layout). Die Bilder hier stammen aus Läufen mit je **frischem**
  `--user-data-dir` und `--force-device-scale-factor=1`.

Erzeugt mit headless Chromium gegen die **unveränderte** `cockpit.html`
(`--headless=new`, 1:1-Viewport = Bildgröße, **ohne** `--hide-scrollbars`);
als Backend lief lokal ein Mock (kein Dedicated Server nötig), der auf einem
Origin die Seite selbst (`GET /`, byte-identisch, SHA-256
`9d22f29a…203bfe2`, 38771 Bytes) und die API-Routen bediente: `POST /get_state`
(dokumentierte Bridge-Antwort inkl. `creatures_base_difficulty: 1.5` und
`mission_flow_payload.spawn_point`), `GET /server/status`,
`GET /server/logs?tail=50` (30 Container-Zeilen) sowie `{"ok": true}` für alle
POST-Aktionen. Viewport-Größen: 1440x900, 1200x900, 760x1100
(`--virtual-time-budget=3000`).
