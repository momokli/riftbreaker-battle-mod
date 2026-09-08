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
4. Ungültige Stufe testen: `rb_wave 99` → Konsole warnt „level 99 ungueltig (1..3), nutze 1“, Log: `event=wave level=99 status=invalid_level`. Danach greift der Code-Fallback auf **Welle 1** — der Fallback spawnt aber nur, wenn ein Spieler-Mech aktiv ist.
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
  `event=wave ... status=no_player`; im `rb_wave 99`-Fallback-Pfad zusätzlich
  Konsole + Log `warn=no_player_skip_fallback` (seit v0.1.2, s. Edge-Cases).

## Edge-Cases (v0.1.2)

- **`rb_wave 99`-Fallback:** Ungültige Level (>3 oder <1) fallen intern auf Welle 1
  zurück (`status=invalid_level` im Log). Der Fallback-Spawn braucht einen
  **aktiven Spieler-Mech** (Karte geladen, Mech in der Welt).
- **Kein aktiver Mech im Fallback-Pfad:** Der Spawn wird übersprungen — seit v0.1.2
  **nicht mehr still**: zusätzlich zur `status=no_player`-Zeile kommt eine
  explizite Warnung (Konsole + Log `warn=no_player_skip_fallback`), z. B.:

```
[RBBATTLE] event=wave level=1 requested=99 status=no_player
[RBBATTLE] event=wave level=1 requested=99 warn=no_player_skip_fallback msg=fallback_uebersprungen_kein_aktiver_mech
```

- **In-Game-Test 08.09.2026:** `rb_wave 99` ohne aktiven Mech → Warnung +
  `invalid_level` ok, Spawn übersprungen (damals ohne sichtbare Warnung).
  Retest mit aktivem Mech (Fallback spawnt Welle 1) steht aus.

## Status

- [x] Code abgeleitet aus Spike Experiment A (PR #1)
- [x] In-Game-Test: `rb_wave 1` spawnt 5 Brabits (Momo, 08.09.2026)
- [x] In-Game-Test: `rb_wave 3` spawnt gemischte Welle (Momo, 08.09.2026)
- [ ] In-Game-Test: `rb_wave 99`-Fallback spawnt Welle 1 **mit aktivem Mech**
      + `warn=no_player_skip_fallback` ohne Mech (Retest offen)
