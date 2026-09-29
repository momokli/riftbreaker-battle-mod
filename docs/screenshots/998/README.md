# Screenshots — Issue #998 (Queue (vs) in der Relay-Lobby-UI)

Frontend-Nachweis für die in `tools/gns-proxy/gns_probe.cpp` (`kUiHtml`)
ergänzte Operator-UI: Queue-Badges am Session-Card + Button **[ Queue (vs) ]**
(`queueJoin(...)`, `POST /queue`) — Reviewer-Blocker 4 zu PR #1017.

Ohne laufenden Relay/Dedicated Server gerendert: `kUiHtml` aus dem Quelltext
extrahiert, mit einem Mock-Backend (`/targets`, `/sessions`) serviert und per
headless Chromium (`--force-device-scale-factor=2`) aufgenommen. Die Seite ist
die **echte** eingebettete UI (kein Nachbau); nur die Backends sind gemockt.
Aufgenommen am 2026-09-29, Branch `feature/998-queue-service`.

- `01-session-cards-queue.png` — Ausschnitt der beiden Session-Cards
  (2180x800 px). Links **IN QUEUE · Position 1** + Button `Queue (vs)`
  (`queuePhase:"queued"`, `queuePosition:1`); rechts **GEPAART · Match 1 · Welt B**
  + Button `Queue (vs)` (`queuePhase:"matched"`, `matchId:1`, `vsWorld:"B"`).
- `02-lobby-queue-full.png` — ganze Ansicht oben (2200x1560 px) mit Kontext:
  Modus-Kacheln, Spieler-Auswahl und aufgeklappter Bereich
  **Sessions / Diagnose** mit beiden Cards inkl. der Queue-Zeile.

Mock-Werte (nur Darstellung, keine echten Serverdaten; **keine Tokens**):
`str:aa`/`str:bb`, Provisioner-Endpoints `127.0.0.1:40000/40001`.

Hinweis: Die Badges (`IN QUEUE`, `GEPAART`) und der Button werden aus den
additiven `/sessions`-Feldern `queuePhase`/`queuePosition`/`matchId`/`vsWorld`
gerendert; ein Live-Klickpfad (`POST /queue` gegen einen laufenden
Queue-Dienst) ist hier nicht Teil des Belegs.
