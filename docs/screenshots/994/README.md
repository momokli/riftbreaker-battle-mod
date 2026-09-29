# Screenshots — Issue #994 (Lobby Main-Screen: Modi-Kacheln + Status-Badge)

Beleg für den Umbau der `kUiHtml`-Steuer-UI (`tools/gns-proxy/gns_probe.cpp`):
die Lobby wird vom „Session-Liste"-Tool zum **Main-Screen** mit Modi-Kacheln
zuerst, Provision-Trigger und einem einheitlichen Status-Badge.

## Dateien

| Datei | Zeigt |
| --- | --- |
| `01-lobby-before.png` | **Vorher** (`origin/main` @ `9ba5ca3`): Session-Liste zuerst — Karten je Identität mit `route`/`solo`/`self-send`/`join`/`READY` und `solo-<phase>`-Badges. |
| `02-lobby-after.png` | **Nachher**: Main-Screen mit den zwei Modi-Kacheln (`SOLO vs yourself` = `solo_self`, `SOLO vs persona:aggro` = `solo_persona:aggro`) und „Spieler hinschicken"-Trigger, Spieler-Auswahl über `/sessions` sowie dem einheitlichen Status-Badge (`LÄUFT`). Die Session-Liste ist sekundär in `<details>` „Sessions / Diagnose" (default zu). |

## Erzeugung

`render.py` (dieses Verzeichnis) extrahiert die `kUiHtml`-Seite zwischen
`R"HTML(` und `)HTML"` aus `gns_probe.cpp`, rendert sie in **headless Chromium**
(Playwright-Cache, `--no-sandbox`, `--force-device-scale-factor=2`) und stellt
dafür einen minimalen lokalen Mock der Steuer-API bereit:

- `GET /` → extrahierte `kUiHtml` (unverändert aus `gns_probe.cpp`)
- `GET /targets` → zwei Ziele
- `GET /sessions` → drei Sessions mit `soloPhase` = `running` / `underway` /
  `provisioned` (Badge-Demo) plus `state`/`pinned`
- `POST /solo` → 200-Stub

Für `01-lobby-before.png` stammt die UI aus `git show 9ba5ca3:tools/gns-proxy/gns_probe.cpp`
(Baseline vor #994); für `02-lobby-after.png` aus dem Arbeitsbaum.

```bash
python3 docs/screenshots/994/render.py
```

Keine Secrets/Tokens in Bild oder Dokument. Chromium wird über `CHROMIUM` (Env)
oder den Playwright-Cache (`~/.cache/ms-playwright`) gefunden.

- Issue: #994 · Branch: `feature/994-lobby-main-screen`
