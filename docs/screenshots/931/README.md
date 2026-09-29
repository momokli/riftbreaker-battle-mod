# Screenshots — Issue #931 / PR #949 (Kapsel-Flow, US8)

Beleg für die neuen Cockpit-Elemente des Kapsel-Flows im **Game Config**-Tab:

- **`capsule: <phase>` Readout** (`#gc_capsule`) — zeigt `phase` · `instance` ·
  `cycle.state` aus `GET /capsule/status`; Fallback `capsule: —`, wenn kein
  Kapsel-Dienst konfiguriert/erreichbar ist.
- **`auto`-Button** (`#gc_auto`) — Operator-Override, gibt die Engine-Steuerung
  frei (`POST /capsule/auto` → Bridge `/pause_game {"op":"auto"}`).

## Dateien

| Datei | Zeigt |
| --- | --- |
| `01-game-config-capsule-readout.png` | Voller Game-Config-Tab, Kapsel-Row live: `auto`-Button + `capsule: running · parked-1 · cycle:running`. |
| `02-capsule-row-crop.png` | Geclippte Kapsel-Row (Detail): `auto`-Button + Readout im laufenden Zustand. |
| `03-capsule-fallback.png` | Geclippte Kapsel-Row mit Fallback: `capsule: —`, wenn `/capsule/status` 404 liefert (kein Dienst). |

## Erzeugung

Rendering des echten `cockpit/cockpit.html` in Chromium (Playwright, headless)
gegen den Mock aus `tools/cockpit-ui-review/shoot.js` (u. a. `/capsule/status`
mit `phase:"running"`, `instance:"parked-1"`, `cycle.state:"running"`).

```
cd tools/cockpit-ui-review && node shoot.js   # Mock-Basis + alle Tabs
```

Die drei fokussierten Shots wurden mit demselben Mock-Satz erzeugt
(Viewport 1440×1000, `deviceScaleFactor: 2`), Tab `Game Config`, Ausgabe nach
`docs/screenshots/931/`.

- Commit: siehe `git log -- docs/screenshots/931`
- Issue: #931 · PR: #949 · Branch: `feat/931-capsule-flow`
