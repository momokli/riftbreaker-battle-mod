---
name: cockpit-ui-review
description: Testet und reviewt das Riftbreaker-Cockpit (cockpit/cockpit.html) — echtes Browser-Rendering via Playwright/Chromium, Screenshots je Tab, Layout-Heuristiken (Overflow/abgeschnittene Texte/kleine Klickziele) und die Format-Falle beim Editieren der Datei. Nutze bei jeder Cockpit-UI-Änderung vor dem PR.
---

# Cockpit-UI-Review

Das Cockpit (`cockpit/cockpit.html`) ist eine **Single-File-Web-UI** (Tabs + inline JS). Unit-Tests (jsdom) prüfen *Funktion*, aber nicht *Aussehen*. Dieses Skill ist der Test-/Review-Workflow für jede UI-Änderung.

## Wann nutzen

Bei jeder Änderung an `cockpit/cockpit.html` (neuer Tab, neues Panel, Layout, Log-Ansicht) — **vor** dem PR.

## 1. Format-Falle (HART — sonst zerstörst du die Datei)

`cockpit/cockpit.html` ist groß und wird von einem Editor-Formatter **zerstört**, sobald die Datei ganz gespeichert/neu-formatiert wird: hunderte Deletions, unlesbarer Diff.

- NUR **exakte String-Replacements**: kleine `edit_file`-Edits mit engem Kontext ODER ein Wegwerf-Skript mit `io.open(path, "w", encoding="utf-8")` (danach löschen).
- Nach JEDER Änderung: `git --no-pager diff --stat cockpit/cockpit.html`. Die **Deletions müssen klein** bleiben (nur deine eigenen Zeilen). Bei Massen-Deletions sofort `git checkout cockpit/cockpit.html` und erneut sorgfältig.

## 2. Funktion prüfen (Unit + Render)

- **Marker-Block-Unit-Test:** Die testbaren JS-Blöcke sind markiert mit `// --- <name> (testable) ---` … `// --- end <name> ---`. Jeder Block hat einen Node-Test unter `tests/<name>/` (extrahiert den Block aus der HTML, läuft mit `node --test`). Neuer Editor → neuer Test-Ordner (`package.json` mit `"test": "node --test"`) + CI-Schritt in `.github/workflows/ci.yml`.
- **Render-Test (jsdom):** lädt die HTML, mockt `fetch`, prüft: Tabs klickbar, Section wird sichtbar, Formulare rendern.
- **Echter Browser (Playwright/Chromium):** für Layout/full-width, das jsdom nicht kann.

## 3. UI-Post-Check (Playwright) — der eigentliche Review

```bash
cd tools/cockpit-ui-review
node shoot.js                          # bzw. NODE_PATH=<node_modules> node shoot.js
```

Das Tool:
- rendert `cockpit.html` im echten Chromium,
- shootet **jeden Tab** nach `docs/screenshots/ui/<tab>.png`,
- meldet Layout-Heuristiken: horizontaler Seiten-Overflow, horizontal ragende Elemente, abgeschnittene Texte (`scrollWidth > clientWidth`), Klickziele < 24 px, JS-Fehler.

Vorgehen:
1. `shoot.js` laufen lassen.
2. **Report** lesen → jeder Eintrag ist ein Kandidat für Nacharbeit.
3. **Screenshots** ansehen (Mensch) → „gut vs. clunky", tab-weise.
4. Befunde **nacharbeiten** (Code) → erneut ab Schritt 1.

> **Wichtig:** Dieses Modell kann Bilder NICHT selbst ansehen (`read_file` auf PNG → „doesn't support it"). Die ästhetische Abnahme bleibt beim Menschen; das Skill liefert die Screenshots + maschinelle Auffälligkeiten.

## Mock-Endpunkte mitwachsen lassen

`shoot.js` mockt die Endpunkte, die das Cockpit pollt (`game_config`, `personas`, `attack_status`, `natural_attack_rules`, `attack_interval`, `difficulty_interval`, `get_state`, `server/status`, `server/logs`). Kommt ein neuer Poll dazu, den Mock ergänzen — sonst bleiben Panels leer und der Review ist wertlos.

## Refs

`tools/cockpit-ui-review/README.md`, `cockpit/README.md`, Issue #832.
