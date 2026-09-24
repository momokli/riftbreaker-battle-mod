# deploy/parked — Warm-Pool „Parked Solo" (Issue #909)

Für **einen Spielwunsch** einen Dedicated-Server **vorab hochfahren** und ihn
*geparkt* bereithalten: Container + Mod-Load + Bridge laufen bereits, die Welt
ist angehalten. Der Handover an ein echtes Spiel ist dann nur noch
`POST /resume_game` statt eines kompletten Cold-Boots.

Nur Standardbibliothek, kein venv/pip. Nutzt den Provisioner aus
[`../provisioner`](../provisioner) (Issue #908) für Start/Stop — **keine**
Duplikation der Docker-Logik.

## Parked-Zustandsdefinition

| Zustand | Bedeutung | Welt-Fortschritt |
|---|---|---|
| `WARMING` | Container startet, noch nicht geparkt | (Boot) |
| `PARKED` | läuft, `pause_game` aktiv | **nein** |
| `CLAIMED` | an ein Spiel übergeben (`resume_game`) | ja |
| `RECYCLING` | nach Spielende: `end_game`/`round_reset` laufen | nein |
| `STOPPED` | Container gestoppt bzw. entfernt | — |

**Invariante „kein Weltfortschritt":** Zwischen `pause_game()` (Übergang →
`PARKED`) und `resume_game()` (Übergang → `CLAIMED`) darf keine Spielzeit
vergehen, kein Wellen-Tick laufen und kein Rundenzähler steigen. Jeder
Zustandswechsel ist explizit und messbar.

Übergänge:

```
WARMING --pause_game--> PARKED --resume_game--> CLAIMED
CLAIMED --end_game/round_reset--> RECYCLING --pause_game--> PARKED
{*, PARKED} --stop--> STOPPED
```

## API

`ParkedPool(provisioner, bridge_factory=None, clock=time.monotonic, sleep=time.sleep)`

| Methode | Zweck | Rückgabe |
|---|---|---|
| `warm_up(env=None, instance_id=None)` | Provisioner `start()` → `pause_game()` → `PARKED`. **Idempotent**: schon `PARKED` → kein zweiter Start. | `ParkedEntry` |
| `claim(env=None, instance_id=None)` | Health prüfen, `resume_game()`, Handover messen → `CLAIMED`. | `{instance, env, bridge_url, state, handover_seconds}` |
| `recycle(env=None, instance_id=None, keep_warm=True, result=None)` | `end_game()` + `round_reset()` + `pause_game()` → wieder `PARKED`; `keep_warm=False` → `stop()` → `STOPPED`. | `ParkedEntry` |
| `reap(max_park_seconds)` | Auslaufschutz: zu lange geparkte Instanzen sauber stoppen. | `list[ParkedEntry]` |
| `status()` | Snapshot aller Einträge. | `list[dict]` |

`BridgeClient(base_url, timeout=5.0, opener=None)` — dünner stdlib-HTTP-Client:
`health_ok()` (`GET /health`), `pause_game()`, `resume_game()`, `round_reset()`,
`end_game(result)`, `get_state()` — jeweils `POST <base>/<path>`, Antwort JSON.
Über `bridge_factory` injizierbar.

**Fehlerverhalten:** unbekannte/falsche Zustände, Bridge-Fehler und Timeouts
brechen **laut** mit `ParkedError` ab — nie ein halber Zustand. Scheitert
`pause_game` nach dem Start, wird die Instanz zurückgerollt und gestoppt.

## Parked VS (#910)

Zusätzlich zum Solo-Warm-Pool (`ParkedPool`, #909) gibt es `ParkedVSPool`
(`parked_vs.py`): **ein** geparkter Server für **zwei** Spieler. Er komponiert
`ParkedPool` (kein Docker-/HTTP-Code dupliziert) und setzt ein **Ready-Gate**
zwischen Beitritt und Handover. Die Welt läuft erst, wenn **beide** beigetreten
**und** beide `ready` sind — bis dahin bleibt der `ParkedEntry` in `PARKED`
(kein Weltfortschritt). Kein A/B-Split (das ist #875, out of scope).

### VS-Zustandsmaschine

| Zustand | Bedeutung |
|---|---|
| `WAITING_OPPONENT` | weniger als `required_players` (Default 2) beigetreten |
| `WAITING_BOTH_READY` | alle beigetreten, aber nicht alle `ready` |
| `CLAIMED` | Handover (`resume_game`) erfolgt |
| `RECYCLING` | `end_game`/`round_reset` laufen |
| `STOPPED` | Container gestoppt |

```
(warm_up) --> WAITING_OPPONENT
WAITING_OPPONENT   --join(<2)-------------> WAITING_OPPONENT
WAITING_OPPONENT   --join(2.)-------------> WAITING_BOTH_READY
WAITING_BOTH_READY --ready(nicht alle)----> WAITING_BOTH_READY
WAITING_BOTH_READY --ready(alle)--> [pool.claim()/resume_game] --> CLAIMED
CLAIMED --recycle(keep_warm=True)--> RECYCLING --> WAITING_OPPONENT
CLAIMED --recycle(keep_warm=False)---------------> STOPPED
```

**Gate-Invariante:** `pool.claim()` (= `resume_game`) wird **ausschließlich**
ausgelöst, wenn `len(players) == required_players` **und**
`len(ready) == required_players`. Das ist red-before-green getestet (ein
absichtlich zu frühes Gate lässt den Test fehlschlagen).

### API `ParkedVSPool`

`ParkedVSPool(provisioner, pool=None, bridge_factory=None, clock=time.monotonic, sleep=time.sleep, required_players=2)`

| Methode | Zweck | Rückgabe |
|---|---|---|
| `warm_up(env=None, instance_id=None)` | Instanz parken (delegiert an `ParkedPool.warm_up`); **idempotent** | `VSSession` |
| `join(player, env=None, instance_id=None)` | Spieler beitritt; bis 2 beigetreten bleibt `WAITING_OPPONENT` | `VSSession` |
| `ready(player, env=None, instance_id=None)` | Ready-Flag; Gate: bei beiden ready → `pool.claim()` → `CLAIMED`, Handover gemessen | `VSSession` |
| `recycle(env=None, instance_id=None, keep_warm=True, result=None)` | Matchende: wieder `WAITING_OPPONENT` (warm) bzw. `STOPPED` (kalt) | `VSSession` |
| `status()` | Snapshot aller VS-Sessions | `list[dict]` |

**Fehlerverhalten (laut, `VSError`):** unbekannte/nie `warm_up`-te Instanz,
doppeltes `join`/`ready` desselben Spielers, dritter Spieler
(> `required_players`), `ready` vor `join`, `join`/`ready` nach `CLAIMED`,
`recycle` vor `CLAIMED`. Scheitert das Gate-`claim` (Bridge nicht healthy),
bleibt der Zustand `WAITING_BOTH_READY` und der Eintrag `PARKED`; das
Ready-Flag des auslösenden Spielers wird zurückgerollt (kein halber Handover).

### VS-DoD-Mapping (#910)

| DoD (#910) | Umsetzung | Nachweis |
|---|---|---|
| Match-Start geparkt messbar schneller als Cold-Boot | `measure_boot.run_vs_measurement` (Cold-Boot vs. 2-Beitritt-Handover) → `saved_seconds` | [`MEASUREMENT.md`](MEASUREMENT.md) VS-Abschnitt + `test_measure_boot.MeasureVSTests` |
| Zwei Spieler im selben Spiel, Pause bis `ready` beider Seiten | `join`×2 + Ready-Gate vor `pool.claim()` | `test_parked_vs.py` (`ReadyGateTests`, red-before-green) |
| Nach Matchende Instanz wieder verfügbar/sauber gestoppt | `recycle(keep_warm=True/False)` | `test_parked_vs.py` (`RecycleTests`) |

## DoD-Mapping

| DoD (#909) | Umsetzung | Nachweis |
|---|---|---|
| Handover **messbar schneller** als Cold-Boot | `measure_boot.py` misst beide Pfade und liefert `saved_seconds` | Messung unten + `test_measure_boot` |
| **Auslaufschutz** für zu lange geparkte Instanzen | `ParkedPool.reap(max_park_seconds)` stoppt überfällige | `test_reap_stops_only_overdue_parked_instances` |
| **Kein Weltfortschritt** im Parked-Zustand | `pause_game` beim Parken, `resume_game` erst beim Claim; Invariante über `get_state` (Welt-Tick) | **hermetisch**: `WorldProgressInvariantTests` (`get_state`-Tick unverändert im PARKED, steigt nach `claim`, red-before-green). **Live-Nachweis auf laufender Welt offen** (§3/§5 in [`MEASUREMENT.md`](MEASUREMENT.md)) — hängt an #880 + Live-Spieler; Follow-up [#919](https://github.com/momokli/riftbreaker-battle-mod/issues/919) |
| Kein bestehender Code kaputt | neues Paket, wiederverwendeter Provisioner, nichts angefasst | nur `deploy/parked/` neu |

## Scope — Spike-Bericht vs. Code-Deliverable

Milestone #13 trennt methodisch **Spike (#880 + Mess-Teil #909) = Bericht,
kein PR** von Code-Deliverables. Für #909 gilt bewusst **EIN PR** (#917);
die beiden Artefakt-Typen sind darin klar getrennt:

- **Spike-/Mess-Bericht** (kein Code): [`MEASUREMENT.md`](MEASUREMENT.md) —
  Methode, Rohzahlen (Spike 15,2 s / Re-Messung 9,35 s; Handover ≈ 0,12 s;
  Parken ≈ 0,13 s), Messumgebung, Datum, **rohe Live-Belege** ([`evidence/`](evidence/))
  und der ehrliche Stand des Live-`get_state`-Nachweises (hermetisch belegt, live offen).
- **Code-Deliverable**: dieses Verzeichnis `deploy/parked/` — Warm-Pool
  (`parked_pool.py`), Mess-Harness (`measure_boot.py`) und hermetische Tests.

Der Bericht in `MEASUREMENT.md` ist das committete Ergebnis des Spike-Teils,
unterlegt mit den Roh-Logs in `evidence/`; der Code ist das davon getragene
Deliverable.

## Gemessene Zahlen (live auf planet, 2026-09-24)

Vollständiger Mess-Bericht **mit rohen Live-Belegen**:
[`MEASUREMENT.md`](MEASUREMENT.md) und [`evidence/`](evidence/).
Realer Dedicated-Container, ephemer publizierte Bridge.

| Vorgang | Spike | Re-Messung live |
|---|---|---|
| **Cold-Boot** (Container-Start + Mod-Load + Bridge healthy) | ≈ 15,2 s | **9,35 s** |
| **Parked-Handover** (`POST /resume_game`) | ≈ 0,13 s | **0,12 s** |
| Parken (`POST /pause_game`) | ≈ 0,5 s | **0,13 s** |

**Ersparnis:** 9–15 s pro Handover (Cold-Boot, host-cacheabhängig) statt
sub-sekundigem Handover; im echten Deploy zusätzlich die ~664 MB Content-Copy +
Ansible (CI-Budget 240 s). Rohbelege:
[`evidence/909-idle-roundtrips-2026-09-24.txt`](evidence/909-idle-roundtrips-2026-09-24.txt),
[`evidence/909-coldboot-handover-2026-09-24.txt`](evidence/909-coldboot-handover-2026-09-24.txt).

> Der Live-Nachweis „kein Weltfortschritt" auf einer **laufenden** Welt steht
> **aus** (auf `planet` keine Welt mit Spieler/Tick verfügbar; `get_state`
> durchgehend `ok:false`). Er ist hermetisch belegt und als Follow-up
> [#919](https://github.com/momokli/riftbreaker-battle-mod/issues/919)
> nachverfolgt; der volle Live-Happy-Path ist durch
> [#918](https://github.com/momokli/riftbreaker-battle-mod/issues/918) blockiert.
> Siehe [`MEASUREMENT.md` §3/§4/§5](MEASUREMENT.md).

## Config / Konventionen

- Instanz-`instance_id` folgt dem Provisioner-Schema (Regex
  `^[A-Za-z0-9_.-]{1,40}$`); ohne Angabe vergibt der Pool `parked-<n>`.
- `env` default aus `provisioner.cfg.env`.
- Container-/Portnamen kommen ausschließlich aus `InstanceSpec` (#908), nie aus
  Nutzereingabe.
- `measure_boot.py` CLI braucht `PROVISIONER_*`-Config:
  `PROVISIONER_IMAGE=... python3 measure_boot.py --json` →
  `{"cold_boot_seconds":..,"parked_handover_seconds":..,"saved_seconds":..}`.
  **Hinweis:** gegen das reale Image ist der Live-Lauf derzeit blockiert (Provisioner
  #908: Container-Port `8080` statt `9001`, Mounts weichen von der Deploy-Compose ab →
  [#918](https://github.com/momokli/riftbreaker-battle-mod/issues/918)) →
  Health-Timeout, siehe [`MEASUREMENT.md` §4](MEASUREMENT.md).

## Test (hermetisch, ohne Docker/Netz/Spiel)

```sh
cd deploy/parked && TMPDIR=/dev/shm/parked-test python3 -m unittest -v
```

52 Tests. Abgedeckt: warm_up happy + idempotent + Rollback bei
`pause_game`-Fehler, **Welt-Tick-Invariante via `get_state`** (kein Fortschritt
im PARKED, Fortschritt nach `claim`, red-before-green), claim misst Handover +
verlangt `PARKED` + healthy, recycle warm/kalt, reap stoppt nur Überfällige und
stoppt bei einem `stop`-Fehler die übrigen trotzdem (aggregierter
`ParkedError`), status, Fehler → `ParkedError`, `measure_boot` liefert Differenz
und räumt Cold+Parked auf (kein Container-Leak).

**VS (#910)** zusätzlich: `ReadyGateTests` (erst beide beigetreten + beide
`ready` lösen genau **ein** `resume_game` aus; kein `resume` nach Join 1/2 bzw.
einer `ready`; **red-before-green**), `RecycleTests` (Slots geleert/kalt
gestoppt, Match 2 sauber), `ErrorTests` (doppeltes join/ready, dritter Spieler,
`ready` vor `join`, `join` nach `CLAIMED`, Gate-Fehler bleibt `PARKED`),
`WorldProgressInvariantTests` (kein Welt-Tick bis beide ready, danach ja),
`StatusTests`; `test_measure_boot.MeasureVSTests` (2-Beitritt-Handover,
`saved_seconds`, Cleanup auch bei Gate-Fehler, CLI-`--vs`-Routing).

> Lint (ruff) läuft separat in der CI, nicht Teil dieses Verzeichnis-Setups —
der `ruff`-Aufruf wurde entfernt.
