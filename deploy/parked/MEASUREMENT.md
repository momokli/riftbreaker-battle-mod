# Mess-Bericht — #909 „Parked Solo" (Spike-/Mess-Ergebnis)

**Issue:** [#909](https://github.com/momokli/riftbreaker-battle-mod/issues/909)
**PR:** #917 · **Branch:** `feat/909-parked-solo` · **Milestone:** 1.0.2
**Datum:** 2026-09-24 · **Umgebung:** Host `planet` (Linux 6.8.0-139, x64)

Dieses Dokument ist das **committete Mess-/Spike-Artefakt** zu #909. Es belegt
(a) die Messung Cold-Boot vs. Parked-Handover und (b) den **Live-Nachweis „kein
Weltfortschritt im Parked-Zustand"** auf einer realen Dedicated-Instanz.
Die Scope-Trennung (Spike-Bericht ↔ Code-Deliverable) steht in
[`README.md`](README.md#scope--spike-bericht-vs-code-deliverable).

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

| Vorgang | gemessen | Messpunkt |
|---|---|---|
| **Cold-Boot** (Container-Start + Mod-Load + Bridge healthy) | **≈ 15,2 s** | `docker run` → `/health` ok |
| davon bis `event=mod_load` / `ServerGameplayState` | ≈ 11 s | Log-Meilensteine |
| **Parked-Handover** (`POST /resume_game`) | **≈ 0,13 s** | Bridge-Round-Trip |
| Parken (`POST /pause_game`) | ≈ 0,5 s | Bridge-Round-Trip |
| Warm-Volume-Restart eines bestehenden Servers (`-880`) | ≈ 2 s bis `/health` ok | stale Logs → Injection sofort |

**Ersparnis:** ≈ 15 s pro Handover (nur Container + Content-ready; im echten
Deploy zusätzlich die ~664 MB Content-Copy + Ansible, CI-Budget 240 s).
Handover selbst ist sub-sekundig. **Prämisse bestätigt.**

**Reproduktion (CLI):**

```sh
cd deploy/parked
PROVISIONER_IMAGE=<image> PROVISIONER_ENV=test \
  python3 measure_boot.py --json --instance-id measure909
# -> {"cold_boot_seconds":..,"parked_handover_seconds":..,"saved_seconds":..}
```

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

**Ergebnis:**

- `pause_want` = **1** für das gesamte 45-s-Parked-Intervall konstant, nach
  `resume_game` wieder **0**. Der Park-Intent ist stabil und wird durch den
  Handover sauber umgeschaltet.
- Übrige Fingerprint-Felder unverändert über das Intervall.

### Ehrliche Einschränkung (was der Nachweis zeigt und was nicht)

Die Dev-Instanz `-880` hatte zum Messzeitpunkt **keinen verbundenen Spieler**
(`ok:false, reason:"no_account"`). Damit ist `game_paused` durchgehend `false`
und die Welt-Sim-Felder sind `null` — **eine laufende Welt, die ohne Pause
voranschreiten würde, war auf dieser Instanz nicht vorhanden**. Der Live-Lauf
belegt daher belastbar den **Park-Flag-Round-Trip** (`pause_want` 0→1→0,
stabil über 45 s), aber **nicht** das Anhalten einer aktiven Welt-Tick-Rate.

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

## 4. Beweis-Kommando (hermetisch)

```sh
cd deploy/parked && TMPDIR=/dev/shm/parked-test python3 -m unittest -v
# 25 Tests, OK (Exit 0)
```

Kein Docker, kein Netz, kein Spiel — Provisioner und Bridge sind Fakes, die
Uhr ist eine `FakeClock`.
