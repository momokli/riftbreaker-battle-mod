# cockpit-ui-review — UI-Post-Check

Rendert `cockpit/cockpit.html` im **echten Chromium** (Playwright), shootet **jeden Tab** und meldet **maschinelle Layout-Heuristiken** als „clunky"-Indikatoren. Gedacht als **extra Post-Check nach UI-Änderungen** (Folge von #832).

## Warum

Unit-Tests (jsdom) und der Render-Test pruefen *Funktion* (Tabs klickbar, Formulare rendern), aber **nicht**, ob das UI *gut* aussieht. Dieses Tool liefert:

1. **Screenshots** je Tab (visuelle Abnahme durch den Menschen) — in `docs/screenshots/ui/`.
2. **Layout-Heuristiken** (maschinell, weil das Modell Bilder nicht selbst ansehen kann):
   - horizontaler Seiten-Overflow,
   - Elemente, die horizontal ragen,
   - abgeschnittene Texte (`scrollWidth > clientWidth`),
   - zu kleine Klickziele (< 24 px).
3. **JS-Fehler** beim Rendern.

## Aufruf

```bash
cd tools/cockpit-ui-review
npm install        # playwright (Browser gecached unter ~/Library/Caches/ms-playwright)
node shoot.js      # bzw. npm run shoot
```

Ohne lokale Installation (z. B. in CI mit geteiltem Store): `NODE_PATH=<pfad-zu-node_modules> node shoot.js`.

## Ablauf (Post-Check)

1. UI geaendert (Tabs/Panels/Log).
2. `node shoot.js` -> Screenshots + Report.
3. **Report lesen**: jeder Eintrag ist ein Kandidat fuer Nacharbeit (kleine Klickziele, Overflow, abgeschnittene Labels).
4. **Screenshots ansehen** (Mensch) und „gut vs. clunky" beurteilen — pro Tab, in kleinen Schritten.
5. Jeder Befund, der bleibt: **Code-Nacharbeit** (eigenes Mini-PR oder Teil des laufenden PR) -> danach Schritt 1 erneut.

## Bewusste Grenzen

- Das Tool **bewertet nicht „schoen"** — es findet nur maschinelle Auffaelligkeiten. Die ästhetische Abnahme bleibt beim Menschen.
- „kleines Klickziel" ist streng (24 px) — viele Treffer im Bestand sind historisch (`send_wave_*`, Checkboxen). Sie sind als **Backlog** zu behandeln, nicht als Blocker.
- Die Mock-Endpunkte in `shoot.js` muessen mitwachsen, wenn das Cockpit neue Polls bekommt (sonst leere Panels).
