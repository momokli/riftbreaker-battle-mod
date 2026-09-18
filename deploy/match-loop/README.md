# match-loop — Solo-Round-Loop (Issue #730)

Erkennt den HQ-Status über die Bridge (`POST /get_state`) und schließt den
Solo-Round-Loop: HQ zerstört → `end_game(lose)` (LOST-Screen) → 10 s warten →
`restart_map` (neue Runde, Map-Reload, Economy 0).

```
poll /get_state (1 s)
  ├─ hq_hp > 0            → Latch "seen-alive"
  └─ Latch + (dead|weg)   → POST /end_game {"result":"lose"}
                             (10 s warten)
                             POST /restart_map {"op":"reset"}
```

## Warum ein Sidecar?

- **Nicht in der Website** (Cockpit ist manuell), **nicht in der DLL** — die
  DLL bleibt reine IO-Primitive (`get_state`/`end_game`/`restart_map` sind dort
  schon verdrahtet). Der Round-Loop ist Game-Logik und lebt deshalb als eigener
  Server-Sidecar.
- Gleiches Muster wie `deploy/send-tailer`/`session-recorder`, nur dass er
  **pollt** statt eine Logdatei tailt (die native HQ-Quelle ist `get_state`,
  nicht das Mod-Log).

## Edge-Triggerung (wichtig)

Ein Defeat feuert genau **ein** `end_game` und (nach dem Delay) genau **ein**
`restart_map`. Der `seen-alive`-Latch verhindert, dass ein noch nicht gebautes
HQ zu Rundenstart fälschlich als "tot" gewertet und sofort restartet:

- HQ nicht gebaut (`hq_hp:null`) → nichts (kein Latch, kein Fire).
- HQ gebaut (`hq_hp>0`) → Latch gesetzt.
- HQ tot (`hq_dead:true` → `hq_hp:0`) **oder** Entity weg (`hq_hp:null` nach
  vorherigem Leben) → Defeat.

## Test

```bash
cd deploy/match-loop
python3 -m unittest test_match_loop -v
```

Hermetisch: `parse_state`, `is_alive`/`is_defeat` und der Zustandsautomat laufen
gegen Fake-Poster/Fake-Clock — kein Netz, kein Spiel, kein DOM.
