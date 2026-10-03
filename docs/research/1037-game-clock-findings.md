# S3 — Spiel-Tick / Game-Clock (Vorarbeit #1037)

Status: **gelöst (Disasm)** — fixer Sim-Tick + `LogicTimeDelta` gefunden;
monotone Uhr = Akkumulator im 30-Hz-Hook (Umsetzung separat).

Build **2.0.58485**, PDB+DLL: `/opt/rb-re/bin/riftbreaker_dll_win_release.{pdb,dll}`.
Kontext: RFC `0001` §3/§6 (S3), RFC `0002` §4.1 („der Hook liefert nur
`World*` + **Tick**"). S6-Tick-Rate (`tick_hz`) ist bereits umgesetzt (#1069).

## Ergebnis (TL;DR)

`GameplayState::UpdateGameplayLogic(float,float,u64)` (`0x1A1C100`, der
Game-Thread-Hook) ist eine **Fixed-Timestep-Schleife**:

- **Fester Step = 33333,33 µs = 33,33 ms → 30 Hz** (Konstante `.rdata 0x2F32338`).
- Max-`dt`-Clamp `0,0667 s` (1/15) @ `.rdata 0x2E0D820`.

Der „Tick" ist ein **`LogicTimeDelta`-Singleton** auf `World` — ein **`float`**
(= per-Frame-Logik-`dt`, bei Pause 0), scan-frei über den bekannten
`World*`-Root (S1: `self+0x358+type*0x58`) erreichbar.

**Es gibt kein monotones „totale Sim-Zeit"-Feld.** `LogicTimeDelta` ist
per-Frame (`movss [..]` mit `=`, nicht `+=`). Die `.data`-Globals
`0x47E4D9C`/`0x47E4DA0` sind der geglättete `dt` (EMA α=0,5) bzw. der µs-Rest
des Fixed-Step-Akkumulators — beide **oszillierend, nicht monoton**.

⇒ **Monotone Spielzeit = im 30-Hz-Hook akkumulieren** (`+= dt`), nicht als
statisches Feld lesen. Das ist die im RFC `0002` designseitig vorgesehene
Stelle für den Tick.

## Kette (Disasm-belegt)

```
GameplayState::UpdateGameplayLogic(float a=dt, float b, u64, u64)  0x1A1C100  ← Hook (30 Hz)
  ├─ Fixed-Step-Loop:  step 0.0333 s (0x2F32338), dt-Clamp 1/15 s (0x2E0D820)
  │     Akkumulator (float) @ .data 0x47E4D9C ; µs-Rest (int64) @ 0x47E4DA0
  └─ GameplayState::LogicUpdate(float)                              0x1A08B10
       └─ WorldStatesHolder::Update(float)                          0x18FE000   (self+0x358)
            └─ WorldState::Update(float)                            0x1A1C0A0   (Stride 0x58, Dispatch [+0x10/+0x30])
       └─ WorldStatesHolder::Synchronize(float,float)               0x18FDB80
            └─ WorldState::Synchronize(float,float)                 0x1A1B090
                 └─ World::GetOrCreateSingleton<LogicTimeDelta>()   0x194E7B0
                      └─ movss [LogicTimeDelta], dt   (0 bei Pause: Flag WorldState+0x54/+0x55)
```

`WorldState::Update` ist ein Dispatcher: liest Flag `+0x54`, wählt `[+0x10]`
oder `[+0x30]`, ruft deren vtable `+0x18`. Die eigentliche Sim-Update-Kette
läuft über die Sub-Objekte — der Clock-Write landet aber immer in
`LogicTimeDelta`.

## Belegte RVAs (Build 2.0.58485)

| Symbol | RVA | Rolle |
|---|---|---|
| `GameplayState::UpdateGameplayLogic(float,float,u64,u64)` | `0x1A1C100` | Fixed-Step-Loop (Hook-Target) |
| `GameplayState::LogicUpdate(float)` | `0x1A08B10` | Per-Step-Update |
| `WorldStatesHolder::Update(float)` | `0x18FE000` | iteriert WorldStates (`+0x108` Ende) |
| `WorldStatesHolder::Synchronize(float,float)` | `0x18FDB80` | iteriert WorldStates |
| `WorldStatesHolder::GetWorldState(WorldType)` | `0x18F1970` | `(this+0x358) + type*0x58` |
| `WorldStatesHolder::GetWorld(WorldType)` | `0x18F1960` | `*(this+0x358+type*0x58)` (World*) |
| `WorldState::Update(float)` | `0x1A1C0A0` | Dispatch `[+0x10/+0x30]` → vtable `+0x18` |
| `WorldState::Synchronize(float,float)` | `0x1A1B090` | schreibt `LogicTimeDelta` |
| `World::GetOrCreateSingleton<LogicTimeDelta>()` | `0x194E7B0` | → `LogicTimeDelta*` (float) |

Konstanten: Fixed-Step **`0x2F32338`** (=33333,33 µs), dt-Clamp `0x2E0D820`
(=0,0667 s), EMA-Konstante `0x2DCD0B0` (=0,5), Skalen `0x2DF5DBC`/`0x2DF5DD0`
(=1e-6/1e6). `.data`-State: `0x47E4D9C` (float), `0x47E4DA0` (int64).

## Umsetzung (Empfehlung, separat)

Monotone Spielzeit im bereits existierenden `gameplay_updlogic_hook`
(`server/dll/rbbridge.c`) akkumulieren: `g_logic_seconds += (double)a`
(`a` = dt in Sekunden), gated auf den Pause-Flag (`self+0x534`), und als
Feld in `get_state` exponieren (analog `tick_hz`). Kein Engine-Call, kein
Fallback, kein Proxy (`GetTickCount64`-Uptime ist NICHT die Sim-Zeit).

Referenz bereits vorhanden: `g_tick_hz_x100` (S6, #1069) zählt die
UpdLogic-Aufrufe/Gerätewand → reale Hz; das ist das Dilation-Signal, aber
**nicht** die akkumulierte Spielzeit.
