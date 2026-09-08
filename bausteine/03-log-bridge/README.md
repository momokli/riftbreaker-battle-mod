# Baustein 03 — Log-Bridge (`[RBBATTLE]` → exor_logs.txt → JSON)

> **Rolle: DIAGNOSE-ONLY — kein Architektur-Baustein.** Die Kommunikation läuft ausschließlich über die Trainer-DLL (04-trainer-io). Dieser Baustein dient nur dazu, beim In-Game-Test ohne Trainer live zu sehen, was der Mod macht.

**Was es testet:** Den Outbound-Pfad der Mod ohne Spiel-UI-Abhängigkeiten —
`LogService:Log` schreibt `[RBBATTLE]`-Zeilen in
`<Documents>\The Riftbreaker\exor_logs.txt`, und `tail_events.py` liest die
Datei **live**, filtert `[RBBATTLE]`-Zeilen und gibt sie als JSON aus. Das
ist die Basis für jedes externe Tooling (Log-Bridge als Notnagel/Verifikation,
s. `docs/concept.md`).

Abgeleitet aus `mod/lua/rbbattle_autoexec.lua` — Experiment C (Log-Bridge),
Spike PR #1: das `LogService:Log` + `[RBBATTLE]`-Präfix-Muster, isoliert mit
einem eigenen Test-Command `rb_bridge_test [n]`.

## Inhalt

```
rbbattle_03_logbridge/                <- Mod-Ordner (Name = Mod-Name)
└── lua/
    └── rbbattle_03_logbridge_autoexec.lua
tail_events.py                        <- Live-Tail + JSON-Ausgabe (Python 3, kein pip-Paket nötig)
```

## Installation

### Mod (Windows, Steam)
1. Steam-Bibliothek finden (z. B. `D:\SteamLibrary\steamapps\common\Riftbreaker`).
2. Den Ordner `rbbattle_03_logbridge/` (komplett) nach
   `<SteamLibrary>\steamapps\common\Riftbreaker\mods\` kopieren →
   Ergebnis: `mods\rbbattle_03_logbridge\lua\rbbattle_03_logbridge_autoexec.lua`.
   (`mods\` ggf. neu anlegen.)
3. Spiel starten, Karte laden (Mod lädt automatisch).

### tail_events.py
- Python 3.7+ (Windows: python.org, „Add python.exe to PATH“ anhaken).
- **Keine** zusätzlichen Pakete — nur Standardbibliothek.

## Testschritte

1. Spiel mit installiertem Mod starten, Karte laden.
2. Terminal öffnen, Skript starten:
   ```
   python bausteine\03-log-bridge\tail_events.py
   ```
   (Log-Pfad wird automatisch aus `%USERPROFILE%` abgeleitet; abweichender
   Pfad: `python tail_events.py --log "D:\pfad\exor_logs.txt"`)
3. In-Game-Konsole öffnen (deutsche Tastatur: `ö`) und eingeben:
   - `rb_bridge_test` → 1 Test-Durchlauf (2 Zeilen)
   - `rb_bridge_test 3` → 3 Durchläufe (6 Zeilen)
4. Im Terminal erscheinen die `[RBBATTLE]`-Zeilen live als JSON.

## Erwartetes Ergebnis

Terminal-Ausgabe (live, eine Zeile je Log-Ereignis, als JSON):

```json
{"event": "bridge_test", "run": "1", "status": "start"}
{"event": "bridge_test", "run": "1", "status": "done"}
{"event": "bridge_test", "run": "2", "status": "start"}
...
```

Beim Mod-Load (Kartenerstellung) zusätzlich:
`{"event": "mod_load", "version": "0.1.0-baustein03", "status": "ok"}`

Zeilen **ohne** `key=value`-Paare (z. B. `[RBBATTLE] skeleton ok` aus
Baustein 00) erscheinen als `{"raw": "skeleton ok"}` — auch damit ist der
Baustein zum Prüfen anderer Mods nutzbar.

Rotation: rotiert das Spiel die Log-Datei (6 Dateien), erkennt das Skript
das am Größen-Sprung und liest die neue Datei ab Anfang.

## Status

- [x] Code abgeleitet aus Spike Experiment C (PR #1)
- [ ] In-Game-Test: `rb_bridge_test` erzeugt `event=bridge_test`-Zeilen (Momo)
- [ ] Tail-Test: `tail_events.py` zeigt die Zeilen live als JSON (Momo/Matheo)
- [ ] Rotationstest: Log-Rotation bricht das Tail nicht (Momo)
