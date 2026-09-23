# pipe_bridge host-test

Kompiliert zur Laufzeit die **Windows-freie** Health-Logik aus
`server/pipe-bridge/health_logic.h` mit dem Host-CC (`cc`, dann `gcc` —
**kein mingw**) gegen `hosttest/pipe_bridge_health_hosttest.c` und führt sie
aus: kein Windows, kein Spielprozess, kein Wine, kein Netz.

Geprüft (Issue #902, US1/US2):

- `bridge_health_status` / `bridge_health_status_deep` — Statuscode
  (`pipe_ok=1` → `200`, `pipe_ok=0` → `503`, deep + Ping-Fehler → `503`).
- `bridge_health_body` — JSON-Felder je Kombination aus `pipe_ok`, `ping_ok`,
  `deep`: `ok`, `pipe`, `ping`, `reason` (`pipe_unavailable` /
  `ping_failed`).
- Bestandsformat: `pipe_ok=1`, `deep=0` → body bitgleich `{"ok":true,"pipe":true}`
  (nur der Statuscode war vorher unehrlich).
- Robustheit: kleiner Puffer bleibt NUL-terminiert, `out=NULL`/`n=0` schreiben nicht.

„Pipe künstlich trennen" = `pipe_ok=0` (Hauptpfad, beweist Abnahme (a) ohne
Spielsession). Ein realer In-Container-Nachweis ist zusätzlich möglich:
`RBB_BRIDGE_PIPE` auf eine nicht existierende Pipe zeigen (z. B.
`\\.\pipe\rbbattle_missing`) und `/health` prüfen → `503` (nur dokumentierter
manueller Check, **nicht** CI — kein Wine auf planet).

Ist kein Host-C-Compiler vorhanden, wird der Test **sichtbar übersprungen**
(skip mit Begründung, nie stillschweigend grün).

## Aufruf

```bash
cd tests/pipe-bridge-hosttest
npm ci        # keine externen Deps (nur node:test); ohne lock-Install auch `npm test` möglich
npm test      # = node --test
```

Voraussetzung: ein Host-C-Compiler (`cc`/`gcc`).
