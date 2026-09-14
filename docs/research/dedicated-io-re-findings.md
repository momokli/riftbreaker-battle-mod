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


## Phase E: dom_mananger C++-only Auflösung (Issue #387, statisch)

RE gegen `/srv/rbgame/bin/riftbreaker_dll_win_release.{dll,pdb}` (Build 2.0.58485),
`llvm-pdbutil` = `/usr/lib/llvm-18/bin/llvm-pdbutil`, Disasm via
`/opt/rb-re/venv/bin/python /tmp/disasm.py <RVA> [count]`.

### RVA-Umrechnung (bestätigt)
`.text` (0001) → RVA = `0x1000 + dec`; `.rdata` (0002) → `0x2DA2000 + dec`.
Sanity: `GetTypeName@LuaGraphNode` `0001:28945904` → `0x1B9BDF0` (Disasm liefert
`lea rax,[rip+…]; ret`). `??_7LuaGraphNode@Exor@@6B@` `0002:1723760` →
`0x2F46D70` (deckt sich mit dem bereits dokumentierten vftable).

### vftable-Layout `LuaGraphNode` (RVA `0x2F46D70`)

Die vftable hat **9 Slots**; die nächste (`LuaGraphNodeSelector`) beginnt bei
`0x2F46F38` (Stride 0x48 = 9 Slots). Belegte Slots:

| Slot | RVA | Symbol | Befund |
|---|---|---|---|
| 1 | `0x1B9BDF0` | `LuaGraphNode::GetTypeName()` (virtual) | `lea rax,[rip+_typeName]; ret` |
| 2 | `0x1B9BD40` | `LuaGraphNode::GetTypeHash()` (virtual) | TypeHash-Getter |
| 7 | `0x1BAA140` | `LuaGraphNode::Update(float)` (virtual) | `cmp byte [rcx+0xF1],0; je …; ret` |

`GetTypeName` ist ein **per-Klasse überschriebener** Virtual: die vftable von
`LuaGraphNodeSelector` (`0x2F46F38`) hat in Slot 1/2 **andere** Funktions­zeiger
(`0x1B9BE00` / `0x1B9BD50`, ebenfalls `lea rax,[rip+…]; ret`). Exor-Reflection
(„`_typeName`") ist also das Klassen-Identitäts-Idiom.

`_typeName`-Strings: `LuaGraphNode` @ RVA `0x2DCD960` (roh = `"LuaGraphNode"`),
`LuaGraphNodeSelector` @ RVA `0x2DCDA08`.

### `SetSuspended(bool)` — bestätigt

```
LuaGraphNode::SetSuspended(bool)  RVA 0x1BA6CB0   (0001:28990640)
  0x181ba6cb0: mov byte ptr [rcx + 0xf1], dl
  0x181ba6cb6: ret
```

→ `__thiscall`: `this` = `rcx`, `bool` = `dl`; reiner Flag-Write `[this+0xF1]`.
**Thread-agnostisch** (kein Lua, kein State) — sicher von jedem Thread.
Korrespondiert exakt mit dem Suspend-Check in `Update` (Slot 7).

### Klassen-Identität: KEIN C++-Subclass-Marker vorhanden

Statisch geprüft (publics):

- Es existiert **keine** C++-Klasse `*Dom*` (Grep `[A-Za-z_]*Dom[A-Za-z_]*@(Riftbreaker|Exor)` → leer).
- `dom_mananger` / `event_manager` sind **pure Lua-Klassen**
  (`missions/v2/dom_manager.lua`, `class 'X' ( base )`), keine `??_7`/`??_R0`
  der Subklasse. Die LuaGraphNode-Familie im Binary ist `LuaGraphNode`,
  `LuaGraphNodeSelector`, `LuaGraphPackedNode`, `LuaBehaviourNode`,
  `LuaEmbeddedBehaviourNode` — **kein** DOM/Event-Manager.
- Konsequenz: `dom_mananger` und `event_manager` teilen sich real die
  C++-vftable `0x2F46D70`. Die vorgeschlagenen Marker (eigene `??_7`/`??_R0`,
  Layout-Unterschied) existieren **nicht**; die Klassen-Identität lebt
  ausschließlich im Lua-Objekt (`LuaGraphNode +0x20` = `luabind::object`
  {`lua_State*` @+0, `int ref` @+8}).

### Konsequenz für den Resolver (#387)

1. **AOB/Signatur statt fixer RVA:** Die LuaGraphNode-Herkunft wird über eine
   Byte-Signatur (`RBBRIDGE_DOM_SIG`/`_MASK`, rel32 wildcarded) + vtable-
   Rückrechnung aufgelöst — nicht über hartkodierte RVA.
2. **Instanz-Scan:** `VirtualQuery`-Scan (nur lesbare Regionen) auf
   `qword == LuaGraphNode-vftable` (Muster wie `resolve_console_instance`).
3. **C++-only Diskriminator** (ersetzt `currentDifficultyLevel is number`):
   Der per-Klasse überschriebene Virtual **`GetTypeName()` (vtable-Slot 1)**
   ist ein reiner C++-Call und liefert den registrierten Klassennamen; als
   Bestätigung dient `GetTypeHash()` (Slot 2) + Layout-Check
   (`+0xF0`/`+0xF1` lesbar, `+0xB8/+0xC0` child-vector). **Kein `lua_*`.**
   Restrisiko (statisch nicht entscheidbar): ob luabind für Lua-abgeleitete
   Klassen eine eigene Wrapper-vftable mit überschriebenem `GetTypeName`
   installiert — wird im Live-Probe (planet) verifiziert; bei Nicht-Treffer
   degradiert der Resolver graceful (`ok:false`, kein Crash).

### Live-Probe (planet, Build 2.0.58485) — Marker-Hypothesen widerlegt

Probe-DLL (`probe_dom_nodes`, TEMP) in `rbbridge.dll` via
`/opt/rbmods/rbtools-drift/rbbridge.dll` + `docker restart riftbreaker-dedicated`,
Abruf über `POST http://127.0.0.1:9001/probe`.

Scan-Kriterium: Objekt, dessen Qword[0] in das Modul-Image zeigt **und** dessen
vtable-Slot 7 == `LuaGraphNode::Update` (`base+0x1BAA140`) ist.

| Messung | Ergebnis |
|---|---|
| Familien-Instanzen | **109** (mehr als ein DOM/Event-Manager) |
| vtables | `0x2F46D70` (LuaGraphNode) und `0x2F46F38` (LuaGraphNodeSelector) |
| `GetTypeName()` (Slot 1) | `"LuaGraphNode"` **für alle** Instanzen (per-Class-Override, aber nur C++-Klassen) |
| `GetTypeHash()` (Slot 2) | nur **zwei** Werte: `0xde5d72b3` = FNV1a("LuaGraphNode"), `0xde… ` / `0xc7919a42` = FNV1a("LuaGraphNodeSelector") |
| Member-Strings `+0x08/+0x50/+0x100/+0x120` | leer (1 Treffer = Garbage) |
| luabind-Object `+0x20` | `lua_State*` konstant, `int ref` pro Instanz verschieden (1912…3479) |

**Schlussfolgerung (widerlegt die Task-Annahme):** es gibt **keinen** C++-only
Marker (keine eigene `??_7`/`??_R0`-vftable, kein Layout-Unterschied, kein
eingebetteter Klassenname), der `dom_mananger` von `event_manager` trennt.
`dom_mananger`/`event_manager` sind **pure Lua-Klassen**; die C++-Objekte sind
`LuaGraphNode`- bzw. `LuaGraphNodeSelector`-Instanzen. `GetTypeHash()` ist ein
**C++-Klassen**-Hash (FNV-1a des C++-Namens), **nicht** ein Lua-Klassen-Hash —
für Lua-abgeleitete Klassen liefert er den Basis-Hash.

Die Klassen-Identität lebt ausschließlich im Lua-Table, auf den nur das
luabind-Object (`+0x20` = {`lua_State*` @+0, `int ref` @+8}) zeigt. Ein reiner
C++-Read dieser Identität erfordert einen rohen Lua-Table-/Metatable-Walk
(Lua-5.1-Fork-Interna), der **nicht** Teil dieses Reverts ist.
