# tests/core-io — Core-IO-Gate (Issue #289)

Das Gate sichert das Kern-Versprechen des Projekts ab:

> **We can interact with the running game we host.**

Konkret (C1, aktuell): Der Container bootet stabil aus dem Nichts, die Bridge
ist per HTTP erreichbar, und der Mod ist geladen (`[RBBATTLE] event=mod_load`
im dedi-Log) — ohne dass ein Spieler verbunden ist.

> Historie: C2–C4 (Ingress-Effekt `rb_status`, Egress-Consumer, Server-Wave
> `rb_wave`) sicherten zusätzlich den vollen Kanal ab. Nach der Green-Field-
> Refaktorierung (v2) ist der Lua-Mod nur noch die Player-HUD-Schicht und hat
> diese Commands nicht mehr — C2–C4 sind eingestampft und werden gegen die
> C++-Backend-Pfade (`get_state`/`add_resource`) neu aufgebaut (Folge-Issue).

## Der Kern (C1)

| #   | Richtung  | Was das Gate automatisiert prüft                                                                                                   | Player nötig? |
| --- | --------- | ---------------------------------------------------------------------------------------------------------------------------------- | ------------- |
| C1  | Boot/Host | Container stabil (`running`, kein Restart-Loop), Bridge `/health` `ok:true`, Mod geladen (`[RBBATTLE] event=mod_load` im dedi-Log) | nein          |

## Bausteine

| Datei              | Rolle                                                         |
| ------------------ | ------------------------------------------------------------- |
| `core_io_probe.py` | Live-Probe (C1) gegen den gebooteten Test-Stack; Exit 1 = rot |

### Live-Probe

```bash
# Gegen den isolierten Test-Stack (Test-Ports, Container …-test):
python3 tests/core-io/core_io_probe.py --remote "ssh planet"

# Read-only-Diagnose gegen eine laufende Instanz:
python3 tests/core-io/core_io_probe.py --remote "ssh planet" \
  --container riftbreaker-dedicated \
  --bridge-url http://127.0.0.1:9001/exec
```

Wichtige Optionen: `--container`, `--bridge-url`, `--remote` (Command-Präfix,
Default `ssh planet`) / `--local`, `--boot-timeout`.

## CI-Verdrahtung

- Der **Required-Check `boot-test`** (`.github/workflows/boot-test.yml`) bootet den
  Test-Stack (wie bisher, `deploy/test-deploy.yml` + `test-vars.yml`, Dummy-Vault,
  keine Secrets) und fährt an **demselben** gebooteten Stack die Live-Probe
  (Schritt „Core-IO-Gate (C1 Boot)“). Ein Workflow, ein Boot, keine
  `concurrency`-Kollision — der Job-Name `boot-test` (Required-Context) bleibt
  unverändert.
