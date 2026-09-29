# Screenshots — Issue #930 / PR #948 (Proxy-Lobby Solo-Button, US4)

Beleg für die neuen Lobby-Elemente der `kUiHtml`-Steuer-UI (`tools/gns-proxy/gns_probe.cpp`):

- **`[ solo ]`-Button** — löst `POST /solo {identitaet, self_send}` aus (Claim gegen den
  Parked-Pool + Auto-Pin je nach `self_send`).
- **`self-send on/off`-Toggle** — schaltet das `self_send`-Flag pro Identität um; Label
  zeigt den aktuellen Zustand (`self-send on` = Standard).
- **Phasen-Badges** — `solo-<phase>`: `PROVISIONIERT` / `SPIELER UNTERWEGS` /
  `IM SPIEL (PAUSED)` / `LÄUFT` (abgeleitet über `deriveSoloPhase` / `soloPhaseName`,
  via `/sessions` → `soloPhase`).

## Dateien

| Datei | Zeigt |
| --- | --- |
| `01-lobby-solo-self-send.png` | Lobby-Hauptansicht mit vier Session-Karten: `solo`-Button + `self-send on`-Toggle in jeder Karte, sowie alle vier Solo-Phasen-Badges (`PROVISIONIERT`, `SPIELER UNTERWEGS`, `IM SPIEL (PAUSED)`, `LÄUFT`) und `- pin`-Suffix bei gepinnten Claims. |

## Erzeugung

Die exakt aus dem Branch extrahierte `kUiHtml`-Seite wurde in headless Chromium
gerendert (CLI, `--no-sandbox`, `deviceScaleFactor 2`), gegen einen minimalen lokalen
Mock der Steuer-API:

- `GET /` → `kUiHtml` (unverändert aus `gns_probe.cpp` `R"HTML(…)HTML"` extrahiert)
- `GET /targets` → `[{name:"parked-1",…},{name:"parked-2",…}]`
- `GET /sessions` → vier Sessions mit `soloPhase` = `provisioned` / `underway` /
  `in_game_paused` / `running`, `pinned` true/false

Keine Secrets/Tokens in Bild oder Dokument.

- Issue: #930 · PR: #948 · Branch: `feat/930-proxy-solo-self-send`