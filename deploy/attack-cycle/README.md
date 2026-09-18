# attack-cycle — 7-Minuten-Angriffszyklus (PoC)

Ersetzt den ehemaligen 2-Minuten-Order-Scheduler der Bridge: eine gekaufte
Welle feuert nicht mehr 2 min nach dem Kauf einzeln, sondern wird in einen
festen Zyklus gestapelt, der beim Bau des HQ startet.

```
round start (HQ gebaut, hq_hp > 0)
  └─ alle `interval` Sekunden (Default 7 min) EINE natürliche Welle
       Level 1..9 (cap 9)
       + alle in diesem Fenster gekauften Wellen (pending[])

Kauf (`-send waveN` → send-tailer → POST /queue_send {"name":"waveN"}):
  1. Cost-Tabelle → cost (Spiegel der client-mod .ent-Preise)
  2. POST /try_spend {"amount":"<cost>"} → SOFORT bezahlt (nur wenn Guthaben reicht)
  3. ok → in pending[] stapeln; insufficient → 402 (nicht gestapelt)
```

Feuern läuft ausschließlich über `POST /activate_mission_flow` (derselbe Kanal
wie der Cockpit-Button „start wave“). Naturwelle und gekaufte Wellen eines
Ticks feuern nacheinander.

## Warum ein Sidecar?

- **Nicht in der Website** (Cockpit ist manuell), **nicht in der DLL** — gleiches
  Muster wie `deploy/match-loop` (pollt `get_state`) + `tools/wave-scheduler`
  (Queue + `activate_mission_flow`). Reine Game-Logik als eigener Server-Sidecar.

## Endpunkte

| Route         | Methode | Zweck                                                     |
| ------------- | ------- | --------------------------------------------------------- |
| `/queue_send` | POST    | Welle kaufen (`{"name":"waveN"}` oder `{"level":N}`)      |
| `/status`     | GET     | `active`, `level`, `seconds_to_next_attack`, `pending`, … |

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
  (`logic/missions/survival/attack_level_N_{entry,id_1}.logic`) — `logic/dom/*`
  loest nur „attack incoming“ aus, ohne Spawn.
- Runden-Reset (HQ zerstört → neue Runde) ist noch NICHT behandelt (PoC).
