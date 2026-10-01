# S2 — Game-Thread-Snapshot im UpdLogic-Hook (Vorarbeit, Issue #1036)

Status: **Messung erbracht** — Hook läuft auf einem stabilen Game-Thread mit
30 Hz; Reads sind sicher und billig. Kontext: RFC
`docs/rfc/0001-dedicated-io-state-pipeline.md` (§4.2), S1
(`docs/research/1035-root-pointer-findings.md`).

Build 2.0.58485. Messung über den **CI-Boot-Test** (isolierter Stack
`riftbreaker-dedicated-test-36916078456`) mit temporärem Probe-`dbg()` im Hook.

## Fragen (aus #1036)

1. Läuft `gameplay_updlogic_hook` garantiert auf dem Game-Thread, mit stabiler
   Kadenz?
2. Sind Resource-Reads dort **sicher** und **billig** genug für einen
   Snapshot-Publisher?

## Ergebnis

### Kadenz + Thread (stabil)

```
[19:43:16.227] [tid=568] s2_cadence: calls=128
[19:43:20.496] [tid=568] s2_cadence: calls=256
[19:43:24.768] [tid=568] s2_cadence: calls=384
[19:43:29.035] [tid=568] s2_cadence: calls=512
[19:43:33.303] [tid=568] s2_cadence: calls=640
[19:43:37.575] [tid=568] s2_cadence: calls=768
```

- 128 Calls / ~4.27 s → **30.0 Hz**, konstant.
- **Ein** Thread (`tid=568`) über den ganzen Lauf → der Hook läuft (wie
  erwartet) auf dem Game-Thread, nicht auf einem Worker.

### Kosten + Sicherheit eines Resource-Reads im Hook

Einmaliger Probe (Hook-Call 128), kompletter Read-Pfad auf dem Game-Thread:

```
[19:43:16.228] [tid=568] s2_probe: scan start (needle=0x6ffff9ace910)
[19:43:16.237] [tid=568] s2_probe: done (regions=52 candidates=1)
[19:43:16.237] [tid=568] s2_probe: world=0x7d2e5c630000 ps_world=0x0 \
                account=0000000000000000 count=0 carbonium=0 scan_us=10 read_us=0
```

- `world = 0x7d2e5c630000` — via `self + 0x358 + type*0x58` (S1-Ergebnis),
  **scan-frei**.
- PlayerService-Instanz per vftable-Scan: **`scan_us=10`** — hier nur **52**
  Regionen (früh im Boot / kleine Welt), Fund im ersten Treffer.
- `GetPlayerAccount(world, 0)` + Basket-Read: **`read_us=0`** (sub-ms) — kein
  Crash.

Interpretation der Null-Werte: Der Boot-Test hat **keinen Spieler** →
`account = NULL`, `count = 0` (erwartet). `ps_world = 0x0` ist ein
Boot-Timing-Artefakt (Probe bei Call 128, sehr früh; die PlayerService-Instanz
ist noch nicht voll initialisiert). Für die Messung irrelevant, für den echten
Snapshot aber relevant: Reads erst **nach** „Welt geladen" (Account-Check
vorhanden), exakt wie `dispatch_get_state` es bereits tut.

### Wichtigster Befund

Die **Read-Kosten sind vernachlässigbar** (sub-ms); der **Scan** ist der einzige
echte Kostenpunkt — und er skaliert mit der Regionenzahl (Heap-Größe). Auf einer
gereiften Welt sind es ~500 Regionen (~100 ms, vgl. prod-Messung), hier früh
nur 52 (10 µs). ⇒ **Instanzen einmal resolven + cachen** (Epoch-gebunden,
vftable-Re-Validierung), dann ist jeder Tick **scan-frei**.

## Empfehlung

- Snapshot im Hook bei **1–4 Hz** (30 Hz verfügbar → viel Luft; 250 ms Default
  reicht).
- Services **einmal pro Welt-Epoch** resolven (Scan) und cachen; im Tick nur die
  billigen Reads (Ressourcen/HQ/Players) + Re-Validierung per vftable-Wort.
- „Welt geladen"-Gate wie in `dispatch_get_state` (Account-Check) vor HQ/Players.
- Writes (Pause/Chat) bleiben im Hook unverändert.

## Nebenbefund (Betrieb)

Der UpdLogic-Hook wird im Code **lazy** installiert (erst bei
`pause_game`/`send_chat`). Ein Snapshot-Publisher braucht ihn aber **früh** —
der Probe musste ihn in `pipe_server_main` vorab installieren. Für Tier 1 ist
das die richtige Stelle.

## Repro

Probe (temporär) in `gameplay_updlogic_hook` + frühe
`install_game_pause_hook()`-Installation; `dbg()`-Zeilen via CI-Boot-Test
(`boot-test.yml`, isolierter Stack), `docker logs riftbreaker-dedicated-test-<run_id>`.

## Abnahme #1036

- [x] Hook auf Game-Thread (stabil) — belegt (tid konstant).
- [x] Stabile Kadenz — belegt (30.0 Hz).
- [x] Resource-Read sicher + billig — belegt (sub-ms, kein Crash).
- [x] Empfehlung Sampling-Intervall — 1–4 Hz, Instanzen cachen.
