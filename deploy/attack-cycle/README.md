# attack-cycle — 7-Minuten-Angriffszyklus (PoC)

Ersetzt den ehemaligen 2-Minuten-Order-Scheduler der Bridge: eine gekaufte
Welle feuert nicht mehr 2 min nach dem Kauf einzeln, sondern wird in einen
festen Zyklus gestapelt, der beim Bau des HQ startet.

```
round start (HQ gebaut, hq_hp > 0)
  └─ alle `interval` Sekunden (Default 7 min) EINE natürliche Welle mit dem
       aktuellen Level (1..9, cap 9)
       + alle in diesem Fenster gekauften Wellen (bought[])
  └─ UNABHÄNGIG davon: alle `difficulty_interval` Sekunden (Default 200s,
       Issue #778) steigt das Level selbst um 1 — eigener Timer, entkoppelt
       vom Wellen-Feuer-Intervall. Wave 1 startet bei Level 1; nach 200s
       Level 2; nach 400s Level 3; usw. (cap 9)

Kauf (`-send waveN` → send-tailer → POST /queue_send {"name":"waveN"}):
  1. Cost-Tabelle → cost (Spiegel der client-mod .ent-Preise)
  2. Order landet sofort in der Order-Liste; ein Hintergrund-Resolver bezahlt
     sie via `POST /try_spend` und verschiebt sie in die bought-Queue
     (nur bezahlte Wellen feuern — kein optimistischer Spawn)
```

Feuern läuft ausschließlich über `POST /activate_mission_flow` (derselbe Kanal
wie der Cockpit-Button „start wave“). Naturwelle und gekaufte Wellen eines
Ticks feuern nacheinander, jeweils mit dem zu diesem Zeitpunkt aktuellen Level.

## Warum ein Sidecar?

- **Nicht in der Website** (Cockpit ist manuell), **nicht in der DLL** — gleiches
  Muster wie `deploy/match-loop` (pollt `get_state`) + `tools/wave-scheduler`
  (Queue + `activate_mission_flow`). Reine Game-Logik als eigener Server-Sidecar.

## Endpunkte

| Route         | Methode | Zweck                                                                                            |
| ------------- | ------- | ------------------------------------------------------------------------------------------------ |
| `/queue_send` | POST    | Welle kaufen (`{"name":"waveN"}` oder `{"level":N}`)                                             |
| `/status`     | GET     | `active`, `level`, `seconds_to_next_attack`, `seconds_to_next_difficulty`, `bought`, `orders`, … |

## Personas (Send-Profile)

Eine Persona ist eine optionale Folge von **Attacken**; jede Attack ist eine
Liste der vom Gegner gekauften **Extra-Wellen** (mehrere erlaubt), geschichtet
auf die Natural Waves — der simulierte „Gegner“, der „auch sendet“. Kein Loop:
die Persona laeuft aus (wie im echten Spiel), und der Attack-Zaehler resettet
bei Runden-Reset. Natural Attack N = normale Welle (aktuelle Difficulty) +
ALLE Wellen, die der Gegner fuer diese Attack gesendet hat.

```json
{
  "personas": {
    "aggro": [[3, 5], [7], [9, 9]],
    "ruhig": [[], [2], [], [2]]
  }
}
```

- `aggro` Attack 1 -> natural + Wave 3 + Wave 5, Attack 2 -> natural + Wave 7, …
- `[]` = keine Extra-Wellen fuer diese Attack
- Default `--persona none` -> nur Natural Waves
- Die Bridge seedet 4 Default-Personas (Platzhalter-Werte, runtime editierbar):
  `aggro`, `ruhig`, `build`, `zerg` — Werte spaeter auf echte, sinnige
  Build-Orders anpassen.

Start:

```bash
python3 attack_cycle.py --persona aggro --persona-file personas.example.json
```

Zur Laufzeit werden die Personas ueber die **Bridge** gesteuert (statt
CLI-Flag): die Bridge haelt die Defs (`GET/POST /personas`) und die aktive
Persona (`POST /persona_active`). Der Cycle pollt `GET /personas`
(`sync_personas`) und uebernimmt den State — die CLI-Flags sind nur der
Start-Fallback, bis der erste Poll greift. Das Cockpit editiert Personas im Tab
„Persona Editor".

Der Toggle `send_yourself` gehoert seit #851 zur **Game-Config**, nicht mehr zu
`/personas`: einzige Quelle ist `GET /game_config` (`sync_game_config`), der
Cockpit-Tab „Game Config" schreibt ihn.

## send-yourself (Routing eigener Kaeufe)

Zur Laufzeit fuehrt **`game_config.send_yourself`** (Cockpit-Tab „Game Config",
`sync_game_config`); das CLI-Flag ist nur der Start-Fallback (#851).

| Modus          | Verhalten                                                                                                                                                      |
| -------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `on` (Default) | eigener Kauf feuert lokal (heutiges Verhalten)                                                                                                                 |
| `off`          | Carbonium wird trotzdem abgezogen (`try_spend`), die Welle feuert NICHT lokal, sondern wird als Outgoing-Send getrackt (`status().outgoing`, spaeter Server B) |

```bash
python3 attack_cycle.py --send-yourself off
```

## Round-Reset (neue Runde, #854)

Ein Match endet in `GAME_OVER` (terminal). Ein `docker restart` des **Spielservers**
aendert daran nichts — der Attack-Cycle laeuft im **eigenen Container** und bleibt in
`GAME_OVER`. Fuer „neue Runde in einem Schritt" gibt es den Wrapper:

`POST /round_reset` (Bridge) erhoeht `round_reset_epoch` und stoesst den nativen
`restart_map`-Reset an. Der Cycle pollt `POST /round_reset {}` (`sync_round_reset`)
und wendet **atomar** `reset()` **+** `signal_start()` an → aus jedem Zustand (auch
`GAME_OVER`) direkt nach `WARMUP`. Cockpit: Button „new round" im Attack-Cycle-Panel.

## Test

```bash
cd deploy/attack-cycle
python3 -m unittest test_attack_cycle -v
```

Hermetisch: `parse_hq_alive`, `parse_send_level` und die Zustandsmaschine laufen
gegen Fake-Poster/Fake-Clock — kein Netz, kein Spiel, kein DOM.

## Offene Punkte (PoC)

- Cost-Tabelle ist ein **Spiegel** der client-mod `.ent`-Preise und der ehemaligen
  Bridge-`g_order_specs` (bewusst dupliziert, wird später bereinigt).
- Logic-Pfade je Level sind der **Spiegel der SEND-MENU-Presets**
  (`logic/missions/survival/attack_level_N_id_1.logic`, raw spawn) — `logic/dom/*`
  loest nur „attack incoming“ aus, ohne Spawn; `_entry` hat einen "attack incoming"-Delay.
- Runden-Reset (HQ zerstört → neue Runde) ist noch NICHT behandelt (PoC).
