# Screenshots — Issue #1024 (Referee-Bruecke in der Relay-Lobby-UI)

Frontend-Nachweis für die in `tools/gns-proxy/gns_probe.cpp` (`kUiHtml`)
ergänzte Operator-UI: neue Referee-Zeile am Session-Card (Phasen-/Sieger-Badge,
Welt-`<select>`, Button **[ READY (Referee) ]**, `refereeReady(...)` /
`POST /referee/ready`) — Reviewer-Blocker 1 zu PR #1050.

Ohne laufenden Relay/Referee gerendert: `kUiHtml` aus dem Quelltext extrahiert,
mit einem Mock-Backend (`/targets`, `/sessions`, `/queue/status`,
`/referee/state`) serviert und per headless Chromium
(`--force-device-scale-factor=2`) aufgenommen. Die Seite ist die **echte**
eingebettete UI (kein Nachbau); nur die Backends sind gemockt. Für den
unkonfigurierten Fall ruft die Demo nach jedem echten `loadSessions`-Render die
reale `hint()`-Funktion mit dem 503-Text auf (kein nachgebautes Markup).
Aufgenommen am 2026-10-01, Branch `feature/1024-referee-bridge`.

Reproduktion (aus dem Repo-Wurzelverzeichnis):

    python3 docs/screenshots/1024/render.py

- `01-referee-running.png` — Referee **konfiguriert**, Phase `Running`
  (`GET /referee/state` → `{"phase":"Running","both_ready":true}`): Badge
  **REF: LÄUFT**, Zusatz „beide ready", Welt-`<select>` (Welt A/B) und aktiver
  Button `READY (Referee)`.
- `02-referee-finished.png` — Referee **konfiguriert**, Phase `Finished` mit
  Sieger (`{"phase":"Finished","winner":"A"}`): Badge **REF: BEENDET**,
  Zusatz „Sieger A", Welt-`<select>` und aktiver Button `READY (Referee)`.
- `03-referee-unconfigured.png` — Referee **unkonfiguriert**
  (`GET /referee/state` → `503 referee_unconfigured`, `REF_SNAP === null`):
  `READY (Referee)` ist **deaktiviert** (ausgegraut) und der Hinweis
  **„Referee-Dienst nicht konfiguriert (503 referee_unconfigured)"** ist
  sichtbar.

Mock-Werte (nur Darstellung, keine echten Serverdaten; **keine Tokens**):
`str:AB12`/`str:CD34`, Instanzen `parked-1`/`parked-2`.

Hinweis: Badge/Button werden additiv aus `GET /referee/state` gerendert
(`REF_SNAP.phase`/`winner`/`both_ready`); ohne Config degradiert der Button
sichtbar. Ein Live-Klickpfad (`POST /referee/ready` gegen einen laufenden
Referee) ist hier nicht Teil des Belegs.
