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
