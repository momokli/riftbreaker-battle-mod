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

## Konsumierte Quellen

### 1. IO-Kanal — Baustein 04 (`pipe_bridge`, HTTP → `\\.\pipe\rbbattle`)

Relative Pfade auf derselben Origin; die Bridge übersetzt sie in
exec-Zeilen auf die rbbridge-Named-Pipe:

| Route (POST) | Zweck in der UI |
|---|---|
| `get_state` | Ressourcen (`carbonium`/`ironium` + Max), `mission_flow`, `mission_flow_active`, `mission_flow_payload.spawn_point`, `creatures_base_difficulty` |
| `add_resource` | Ressourcen addieren/subtrahieren (`{amount, resource?}`) |
| `activate_mission_flow` | Welle starten (`{logic, mode:"default"}`) |
| `deactivate_mission_flow` | Welle stoppen (`flow-id`, leer = zuletzt gestarteter) |
| `creatures_difficulty` | Kreaturen-Basis-Difficulty lesen/setzen/erhöhen/senken |
| `probe` | Bridge-/Pipe-Erreichbarkeit (API-Fläche der Bridge) |

`GET /health` der Bridge gehört ebenfalls zur API-Fläche. Details und
Verdrahtung: `docs/INGRESS_IO.md`, `bausteine/04-trainer-io/README.md`.

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

### 3. Später (offen)

Denkbar sind weitere Quellen (z. B. eine Datenbank / weitere Dienste). Sie
kommen als zusätzliche Konsum-Pfade dazu, ohne dass die UI selbst Logik
bekommt.

## Auslieferung (Stand: Schritt 1, #474)

Die UI wird **noch** in `pipe_bridge.exe` eingebettet: `scripts/gen_cockpit_html.py`
liest diese Datei und erzeugt `cockpit_html.inc` im Bridge-Ordner
(`bausteine/04-trainer-io/bridge/`, Build-Artefakt, gitignored, direkt neben
`pipe_bridge.c`), das die Bridge unter `GET /` ausliefert. **Einzige Quelle**
bleibt `cockpit.html` hier — es gibt keine handgepflegte Kopie.

**Schritt 2 (#474, Follow-up)** entkoppelt die Auslieferung: Caddy serviert die
Datei **statisch** (`file_server`) und proxyt nur die API-Pfade an
`pipe_bridge`; `cockpit_html.inc` entfällt dann. Bis dahin ist der Pfad hier
verhaltensneutral verschoben (dieser Baustein).

## Wie testen

Das Panel `server control (plane B)` ist ohne Netzwerk/DOM testbar: der
testbare Marker-Block wird aus `cockpit.html` extrahiert und in einem
`vm`-Kontext ausgewertet (Fake-`fetch` + Fake-`document`):

```bash
cd tests/server-control-panel && npm test
```

Geprüft wird das defensive Contract: immer `—`, nie werfen, nie
`location.reload`.

## Status

- [x] `cockpit.html` als eigener Baustein `08-control-ui` (#474, Schritt 1 — verhaltensneutral verschoben)
- [x] Panel `server control (plane B)` (#422) + Node-Test `tests/server-control-panel`
- [ ] Schritt 2 (#474): Caddy serviert die UI statisch, proxyt nur die API-Pfade; `cockpit_html.inc` entfällt
- [ ] Live-Daten des Plane-B-Panels brauchen gemergtes #424 (Agent + Caddy-Route `handle /server/*` + Bearer-Injektion)
