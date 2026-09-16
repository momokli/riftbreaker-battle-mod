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
25px-Controlhöhe, `tabular-nums` für alle Zahlen). Vier Gruppen, geschnitten nach
Änderungsrate — das Kriterium für einen Operator, der parallel im Spiel arbeitet
und die Seite nur scannt:

| Gruppe | Inhalt |
|---|---|
| `Resources` | Carbonium + Ironium als *eine* ausgerichtete Tabelle (Wert / Max / Menge / `+` / `−`) |
| `Mission Flow (Wave)` | Readouts (`flow`, `active`, `payload.spawn_point`) + Start- und Stop-Reihe |
| `Creatures Base Difficulty` | Readout + `set` / `+ increase` / `− decrease` |
| `Send Tracker (Carbonium)` | persistentes Send-Log (timestamp · resource · amount) + Summe/Anzahl, Filter, Quelle |
| `Server Control (Plane B)` | Status-Strip, Lifecycle-Toolbar, Container-Log |

Das **Container-Log** ist die einzige unbegrenzt wachsende, vertikal geformte
Inhaltstrommel: Es sitzt deshalb in einer rechten Spalte über die volle Höhe, mit
eigenem Scrollbereich, dessen Höhe nicht am Inhalt hängt — ein Log mit 300 Zeilen
verschiebt also keine Bedienelemente. Kein Auto-Scroll; geladen wird nur auf
Knopf (s. Konsum-Pfad 2).

Eine **Statusleiste** über die volle Breite (24px, Höhe reserviert) trägt die
beiden Meldungs-Slots `#out` (Bridge-/Kommando-Fehler) und
`#server_control_msg` (Server-Panel-Meldungen) — Fehler landen damit an einer
vorhersagbaren Stelle statt verstreut pro Panel.

Responsiv: eine Spalte unter 980px (Log dann max. 40vh, eigenes Scrollen), zwei
Spalten ab 980px (links gestapelte Gruppen, rechts Server + Log), drei Spalten ab
1400px.

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
| `get_state` | Ressourcen (`carbonium`/`ironium` + Max), `mission_flow`, `mission_flow_active`, `mission_flow_payload.spawn_point`, `creatures_base_difficulty`, `end_game`, `hq_hp`/`hq_hp_max`/`hq_dead` (nativ C++, #573/#511) |
| `add_resource` | Ressourcen addieren/subtrahieren (`{amount, resource?}`) |
| `activate_mission_flow` | Welle starten (`{logic, mode:"default"}`) |
| `deactivate_mission_flow` | Welle stoppen (`flow-id`, leer = zuletzt gestarteter) |
| `creatures_difficulty` | Kreaturen-Basis-Difficulty lesen/setzen/erhöhen/senken |
| `end_game` | Match-Ende setzen (`{result:"win"|"lose"}`) — Readout `end_game` aus `get_state` (`null` \| `{result,status}`) |
| `probe` | Bridge-/Pipe-Erreichbarkeit (API-Fläche der Bridge) |

`GET /health` der Bridge gehört ebenfalls zur API-Fläche. Details und
Verdrahtung: `docs/INGRESS_IO.md`, `server/README.md`.

**`hq_dead`-Semantik (#573/#511).** `hq_dead` ist eine Interface-Konvention,
kein Engine-Flag: `true` bedeutet `hq_hp <= 0` **oder** die HQ-Entity ist
verschwunden, nachdem sie in dieser Session schon mit `hq_hp > 0` gelesen
wurde (Entity-Verschwinden = zerstört). Vor dem ersten HQ-Leben liefert der
Read `null` (unbekannt ≠ tot); ebenso bleibt es `null`, solange kein HQ
gebaut ist (Namens-Lookup -> INVALID_ID, **kein** Health-Call). Welche
Abbildung die Engine tatsächlich nutzt (`hp == 0` vs. Entity-Entfernen),
prüft der Player-Test — offener Punkt in Issue #573.

### 2. Server-Control-Agent — Plane B (#424)

Panel `server control (plane B)`: Status, Logs und Lifecycle-Buttons. Absolute
Pfade auf derselben Origin (Bearer-Injektion macht die Caddy-Route):

| Route | Zweck |
|---|---|
| `GET /server/status` | `state`/`health`/`uptime`/`started_at` (Poll ~5 s) |
| `GET /server/logs?tail=N` | letzte N Log-Zeilen (N ≤ 5000, nur auf Knopf) |
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

Die Panels `server control (plane B)` und `send tracker` sind ohne
Netzwerk/DOM testbar: der jeweilige testbare Marker-Block wird aus
`cockpit.html` extrahiert und in einem `vm`-Kontext ausgewertet (Fake-`fetch`,
Fake-`document`, Fake-store/source):

```bash
cd tests/server-control-panel && npm test
cd tests/send-tracker && npm test
```

Geprüft wird das defensive Contract (immer `—`, nie werfen, nie
`location.reload`) und beim Send-Tracker zusätzlich Persistenz über den Store,
Filter-Query, Adapter-Kapselung und idempotentes Merge.

## Status

- [x] `cockpit.html` als eigener Baustein `08-control-ui` (#474, Schritt 1 — verhaltensneutral verschoben)
- [x] Panel `server control (plane B)` (#422) + Node-Test `tests/server-control-panel`
- [x] Qt-Stil-Layout: vier Gruppen, Log-Pane rechts, Statusleiste (#468)
- [x] Send-Tracker-Panel: persistentes Send-Log + austauschbarer Quell-Adapter (#527); Node-Test `tests/send-tracker`
- [ ] Send-Tracker an den finalen Transport anschließen (Adapter tauschen) — hängt an Spike #526
- [ ] Schritt 2 (#474): Caddy serviert die UI statisch, proxyt nur die API-Pfade; `cockpit_html.inc` entfällt
- [ ] Live-Daten des Plane-B-Panels brauchen gemergtes #424 (Agent + Caddy-Route `handle /server/*` + Bearer-Injektion)
