# Baustein 01 — Wave-Spawn (`rb_wave <level>`)

**Was es testet:** Laufzeit-Spawning über die offizielle Service-API
`EntityService:SpawnEntity(blueprint, x, y, z, team)` — exakt der Weg, den
EXORs eigener Cheat-Command `debug_spawn_entity` nutzt (Team `""`). Der
Baustein registriert genau **einen** Konsolen-Command (`rb_wave <level>`) mit
3 vordefinierten Wellen; kein UI, keine Bindings.

Abgeleitet aus `mod/lua/rbbattle_autoexec.lua` — Experiment A (Wave-Spawn),
Spike PR #1.

## Inhalt

```
rbbattle_01_wavespawn/                <- Mod-Ordner (Name = Mod-Name)
└── lua/
    └── rbbattle_01_wavespawn_autoexec.lua
```

## Installation (Windows, Steam)

1. Steam-Bibliothek finden (z. B. `D:\SteamLibrary\steamapps\common\Riftbreaker`).
2. Den Ordner `rbbattle_01_wavespawn/` (komplett) nach
   `<SteamLibrary>\steamapps\common\Riftbreaker\mods\` kopieren →
   Ergebnis: `mods\rbbattle_01_wavespawn\lua\rbbattle_01_wavespawn_autoexec.lua`.
   (`mods\` ggf. neu anlegen.)
3. Spiel starten, Karte laden (Mod lädt automatisch; Command-Registrierung
   erfolgt bei Kartenerstellung).

## Testschritte

1. In-Game-Konsole öffnen (deutsche Tastatur: `ö`).
2. `rb_wave 1` eingeben → kleine Brabits-Welle um den Spieler (5 Brabits).
3. `rb_wave 3` eingeben → gemischte Welle (5 Baxmoth, 2 Artigian, 1 Canceroth).
4. Ungültige Stufe testen: `rb_wave 99` → Meldung „level ungueltig (1..3)“
   in der Konsole, keine Welle.
5. Log-Datei prüfen: `<Documents>\The Riftbreaker\exor_logs.txt`
   (oder live via `bausteine/03-log-bridge/tail_events.py`).

## Erwartetes Ergebnis

- Jede gültige Welle spawnt die Kreaturen **sichtbar um den Spieler**
  (Ring 8–20 m), Konsole bestätigt: `rb_wave level X: N Kreaturen gespawnt`.
- Log-Zeilen im Format:

```
[RBBATTLE] event=wave level=1 status=start
[RBBATTLE] event=spawn ok blueprint=units/ground/brabit entity=<id>
[RBBATTLE] event=wave level=1 status=done spawned=5 skipped=0
```

- Unbekannte Blueprints würden als `reason=not_found` übersprungen (Log),
  nicht gecrasht — Blueprint-Namen sind gegen die Original-Spieldaten
  verifiziert (`units/ground/brabit|baxmoth|artigian|canceroth`).
- Kein Spieler-Mech (Karte nicht geladen): graceful no-op
  `event=wave ... status=no_player`.

## Status

- [x] Code abgeleitet aus Spike Experiment A (PR #1)
- [ ] In-Game-Test: `rb_wave 1` spawnt 5 Brabits (Momo)
- [ ] In-Game-Test: `rb_wave 3` spawnt gemischte Welle (Momo)
- [ ] In-Game-Test: `rb_wave 99` → Fehlermeldung, keine Welle (Momo)
