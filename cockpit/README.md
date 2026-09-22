# Baustein 08 — Control-UI (manuelles Backend / Operator-Cockpit)

**Was es ist:** Die Operator-Oberfläche (`cockpit.html`) — das **manuelle
Backend**. Sie kapselt **genau eine** Fähigkeit: einen Menschen die Aktionen
auslösen lassen, die sonst eine Automation fährt. Sie ist **Konsument** des
IO-Kanals (Baustein 04) und des Server-Control-Agents (Plane B, #424) — kein
Teil davon.

> **Richtung:** Was man hier klickt, wird später eine **verkettete Automation**
> aus dem Rust-Backend (#378, der „Captain") — die UI ist die manuelle
> Entsprechung derselben Aktionen, nicht der Ort der Logik.

## Rolle

Die UI ist **reines Frontend** (eine HTML-Datei, kein Build, keine
Dependencies). Sie hält **keine** eigene Zustandslogik: jeder Klick ist ein
HTTP-Aufruf an einen der beiden Konsum-Pfade unten, und die Anzeige ist die
Antwort darauf. Fällt eine Quelle aus, zeigt die UI `—` bzw. eine Fehlerzeile
und **lädt die Seite nie neu** (defensives Contract, s. Tests).

## Layout (Qt-Stil, #468)

Die Oberfläche ist als dichtes, flaches Qt-/QML-artiges Desktop-Werkzeug gebaut
(1px-Haarlinien, 3px-Radius, Gruppen als `fieldset` im QGroupBox-Stil, 4px-Raster,
25px-Controlhöhe, `tabular-nums` für alle Zahlen). Sechs **Tabs** trennen die
Änderungsrate bzw. die Rolle der Bereiche (#832):

| Tab | Inhalt |
|---|---|
| `Operator` | Resources (Carbonium/Ironium), HQ Health, Send Menu, Send Tracker, Attack Cycle — die aktiven Steuer-Dinge |
| `Game Config` | Game-Config-Editor (`mode` / `warmup_s` / Toggles) + `start` / `ready` |
| `Persona Editor` | Send-Profile editieren (CRUD), aktive Persona setzen |
| `Natural Attacks` | Natural-Attack-Rules (Difficulty 1-9, Attack-Count/Boss, Event-Offset) |
| `Docker` | Server-Control (Plane B): Status-Strip, Lifecycle-Toolbar, full-width Container-Log |
| `Advanced` | Low-Level-Debug: Mission Flow, Match End, Creatures Base Difficulty, Natural Waves (Vanilla), Round Reset |

Das **Container-Log** im `Docker`-Tab ist der einzige unbegrenzt wachsende Inhalt:
Es füllt die volle Tab-Breite **und** -Höhe (eigener Scrollbereich, dessen Höhe
nicht am Inhalt hängt — ein wachsender Log verschiebt also keine Bedienelemente)
und läuft **auto-tailed** (pollt alle 2s; scrollt nur ans Ende, wenn man schon
unten war). Kein Refresh-Klicken. Das Backend liefert pro Request nur ein
Tail-Fenster (`server_control.py`, `MAX_TAIL = 5000`, kein Cursor): das Cockpit
holt beim Öffnen das größte Fenster und **akkumuliert** danach nur die neuen
Zeilen (`mergeTail`), sodass der Log über die Request-Grenze hinaus wächst
(Session-Scope) statt auf ein Fenster beschränkt zu bleiben. Ein Zähler im Kopf
zeigt die akkumulierte Zeilenzahl.

**Lazy-Loading (#832):** nur der offene Tab pollt. Beim Tab-Wechsel werden die
Timer des verlassenen Tabs gestoppt und die des neuen gestartet; der Docker-Log
wird erst beim Öffnen des Docker-Tabs geladen (vorher kein `/server/*`-Poll).

Eine **Statusleiste** über die volle Breite (24px, Höhe reserviert) trägt die
beiden Meldungs-Slots `#out` (Bridge-/Kommando-Fehler) und
`#server_control_msg` (Server-Panel-Meldungen) — Fehler landen damit an einer
vorhersagbaren Stelle statt verstreut pro Panel.

Responsiv: einspaltig unter 980px; mehrspaltiges Karten-Raster (auto-fill) ab
980px; der `Docker`-Tab bleibt eine Spalte, das Log füllt die Höhe.

Der id-/JS-Vertrag ist unabhängig vom Layout: gleiche ids, gleiche Endpunkte und
Request-Bodies, gleiches Polling, weiterhin kein Reload — der Node-Test
`tests/server-control-panel` und das serverseitige C-String-Embedding hängen
genau daran.

## Konsumierte Quellen

### 1. IO-Kanal — Baustein 04 (`pipe_bridge`, HTTP → `\\.\pipe\rbbattle`)

Relative Pfade auf derselben Origin; die Bridge übersetzt sie in
exec-Zeilen auf die rbbridge-Named-Pipe:

| Route (POST) | Zweck in der UI |
|---|---|
| `get_state` | Ressourcen (`carbonium`/`ironium` + Max), `mission_flow`, `mission_flow_active`, `mission_flow_payload.spawn_point`, `creatures_base_difficulty`, `end_game` |
| `add_resource` | Ressourcen addieren/subtrahieren (`{amount, resource?}`) |
| `activate_mission_flow` | Welle starten (`{logic, mode:"default"}`) |
| `deactivate_mission_flow` | Welle stoppen (`flow-id`, leer = zuletzt gestarteter) |
| `creatures_difficulty` | Kreaturen-Basis-Difficulty lesen/setzen/erhöhen/senken |
| `end_game` | Match-Ende setzen (`{result:"win"|"lose"}`) — Readout `end_game` aus `get_state` (`null` \| `{result,status}`) |
| `probe` | Bridge-/Pipe-Erreichbarkeit (API-Fläche der Bridge) |

`GET /health` der Bridge gehört ebenfalls zur API-Fläche. Details und
Verdrahtung: `docs/INGRESS_IO.md`, `server/README.md`.

### 2. Server-Control-Agent — Plane B (#424)

Panel `server control (plane B)`: Status, Logs und Lifecycle-Buttons. Absolute
Pfade auf derselben Origin (Bearer-Injektion macht die Caddy-Route):

| Route | Zweck |
|---|---|
| `GET /server/status` | `state`/`health`/`uptime`/`started_at` (Poll ~5 s) |
| `GET /server/logs?tail=N` | letzte N Log-Zeilen (N ≤ 5000); im Docker-Tab auto-tailed gepollt und client-seitig akkumuliert |
| `POST /server/{restart,start,stop}` | Lifecycle (Body `{}`) |

Ist der Agent nicht erreichbar (HTTP !ok, Parse-Fehler), zeigt das Panel nur
`—` und meldet den Fehler in einer eigenen Statuszeile — **ohne Reload**.

### 3. Send-Signal — Send-Tracker (#527, offener Anschluss #526)

Panel `send tracker (carbonium)`: das Cockpit bekommt eine **persistente**
Send-Liste (timestamp + amount + resource) für den 1.0-Send-Loop (#517). Sie ist
**nicht transient**: Einträge liegen unter dem `localStorage`-Key `rbb.send_log`
und werden bei jedem Laden aus dem Store gelesen — ein Reload verliert sie
nicht. Zusätzlich ist das Log **querybar**: das Filterfeld blendet
Nicht-Treffer aus (Summe/Anzahl folgen dem Filter).

Die **Quelle ist gekapselt und austauschbar**: `createSendTracker({source})`
konsumiert nur ein Objekt mit `poll(cursor) -> {entries, cursor}`
(`{timestamp, resource, amount}`). Der Default-Adapter
`createBridgeSendSource()` ist **provisorisch** — er postet `get_send_log`
(analog zu den übrigen IO-Kanal-Routen) und ist **nicht** als endgültiger
Transport zu lesen: den legt der Spike [#526](https://github.com/momokli/riftbreaker-battle-mod/issues/526)
fest. Fällt/wirft die Quelle, bleibt das persistente Log sichtbar und die
Statuszeile meldet den Ausfall (kein Reload, kein Wurf).

> **Offener Anschluss (bewusst):** Bis #526 entschieden ist, antwortet die
> Bridge-Route `get_send_log` nicht; das Panel zeigt dann `—` und
> `kein Send-Source-Adapter (#526)`. Anschluss = **nur** den Adapter tauschen
> (`source`-Objekt), keine UI-Änderung.

### 4. Später (offen)

Denkbar sind weitere Quellen (z. B. eine Datenbank / weitere Dienste). Sie
kommen als zusätzliche Konsum-Pfade dazu, ohne dass die UI selbst Logik
bekommt.

## Auslieferung (Stand: Schritt 1, #474)

Die UI wird **noch** in `pipe_bridge.exe` eingebettet: `scripts/gen_cockpit_html.py`
liest diese Datei und erzeugt `cockpit_html.inc` im Bridge-Ordner
(`server/pipe-bridge/`, Build-Artefakt, gitignored, direkt neben
`pipe_bridge.c`), das die Bridge unter `GET /` ausliefert. **Einzige Quelle**
bleibt `cockpit.html` hier — es gibt keine handgepflegte Kopie.

**Schritt 2 (#474, Follow-up)** entkoppelt die Auslieferung: Caddy serviert die
Datei **statisch** (`file_server`) und proxyt nur die API-Pfade an
`pipe_bridge`; `cockpit_html.inc` entfällt dann. Bis dahin ist der Pfad hier
verhaltensneutral verschoben (dieser Baustein).

## Wie testen

Die testbaren Panels/Editoren sind ohne Netzwerk testbar: der jeweilige
Marker-Block wird aus `cockpit.html` extrahiert und in einem `vm`-Kontext
ausgewertet (Fake-`fetch`, Fake-`document`):

```bash
cd tests/server-control-panel && npm test
cd tests/self-send-tracker && npm test
cd tests/persona-editor && npm test
cd tests/natural-attack-editor && npm test
cd tests/game-config-editor && npm test
```

Geprüft wird das defensive Contract (immer `—`, nie werfen, nie
`location.reload`).

Zusätzlich rendert `tests/cockpit-render` die ganze UI im echten Chromium
(Playwright): Tab-Struktur + roving tabindex, dass Formulare rendern, dass der
Docker-Log auto-tailt (lazy geladen) und dass keine JS-Fehler auftreten. Ohne
Playwright/Chromium überspringt sich der Test selbst. Für Layout-/Optik-Review
per Screenshot siehe `tools/cockpit-ui-review/` (Skill `cockpit-ui-review`).

## Status

- [x] `cockpit.html` als eigener Baustein `08-control-ui` (#474, Schritt 1 — verhaltensneutral verschoben)
- [x] Panel `server control (plane B)` (#422) + Node-Test `tests/server-control-panel`
- [x] Qt-Stil-Layout: vier Gruppen, Log-Pane rechts, Statusleiste (#468)
- [x] Send-Tracker-Panel: persistentes Send-Log + austauschbarer Quell-Adapter (#527); Node-Test `tests/send-tracker`
- [x] Personas-Panel: Send-Profile editieren (#788); Node-Test `tests/persona-editor`
- [x] `send yourself` hat nur noch eine Quelle: Game-Config-Toggle (#851); Alt-Pfad `/send_yourself` + `/personas.send_yourself` stillgelegt
- [x] Cockpit-Refactor (#832): 6 Tabs (Operator/Game Config/Persona/Natural/Docker/Advanced), Docker-Log full-width + auto-tail, Lazy-Polling pro Tab; Render-Test `tests/cockpit-render`
- [ ] Send-Tracker an den finalen Transport anschließen (Adapter tauschen) — hängt an Spike #526
- [ ] Schritt 2 (#474): Caddy serviert die UI statisch, proxyt nur die API-Pfade; `cockpit_html.inc` entfällt
- [ ] Live-Daten des Plane-B-Panels brauchen gemergtes #424 (Agent + Caddy-Route `handle /server/*` + Bearer-Injektion)
