# Screenshots — Issue #1000 (Lobby: Queue-Button + Status)

Frontend-Nachweis für die in `tools/gns-proxy/gns_probe.cpp` (`kUiHtml`)
veredelte Operator-UI: die Queue-Status-Kette **`In Queue … (X warten)` →
`Match gefunden` → `provisioniert` → `läuft`**, der Wartenden-Zähler aus
`GET /queue/status` und der Abbruch-Button **[ Queue verlassen ]**
(`POST /queue/leave`).

Ohne laufenden Relay/Queue-Dienst gerendert: `kUiHtml` aus dem Quelltext
extrahiert (Slice `R"HTML(`…`)HTML"`), mit einem Mock-Backend (`/`, `/targets`,
`/sessions`, **`/queue/status`**) serviert und per headless Chromium
(`--force-device-scale-factor=2`, `--virtual-time-budget=4000`) aufgenommen.
Reproduzierbar über `python3 docs/screenshots/1000/render.py`.

Die Seite ist die **echte** eingebettete UI (kein Nachbau); nur die Backends
sind gemockt. Einzige Anpassung am HTML: `<details class="diag">` (Sessions /
Diagnose, in dem die Cards liegen) wird für die Aufnahme aufgeklappt
(`open`-Attribut), sonst wären die Cards nicht sichtbar.

## Dateien

| PNG | Phase | Label |
|---|---|---|
| `01-queued.png` | `queued` | **IN QUEUE … (2 WARTEN)** · Position 1 · Button `Queue verlassen` |
| `02-matched.png` | `matched` | **MATCH GEFUNDEN** · Match 1 · Welt A |
| `03-provisioning.png` | `provisioning` | **PROVISIONIERT** · Match 1 |
| `04-ready.png` | `ready` | **LÄUFT** · Match 1 |
| `05-lobby-full.png` | gemischt | Gesamtansicht: Modi-Kacheln, Spielerauswahl, Cards (1 wartend / 1 läuft) |

## Mock-Werte (nur Darstellung, keine echten Serverdaten; **keine Tokens**)

- Identitäten `str:AB12` (momo) / `str:CD34` (gast); IPs `10.0.0.5`.
- Provisioner-Endpoints `127.0.0.1:32768/32769` (Instanzen `parked-1/2`).

## Phase-Herkunft (D3)

Die Phase wird bevorzugt **live** aus `GET /queue/status` abgeleitet: zuerst
Match-Zuordnung (`matches[].participants[].identitaet` → Phase aus
`match.state`), sonst Queue-Treffer (`queue[]` → `queued`), sonst Fallback
`/sessions.queuePhase`.

- `01-queued`, `05-lobby-full`: `/queue/status.queue` enthält die Identität,
  `queued:2` bzw. `1` → Zähler „(X warten)“; `Leave`-Button nur in dieser Phase.
- `03-provisioning`, `04-ready`: aus `matches[].state` (`provisioning` bzw.
  `ready`).
- `02-matched`: **Fallback-Pfad** — der Relay meldet `queuePhase:"matched"`,
  während der Dienst-Snapshot den Match noch nicht führt; die UI zeigt die
  Fallback-Phase `matched`.

Hinweis: `ready` bedeutet serverseitig „A/B provisioniert + Lobby registriert“;
auf Wunsch des Reviewers ist der strengere `läuft`-Moment über
`/sessions.soloPhase ∈ {running, in_game_paused}` ableitbar (siehe Plan D4).
