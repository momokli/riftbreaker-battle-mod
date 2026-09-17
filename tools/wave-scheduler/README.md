# wave-scheduler — Freeplay-Wellen-Timer

Nachbau *nur* des Timing-Teils von `dom_mananger` (`lua/missions/v2/dom_manager.lua`)
für den Freeplay/Sandbox-Fall, in dem keine natürlichen Wellen laufen
(`pauseAttacks = true`, siehe `docs/research/515-survival-modes.md`). Kein
HQ-Upgrade-Pfad, keine Idle-Events, kein Multiplayer-Wave-Pool — nur:

- ein Tick-Loop, der in konfigurierbarem Intervall eine "natürliche" Welle
  über `POST /activate_mission_flow` (bereits live verifiziert, `pipe_bridge`,
  #385/#386) auslöst und dabei die Difficulty (Level 1..9) mit eigener
  Geschwindigkeit hochzählt,
- eine Queue für extern "gesendete" Wellen: Anfragen landen nicht sofort,
  sondern werden beim nächsten ohnehin fälligen Tick **zusammen mit** der
  natürlichen Welle gefeuert (gleicher `spawn_point`-Wert, falls gesetzt).

Reine Standardbibliothek (wie `pipe_client.py`), kein pip nötig.

## Start

```
python3 wave_scheduler.py --config config.example.json --dry-run
```

`--dry-run` loggt nur, was gefeuert würde — kein echter Call an `pipe_bridge`.
Für den echten Betrieb `--dry-run` weglassen und `bridge_url` auf den
laufenden `pipe_bridge.exe`-Endpunkt zeigen lassen (Default `127.0.0.1:9001`,
siehe `server/README.md`).

## Eine Welle "senden" (queuen)

```
curl -X POST http://127.0.0.1:9101/queue_wave -d '{"level": 1}'
```

Wird beim nächsten fälligen Tick zusammen mit der dann aktuellen natürlichen
Welle gefeuert — nicht sofort.

## Status abfragen

```
curl http://127.0.0.1:9101/status
```

Liefert `current_level`, `seconds_to_next_wave`, `seconds_to_next_difficulty`,
`pending` (Queue) und `last_fire`.

## Bekannte offene Punkte

- Nur `attack_level_1_entry.logic` ist live verifiziert (2026-09-15, planet).
  Level 2–9 sind ein angenommenes Namensmuster — vor produktivem Einsatz mit
  `get_state` gegenprüfen, ob `mission_flow`/`ok:true` zurückkommt.
- Ob `spawn_point` den tatsächlichen Spawn-Ort steuert, ist laut
  `docs/research/dedicated-io-write-functions.md` #386 nicht live mit Player
  bestätigt. Falls nicht: beide Wellen feuern trotzdem gleichzeitig, nur ohne
  garantiert gleichen Spawn-Punkt.
- Die Zeitwerte in `config.example.json` sind frei gewählte Platzhalter,
  keine aus dem Spiel extrahierten Werte (die echten `DifficultyService`-
  Getter sind C++-seitig, s. `docs/research/515-survival-modes.md` §8).
- Es gibt keinen Konflikt-Schutz gegen manuelles Cockpit-"start wave"
  parallel zu diesem Script (bewusst offen gelassen, da Cockpit nur zum
  Testen genutzt wird).
