# Screenshots — Issue #1031 (Öffentliche Match-View, `GET /match`)

Frontend-Nachweis für die in `tools/gns-proxy/gns_probe.cpp` (`kMatchHtml`)
eingebettete, **read-only** Zuschauer-Seite im GNS-Relay — Reviewer-Blocker 1
zu PR #1085 (AGENTS.md: bei Frontend-/UI-Änderungen Screenshots Pflicht).

Ohne laufenden Relay/Referee gerendert: das `kMatchHtml`-Raw-String-Literal aus
dem Quelltext extrahiert, mit einem Mock-Backend (`/referee/state`,
`/referee/events`) serviert und per headless Chromium
(`--force-device-scale-factor=2`, Fenster `1100x900`) aufgenommen. Die Seite ist
die **echte** eingebettete UI (kein Nachbau); nur die Backends sind gemockt.
Reproduktion (aus dem Repo-Wurzelverzeichnis):

    python3 docs/screenshots/1031/render.py

| Datei | `GET /referee/state` (`phase`/`paused`) | Sichtbare Besonderheit |
|---|---|---|
| `00-lobby.png` | `lobby`, beide `ready:false` | Kopf `Round 0`, Badge `LOBBY`, `NOT READY` |
| `01-ready.png` | `ready`, beide `ready:true` | Badge `READY`, je Seite ein `ready`-Event |
| `02-running.png` | `running`, `round:2` | HQ-Balken (A 100, B 68.4), Score/Wave/Sends, „Globale Events" |
| `03-paused.png` | `running` + `paused:true` | Badge `RUNNING · PAUSED`, Statusleiste `paused` |
| `04-offline.png` | HTTP 503 `{"ok":false,"reason":"referee_unconfigured"}` | Kopf durchgängig `-`, Statusleiste `offline (referee nicht konfiguriert)` |

Mock-Zustände (`docs/screenshots/1031/render.py`): Match `rift-1`, Mode `vs`,
Spieler `str:AB12` (A) / `str:CD34` (B); Feeds synthetisch. Der Cursor-Poll
`GET /referee/events?since=<seq>` liefert `{events:[…], last_seq:5}` und wird nur
abgerufen, sobald `/state.feed` ein numerisches `seq` geliefert hat (defensives
Gating, siehe `pollEvents`).

PNG-Format wie `docs/screenshots/1030/*.png` (PNG, 8-bit sRGB,
`--force-device-scale-factor=2`); Abmessung **2200x1800** (Fenster `1100x900`,
die Match-View braucht das breitere Zwei-Spalten-Layout, #1030 nutzte `900x1020`).

Mock-Werte rein synthetisch (`str:AB12`/`str:CD34`, `rift-1`) — **keine Tokens,
keine echten Serverdaten**. Der Read-only-Vertrag selbst ist durch den
CI-Schritt „Read-only-Nachweis Match-View (Issue #1031)" (Allow-List) belegt.
