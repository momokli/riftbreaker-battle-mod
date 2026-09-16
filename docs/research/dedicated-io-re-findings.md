# Dedicated IO Interface — RE-Befunde (Build 2.0.58485)

Live-RE-Stand für den **READ-OUT**-Pfad des Dedicated IO Interface (Issue #363).
Ergänzt `docs/research/pdb-symbol-validation.md` (Issue #364) um die konkreten
Disasm-Befunde. Stand: 2026-09-13, auf `planet` gegen
`/srv/rbgame/bin/riftbreaker_dll_win_release.dll` + `.pdb` (Build 2.0.58485 /
GOG == Dedi) verifiziert.

> **⚠️ Teilweise historisch.** Die **Lua**-basierten Pfade dieses Dokuments sind
> **nicht mehr im Code**: sowohl die State-Egress-Capture (Phase C,
> `rbbridge_capture_state`) als auch der WRITE-Marshal (Phase D, `g_pending_cmd` +
> `ConsoleService::Update`-vtable-Detour) wurden mit dem C++-direct-only-Umbau
> entfernt (`c4db642` Refs #376; `f022f35` #446). Die RE-Befunde (RVAs, Layouts)
> bleiben Referenz; der **Ist-Stand + das Thread-Modell** stehen in
> **[dedicated-io-thread-model.md](dedicated-io-thread-model.md)**.

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

## Phase C: Wave-Counter + time-to-next + full state egress — historisch (#376, in `main` entfernt)

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

> **Historisch:** dieser Lua-Capture-Pfad wurde mit `c4db642` (C++-direct-only,
> Refs #376) entfernt — `main` hat kein `lua_*`/`rbbridge_capture_state` mehr.

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

## Phase D: WRITE path — Game-Thread-Kommando-Marshalling (historisch; #376-Bau in `main` entfernt)

> **Historischer RE-/Machbarkeits-Nachweis.** Die folgende Architektur
> (`g_pending_cmd` + `ConsoleService::Update`-vtable-Detour, `dispatch_exec`) ist
> **nicht mehr im Code** (entfernt `c4db642`/`f022f35`, #387/#446). Aktueller
> Ist-Stand + Marshal-Optionen: **[dedicated-io-thread-model.md](dedicated-io-thread-model.md)**.

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

## #512: verbundene Spielerzahl nativ (`players`) — nativ, mit-Player-Test offen

**Frage:** Solo-/Koop-Erkennung ohne Lua. Lua-Pfad im Spiel:
`dom_mananger:GetPlayersCounter()` == `#PlayerService:GetConnectedPlayers()`.

**Befund:** Es gibt **kein** statisches "connected player count"-Feld. Die Zahl
wird berechnet (Filter über den Spieler-Container) — ein reiner Offset-Read ist
deshalb unmöglich; der einzige praktikable native Weg ist **ein Game-Call + ein
Deref** (genau so, wie `dom_mananger:GetPlayersCounter()` es in Lua tut).

### Aufgelöste Funktion (AOB, keine feste Adresse)

`Riftbreaker::GetConnectedPlayers(Exor::World*)` — freie Funktion,
RVA `0xC5EE70` (Build 2.0.58485; **nur Verifikations-Notiz**).

```
rcx = out-Vektor (hidden return ptr, MSVC-x64-Aggregat-Return)
rdx = World*
  48 8D 9A C0 00 00 00   lea rbx,[rdx+0xc0]    ; Sessions-Store (World+0xC0)
  41 B8 B1 B8 7E 10      mov r8d,0x107eb8b1    ; TypeHash (ServerGameplaySessions)
  Helper 0x1DF3F50 füllt `out` mit ALLEN Spieler-Ids,
  Filter in-place auf "connected" via 0x1CF5AB0.
Rückgabe-Typ: Exor::Vector<uint32,StlAllocatorProxy<uint32>>
```

### Vektor-Layout (drei unabhängige Disasm-Belege)

| Beleg | Fundstelle |
|---|---|
| Erzeuger/Filter | `0xC5EE70` (`[r15+8]` = begin, `[r15+0x10]` = size) |
| Konsument | `0x12B8386` (`lea rcx,[rbp+0x38]`, liest `[rbp+0x40]`/`[rbp+0x48]`) |
| Destruktor | `0x26F340` (`[rcx+0x18]` = capacity, `[rcx]` = Allocator-Objekt) |

```
Exor::Vector<uint32> (+0x00 Allocator-Objekt*, +0x08 begin, +0x10 size, +0x18 capacity) = 0x20 B
=> players = vec[+0x10]
```

### AOB statt RVA

114-Byte-Signatur ab Funktions-Entry `RBBRIDGE_CONNPLAYERS_SIG`
(`rbbridge.c`), im `.text` **eindeutig** (Gegenprobe planet 2026-09-15: genau
1 Treffer @ RVA `0xC5EE70`). Wildcards: die drei `E8`-rel32 (buildabhängige
Call-Ziele) und die drei rel8-Sprünge; die drei `E8`-Opcodes, die beiden
`mov r8d,0x107eb8b1`-Anker und `lea rbx,[rdx+0xc0]` bleiben Pflicht.

**Warum der Member-Wrapper `PlayerService::GetConnectedPlayers()` (RVA
`0xF27960`) NICHT aufgelöst wird:** seine 30-Byte-Signatur matcht 5× (er teilt
die Form mit `GetConnectedPlayersFromTeam`/`GetPlayersFromTeam` + 2 weiteren
Geschwistern) — eine eindeutige AOB ist dort unmöglich. Deshalb wird die freie
Impl direkt aufgelöst und mit dem bereits vorhandenen `World*` (aus
`PlayerService[+0x08]`) aufgerufen.

### Thread-Modell

Reiner C++-Read, **kein `lua_*`** → thread-agnostisch; der Pipe-Thread ruft ihn
direkt (analog `GetPlayerAccount`). Kein Vtable-Detour.

### Aufräumen (kein Leak)

Der Rückgabe-Vektor wird wie in `~Vector` (Disasm `0x26F340`) freigegeben:
`cap = vec[+0x18]`; bei `cap != 0` → `vec[+0x00]->vtable[0x10](alloc,
vec[+0x08], cap*4)`. **Keine Dtor-Symbolauflösung:** die Dtor-Bytes liegen
dreifach byte-identisch im `.text` (3 Instanzen für 4-Byte-Elemente:
`0x26F340`/`0x2B30F0`/`0x18906E0`) — eine AOB wäre nicht eindeutig. Der
Deallocate-Aufruf über die Allocator-vtable des von der Engine selbst gesetzten
Allocator-Objekts ist dagegen deterministisch und semantisch identisch.

### Egress

`get_state` liefert `"players":<n>` (Solo = 1) bzw. `"players":null`, wenn der
AOB nicht auflösbar ist **oder die Welt nicht live ist** (graceful, kein Call).
Cockpit: `Mission Flow (Wave)` → `players`.

### WICHTIG: Aufruf erst NACH dem Account-Check (LIVE-Crash #512)

`Riftbreaker::GetConnectedPlayers` ist **nicht** safe auf einer noch nicht
geladenen Welt: die Funktion holt den Session-Pointer aus `World+0xC0` und
ruft damit das Session-Prädikat (RVA `0x1CF5AB0` → `cmp rax,[rcx+0x1e0]`). Ist
der Store noch nicht da, ist der Pointer NULL → Page-Fault:

```
wine: Unhandled page fault on read access to 00000000000001E0
      at address 00006FFFF8EC5AD3 (thread 025c)
```

`0x6FFFF8EC5AD3 - base(0x6FFFF71D0000)` = **DLL+0x1CF5AD3** = genau
`cmp rax,[rcx+0x1e0]` mit `rcx = 0` (NULL-Session). Der Read läuft deshalb erst
**nach** erfolgreichem `GetPlayerAccount` — dieselbe live-bewiesene Vorbedingung
"Welt wirklich da" wie beim carbonium-Read.

### Live-Stand (Build mit Fix)

Der Fix wurde gegen die dev-Instanz (`riftbreaker-dedicated`, Port 9001)
verifiziert: `get_state` liefert ohne Spieler `ok:false, reason:no_account` mit
`"players":null`, die Pipe bleibt oben (**kein Crash**).

**Offener Punkt (Player-Test Momo/Matheo):** Ein Dedicated-Server ohne
verbundenen Spieler lädt kein Konto — der `players`-Read ist dann per Design
`null`. Die Zahlen 0/1/2 bei 0/1/2 Spielern (Solo = 1) sind damit erst mit
verbundenem Spieler bestätigbar und **nicht** live verifiziert.

### Player-Test — Stand 2026-09-16 (OFFEN, NICHT erledigt)

**Deploy-Beleg:** Die dev-Instanz fährt exakt den Branch-Build: die deployte DLL
`/opt/rbmods/rbtools/dev/rbbridge.dll` ist **byte-identisch** mit dem frischen
Branch-Build (`sha256 9c76d836…427a5`; `strings` → 4× `"players"` in den vier
`get_state`-Pfaden). Der Read-Pfad ist also live geladen.

**Was live messbar ist (ohne Client): 0 Zahlenwert.** 3×
`POST /get_state` (2026-09-16, Abstand 5 s) → alle
`ok:false, reason:"no_account"`, `players:null`; `/health` →
`{"ok":true,"pipe":true}`; Container `running`, `RestartCount=0`.
Das belegt nur den **Graceful-Pfad** (kein Crash, `null` statt Blind-Call) —
**nicht** die zurückgegebene Zahl.

**Durchführung (mit verbundenem Client, Momo/Matheo):**

1. Client auf die dev-Instanz verbinden (`:9001`/`:6321`), Mission starten
   (Welt/Konto laden — `get_state` muss `ok:true` liefern).
2. `curl -s -X POST http://127.0.0.1:9001/get_state -d '{}'`.
3. Erwartet: Solo → `"players":1`; zweiter Client → `"players":2`; nach
   Verlassen zurück auf 1/0 (0 nur bei leerem, aber geladenem Konto).
4. Roh-JSON-Zeile **mit Zeitstempel** als Beleg-Kommentar an Issue #512.

Erst danach ist Blocker 2 (Live-Zahlenwert) bestätigt. Bis dahin bleibt der
Wert `players` ausdrücklich **nicht live verifiziert** — kein „proven".

## #367: Wave-Counter — Instanz-Navigation + Feld-Offset (statisch, Build 2.0.58485)

**Auftrag:** Instanz + Feld-Offset des Wave-Counters statisch belegen (PDB-publics
grep `Wave` → Getter-RVA → vftable-Scan-Instanz → Offset per Disasm), **AOB-Signatur
statt fester Adresse**. **Ergebnis vorab:** es gibt **keinen** nativen Wave-Counter —
der Zähler ist das **Lua-Feld** `dom_mananger.currentDifficultyLevel`. Statisch
belegt ist damit die **Instanz-Navigation** (bis zum `lua_State*` + Registry-Ref);
ein **fester C++-Struct-Offset existiert nicht** (Lua-Hashfeld). Deckt sich mit #376.

### 1. PDB-publics-Scan (negativ, vollständig)

`llvm-pdbutil dump -publics` → **556.365** Public-Symbole; `grep -i wave` →
**2.545** Treffer. Alle gehören zu ECS/Component/Audio/Cheat: `WaveSystem`,
`WaveSpawnerComponent`, `WaveSpawnerSplineComponent`, `WaveGround`, `WaveDesc`,
`WaveUnitComponent`, `AIWaveMovementSystem`, `DebugSpawnWave`, `ShockWave*` —
**kein** Zähler-/Level-Getter.

- `grep -i 'dom_?man'` → **0 Treffer**: `dom_mananger` ist **keine** native Klasse
  (reine Lua-Klasse, `lua/missions/v2/dom_manager.lua`).
- `WaveSystem::Update` (RVA `0x290D20`) ruft nur `HandleWaveSpawn` (`0x2D5B50`) +
  `DetectAndHandleBlockedWaveUnits` (`0x2D18A0`) — kein Zähler.
- `Riftbreaker::DifficultyService` hat nur Zeit-/Multiplikator-Getter
  (`GetWaveIntermissionTime` `0x10104E0`, `GetPrepareAttackTimeMultiplier`
  `0x100DEC0`, …); `Riftbreaker::MissionService` keinen Wave-Zähler.
- Die **Lua-Feldnamen** `currentDifficultyLevel`, `waitForSpawnTimer`, `idleTimer`,
  `cooldownTimer` kommen in `/opt/rb-re/strings_dll.txt` (9,1 MB) **nicht** vor →
  kein nativer Code liest den Zähler namentlich.

Der Zähler ist damit ausschließlich Lua-seitig; Identität/Read laufen über #376.

### 2. LuaGraphNode-Layout (Disasm-belegt)

`?Update@LuaGraphNode@Exor@@UEAAXM@Z` RVA `0x1BAA140` und
`?SetSuspended@LuaGraphNode@Exor@@QEAAX_N@Z` RVA `0x1BA6CB0`:

```
0x1BAA140  80 B9 F1 00 00 00 00   cmp  byte [rcx+0xF1], 0
           0F 84 <rel32>           je   <body>        ; suspended -> return
0x1BA6CB0  88 91 F1 00 00 00      mov  byte [rcx+0xF1], dl
           C3                      ret
```

Basis-Ctor RVA `0x1B33630` (nicht public; von beiden Node-Ctors gerufen):

```
[rsi+0x00] = vftable            ; LuaGraphNode 0x2F46D70 | Selector 0x2F46DB8
[rsi+0x08] = member-ctor        ; call 0x2C8090
[rsi+0x10] = 0                  ; luabind::object #1   L
[rsi+0x18] = 0xFFFFFFFE         ;                     ref = LUA_NOREF (-2)
[rsi+0x20] = luabind::object #2 ; <-- Lua-Instanz (self-Tabelle)
            call 0x1DA9F10 (object-assign; rcx=&dest+0x20, r8=&src-arg)
[rsi+0x38] = 0
[rsi+0x40] = std::string/UtfString-Ctor (0x26A060)
[rsi+0x88] = luabind::object #3
[rsi+0xA8] = 0xFFFFFFFF ; +0xAC = 0
[rsi+0xB0] = new (0x25C0050 = TLS-Counter) ; +0xB8/+0xC0/+0xC8 = 0
[rsi+0xD0] = new (0x25C0050) ; +0xD8/+0xE0/+0xE8 = 0
```

`0x1DA9F10` = `luabind::object`-Assign (Disasm):

```
mov rcx,[r8] ; mov [rdi],rcx            ; dest.L = src.L
mov dword [rdi+8], 0xFFFFFFFE           ; dest.ref = LUA_NOREF
if (L) { r8d=[r8+8]; edx=LUA_REGISTRYINDEX(-10000)
         call 0x290CFA0                 ; lua_rawgeti  (push src value)
         call 0x291C4E0                 ; luaL_ref(REGISTRY) -> eax
         mov [rdi+8], eax }
mov [rdi+0x10], ebx                     ; dest.extra (int)
```

⇒ **`luabind::object` = 24 B: `lua_State* L @+0x00`, `int ref @+0x08`,
`int extra @+0x10`.** Am Node also: **`L @+0x20`, `ref @+0x28`, `extra @+0x30`**
(deckt die `+0x20`-Angabe aus Phase C / `riftbreaker-re`-Skill statisch ab).
Neue RVAs aus diesem Pfad: `luaL_ref = 0x291C4E0`, `luaL_unref = 0x291C5C0`.

### 3. Instanz-Navigation + AOB-Signaturen (statt fester Adresse)

| Symbol | RVA |
|---|---|
| `??_7LuaGraphNode@Exor@@6B@` (vftable) | `0x2F46D70` |
| `??_7LuaGraphNodeSelector@Exor@@6B@` | `0x2F46DB8` |
| `LuaGraphNode::Update` | `0x1BAA140` |
| `LuaGraphNode::SetSuspended` | `0x1BA6CB0` |
| Basis-Ctor | `0x1B33630` |
| LuaGraphNodeSelector-Ctor | `0x1B95070` |
| LuaGraphNode-vtable-Stores (Ctor-Sites) | `0x1B84164`, `0x1BAEDF1`, `0x1BB11AA` |

Verifizierte **AOB-Signaturen** (eindeutig im `.text`, Build 2.0.58485):

| Zweck | AOB | Treffer |
|---|---|---|
| LuaGraphNode-**vtable**-Store im Ctor | `48 8D 05 ?? ?? ?? ?? 48 89 03 66 C7 83 F0 00 00 00 00 00` | 3 (`0x1B84164`,`0x1BAEDF1`,`0x1BB11AA`) — alle laden dieselbe vtable |
| `LuaGraphNode::Update` | `80 B9 F1 00 00 00 00 0F 84` | 1 (`0x1BAA140`) |
| `LuaGraphNode::SetSuspended` | `88 91 F1 00 00 00 C3` | 1 (`0x1BA6CB0`) |

**vtable-Auflösung ohne feste Adresse:** ersten `AOB-VTABLE`-Treffer nehmen →
`vtable_RVA = site + 7 + disp32`. Alle drei Ctor-Sites zeigen auf `0x2F46D70`
→ erster Treffer genügt. (Die Selector-Site `0x1B95090` lädt `0x2F46DB8` mit
`48 89 07`.)

**Reader-Rezept (read-only, kein World-/System-Map-Zugriff):**

```
base   = Modulbasis riftbreaker_dll_win_release.dll
vtable = AOB-VTABLE  ->  site + 7 + disp32
node   = Scan committed/readable Memory: QWORD == base + vtable
L      = *(uint64*)(node + 0x20)     ; lua_State*
ref    = *(int32 *)(node + 0x28)     ; Lua-Registry-Ref der self-Tabelle
lua_rawgeti(L, LUA_REGISTRYINDEX, ref)         -> self-Tabelle
lua_getfield(L, -1, "currentDifficultyLevel")  -> Zahl
```

### 4. Ergebnis / Grenzen

- **Feld-Offset:** `currentDifficultyLevel` liegt in der **Lua-Hashpart** der
  self-Tabelle — es gibt **keinen stabilen C++-Struct-Offset**. Ein Read muss über
  die Lua-C-API (`lua_getfield`) auf dem **Lua/main-Thread** laufen (Thread-Modell:
  #378/#446; `lua_*` ist NICHT thread-safe) → gehört zu #376.
- **Instanz-Identität:** der `0x2F46D70`-Scan liefert *einen* LuaGraphNode
  (live-belegt: gültiger `lua_State*`), **nicht** beweisbar *die*
  `dom_mananger`-Instanz. Sichere Identität liefert nur der Lua-Hook
  (`dom_mananger:Update`-Wrapper, #376).
- **Konsequenz:** ein rein-nativer Wave-Counter ist nicht verfügbar; belastbare
  Quelle bleibt der Lua-Capture aus #376. Delta dieses Issues ist die **statische
  Belegkette** (publics-Scan, Layout, AOB) — keine Doppel-Implementierung.

### 5. Test-Split

- **OHNE Player:** dieser PR ist **docs-only** — Host-Test
  (`tests/rbbridge-hosttest`, `HOSTTEST_PASS`) + Tool-Build
  (`scripts/build_rbbridge_tools.sh`) als Regressionsbeleg; DLL/PDB-Disasm ist
  offline reproduzierbar.
- **NUR mit Player (OFFEN, nicht erledigt):** Live-Verifikation des Werts (Welle
  abwarten bzw. `debug_dom_manager_spawn_wave_level N`) und der
  Instanz-Identität — siehe #376.
