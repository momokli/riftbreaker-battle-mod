# Baustein 00 — Mod-Skeleton

**Was es testet:** Der minimale, offizielle Mod-Load-Pfad — Ordner-Layout
`<game>/mods/<ModName>/lua/*_autoexec.lua` wird von der Engine bei
Kartenerstellung ausgeführt, und `LogService:Log` schreibt ins Spiel-Log.
Kein Command, kein UI, keine Seiteneffekte: **Genau eine** erwartete
Log-Zeile `[RBBATTLE] skeleton ok`.

Abgeleitet aus `mod/lua/rbbattle_autoexec.lua` (Spike, PR #1) — reduziert auf
den Skeleton-Lebenszeichen-Log.

## Inhalt

```
rbbattle_00_skeleton/                  <- Mod-Ordner (Name = Mod-Name)
└── lua/
    └── rbbattle_00_skeleton_autoexec.lua
```

## Installation (Windows, Steam)

1. Steam-Bibliothek finden (z. B. `D:\SteamLibrary\steamapps\common\Riftbreaker`).
2. Den Ordner `rbbattle_00_skeleton/` (komplett) nach
   `<SteamLibrary>\steamapps\common\Riftbreaker\mods\` kopieren →
   Ergebnis: `mods\rbbattle_00_skeleton\lua\rbbattle_00_skeleton_autoexec.lua`.
   (`mods\` ggf. neu anlegen.)
3. Spiel starten, **beliebige Karte laden** (Kampagne/Survival) — die
   `*_autoexec.lua` läuft automatisch bei Kartenerstellung, kein weiterer
   Aktivierungsschritt.

## Testschritte

1. Installation wie oben, Spiel starten, Karte laden.
2. Log-Datei öffnen: `<Documents>\The Riftbreaker\exor_logs.txt`
   (Ende der Datei; oder live beobachten mit
   `bausteine/03-log-bridge/tail_events.py`).
3. In-Game-Konsole öffnen (deutsche Tastatur: `ö`) und prüfen, ob die
   Meldung `[RBBATTLE] skeleton ok` auch in der Konsole steht.

## Erwartetes Ergebnis

In `exor_logs.txt` erscheint bei jeder Kartenerstellung **genau einmal**:

```
[RBBATTLE] skeleton ok
```

Fehlt die Zeile → Mod-Ordner liegt falsch (Pfad prüfen), oder die Engine
lädt den Ordner nicht (Layout `mods/<ModName>/lua/*_autoexec.lua` prüfen).

## Status

- [x] Code abgeleitet aus Spike-Skeleton (PR #1)
- [ ] In-Game-Test: `[RBBATTLE] skeleton ok` in exor_logs.txt gesehen (Momo)
- [ ] In-Game-Test: Konsolen-Ausgabe sichtbar (Momo)
