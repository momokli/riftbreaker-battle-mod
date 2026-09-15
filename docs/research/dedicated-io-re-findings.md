# Dedicated IO Interface — RE-Befunde (Build 2.0.58485)

Live-RE-Stand für den **READ-OUT**-Pfad des Dedicated IO Interface (Issue #363).
Ergänzt `docs/research/pdb-symbol-validation.md` (Issue #364) um die konkreten
Disasm-Befunde. Stand: 2026-09-13, auf `planet` gegen
`/srv/rbgame/bin/riftbreaker_dll_win_release.dll` + `.pdb` (Build 2.0.58485 /
GOG == Dedi) verifiziert.

## Namespace-Korrektur

Die Spiel-Objekte liegen im Namespace **`Riftbreaker`**, nicht `Exor` (das ist
nur die Engine: `Exor::Vector`, `Exor::TypeRegistry`, `Exor::UtfString`, …):

- `Riftbreaker::PlayerService`
- `Riftbreaker::ResourceAccount`
- `Riftbreaker::ResourceBasket`

Relevante public Symbole (aus `llvm-pdbutil dump -publics`, mit RVAs):

| Symbol | RVA |
|---|---|
| `Riftbreaker::ResourceBasket::SetResourceAmount(GameplayResourceDefHolder const&, ResourceValue)` | public |
| `Riftbreaker::ResourceBasket::RemoveNullResources()` | public |
| `Riftbreaker::ResourceAccount::CanAffordExpense(ResourceBasket const&)` | public |
| `Riftbreaker::PlayerService::AddResourceAmount(...)` | `0xF1E3D0` |
| `Riftbreaker::PlayerService::GetGlobalResourcesList(...)` | `0xF28060` |
| `Riftbreaker::ResourceBasket::GetResourceAmount(...)` | `0x2D3520` |

## FNV-1a bestätigt (im Binary)

`GetResourceAmount` enthält die Hash-Konstanten wörtlich:

```
mov r9d, 0x811c9dc5          ; FNV-1a Offset-Basis
...
imul r9d, eax, 0x1000193     ; FNV-1a Primzahl
```

→ carbonium-Hash = `0x659cc791` (FNV-1a("carbonium"), extern berechnet).

## ResourceBasket-Layout (aus GetResourceAmount-Disasm)

`this` = `ResourceBasket*`:

| Offset | Typ | Bedeutung |
|---|---|---|
| `+0x08` | `entry*` | sortiertes Array |
| `+0x10` | `size_t` | Anzahl Einträge |

Eintrag = **16 Byte**:
- `[entry+0x00]` = `uint32` StringHash (Sortierschlüssel, aufsteigend)
- `[entry+0x08]` = `uint64` ResourceValue

Zugriff = **Binärsuche** über das sortierte Array (im Disasm klar sichtbar:
`shl rdx,4` / Halbieren / `cmp [rax+r*8], hash`).

## Ketten-Anfang (PlayerService → Container)

Im Disasm der Funktion bei `0xF1E700` (direkt nach `AddResourceAmount`) und
`0x180C60050` bestätigt:

```
PlayerService      (vftable RVA 0x2E8E910)
  [+0x08]          → Resource-System (Pointer)
    [+0x30]        → Container (EMBEDDED, `lea` — NICHT dereferenziert!)
```

**Wichtige Korrektur:** der Container liegt **eingebettet** bei
`resource_system + 0x30` (Adresse), nicht als Pointer *an* Offset 0x30. Der
erste Probe-Entwurf dereferenzierte dort fälschlich (`*(rs + 0x30)`); Probe v2
nutzt `rs + 0x30`.

Container-Lookup wird über `0x180EF4AC0` / `0x180C26E50` / `0x181DD06F0`
abgewickelt (templatisierter `FlatMap`/HashMap-Code, TLS/Registry-Muster).

## Noch offen (2 Hops bis carbonium)

1. Container → `lookup(playerId=0)` → `ResourceAccount*`
2. `ResourceAccount` → `ResourceBasket` (Member-Offset; es existiert
   `ClassField<ResourceAccount, ResourceBasket>`)

Danach: Basket `[+8]`/`[+0x10]` → Binärsuche `0x659cc791` → carbonium-Wert.

**Ansatz:** empirisch statt weiterem Disasm — Probe v2 (in `rbbridge.c`)
dumpt bei einem Live-Restart Speicher-Fenster um `resource_system` + `container`
(`probe_dump`-Events) und liefert die Offsets aus echten Werten.

## PDB / Tooling

- PDB ist **stripped** (keine Typinfo, TPI/IPI leer) — siehe Issue #364. Symbole
  (Namen + Adressen) sind im `-publics`-Stream reichlich vorhanden.
- PDB/DLL liegen auf **planet** (`/srv/rbgame/bin/…`) **und lokal** (macOS/CrossOver,
  `~/Library/Application Support/CrossOver/Bottles/gams/drive_c/Program Files (x86)/The Riftbreaker/bin/…`),
  byte-identisch (77.885.440 / 252.334.080).
- Tooling: `llvm-pdbutil` / `llvm-readobj` (mac + planet), Disasm via
  `/opt/rb-re/venv` (pefile + capstone) + `/tmp/disasm.py` auf planet.

## Symbol-Auflösung + Korrekturen (2026-09-13)

`llvm-pdbutil dump -publics` gegen die Mac-PDB liefert die exakten Namen:

| RVA | Symbol (demangled) |
|---|---|
| `0xC60050` | `ResourceAccount* Riftbreaker::GetPlayerAccount(Exor::World*, unsigned int)` |
| `0x2D3520` | `Optional<pair<StringHash, ResourceValue>> ResourceBasket::GetResourceAmount(StringHash const&) const` |
| `0xF1E3D0` | `bool PlayerService::AddResourceAmount(unsigned int, UtfString const&, float, bool)` |
| `0xF28060` | `Vector<UtfString> PlayerService::GetGlobalResourcesList(unsigned int)` |
| `0xF1E700` | `UnitService::AnimBoneForwardToTargetEntity` (Animation — NICHT Ressource!) |
| `0xEF4AC0` | `Ecs::GetComponents` (nicht der Container-Lookup) |

Konsequenzen:

- `[PlayerService+8]` ist ein **`World*`**, kein „Resource-System". Der Account-Zugriff
  ist `GetPlayerAccount(World*, playerId)` → `ResourceAccount*`; der Container liegt
  bei `World+0x30` (bestätigt über `lea rcx,[rdi+0x30]` in `GetPlayerAccount`).
- **Werte sind `float`** (`AddResourceAmount` nimmt `float`). `ResourceValue` ist ein
  8-Byte-Struct, vermutlich `{float current, float max}` — „100 von 350" = `{100.0f, 350.0f}`.
- Der Basket ist **StringHash-keyed** (`GetResourceAmount(StringHash)`), also muss
  carbonium `0x659cc791` im Basket stecken. Dass der Scan ihn nicht fand, liegt am
  **pipe_bridge-Race** (später Treffer geht verloren), nicht am Key-Format.
- Frühere Annahme „Container-Lookup = 0xEF4AC0" war falsch (das ist `Ecs::GetComponents`);
  der echte Lookup läuft über `0x180C26E50`/`0x181DD06F0` (aus `GetPlayerAccount`).

## LIVE-BESTÄTIGT (2026-09-13): carbonium gelesen = 300.0

Account-Basket-Dump via `GetPlayerAccount(World*, 0)` (Function-Call aus der DLL,
Account[+8]=Array, [+0x10]=Count, 16-B-Entries) liefert live:

| Hash | Ressource | Wert (fixed-point) |
|---|---|---|
| `0x0d01a504` | steel | 300000000 |
| `0x1b9f8256` | titanium | 0 |
| `0x659cc791` | **carbonium** | **300000000 = 300.0** |
| `0x666d2128` | palladium | 0 |
| `0x6ddeafbe` | uranium | 0 |
| `0x9c6fc222` | cobalt | 0 |

**Value-Format = int64 Fixed-Point, Skala 10^6** (`300.0` → `300000000`). Die
`ResourceValue` (8 Byte) ist also ein skaliertes int64, KEIN float/double — deshalb
sahen die rohen Scan-QWORDs „riesig" aus.

Vollständiger Lese-Pfad (deterministisch, im `probe` implementiert):
```
PlayerService → [+8] = World*
  → GetPlayerAccount(World*, 0) = RVA 0xC60050 → ResourceAccount*
  → account[+8] = sortiertes (StringHash u32, ResourceValue int64) Array
  → account[+0x10] = Count
```
Rest noch offen war: max/capacity („/300") — gelöst, siehe „Phase B“ unten.

## Phase A: get_state (sauber, symmetrisch zu exec) — LIVE

`POST /get_state` (pipe_bridge) → `{"cmd":"get_state"}` → `dispatch_get_state` →
**eine** `get_state_result`-Zeile. Symmetrisch zu `/exec` → `exec_result`.

Live bestätigt (Spieler online, HQ gebaut):
- carbonium `0x659cc791`: 300 → 100 (HQ kostet 200)
- steel `0x0d01a504`: 300 → 100 (HQ kostet auch 200 steel)
- `0xa4c40a93`: 4.000.000 → 7.000.000 (steigt beim Bauen — Produktion/Energie)

Damit ist der bidirektionale Kanal **baugleich**: IN (`exec`) / OUT (`get_state`)
über denselben Pfad, dieselbe Request/Response-Form, denselben `handle_line`-Dispatch.

## Phase B: carbonium_max (capacity) lesen — LIVE (#370)

Das Capacity-/Max-Feld pro Ressource liegt in einer **separaten Hash-Map** im
`ResourceAccount` (nicht im Basket):

```
ResourceAccount + 0x20 = UnorderedMap<StringHash, float max>  (Display-Einheiten)
  Lookup: 0x18028ac00(container, &out, &hash)  -> out[0] = node*
  node   : [+0] = next, [+8] = StringHash(u32), [+0xc] = float max
  max_fixed = (int64)(scale * max_float)
             scale = .data-Globale RVA 0x4794210 (zur Laufzeit 1e6,
                     gleiche Konstante wie in AddResourceAmount)
```

Quelle: Disasm der Capacity-Prüfung in `PlayerService::AddResourceAmount`
(RVA `0xF1E3D0`) → `CanAffordExpense` (`0x2D0930`) → `0x1802ccf90`:
`(current + delta) <= (int)(scale * max_float)`; die Lookup-Funktion ist
`0x18028ac00` (bucket/chain, `movss xmm6, [node+0xc]`).

Implementiert in `read_resource_max()` (rbbridge.c); `get_state` liefert
`carbonium_max`. Live validiert: Speicher bauen → max 300 → 350.

## Phase C: Wave-Counter + time-to-next + full state egress — LIVE (#376)

`dom_mananger` (Lua class `dom_mananger -> event_manager -> LuaGraphNode`) hält:

- `currentDifficultyLevel` (1..9) = der **Wave-Counter**.
- `spawner` (StateMachine) + state-abhängige Timer: `cooldownTimer`/`idleTimer`/
  `waitForSpawnTimer`/`sleepSafeTimer`; `time_to_next` = max davon (ceil),
  `wait` = `GetDurationLimit()-GetDuration()` (fixed 5s).
- `difficultyIncrease` (StateMachine) + `time_to_next_difficulty` (Restzeit).

**Thread-safety (der Kern des #376-Fixes):** Lua NICHT vom pipe thread anfassen
(crasht). Deshalb: Mod wrappt `dom_mananger:Update` auf dem game thread und ruft
`_G.rbbridge_capture_state(json)`; die DLL cached den String (spinlock),
`get_state` liest nur den Cache.

**Resolver:** `lua_State*` NICHT über `World::GetSystem<LuaSystem>()` (`0x194EDA0`)
holen — das page-faultet beim Boot (`World::GetSystem(TypeHash)`), weil die
System-Map des World noch aufgebaut wird. Stattdessen Memory-Read: vftable-Scan
auf `LuaGraphNode` (`base + 0x2F46D70`) + `[+0x20]` luabind-object → `lua_State*`.
Reine `VirtualQuery` + `safe_read_u64`, kein World-Zugriff.

Live bestätigt (Build 2.0.58485): `wave=1`, `dom_state="wait"`, `time_to_next=5`
(fixed wait), `time_to_next_difficulty=200`, `players=1`, volle Service-Getter
(Mission/Biome/Warmup/etc.) — kein Crash, pipe stabil (`/health` →
`{"ok":true,"pipe":true}`).

**Noch offen (nächste Schritte):**

- HUD „next wave in X" = `MissionService:ActivateMissionFlow` (`time_max`), NICHT
  im DOM-Snapshot — der fehlende „echte" Next-Wave-Countdown.
- Typed Control-Commands (`pause_dom`/`resume_dom`/`spawn_wave`/`end_game`/…)
  als Wrapper über bestätigte `exec`-Strings.

## Phase D: WRITE path — Game-Thread-Kommando-Marshalling (LIVE #376)

`ConsoleService::ExecuteCommand` (`0x1C0BEF0`) dispatcht den Command-Handler
**inline auf dem Thread des Aufrufers** — ein Lua-Kommando vom Pipe-Thread
crasht den Server (gleiche Klasse wie der READ-Crash). Fix: Kommandos auf den
Game-Thread marshalen.

- `ConsoleService::Update(float)` RVA `0x1C1FBA0` — läuft jede Frame auf dem
  Game-Thread (unabhängig von DOM-Suspension).
- `LuaGraphNode::Update` RVA `0x1BAA140` — prüft `[this+0xF1]` (suspended) und
  kehrt bei suspended sofort zurück → der Mod-Hook `dom_mananger:Update` läuft
  NICHT, solange die DOM suspended ist (darum reicht der Mod-Hook für den
  WRITE-Pfad nicht).

Architektur: `dispatch_exec` legt das Kommando nur in einen Spinlock-Puffer
(`g_pending_cmd`); ein vtable-Detour auf `ConsoleService::Update` (Slot-Scan auf
`base + 0x1C1FBA0` + `VirtualProtect`) drained den Puffer und ruft
`ExecuteCommand` auf dem Game-Thread auf, dann chained er zum Original-`Update`.

Live bestätigt (Build 2.0.58485): `debug_dom_pause`/`debug_dom_resume`/
`debug_dom_manager_spawn_wave_level` → `ok:true`, kein Crash; pause friert
`time_to_next` ein, resume taut auf, spawn_wave spawnt Kreaturen.

## Control-Workflow (STOP/START von außen, manuell)

Join (ohne HQ): DOM steht in `wait` (suspended) — nichts passiert; der Mod hält
den Wellen-Spawn bis zur HQ-Platzierung (`RBB.commenced`). Für volle Kontrolle:

- **STOP/freeze:** `debug_dom_pause` (friert DOM-Timer ein). Achtung: die
  HUD-Mission-Flow `MissionService:ActivateMissionFlow`/`time_max` friert
  dabei NICHT mit ein (kein `deactivate_mission_flow`-Command registriert).
- **START:** `debug_dom_resume` + `debug_dom_manager_spawn_wave_level N`.

## #476: Vanilla-Naturwellen-Schalter (DifficultyService) — RE 2026-09-15

Ziel: den Schalter finden, der `dom_mananger` dauerhaft vom Naturwellen-Spawning
abhält ("aus", nicht "freeze").

> **Namens-Hinweis.** `dom_mananger` ist die **Lua-Klasse** (Eigen-Schreibweise
> des Spiels, so auch `class 'dom_mananger' (event_manager)`); die **Datei**
> heißt `dom_manager.lua`. Beide Schreibweisen sind beabsichtigt (Review #478).

### DifficultyService-Layout (Disasm, Build 2.0.58485, planet)

```
DifficultyService  (RTTI .?AVDifficultyService@Riftbreaker@@)
  +0x08 = Exor::World*
World-System (TypeHash 0x221d7af2; Getter RVA 0xC5F4A0, AOB-signiert):
  +0x08  = difficulty name UtfString
  +0x118 = wave_strength UtfString
  +0x1B9 = mission_infinite bool        (DifficultyService::IsMissionInfinite)
  +0x1BA = waves_disabled bool          (DifficultyService::AreWavesDisabled)
```

- `AreWavesDisabled` (RVA 0x1003610): `mov rcx,[rcx+8]; call 0xC5F4A0;
  movzx eax, byte [rax+0x1BA]; ret`.
- `GetWaveStrength` (RVA 0x1010500): liefert UtfString @ `+0x118`.
- `IsMissionInfinite` (RVA 0x10120C0): `movzx eax, byte [rax+0x1B9]`.
- Getter-Prolog (AOB): `48 83 EC 48 / 48 81 C1 C0 00 00 00 / 48 8D 54 24 20 /
  41 B8 F2 7A 1D 22 / E8 <rel32>` (TypeHash build-stabil).

### Der "aus"-Mechanismus (Lua, dom_manager.lua v2)

`pauseAttacks = AreWavesDisabled() || (GetWaveStrength()=="sandbox") ||
rules.pauseAttacks`. Bei `pauseAttacks==true` läuft der `spawner` nur
`idle<->dummy_state`/`prepare_spawn`; `OnEnterPrepareSpawn` überspringt
`PrepareWave`/`Streaming` → 0 Naturwellen. **Das ist "aus".**

DifficultyDef `sandbox` (`scripts/difficulty/difficulties.difficulty`):
`wave_strength "sandbox"`, `mission_infinite 1`, `mission_duration 0` →
`set difficulty "sandbox"` ist der deklarative Boot-Schalter.

### SetSuspended ist Freeze, NICHT "aus"

```
LuaGraphNode::SetSuspended(bool) RVA 0x1BA6CB0:  mov byte [rcx+0xF1], dl ; ret
LuaGraphNode::Update(float)      RVA 0x1BAA140:  cmp byte [rcx+0xF1],0
                                                 je  <body> ; ret
```

Suspendiert → `Update` kehrt sofort zurück (DOM-Timer stehen) → nur "Pause".

### Live-Beobachtung (dev, planet 2026-09-15)

Injection der neuen DLL + `set difficulty "sandbox"` + Restart: Injection ok
(`rbbridge.dll geladen`, Pipe-Server läuft), aber der DedicatedServer crashte
danach während der Map-Generierung (`CrashHandlerWin32`, `MapGenerator.cpp:976`),
bevor ein `natural_waves`-Kommando ankam.

**Auswertung (Maintainer, PR #478):** Der Crash stammt **nicht** aus dieser
Änderung — die injizierte DLL enthielt keinen `natural_waves`-Code (`strings`:
0 Treffer), und beide Crash-Bundles zeigen dieselbe Instruktion
`riftbreaker_dll_win_release.dll+0x275895` (`mov rdx,[r10]`, `r10 = 0x36`/`0x33`),
erreicht über `dispatch_get_state` → `GetPlayerAccount(World*,0)` →
`GetPlayerTeam` → `Exor::EcsContext::FindIt`: ein **Readiness**-Problem
(`get_state`-Polling während der Map-Gen, `world != NULL` aber noch nicht fertig
initialisiert). Fix läuft in #479 (Readiness-Gate). Der Live-Game-Wert des
Naturwellen-Schalters ist damit weiterhin offen (Player-/Operator-Test), aber
nicht durch diese DLL blockiert. Der Live-Default-Flip auf `planet` ist aus dem
PR #478 de-scoped und landet separat.
## #479: Readiness-Gate — Welt-Init abwarten (Crash-Klasse #436)

**Symptom:** `get_state` während der Welt-/Map-Init → Crash, RIP
`riftbreaker_dll_win_release.dll+0x275895` (`mov rdx,[r10]`, `r10` = Garbage),
Stack `dispatch_get_state → GetPlayerAccount(0xC60050) → GetPlayerTeam(0xC60F50)
→ Exor::EcsContext::FindIt(0x1DD0370)`. Der `world`-Pointer war **non-NULL**,
die ECS-/Team-Map aber noch im Aufbau → der `no_world`-Guard griff nicht.

**Root Cause:** fehlende **Readiness** (nicht Threading, nicht Lua). Der
Pipe-Thread ruft eine Game-Funktion, die eine fertig initialisierte Welt
braucht — zu früh. Zweitursache (Race) ist mit dem Gate entschärft, solange der
Call erst nach Map-Fertigstellung läuft; die Session läuft in `main` komplett
pure-C++ auf dem Pipe-Thread (kein vtable-Detour, kein `lua_*` — #446).

### Ready-Signal (log-belegt, Build 2.0.58485, planet 2026-09-15)

`exor_logs.txt` (unter `%USERPROFILE%\Documents\The Riftbreaker\`) enthält in
JEDEM **erfolgreichen** Boot:

```
[12:40:33.501] [info] MapGenerator.cpp:828 - InstantiateMap took: 4846 ms
[12:40:34.154] [info] NavigationGraph.cpp:462 - NavigationGraph::Generate - Graph generated in 0.595806 sec.
```

Die **gecrashten** Boots (13:05, 12:52, 13:39) enden dagegen bei
`MapGenerator.cpp:976 - ExecuteBuffers took: 48 ms` — d. h. der Crash passiert
VOR der Map-Fertigstellung, ohne die beiden Marker. Das ist der belastbare,
invertierte Beleg (`grep` über `exor_logs*.txt`).

### Implementierung (`rbbridge.c`)

- `RBBRIDGE_READY_MARKERS[]` = `"NavigationGraph::Generate - Graph generated"`,
  `"InstantiateMap took"` (Substring, ohne Zeitstempel).
- `rbbridge_log_is_ready(buf, len)` — **rein**, host-testbar (kein Win32).
- `world_is_ready()` — liest `exor_logs.txt` (Override `RBBRIDGE_EXOR_LOG`,
  sonst `%USERPROFILE%`-Kandidaten), latchet einmalig; ohne Fund konservativ 0.
- `readiness_block(hPipe, event)` — Gate für **alle** Game-Calls:
  `get_state`, `add_resource`, `activate_mission_flow`,
  `deactivate_mission_flow`, `creatures_difficulty`, `probe`.
  Vor Ready → `{"event":"<x>_result","ok":false,"reason":"world_not_ready"}`
  (graceful, KEIN Call).

**Warum Log statt Memory-Flag:** der Page-Fault-Pfad (`World::GetSystem`,
`GetPlayerAccount`) tritt beim Boot selbst auf — ein Memory-Read als
Readiness-Probe wäre genau der Crash. Das Log-Signal kennt den Endzustand ohne
Spielzugriff.

**Thread-Entscheidung (#479-Nachtrag):** kein Vtable-Detour (der #376-Detour ist
mit #387/#446 entfernt; `ConsoleService::Update` = Worker-Thread, für `lua_*`
untauglich — #378). Native Executor/CommandBuffer
(`Exor::InOrderWorldExecutor`, `EcsCommandBuffer::ExecuteCommands` 0x1DD01B0)
bleiben Option, sind aber **nicht nötig**: das Gate allein behebt die
Crash-Ursache. Ein Detour bräuchte den Thread-Nachweis (Regel aus #479).
