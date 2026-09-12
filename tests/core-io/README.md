# tests/core-io — Core-IO-Gate (Issue #289)

Das Gate sichert das Kern-Versprechen des Projekts ab:

> **We can interact with the running game we host.**

Konkret: Ein Kommando von **außen** erreicht den **laufenden** dedicated-Server,
wird dort ausgeführt, und der **Effekt** ist im Game-Log nachweisbar — ohne dass
ein Spieler verbunden ist. Damit wird der Kanal abgesichert, der in #288
regressiert ist (`POST /wave` → `exec_result.ok=true`, aber **0 Effekt**).

## Der Kern (C1–C4)

| # | Richtung | Was das Gate automatisiert prüft | Player nötig? |
|---|---|---|---|
| C1 | Boot/Host | Container stabil (`running`, kein Restart-Loop), Bridge `/health` `ok:true`, Mod geladen (`[RBBATTLE]` im dedi-Log) | nein |
| C2 | **Ingress (rein)** | `POST /exec {"command":"rb_status"}` → `exec_result ok:true` **UND** Effekt-Logzeile `[RBBATTLE] event=status` | nein |
| C3 | **Egress (raus)** | Ein **laufender** Consumer (`docker logs -f`) sieht das Spiel-Event — nicht nur „die Zeile steht irgendwo" | nein |
| C4 | Server-Aktion | `POST /wave` am Tournament-Server (Referee → Bridge → Mod) erreicht den Mod (`event=wave … status=start`) | Spawn-Sicht: **ja** |

**Kern-INVARIANTE** (C2, hart): `ok:true` ⟹ im Game-Log steht nachweisbar der
Effekt. `ok:true` darf **nie** nur „`ExecuteCommand` wurde aufgerufen" heißen
(genau die Falsch-Grün-Semantik aus #288).

## Was das Gate beweist — und was nicht (OFFENER PUNKT)

**Bewiesen (automatisiert, headless):**
- Der Container bootet aus dem Nichts und bleibt stabil (C1).
- Der Ingress-Kanal `HTTP → Bridge → Pipe → ConsoleService → Mod-Handler`
  funktioniert **und** wirkt: das `rb_status`-Kommando erzeugt eine echte
  Log-Zeile im laufenden Spiel (C2).
- Ein laufender Consumer empfängt das erzeugte Spiel-Event (C3).
- Ein **serverseitig ausgelöster** Auftrag (`POST /wave` → Referee → Bridge)
  erreicht den Mod (C4).

**NICHT bewiesen — nur Player-Test (OFFENER PUNKT):**
- **`spawned>0` / „der Spieler sieht den Boss".** Headless ist das nicht
  erreichbar: der Mod findet ohne Spieler keinen Bord-Spawner und keinen Mech
  (`event=wave … status=no_border_spawners` / `status=no_player`) und spawnt
  daher nichts. Das Gate meldet diesen Fall als `::warning::`
  („C4 OFFENER PUNKT"), **nicht** als hartes Rot — die Sicht-Abnahme bleibt bei
  Momo/Matheo (Issue #289, Player-Test).
- **Egress bis in den Tournament-Server** (`POST /event`, `score_update`): im
  headless-Dedicated-Stack läuft kein Relay (`bausteine/07-relay`), der die
  Log-Zeilen als Events einliefert. C3 weist daher die **Log-Egress** (Spiel →
  Consumer) nach, nicht die Server-Einlieferung. Das ist als offener Punkt
  dokumentiert (relay-Deploy auf dem Dedicated-Server fehlt, vgl. #13/#265).

## Bausteine

| Datei | Rolle |
|---|---|
| `core_io_probe.py` | Live-Probe (C1–C4) gegen den gebooteten Test-Stack; Exit 1 = rot |
| `test_invariant.py` | Hermetische Selbsttests der Gate-Logik (Invariante, Negativfälle) — **kein** Container nötig |

### Hermetische Selbsttests (laufen in `ci.yml`, Job `test`)

```bash
cd tests/core-io && python3 -m unittest test_invariant -v
```

Beweisen ohne Infrastruktur, dass die Gate-Logik greift:
- `ok:true` **ohne** Effekt-Logzeile ⇒ **rot** (der #288-Fall).
- `exec_result.ok:false` / Transport-Fehler ⇒ rot.
- fehlender Consumer bzw. Consumer ohne Event ⇒ rot.
- `status=done spawned=0` und `status=no_player` ⇒ „reached, aber kein Spawn"
  (offener Punkt, kein stilles Grün).

### Live-Probe

```bash
# Gegen den isolierten Test-Stack (Test-Ports 9003/8091, Container …-test):
python3 tests/core-io/core_io_probe.py --remote "ssh planet"

# Read-only-Diagnose gegen eine laufende Instanz (Prod), OHNE Wave-Spawn:
python3 tests/core-io/core_io_probe.py --remote "ssh planet" \
  --container riftbreaker-dedicated \
  --bridge-url http://127.0.0.1:9001/exec \
  --tournament-url http://127.0.0.1:8081 --skip-wave
```

Wichtige Optionen: `--container`, `--bridge-url`, `--tournament-url`,
`--remote` (Command-Präfix, Default `ssh planet`) / `--local`, `--boot-timeout`,
`--effect-timeout`, `--skip-wave`.

## CI-Verdrahtung

- Der **Required-Check `boot-test`** (`.github/workflows/boot-test.yml`) bootet den
  Test-Stack (wie bisher, `deploy/test-deploy.yml` + `test-vars.yml`, Dummy-Vault,
  keine Secrets) und fährt an **demselben** gebooteten Stack die Live-Probe
  (Schritt „Core-IO-Gate (C1–C4)"). Ein Workflow, ein Boot, keine
  `concurrency`-Kollision — der Job-Name `boot-test` (Required-Context) bleibt
  unverändert.
- Die hermetischen Selbsttests sind im Required-Job `test` (`ci.yml`)
  verdrahtet und damit **immer** grün/rot, unabhängig vom Self-Hosted-Runner.

> Hinweis: Getrenntes `core-io-gate.yml` wurde verworfen — eine eigene
> `concurrency`-Gruppe hätte mit der Gruppe `boot-test` um dieselben Test-Ports
> konkurriert; die geteilte Gruppen-ID führt dabei zu Run-Abbruch seitens GitHub.

## Grenzen der Robustheit

Nur **deterministische** Checks gaten hart. C1/C2/C3 und `C4-reached` sind
deterministisch; `C4-spawn` ist headless prinzipiell nicht erreichbar und wird
deshalb nur gewarnt. Damit ist das Gate nicht flaky, aber auch nicht
„zu optimistisch" (der Vorwurf aus #288 an die bestehenden Stubs).
