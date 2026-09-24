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

## DoD-Mapping

| DoD (#909) | Umsetzung | Nachweis |
|---|---|---|
| Handover **messbar schneller** als Cold-Boot | `measure_boot.py` misst beide Pfade und liefert `saved_seconds` | Messung unten + `test_measure_boot` |
| **Auslaufschutz** für zu lange geparkte Instanzen | `ParkedPool.reap(max_park_seconds)` stoppt überfällige | `test_reap_stops_only_overdue_parked_instances` |
| **Kein Weltfortschritt** im Parked-Zustand | `pause_game` beim Parken, `resume_game` erst beim Claim; Invariante über `get_state` (Welt-Tick) | hermetisch: `WorldProgressInvariantTests` (`get_state`-Tick unverändert im PARKED, steigt nach `claim`, red-before-green) + live: [`MEASUREMENT.md` §3](MEASUREMENT.md) |
| Kein bestehender Code kaputt | neues Paket, wiederverwendeter Provisioner, nichts angefasst | nur `deploy/parked/` neu |

## Scope — Spike-Bericht vs. Code-Deliverable

Milestone #13 trennt methodisch **Spike (#880 + Mess-Teil #909) = Bericht,
kein PR** von Code-Deliverables. Für #909 gilt bewusst **EIN PR** (#917);
die beiden Artefakt-Typen sind darin klar getrennt:

- **Spike-/Mess-Bericht** (kein Code): [`MEASUREMENT.md`](MEASUREMENT.md) —
  Methode, Rohzahlen (Cold-Boot ≈ 15,2 s / Handover ≈ 0,13 s / Parken ≈ 0,5 s),
  Messumgebung, Datum und der Live-`get_state`-Nachweis „kein Weltfortschritt".
- **Code-Deliverable**: dieses Verzeichnis `deploy/parked/` — Warm-Pool
  (`parked_pool.py`), Mess-Harness (`measure_boot.py`) und hermetische Tests.

Der Bericht in `MEASUREMENT.md` ist das committete, reproduzierbare Ergebnis des
Spike-Teils; der Code ist das davon getragene Deliverable.

## Gemessene Zahlen (live auf planet, 2026-09-24)

Vollständiger, reproduzierbarer Mess-Bericht: [`MEASUREMENT.md`](MEASUREMENT.md).
Realer Dedicated-Container, Bridge auf Host-Port 9011.

| Vorgang | gemessen |
|---|---|
| **Cold-Boot** (Container-Start + Mod-Load + Bridge healthy) | **≈ 15,2 s** |
| **Parked-Handover** (`POST /resume_game`) | **≈ 0,13 s** |
| Parken (`POST /pause_game`) | ≈ 0,5 s |

**Ersparnis:** ≈ 15 s pro Handover (nur Container+Content; im echten Deploy
zusätzlich die ~664 MB Content-Copy + Ansible, CI-Budget 240 s).

Der Live-Nachweis „kein Weltfortschritt im Parked-Zustand" (Blocker 1a) steht
mit Rohbelegen in [`MEASUREMENT.md` §3](MEASUREMENT.md).

## Config / Konventionen

- Instanz-`instance_id` folgt dem Provisioner-Schema (Regex
  `^[A-Za-z0-9_.-]{1,40}$`); ohne Angabe vergibt der Pool `parked-<n>`.
- `env` default aus `provisioner.cfg.env`.
- Container-/Portnamen kommen ausschließlich aus `InstanceSpec` (#908), nie aus
  Nutzereingabe.
- `measure_boot.py` CLI braucht `PROVISIONER_*`-Config:
  `PROVISIONER_IMAGE=... python3 measure_boot.py --json` →
  `{"cold_boot_seconds":..,"parked_handover_seconds":..,"saved_seconds":..}`.

## Test (hermetisch, ohne Docker/Netz/Spiel)

```sh
cd deploy/parked && TMPDIR=/dev/shm/parked-test python3 -m unittest -v
```

25 Tests. Abgedeckt: warm_up happy + idempotent + Rollback bei
`pause_game`-Fehler, **Welt-Tick-Invariante via `get_state`** (kein Fortschritt
im PARKED, Fortschritt nach `claim`, red-before-green), claim misst Handover +
verlangt `PARKED` + healthy, recycle warm/kalt, reap stoppt nur Überfällige und
stoppt bei einem `stop`-Fehler die übrigen trotzdem (aggregierter
`ParkedError`), status, Fehler → `ParkedError`, `measure_boot` liefert Differenz
und räumt Cold+Parked auf (kein Container-Leak).

> Lint (ruff) läuft separat in der CI, nicht Teil dieses Verzeichnis-Setups —
der `ruff`-Aufruf wurde entfernt.
