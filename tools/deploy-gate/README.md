# tools/deploy-gate — Deploy-Parkierung (Issue #118)

Vor jedem Server-Deploy die aktuelle Spielerzahl ermitteln und entscheiden:
**0 Spieler → sofort deployen, sonst parken und automatisch ausrollen, sobald
alle disconnected sind.** Updates kommen so immer erst **nach** dem Game, nie
mitten im Match.

Eigenständiges, testbares Modul — bewusst **keine** vollständige CD-Pipeline:
die CD-Stages (`main→dev` / `tag→prod`) sind Gegenstand von #91 und existieren
im Repo noch nicht als Workflow. Dieses Modul liefert die wiederverwendbare
Parkierungs-Stufe, die jede spätere Pipeline (dev + prod) als Pre-Gate vorschalten
kann.

## Funktionsweise

```
Spielerzahl-Provider (RCON/Query/Logs)  →  decide()  →  deploy | parken
                                                            │
                                     Re-Check (--interval) ─┘  bis 0 / Force / Timeout
```

Entscheidungslogik (`deploy_gate.decide`):

| Spielerzahl | Ergebnis |
|---|---|
| `--force` | deploy (sofort, Detail `forced`) |
| unbekannt (`None`) | parken (sicher, Detail `unbekannt`) |
| 0 | deploy (Detail `leer`) |
| n > 0 | parken (Detail `n Spieler online`) |

Exit-Codes: `0` = deploy (leer/Force), `1` = Fehler (kein Provider/Argument),
`2` = Timeout (bis Deadline geparkt, kein Deploy).

## Aufruf

```bash
# Spielerzahl explizit (Test/Override)
python3 tools/deploy-gate/deploy_gate.py --player-count 0

# Provider-Kommando (RCON/Steam-Query/Log-Grep) + Timeout 30 min
python3 tools/deploy-gate/deploy_gate.py \
    --count-cmd '<RCON/Query-Kommando>' --timeout 1800 --interval 30

# Force: sofort deployen, ignoriert die Spielerzahl
python3 tools/deploy-gate/deploy_gate.py --force
```

Als Pre-Gate vor dem Ansible-Deploy (Integration in die spätere #91-Pipeline):

```bash
python3 tools/deploy-gate/deploy_gate.py \
    --count-cmd '<RCON/Query-Kommando>' --timeout 1800 \
  && ansible-playbook -i deploy/inventory deploy/site.yml --ask-vault-pass
```

## Spielerzahl-Provider

Die verbindliche Quelle ist **noch offen** und hängt an den Server-Details aus
#91. Der Dev-SP-Server (6321) läuft mit `disable_steam=1` + `cli=1`
(`deploy/inventory/host_vars/planet/vars.yml`); eine RCON-/Query-URL ist im
Repo nicht festgelegt. Deshalb ist die Quelle austauschbar:

- `--count-cmd <cmd>` / `RBM_COUNT_CMD`: Kommando, das die Spielerzahl als
  Ganzzahl `>= 0` auf stdout schreibt (RCON-Aufruf, Steam-Query, Log-Grep über
  `exor_logs.txt` o. ä.).
- `--player-count N`: direkter Wert (Tests / manueller Override).

Liefert das Kommando keinen parsebaren Wert (Exit != 0, Timeout, leer), wird
**geparkt statt blind deployed** (sicherer Default).

## Schutzmechanismen

- **Force** (`--force`): manueller Deploy sofort, unabhängig von der Spielerzahl.
- **Timeout** (`--timeout <s>` / `RBM_TIMEOUT_S`): nach Ablauf Exit-Code 2 —
  nichts hängt unbegrenzt. `0`/unset = unbegrenzt.
- **Park-Zustand sichtbar**: jeder Re-Check loggt `geparkt (n Spieler online)`.

## Tests

```bash
cd tools/deploy-gate
python3 -m unittest test_deploy_gate -v
```

Abgedeckt: `0 → deploy`, `n>0 → parken`, `force → sofort`, `unknown → parken`,
`timeout → TIMEOUT` (deterministisch über Fake-Clock/-Sleep), Provider-Parsing.
Läuft auch in CI (`ci.yml`, Job `test`).
