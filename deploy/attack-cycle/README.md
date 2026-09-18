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

| Route         | Methode | Zweck                                                                                          |
| ------------- | ------- | ------------------------------------------------------------------------------------------------ |
| `/queue_send` | POST    | Welle kaufen (`{"name":"waveN"}` oder `{"level":N}`)                                            |
| `/status`     | GET     | `active`, `level`, `seconds_to_next_attack`, `seconds_to_next_difficulty`, `bought`, `orders`, … |

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
