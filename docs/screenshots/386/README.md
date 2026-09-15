# Screenshots — Issue #386 (Exor::Database-Payload / Mission-Flow)

Erweiterung der Cockpit-Sektion *mission flow (wave)* (`cockpit.html`):

- `01-cockpit-mission-flow-payload.png` — ganze Cockpit-Seite inkl. der
  neuen Payload-Zeilen. Sichtbar:
  - **Read**: `payload.spawn_point spawn_03` (aus `get_state`-Feld
    `mission_flow_payload{spawn_point}`, gelesen ueber
    `Database::GetString`).
  - **Write**: Eingabefeld `spawn_point (#386)` neben dem `start wave`-Button,
    das den Wert als `spawn_point` an `POST /activate_mission_flow` schickt.

Erzeugt mit headless Chromium (`--headless=new --no-sandbox`,
`--force-device-scale-factor=2`) gegen die **unveraenderte** `cockpit.html`.
Als Backend lief lokal ein Mock (kein Dedicated Server noetig), der auf
`POST /get_state` die dokumentierte Bridge-Antwort inkl.
`"mission_flow_payload": {"spawn_point": "spawn_03"}` liefert und
`/server/status` + `/server/logs` bedient.

Kein Lua/DOM im Backend: die Anzeige stammt aus dem C++-Payload-Read, der
Button ruft denselben nativen ActivateMissionFlow-Pfad wie #385 auf.
