# Screenshots — Issue #936 / PR #961 (Lobby: mehrere Clients auf einer Solo-Instanz)

Beleg für die neuen Lobby-Elemente der `kUiHtml`-Steuer-UI
(`tools/gns-proxy/gns_probe.cpp`):

- **Join-Button** — jeder Session-Karte neu hinzugefügt; löst
  `POST /solo {identitaet, instance}` aus und **joint einer bestehenden** Solo-Instanz
  (kein neuer Claim). Ohne eigene Instanz zeigt die Karte eine Instanz-Auswahl
  (Dropdown) über die bekannten Instanzen.
- **Instanz-Zeile** — `Instanz · n/max · Mitglieder` in jeder Karte mit
  `soloInstance` (`n` = `soloMemberCount`, `max` = `soloMaxPlayers`, Mitglieder =
  `soloMembers`). So ist auf einen Blick sichtbar, wer in welcher Instanz ist.

## Dateien

| Datei | Zeigt |
| --- | --- |
| `01-lobby-multi-client-join.png` | Lobby mit vier Sessions: **Spieler A** und **Spieler B** zeigen **dieselbe** Instanz `parked-1` (`2/4`, Mitglieder `str:a936, str:b936`) und den **join**-Button — man sieht beide Identitäten auf einer Instanz; **Spieler C** (noch keine Instanz) zeigt den join-Button mit **Instanz-Auswahl** (`parked-1 ▾`); **Spieler D** läuft auf `parked-2` (`1/4`, Phase `LÄUFT`). |

## Erzeugung

Die **unverändert** aus dem Branch extrahierte `kUiHtml`-Seite
(`R"HTML(…)HTML"` aus `gns_probe.cpp`) wurde mit headless Chromium (CLI,
`--headless=new --no-sandbox --disable-gpu --force-device-scale-factor=2
--virtual-time-budget=4000`) gegen einen minimalen lokalen Mock der Steuer-API
gerendert (Python `http.server`); Höhe per `convert -trim` an die Inhaltshöhe
angepasst.

Mock-Endpunkte:

- `GET /` → `kUiHtml` (unverändert aus `gns_probe.cpp` extrahiert)
- `GET /targets` → `[{name:"parked-1",…},{name:"parked-2",…}]`
- `GET /sessions` → vier Sessions; **A und B** mit `soloInstance:"parked-1"`,
  `soloMembers:["str:a936","str:b936"]`, `soloMemberCount:2`, `soloMaxPlayers:4`
  (dieselbe Instanz/Mitgliederliste), **C** ohne Instanz (Auswahl-Pfad), **D** auf
  `parked-2` (`soloMemberCount:1`, Phase `running`).

Keine Secrets/Tokens in Bild oder Dokument.

- Issue: #936 · PR: #961 · Branch: `feature/936-solo-multi-client`