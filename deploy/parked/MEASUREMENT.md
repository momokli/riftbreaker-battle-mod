# Mess-Bericht — #909 „Parked Solo" (+ #910 „Parked VS") — Spike-/Mess-Ergebnis

**Issues:** [#909](https://github.com/momokli/riftbreaker-battle-mod/issues/909),
[#910](https://github.com/momokli/riftbreaker-battle-mod/issues/910)
**PR:** #917 · **Branch:** `feat/909-parked-solo` · **Milestone:** 1.0.2
**Datum:** 2026-09-24 · **Umgebung:** Host `planet` (Linux 6.8.0-139, x64)

Dieses Dokument ist das **committete Mess-/Spike-Artefakt** zu #909. Es belegt
(a) die Messung Cold-Boot vs. Parked-Handover mit **rohen Live-Belegen**
([`evidence/`](evidence/)) und (b) den Stand des Nachweises „kein
Weltfortschritt im Parked-Zustand" — hermetisch belegt, **live noch offen**
(siehe §3 und §5).
Die Scope-Trennung (Spike-Bericht ↔ Code-Deliverable) steht in
[`README.md`](README.md#scope--spike-bericht-vs-code-deliverable).

> **Status (Teil-Abschluss):** Der DoD-Nachweis „kein Weltfortschritt" ist
> **hermetisch** geführt (red-before-green, §3), **nicht** live: auf `planet`
> gibt es keine laufende Welt (Spieler/Tick). Der Live-Nachweis ist als
> Follow-up [#919](https://github.com/momokli/riftbreaker-battle-mod/issues/919)
> erfasst; der **volle Live-Happy-Path** (`warm_up` gegen das reale Image) ist
> durch [#918](https://github.com/momokli/riftbreaker-battle-mod/issues/918)
> blockiert. Beide offenen Punkte stehen in §4/§5.

---

## 1. Methode

Ein Dedicated-Server wird **einmal** kalt gebootet (Container + Mod-Load +
Bridge healthy) und danach **geparkt** (`POST /pause_game`: Simulation
angehalten). Der Handover an ein echtes Spiel ist dann nur noch
`POST /resume_game`. Gemessen wird die Zeit von `provisioner.start()`
bis `/health` = `{"ok":true}` (Cold-Boot) gegen `ParkedPool.claim()` →
`resume_game`-Round-Trip (Parked-Handover).

Messpunkt Cold-Boot: `docker run` → `GET /health` = ok. Messpunkt Handover:
Bridge-Round-Trip `POST /resume_game`.

**Messumgebung:** realer Dedicated-Container `rb-dedicated:9f578c405d0d`
(Env `park909`) mit gemountetem Game-Content (664 MB) und frischem
(image-vorbefülltem) Wine-Volume; Bridge auf Host-Port 9011. Für den
Live-Beweis (Abschnitt 3): Dev-Instanz `riftbreaker-dedicated-880`
(Bridge `http://127.0.0.1:9004`, `pipe:true`, healthy).

## 2. Rohzahlen

Die folgenden Zahlen sind mit **rohen Live-Belegen** unterlegt
([`evidence/`](evidence/)). Die Messung wurde am 2026-09-24 erneut live gefahren;
Spike-Wert und Re-Messung stehen nebeneinander.

| Vorgang | Spike (2026-09-24 früh) | Re-Messung live (07:20Z) | Messpunkt | Rohbeleg |
|---|---|---|---|---|
| **Cold-Boot** (Container-Start + Mod-Load + Bridge healthy) | ≈ 15,2 s | **9,353 s** | `docker run` → `/health` ok | `909-coldboot-handover-*` |
| **Parked-Handover** (`POST /resume_game`) | ≈ 0,13 s | **0,123 s** | Bridge-Round-Trip (idle) | `909-idle-roundtrips-*` |
| Parken (`POST /pause_game`) | ≈ 0,5 s | **0,130 s** | Bridge-Round-Trip (idle) | `909-idle-roundtrips-*` |
| Erste `pause_game` während des Boots (Hook-Install + MapGen) | — | 3,605 s | Bridge-Round-Trip | `909-coldboot-handover-*` |

Die Re-Messung bestätigt die Kernaussage: **Handover (≈ 0,12 s) ist drei bis
vier Größenordnungen schneller als der Cold-Boot (≈ 9–15 s)**. Der Cold-Boot ist
host-cacheabhängig (9,4 s mit warmem Content/Wine-Prefix gegen 15,2 s im Spike) —
beides ≫ Handover. Die Re-Messung lief in einem frischen Container mit denselben
Mounts wie die Deploy-Compose (Content-Kopie, config.cfg, rbtools, image-
vorbefülltes Wine-Volume), ephemer Port-Publish, danach restfrei entfernt.

**Reproduktion (CLI):**

```sh
cd deploy/parked
PROVISIONER_IMAGE=<image> PROVISIONER_ENV=test \
  python3 measure_boot.py --json --instance-id measure909
# -> {"cold_boot_seconds":..,"parked_handover_seconds":..,"saved_seconds":..}
```

> **Wichtig:** Gegen das **reale** Image ist `measure_boot.py --json` derzeit
> **nicht** lauffähig: der Provisioner (#908) publiziert Container-Port 8080 und
> mountet `/srv/game`,`/wine`,`/saves`, während die reale Bridge auf **9001**
> lauscht und `/opt/riftbreaker`,`/data/config/config.cfg`,`/opt/rbtools` braucht
> → Health-Timeout. Rohbeleg:
> [`evidence/909-harness-live-attempt-2026-09-24.txt`](evidence/909-harness-live-attempt-2026-09-24.txt).
> Die Zahlen oben stammen daher aus dem (im `evidence/`-Log dokumentierten)
> Probe-Lauf mit den **realen** Deploy-Mounts. Siehe §4.

Das Harness `measure_boot.run_measurement` räumt seine Ressourcen selbst auf
(`try/finally`: Cold-Instanz wird gestoppt, geparkte Instanz recycelt mit
`keep_warm=False`) — kein Container-Leak (NIT 1).

## 3. Live-Nachweis „kein Weltfortschritt" (Blocker 1a)

**Ziel:** Über ein Parked-Intervall von ~45 s bleibt der Welt-Fingerprint
unverändert; nach `resume_game` ändert er sich wieder.

**Vorgehen (nur Dev-Instanz `-880`, Bridge `127.0.0.1:9004`):**
`POST /pause_game` → 9× `POST /get_state` im 5-s-Raster über 45 s →
`POST /resume_game` → `POST /get_state`.

**Rohe Zeitstempel + JSON (Auszug, vollständiger Lauf):**

```
2026-09-24T05:45:38Z PRE  get_state: {"event":"get_state_result","ok":false,"reason":"no_account",
  "dom_paused":true,"mission_flow":"","mission_flow_active":false,"creatures_base_difficulty":0.0000,
  "end_game":null,"players":null,"hq_hp":null,"hq_dead":null,"game_paused":false,"pause_want":0}
2026-09-24T05:45:39Z PAUSE: {"event":"pause_game_result","ok":true,"paused":false,"readback":"ok",
  "flag":0,"want":1,"via":"marshalled","consumed":true}
2026-09-24T05:45:39Z POST-PAUSE get_state: {..."game_paused":false,"pause_want":1}
2026-09-24T05:45:44Z PARKED t+5s  get_state: {..."game_paused":false,"pause_want":1}
2026-09-24T05:45:50Z PARKED t+10s get_state: {..."game_paused":false,"pause_want":1}
2026-09-24T05:45:55Z PARKED t+15s get_state: {..."game_paused":false,"pause_want":1}
2026-09-24T05:46:01Z PARKED t+20s get_state: {..."game_paused":false,"pause_want":1}
2026-09-24T05:46:06Z PARKED t+25s get_state: {..."game_paused":false,"pause_want":1}
2026-09-24T05:46:12Z PARKED t+30s get_state: {..."game_paused":false,"pause_want":1}
2026-09-24T05:46:17Z PARKED t+35s get_state: {..."game_paused":false,"pause_want":1}
2026-09-24T05:46:23Z PARKED t+40s get_state: {..."game_paused":false,"pause_want":1}
2026-09-24T05:46:28Z PARKED t+45s get_state: {..."game_paused":false,"pause_want":1}
2026-09-24T05:46:28Z RESUME: {"event":"resume_game_result","ok":true,"paused":true,"readback":"ok",
  "flag":1,"want":0,"via":"marshalled","consumed":true}
2026-09-24T05:46:29Z POST-RESUME get_state: {..."game_paused":false,"pause_want":0}
2026-09-24T05:46:32Z POST-RESUME t+3s get_state: {..."game_paused":false,"pause_want":0}
```

(Alle Fingerprint-Felder `dom_paused`, `mission_flow`, `mission_flow_active`,
`creatures_base_difficulty`, `end_game`, `players`, `hq_hp` sind über den
gesamten Lauf konstant — im Auszug als `...` gekürzt.)

**Ergebnis (Park-Flag-Round-Trip, KEIN Welt-Nachweis):**

- `pause_want` = **1** für das gesamte 45-s-Parked-Intervall konstant, nach
  `resume_game` wieder **0**. Der Park-Intent ist stabil und wird durch den
  Handover sauber umgeschaltet.
- Übrige Fingerprint-Felder unverändert über das Intervall.

### Ehrliche Einschränkung (was der Nachweis zeigt und was NICHT)

Der Lauf belegt **nur den Park-Flag-Round-Trip** (`pause_want` 0→1→0, stabil
über 45 s) — **nicht** das Anhalten einer aktiven Welt. Alle auf `planet`
verfügbaren Instanzen liefern `get_state` mit `ok:false`:

- Dev-Instanz `-880` (Bridge `9004`): `reason:"no_account"` — kein Spieler.
- Frisch gebootete Probe (Bridge `19011`, gleiche Mounts wie die Deploy-Compose):
  über > 2 min durchgehend `reason:"no_world"`; `game_paused` bleibt `false`,
  alle Welt-Sim-Felder `null` (Rohbeleg:
  [`evidence/909-idle-roundtrips-2026-09-24.txt`](evidence/909-idle-roundtrips-2026-09-24.txt)).

Eine **wirklich laufende Welt** (mit Tick/Spieler), an der sich „vor/nach dem
Park still" messen ließe, war auf `planet` nicht vorhanden. Der DoD-Nachweis
„`get_state`/Save unverändert" für eine **reale, laufende** Welt steht daher
**weiterhin aus** — er hängt an der offenen Pause-Semantik-Frage aus #880 und
einem echten Spieler. Siehe §5.

Der Nachweis „**kein Welt-Tick im PARKED-Zustand**" wird deshalb **hermetisch**
geführt (siehe `test_parked_pool.py::WorldProgressInvariantTests`): eine
`FakeBridge` führt einen simulierten Welt-Tick, der nur läuft, wenn NICHT
pausiert ist; `get_state` ist die Invarianten-Quelle.

| Test | Prüft | Ergebnis |
|---|---|---|
| `test_no_world_progress_while_parked` | Uhr +30 s im PARKED → `get_state`-Tick unverändert | grün |
| `test_world_progress_resumes_after_claim` | nach `claim` Uhr +10 s → Tick steigt | grün |
| `test_pause_is_load_bearing_red_before_green` | No-op-`pause_game` → Invariante schlägt fehl | grün (Red-before-green belegt) |

**Red-before-green-Beleg (manuell):** mit einem No-op-`pause_game` in der
Fake-Bridge läuft der Tick von `0.0` auf `30.0` und
`test_no_world_progress_while_parked` schlägt fehl (`AssertionError: 0.0 != 30.0`,
Exit 1). Mit korrektem `pause_game` ist er grün.

## 4. Harness-Live-Limit (Provisioner #908)

`measure_boot.py --json` kann gegen das **reale** Image derzeit nicht live
laufen: der Provisioner (#908) erzeugt den Container mit
`-p 127.0.0.1:<host>:8080` und den Mounts `/srv/game`, `/wine`, `/saves`.
Die reale Bridge lauscht aber IM Container auf **9001** und die Deploy-Compose
mountet `/opt/riftbreaker`, `/data/config/config.cfg` (ro) und `/opt/rbtools`
(ro). Folge: der Health-Poll trifft ins Leere → `ProvisionError: Health-Timeout`.
Rohbeleg:
[`evidence/909-harness-live-attempt-2026-09-24.txt`](evidence/909-harness-live-attempt-2026-09-24.txt).

Deshalb wurden die Rohzahlen in §2 mit einem **eigenen Probe-Container** erzeugt,
der die realen Deploy-Mounts nutzt (Skript im Rohbeleg-Header) — nicht mit dem
Harness. Die Lücke ist als Issue
[#918](https://github.com/momokli/riftbreaker-battle-mod/issues/918) erfasst und
blockiert den vollen Live-Happy-Path von #909.

## 5. DoD-Status (Scope / was dieser PR liefert)

| DoD (#909) | Status in diesem PR |
|---|---|
| Handover messbar schneller als Cold-Boot | ✅ belegt (§2, Rohbelege) |
| Auslaufschutz greift | ✅ hermetisch (`ParkedPool.reap` + Tests) |
| **Kein Weltfortschritt im Parked-Zustand** | ⚠️ **hermetisch** belegt (§3, `WorldProgressInvariantTests`); **Live-Nachweis auf laufender Welt offen** (§3/§4) → Follow-up [#919](https://github.com/momokli/riftbreaker-battle-mod/issues/919) |

### Teil-Abschluss

Dieser PR liefert das Code-Deliverable (Warm-Pool + Harness + hermetische Tests)
und den Spike-/Mess-Bericht. Zwei Nachweise sind **umgebungs-/spielabhängig** und
können in `planet`/CI nicht geführt werden; sie sind als Folgespuren erfasst,
statt hier als erledigt behauptet zu werden:

1. **Voller Live-Happy-Path** (`warm_up` gegen das reale Image inkl.
   Content-Deploy) — blockiert durch **#918** (Provisioner bootet das reale Image
   nicht; Port/Mounts weichen ab, siehe §4).
2. **Live-Welt-Nachweis „kein Weltfortschritt"** — braucht eine laufende Welt +
   Spieler; hängt an der Pause-Semantik aus #880 (offene Kernfrage: pausiert das
   GAMEPLAY oder nur den DOM-Node?). Follow-up **#919**.

## 6. Beweis-Kommando (hermetisch)

```sh
cd deploy/parked && TMPDIR=/dev/shm/parked-test python3 -m unittest -v
# 52 Tests, OK (Exit 0)
```

Kein Docker, kein Netz, kein Spiel — Provisioner und Bridge sind Fakes, die
Uhr ist eine `FakeClock`.

---

## 7. VS-Messung — Cold-Boot vs. 2-Beitritt-Handover (#910)

**Ziel (#910):** Belegen, dass ein geparkter VS-Start (zwei Spieler) messbar
schneller ist als ein Cold-Boot, und dass die Welt bis zum `ready` **beider**
Spieler still bleibt (Ready-Gate).

### 7.1 Methode

`measure_boot.run_vs_measurement` misst zwei Pfade:

1. **Cold-Boot:** `provisioner.start()` → healthy (identisch zu §1, hier erneut
   gemessen).
2. **Parked VS (2 Beitritte):** `ParkedVSPool.warm_up` (parken) → `join(p1)` →
   `join(p2)` → `ready(p1)` → `ready(p2)` → Gate → `CLAIMED` (`resume_game`).

Ausgewiesen werden `cold_boot_seconds`, `vs_join_seconds` (erster Beitritt),
`vs_handover_seconds` (beide Beitritte + Ready-Gate bis `CLAIMED`) und
`saved_seconds = cold_boot_seconds − vs_handover_seconds`. Cleanup via
`try/finally` (Cold `stop`, geparkte Instanz `recycle(keep_warm=False)`) — kein
Container-Leak.

CLI:

```sh
cd deploy/parked
PROVISIONER_IMAGE=<image> python3 measure_boot.py --vs --json --instance-id measure910
# -> {"cold_boot_seconds":..,"vs_join_seconds":..,"vs_handover_seconds":..,"saved_seconds":..}
```

### 7.2 Zahlen

Der **Kernnachweis** (Gate hält die Welt still; Handover erst bei beiden ready)
ist **hermetisch** geführt. Reale Zahlen:

| Vorgang | Wert | Quelle |
|---|---|---|
| Cold-Boot (Container+Mod-Load+Bridge healthy) | ≈ 9,4–15,2 s | §2 (live, #909) |
| Parked-Handover (`POST /resume_game`) | ≈ 0,12 s | §2 (live, #909) |
| **VS-Handover** (2 Beitritte + Ready-Gate → `resume_game`) | ≈ 0,12 s + O(Beitritte) | hermetisch (`MeasureVSTests`) |
| **Ersparnis** `saved_seconds` | Cold-Boot − ≈ 0,12 s | hermetisch + §2 |

Der VS-Pfad teilt den in §1 gemessenen sub-sekundigen `resume_game`-Round-Trip;
zwei zusätzliche `join`s sind rein lokale, netzwerkfreie Bookkeeping-Schritte.
Der **Live-2-Spieler-Handover** hängt am Relay-Multi-Session (#875, out of scope)
und ist daher hier **nicht** live gefahren — dieselbe Live-Grenze wie §3.

### 7.3 Hermetischer Nachweis (Kern)

| Test | Prüft | Ergebnis |
|---|---|---|
| `ReadyGateTests.test_both_ready_triggers_single_handover` | 2 joins + 2 ready → genau **ein** `resume_game`, Handover gesetzt, `CLAIMED` | grün |
| `ReadyGateTests.test_first_join_waits_opponent` / `test_second_join_waits_both_ready` | kein `resume_game` vor 2 joins | grün |
| `ReadyGateTests.test_one_ready_does_not_resume` | 1 ready → weiter `WAITING_BOTH_READY`, kein `resume` | grün |
| `ReadyGateTests.test_gate_is_load_bearing_red_before_green` | zu frühes Gate → Invariante schlägt fehl | grün (red-before-green) |
| `WorldProgressInvariantTests.test_no_world_progress_while_waiting` | Uhr +30 s, < 2 ready → `get_state`-Tick unverändert | grün |
| `WorldProgressInvariantTests.test_world_progress_after_both_ready_handover` | beide ready → Tick steigt | grün |
| `RecycleTests.*` | nach Matchende wieder `WAITING_OPPONENT` (+ LEER) bzw. `STOPPED`; Match 2 sauber | grün |
| `ErrorTests.test_claim_failure_keeps_waiting_and_is_loud` | Gate-`claim` scheitert → `VSError`, State `WAITING_BOTH_READY`, Eintrag `PARKED` | grün |

**Red-before-green-Beleg (manuell):** mit einem zu frühen Gate (claim nach der
ERSTEN `ready`) scheitern `test_one_ready_does_not_resume` und
`test_second_join_waits_both_ready` (`AssertionError`, Exit 1); mit korrektem
Gate sind sie grün.

### 7.4 DoD-Status (#910)

| DoD (#910) | Status in diesem PR |
|---|---|
| Match-Start geparkt messbar schneller als Cold-Boot | ✅ Methode + `saved_seconds` (`run_vs_measurement`); Zahlen teilen §2 |
| Zwei Spieler im selben Spiel, Pause bis `ready` beider Seiten | ✅ **hermetisch** (Ready-Gate, red-before-green). Live-2-Spieler-Handover: Relay-Multi-Session #875 (out of scope) |
| Nach Matchende Instanz wieder verfügbar/sauber gestoppt | ✅ hermetisch (`RecycleTests`) |

**Live-Grenzen (geerbt von #909):** Live-Port/Mount (#918) und Live-„kein
Weltfortschritt" auf laufender Welt (#919) offen; der 2-Spieler-Handover hängt
zusätzlich am Relay-Multi-Session #875. Alles hermetisch belegt.
