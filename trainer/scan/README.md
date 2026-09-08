# scan/ — RE-Phase: Prozess-Info & Wert-Scans (pymem)

Werkzeuge, um im laufenden Rift-Breaker-Prozess Adressen/Offsets für
Ressourcen, Score und Wave-Stand zu finden. Ergebnis wandert in
[`offsets.json`](offsets.json) und wird von der RE-Phase der `rbbridge`-DLL
(State-Read + Exec-Dispatch) verbraucht.

| Datei | Zweck |
|---|---|
| `scan_find.py` | Prozess finden (`riftbreaker*.exe`), öffnen, Module + Basisadressen auflisten |
| `scan_values.py` | Interaktiver Wert-Scan (Cheat-Engine-Stil) → Kandidaten → `offsets.json` |
| `offsets.json` | Ergebnis-Datei: Offsets relativ zur Modulbasis |

## Voraussetzungen (Windows)

- Python 3.10+ ([python.org](https://www.python.org/downloads/), bei
  Installation **„Add python.exe to PATH“** anhaken)
- `pip install pymem`
- The Rift Breaker (Steam), **eine Karte geladen und Spiel pausiert**
- Kein Admin nötig — Skript und Spiel laufen als derselbe Windows-Benutzer.

## Live-Session-Ablauf (Momo / Matheo)

### 1. Spiel vorbereiten

1. Rift Breaker starten, Partie/Karte laden, **Spiel pausieren** (Pause-Menü
   oder ESC, wenn das Spiel das erlaubt — sonst ruhige Stelle suchen).
2. Fenster nicht minimieren (GPU-Speicher bleibt stabil), Spielversion notieren
   (Steam-Bibliothek → Rift Breaker → Eigenschaften, oder im Spiel-Menü).

### 2. Prozess/Module prüfen

```bat
python scan_find.py
```

Erwartete Ausgabe: `[+] Prozess offen: pid=... name=riftbreaker.exe`,
Hauptmodul-Basis (`0x...`) und die Modul-Liste. **Basisadresse notieren** —
alle Offsets sind relativ dazu.

### 3. Wert-Scan (Ressource/Score)

Beispiel „Kohlenstoff = 320“:

```bat
python scan_values.py
```

1. Datentyp wählen: `4` (int32) — Standard für Ressourcen. Nur wenn int
   nichts findet: `f` (float) versuchen (Score wird evtl. als float
   gespeichert).
2. `s` → aktuellen Wert eingeben: `320` → Erst-Scan läuft (ganzer
   Prozess-Speicher, kann Sekunden bis Minuten dauern).
3. Im Spiel Wert ändern: Kohlenstoff verbrauchen (bauen) oder verdienen →
   pausieren.
4. `v` → neuen Wert eingeben (z. B. `280`) → Rescan. Alternativ `i`
   (increased) / `d` (decreased) / `u` (unchanged) — z. B. `u` nach einem
   Rescan, bei dem sich nichts geändert hat.
5. Wiederholen bis 1–5 Kandidaten übrig sind. `l` listet Kandidaten mit
   Live-Werten (int32/int64/float) und Modul-Zuordnung.
6. Bei genau 1 Treffer: `q` → Speicher-Dialog → Kandidatenzeile `0` wählen →
   Namen vergeben (z. B. `carbon`). Das Skript schreibt den **Offset
   relativ zur Modulbasis** nach `offsets.json` (inkl. Notiz).

### 4. Ergebnis committen

```bat
git add trainer/scan/offsets.json
git commit -m "scan: offsets für carbon/score gefunden (Session YYYY-MM-DD)"
git push
```

In `offsets.json` zusätzlich in `notes` festhalten: Spielversion, Kartenname,
Datum, wer gescannt hat. **Offsets gelten nur für diese Spielversion** —
nach einem Game-Update neu scannen.

## Interpretation der Offsets

`offsets.json`-Eintrag `{"carbon": "0x4A2F10"}` bedeutet: Wert liegt bei
`<Modulbasis> + 0x4A2F10`. Das Modul steht in der zugehörigen Notiz
(`notes`), meist das Hauptmodul. Absolute Adresse zum Verifizieren:
`0x<Basis> + 0x4A2F10` — im Skript direkt beim Speichern sichtbar.

**Vorsicht:** Ressourcen liegen in Spielen oft hinter einem Pointer-Pfad
(Statische Adresse → Zeigerkette). Wenn der Offset nach einem
Karten-Neustart nicht mehr stimmt, ist ein Pointer-Scan nötig (dann mit
Cheat Engine arbeiten und die Pointer-Kette ebenfalls in `notes`
dokumentieren).

## Hinweise / Grenzen

- **Kein Anti-Cheat** im Spiel, trotzdem: nur im eigenen Singleplayer
  scannen, nichts Fremdes anfassen, Werte nur zum Verifizieren ändern.
- `scan_values.py` scannt nur kommittete, lesbare Speicherregionen des
  Spielprozesses (kein Kernel, keine fremden Prozesse).
- Erst-Scan ist bewusst simpel (Byte-Muster-Suche): Bei > 2 Mio. Treffern
  bricht das Skript ab — Wert zu häufig (z. B. `0`)? Dann zuerst einen
  unterscheidbaren Wert herstellen oder Typ wechseln.
- Float-Vergleiche: Exakte Treffer nur, wenn der Wert wirklich exakt
  gespeichert ist (bei `v`-Rescans ggf. `u`/`i`/`d` verwenden).
