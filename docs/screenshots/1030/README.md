# Screenshots — Issue #1030 (Lobby-Rematch: „Rematch"-Button in der Referee-Zeile)

Frontend-Nachweis für die in `tools/gns-proxy/gns_probe.cpp` (`kUiHtml`)
ergänzte Lobby-UI: neuer Button **[ Rematch ]** in der Referee-Zeile (`card()`,
`queueRematch(...)` → `POST /queue/rematch`) — Reviewer-Blocker 2 zu PR #1058
(AGENTS.md: bei Frontend-/UI-Änderungen Screenshots Pflicht).

Ohne laufenden Relay/Referee gerendert: `kUiHtml` aus dem Quelltext extrahiert, mit
einem Mock-Backend (`/targets`, `/sessions`, `/queue/status`, `/referee/state`)
serviert und per headless Chromium (`--force-device-scale-factor=2`) aufgenommen.
Die Seite ist die **echte** eingebettete UI (kein Nachbau); nur die Backends sind
gemockt. Reproduktion (aus dem Repo-Wurzelverzeichnis):

    python3 docs/screenshots/1030/render.py

| Datei | Quelle | Referee-Zeile (`GET /referee/state`) |
|---|---|---|
| `00-referee-finished-before.png` | Basis `origin/main:tools/gns-proxy/gns_probe.cpp` | `phase=finished` → **ohne** Rematch-Button |
| `01-referee-finished-rematch.png` | Arbeitsbaum (dieser PR) | `phase=finished` → **mit** aktivem **[ Rematch ]** |

Zustand: Referee `phase=f"finished"` (der StateView serialisiert Phasen lowercase,
`tournament/src/state.rs` `Phase::as_str`), Sieger `A`, `both_ready=true`; beide
Spieler kennen ihre Match-ID aus `/sessions` (`matchId=7`). Damit greift die echte
`card()`-Bedingung `REF_SNAP.phase === "finished" && refMid`.

**Layout-Fix im selben Rework:** die erste Aufnahme zeigte den neuen Button am
rechten Kartenrand **abgeschnitten** (die Referee-Zeile nutzt `.solo`, `display:flex`
**ohne** `flex-wrap`). `.solo` bekam `flex-wrap:wrap`; jetzt bricht die Zeile sauber
um (`REF: FINISHED · Sieger A · beide ready · Welt A` / darunter `READY (Referee)` +
`Rematch`) und der Button ist vollständig sichtbar.

Mock-Werte rein synthetisch (`str:AB12`/`str:CD34`, `matchId=7`) — **keine Tokens,
keine echten Serverdaten**. Der Klickpfad (`POST /queue/rematch`) selbst ist nicht
Teil des Belegs (durch E2E `deploy/queue/e2e_1030_rematch.py` gedeckt).
