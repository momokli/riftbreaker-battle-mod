# tools/deploy-gate — Deploy-Parkierung (Issue #118)

Vor jedem Server-Deploy die aktuelle Spielerzahl ermitteln und entscheiden:
**0 Spieler → sofort deployen, sonst parken und automatisch ausrollen, sobald
alle disconnected sind.** Updates kommen so immer erst **nach** dem Game, nie
mitten im Match.

Eigenständiges, testbares Modul. Das Gate-Modul bleibt als Library bestehen,
ist aber **nicht** mehr in
[`.github/workflows/deploy.yml`](../../.github/workflows/deploy.yml) verdrahtet:
mit `e8f7783` wurde die #238-Park-Verdrahtung bewusst entfernt, die CD deployt
sofort bei jedem Merge auf `main`. Ob das Gate wieder eingeführt wird
(#238/#327), ist offen. Dieselbe Stufe lässt sich jederzeit in weitere
Pipelines (z. B. prod) vorschalten.

## Funktionsweise

```
Spielerzahl-Provider (Log)         →  decide()  →  deploy | parken
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

# Log-Provider + Timeout 30 min (so läuft es in CD)
python3 tools/deploy-gate/deploy_gate.py \
    --count-cmd "python3 tools/deploy-gate/player_count.py" \
    --timeout 1800 --interval 30

# Force: sofort deployen, ignoriert die Spielerzahl
python3 tools/deploy-gate/deploy_gate.py --force
```

Als Pre-Gate vor dem Ansible-Deploy:

```bash
python3 tools/deploy-gate/deploy_gate.py \
    --count-cmd "python3 tools/deploy-gate/player_count.py" --timeout 1800 \
  && ansible-playbook -i deploy/inventory deploy/site.yml --ask-vault-pass
```

## Spielerzahl-Provider (`player_count.py`, Issue #238)

Die verbindliche Quelle ist das **Dedicated-Server-Container-Log** (dieselbe
Quelle wie `tools/solo-feed/feed.py`). Der Solo-DEV-Server läuft mit
`disable_steam=1` (+ `cli=1`): **kein** Steam-Query, kein RCON konfiguriert —
deshalb wird das Log ausgewertet statt abgefragt.

`player_count.py` (stdlib-only) schreibt **genau eine Ganzzahl `>= 0`** auf
stdout: Exit `0` = belastbar, Exit `!= 0` = unsicher.

| Signal | Log-Zeile |
|---|---|
| Join | `ServerGameplayState: OnNetPlayerCreateRequest '…':'<name>'` **oder** `ServerGameplayState: Player '…':'<name>'` |
| Leer/Pause | `GameplayState::PauseGame` (`server_pause_game_when_empty=1`) |
| Resume/Join | `GameplayState::ResumeGame` (bei `ConnectReq`) |

**Ableitung** (jüngstes Pause-/Resume-Signal entscheidet, Zeilen chronologisch):

| Lage | Ergebnis |
|---|---|
| letztes Signal `PauseGame`, kein Join danach | `0` |
| Join(s) nach dem letzten `PauseGame` | Anzahl Joins (≥ 1) |
| letztes Signal `ResumeGame`, kein Join danach | `1` (Resume = `ConnectReq`) |
| kein verwertbares Signal (nur unbekannte Zeilen) | kein Wert, Exit ≠ 0 |

Kein Leave-Signal im Log → der Zähler ist ein **Over-Estimate** (bewusst
konservativ: lieber parken als blind deployen). `decide()` behandelt einen
unsicheren Provider (`None`) als **parken**.

### CLI

```bash
# Default: docker logs --tail 400 riftbreaker-dedicated
python3 tools/deploy-gate/player_count.py

# Fixture/Datei oder eigenes Kommando (Tests, andere Quelle)
python3 tools/deploy-gate/player_count.py --log fixture.log
python3 tools/deploy-gate/player_count.py --log-cmd 'docker logs riftbreaker-dedicated'

# Tail-Länge des Default-Kommandos
python3 tools/deploy-gate/player_count.py --tail 800
```

Env `RBM_LOG_CMD` überschreibt das Default-Kommando; `--log-cmd` schlägt die
Env-Variable. Exit-Codes: `0` = belastbar, `1` = kein verwertbares Signal,
`2` = Log-Kommando/-Datei fehlgeschlagen.

## Schutzmechanismen

- **Force** (`--force`): manueller Deploy sofort, unabhängig von der Spielerzahl.
- **Timeout** (`--timeout <s>` / `RBM_TIMEOUT_S`): nach Ablauf Exit-Code 2 —
  nichts hängt unbegrenzt. `0`/unset = unbegrenzt.
- **Park-Zustand sichtbar**: jeder Re-Check loggt `geparkt (n Spieler online)`.

## Wiring in CD (`deploy.yml`)

Der CD-Workflow ist **gate-los** (seit `e8f7783`): `push` auf `main` deployed
sofort per SSH (`ssh ... rbd`); `workflow_dispatch` erlaubt einen manuellen
Re-Deploy ohne Inputs. Der Gate-/Provider-Code (`deploy_gate.py`,
`player_count.py`) bleibt als getestete Library erhalten, ist aber **nicht**
mehr in `deploy.yml` verdrahtet. Ob das Gate wieder eingeführt wird
(#238/#327), ist offen und wird hier nicht entschieden.

- **Merge-Gate**: `deploy-check.yml` führt `check_deploy_wiring.py` aus — es
  prüft nur noch den aktuellen gate-losen Vertrag (`push` auf `main`,
  `workflow_dispatch`, SSH-Deploy-Step) — plus die Unit-Tests des Moduls.
  Änderungen an `.github/workflows/deploy.yml` oder `tools/deploy-gate/**`
  lösen den Check auf `main` aus.
- **Provider-Troubleshooting** (gilt für manuell vorgeschaltete Läufe): parkt
  ein Lauf direkt nach einem Server-Restart ohne `PauseGame`-Zeile, ist der
  Provider unsicher → Park bis Timeout (bewusst, nie blind deployen). Ausweg:
  Workflow **re-run** oder Dispatch mit `--force`.
- **Concurrency** bleibt `cd-dev` / `cancel-in-progress: false`.

## Tests

```bash
cd tools/deploy-gate
python3 -m unittest test_deploy_gate -v
```

Abgedeckt: `0 → deploy`, `n>0 → parken`, `force → sofort`, `unknown → parken`,
`timeout → TIMEOUT` (deterministisch über Fake-Clock/-Sleep), Provider-Parsing,
der Log-Provider `player_count.py` (Fixtures, CLI-Subprozess), die
Negativ-Semantik (online/unbekannt ⇒ nie `DEPLOY`) und der Wire-Contract-Check
`check_deploy_wiring.py` gegen die echte `deploy.yml` (#337).
Läuft auch in CI (`ci.yml`, Job `test`, und `deploy-check.yml`).
