# Native Entity-/Building-Read (Spike #548, DLL/B2)

**Issue:** #548 (Spike) · **Milestone:** 1.0 · **Stand:** 2026-09-16
**Build:** 2.0.58485 (GOG == Dedi, byte-identisch) · **Umgebung:** `planet`,
`/srv/rbgame/bin/riftbreaker_dll_win_release.{dll,pdb}` · PDB-Publics + Disasm

**Frage:** Kann die DLL (`bausteine/rbbridge/dll/rbbridge.c`) den Building-/Entity-
State **nativ (C++)** lesen, um „Player hat X gebaut" / „Ressource ausgegeben" ganz
**ohne Mod** zu erkennen — analog zur bestehenden Carbonium-/DOM-/HQ-Kette in
`get_state`?

**Ergebnis-These:** **Ja.** Es gibt ein direktes, PDB-belegtes natives Primitive
(`BuildingService::GetBuildingByTypeCount` bzw. `FindService::FindEntitiesByType`)
mit build-stabiler AOB-Signatur. Es ist genau der Mechanismus, den das Spiel
**selbst** für seine „Baue X"-Objektive (Kampagne) benutzt. Kein Operator/exec,
kein Lua-Mod, kein Log-Tailing nötig.

---

## 0 · Harness-Randbedingungen (eingehalten)

Nicht erlaubt und **nicht verwendet**: Operator-/exec-Trigger (#543→#546
revertiert), Lua-Mod (auch serverseitig), Log-Tailing als Egress. Der unten
belegte Pfad ist ein **reiner nativer C++-Read** vom Pipe-Thread (wie
`creatures_base_difficulty` #388, HQ #511).

---

## 1 · Methodik

- PDB-Publics: `llvm-pdbutil-18 dump -publics riftbreaker_dll_win_release.pdb`
  (556.365 Name/Adr-Paare; Sektion-Basen `.text`=0x1000, `.rdata`=0x2DA2000,
  `.data`=0x3EE8000 — RVA = Basis + dezimaler `addr`-Offset, siehe
  `riftbreaker-re/SKILL.md`).
- Disasm: `tools/re/disasm.py <RVA>` (capstone + pefile) gegen die planet-DLL.
- AOB-Gegenprobe: 32-Byte-Präfix jeder Zielfunktion exakt 1× in `.text`
  (`/tmp/sigcheck2.py`, siehe §4).
- Gegenprobe gegen den **Spiel-eigenen Lua-Code**
  (`/home/momo/rb-game/lua-src/lua/`) — d. h. der native Weg ist derselbe, den
  die Engine-Lua-API kapselt.

---

## 2 · Kernbefund: das Spiel nutzt genau dieses Primitive

Der „Baue X"-Objektiv-Mechanismus der Engine ruft **denselben** Service:

```lua
-- lua/graph/campaign/objective/campaign_objective_construct_building.lua:42
local count = BuildingService:GetBuildingByTypeCount( self.type )
-- lua/objectives/construct_building.lua:53/67
local count = BuildingService:GetFinishedBuildingByType( self.searchTarget, self.type, self.searchRadius )
local count = BuildingService:GetFinishedBuildingByType( self.type )
```

`self.type` ist ein Gebäude-Typ-String (z. B. `headquarters`, `carbonium_pump`,
`storage`). `GetBuildingByTypeCount`/`GetFinishedBuildingByType` zählen die
**fertig gebauten** Gebäude dieses Typs → exakt „Player hat X gebaut".
Damit ist der native Read inhaltlich identisch mit der Engine-Objektiv-Logik
(kein Reverse-Rätsel, sondern die API, die das Spiel selbst nutzt).

Ergänzend liefert `FindService:FindEntityByType("headquarters")` bereits den
HQ-Read (#511) — dieselbe `FindService`-Instanz trägt die Typ-/Entity-Listen.

---

## 3 · RVAs / Offsets (Build 2.0.58485, PDB + Disasm, planet)

### 3.1 vftables (nur zur **Instanz-Auflösung**, QWORD-Scan analog #511)

| Klasse | Symbol | RVA |
|---|---|---|
| `Exor::FindService` | `??_7FindService@Exor@@6B@` | `0x2E94C98` |
| `Riftbreaker::BuildingService` | `??_7BuildingService@Riftbreaker@@6B@` | `0x2E9B1E0` |
| `Exor::EntityService` | `??_7EntityService@Exor@@6B@` | `0x2E9CA50` |
| `Riftbreaker::HealthService` | `??_7HealthService@Riftbreaker@@6B@` | `0x2E95760` (bereits #511) |

**Layout-Beleg:** `BuildingService::GetBuildingType`/`GetFinishedBuildingByType`
lesen `[this+8]` und rechnen `+0x30` (eingebetteter Container) bzw. reichen es
in Lookups — identisch zum `[Service+8] = Exor::World*`-Muster von
`PlayerService`/`DifficultyService`/`MissionService`. ⇒ Der generische
`resolve_hq_service()`-Scan aus #511 (Kandidat mit lesbarem, non-NULL `+0x08`)
funktioniert für `BuildingService`/`FindService` unverändert.

### 3.2 Read-Primitive (MSVC x64: `this`=RCX; sret-Out = RDX; arg = R8)

| Symbol (demangled) | RVA | Aufruf | Rückgabe |
|---|---|---|---|
| `FindService::FindEntityByType(char const*)` | `0x1C0E420` | this=rcx, name=r8 | `uint32` (0xFFFFFFFF = INVALID_ID) |
| `FindService::FindEntitiesByType(char const*)` | `0x1C0CFA0` | this=rcx, **out=rdx**, name=r8 | `Vector<uint32>` (Entity-IDs) |
| `FindService::FindEntitiesByBlueprint(char const*)` | `0x1C0C120` | this=rcx, out=rdx, name=r8 | `Vector<uint32>` |
| `FindService2::FindEntitiesByType(char const*)` | `0xF983B0` | this=rcx, out=rdx, name=r8 | `Vector<uint32>` (Riftbreaker-Variante) |
| `BuildingService::GetBuildingByTypeCount(UtfString const&)` | `0x100B2C0` | this=rcx, **UtfString*=rdx** | `uint32` (Count!) |
| `BuildingService::GetBuildingByBpCount(UtfString const&)` | `0x100B1A0` | this=rcx, UtfString*=rdx | `uint32` |
| `BuildingService::GetBuildingByNameCount(UtfString const&)` | `0x100B230` | this=rcx, UtfString*=rdx | `uint32` |
| `BuildingService::GetFinishedBuildingByType(char const*)` | `0x100CC20` | this=rcx, out=rdx, name=r8 | `Vector<uint32>` |
| `BuildingService::GetFinishedBuildingByBp(char const*)` | `0x100C1E0` | this=rcx, out=rdx, bp=r8 | `Vector<uint32>` |
| `BuildingService::GetFinishedBuildingByName(char const*)` | `0x100C840` | this=rcx, out=rdx, name=r8 | `Vector<uint32>` |
| `BuildingService::GetBuildingType(Entity)` | `0x100B8D0` | this=rcx, out=rdx, entity=r8d | `UtfString` (Typ des Gebäudes) |
| `EntityService::GetBlueprintName(uint32)` | `0x1C0F960` | this=rcx, out=rdx, entity=r8d | `UtfString` (Blueprint-Name) |
| `BlueprintComponent::GetBlueprintName()` | `0x79EF70` | this=rcx | `char const*` |

**Empfehlung für den Spike-Kern:** `BuildingService::GetBuildingByTypeCount`
liefert einen **skalaren `uint32`** — keine Heap-Allokation, kein
Vector-Dtor-Problem, keine Out-Struktur. Das ist das sauberste, thread-taugliche
Primitive.

### 3.3 `Exor::Vector<uint>`-Layout (aus Disasm, `out`-Parameter)

Der sret-`out`-Puffer ist 0x20 Byte:

```
+0x00  owner/allocator-proxy (von 0x18253DEE0 gesetzt)
+0x08  T*    begin
+0x10  size  count      <-- Element-Anzahl (das, was Lua als `#count` sieht)
+0x18  size  capacity
```

Beleg: `FindEntitiesByType`/`GetFinishedBuildingByType` initialisieren
`[out+8..0x18]=0`, inkrementieren einen Zähler und vergleichen ihn mit
`[out+0x18]` (Kapazität) beim Append; das Ergebnis schreiben sie nach
`[out+0x10]`. Für `Vector<uint>` = Entity-ID-Liste: `count` = Anzahl.
(Deckt sich mit dem bestehenden `ResourceBasket`-Array `[+8]=begin`,
`[+0x10]=count`.)

> **Build-Stabilität:** PDB/DLL sind byte-identisch (GOG == Dedi). Die
> Funktionsadressen kommen trotzdem ausschließlich aus AOB (build-unabhängig).

---

## 4 · AOB-Signaturen (32-Byte-Präfix, je **genau 1×** in `.text`, planet)

```
FindEntitiesByType         0x1C0CFA0
48 89 5C 24 08 48 89 74 24 10 55 57 41 56 48 8B EC 48 81 EC 80 00 00 00 49 8B D8 4C 8B F2 48 8B

GetBuildingByTypeCount     0x100B2C0
48 89 5C 24 18 48 89 74 24 20 48 89 54 24 10 57 48 83 EC 30 48 8B FA 48 8B F1 48 8B 4A 18 48 8D

GetFinishedBuildingByType  0x100CC20
48 8B C4 4C 89 40 18 48 89 50 10 55 53 56 57 48 8D A8 D8 FC FF FF 48 81 EC 08 04 00 00 0F 29 70

GetBlueprintName           0x1C0F960
40 55 53 56 57 41 56 48 8D AC 24 40 FE FF FF 48 81 EC C0 02 00 00 45 8B F0 48 8B F2 48 8B F9 33
```

Optionale Zusatz-Primitive (Count-/Bp-Varianten), ebenfalls je 1×:

```
GetBuildingByBpCount       0x100B1A0
48 89 5C 24 08 48 89 54 24 10 57 48 83 EC 20 48 8B DA 33 D2 48 8B 49 08 E8 ?? ?? ?? ?? 48 85 C0
   (Bytes 24–27 = rel32 der `call` — im AOB-Code maskieren, s. Masken-Konvention #511)

GetBuildingByNameCount     0x100B230
48 89 5C 24 08 48 89 54 24 10 57 48 83 EC 20 48 8B DA 33 D2 48 8B 49 08 E8 ?? ?? ?? ?? 48 85 C0
```

Gegenprobe-Kommandos: `/tmp/sigcheck2.py` (planet) — `n=32`, exakter
Byte-Match, `re.finditer` über `.text` → Treffer-Liste Länge 1 @ Zielfunktion.

---

## 5 · Thread-Modell (#378 / #436 / #479)

- **Reiner C++-Read (ECS-/Service-Lookup), kein `lua_*`.** Aufruf vom
  **Pipe-Thread** wie die übrigen `get_state`-Reads (#388
  `creatures_base_difficulty`, #511 HQ).
- `GetBuildingByTypeCount`/`GetBuildingByBpCount`/`GetBuildingByNameCount`
  geben **skalare** Werte zurück → keine Allokation, keine Dtor-Pflicht.
- Die `Vector`-liefernden Varianten (`FindEntitiesByType`,
  `GetFinishedBuildingByType`) **allokieren Heap**; der sret-Rückgabewert
  müsste durch den Aufrufer zerstört werden (oder bewusst geleakt werden). Für
  den produktiven Read daher die **Count-Variante** bevorzugen.
- **Readiness-Gate (#479) gilt weiter:** erst nach `world_is_ready()` aufrufen
  (der Service-/World-Zugriff faultet sonst beim Boot).
- **Bei Race/Crash** im Live-Test (paralleler `get_state`) → wie #511 behandeln
  (offener Punkt).

---

## 6 · Antwort auf die Spike-Frage (Mapping)

| Spike-Frage | Nativer Read |
|---|---|
| „Player hat X gebaut" | `BuildingService::GetBuildingByTypeCount(UtfString(type))` > 0 bzw. **Delta** zwischen zwei Polls (Count steigt). Für Enumeration/Detail: `FindEntitiesByType(type)` → Entity-IDs, dann `EntityService::GetBlueprintName(id)`. |
| „Ressource ausgegeben" | bereits als **Delta** im bestehenden `get_state`-Ressourcen-Read (carbonium/steel/…); der Gebäude-Count korreliert das (Bau kostet Ressourcen). Kein neuer RE-Hebel nötig. |

Damit ist **kein** Operator-/exec-Trigger, **kein** Lua-Mod und **kein**
Log-Tailing erforderlich — die Erkennung läuft über den bereits etablierten
nativen `get_state`-Pfad.

---

## 7 · Empfohlene Folge-Implementierung (nicht Teil dieses Spikes)

Analog `read_hq_health()` (#511) in `rbbridge.c`:

1. `resolve_building_service(base, RBBRIDGE_RVA_BUILDING_VFTABLE)` — QWORD-Scan
   `0x2E9B1E0` (generisches `resolve_hq_service`-Muster), `[+8]!=0`-Guard.
2. AOB-Auflösung `GetBuildingByTypeCount` über 32-Byte-Signatur (§4).
3. `UtfString`-Ctor (`RVA 0x3AE1E0`, bereits in `main` genutzt) für den Typ-String,
   danach `~UtfString` (`RVA 0x26F1F0`).
4. `get_state`-Feld z. B. `buildings{<type>:count}` (Delta optional im Backend);
   `null`/weggelassen bei unauflösbarer Kette (graceful, kein Crash).
5. Host-Test: AOB vorhanden/fehlend + Count-Parser; Live-Test **mit Player**
   (Wert-Proof offen, §8).

---

## 8 · Offene Punkte

1. **Live-Wert-Proof fehlt (nur mit Player prüfbar).** Der Spike belegt Kette +
   RVAs/Offsets + AOB statisch (PDB + Disasm) und die Semantik über den
   Spiel-eigenen Lua-Aufruf. Ein Live-Beweis „Count steigt beim Bauen" braucht
   einen verbundenen Spieler (analog #511-Player-Test).
2. **Exaktes `type`-String-Set.** Die zulässigen Typ-Strings (`headquarters`,
   `carbonium_pump`, …) sind aus dem Kampagnen-Objektiv-Datenfluss abgeleitet;
   eine vollständige Liste ist nicht inventarisiert (Folge-Spike bei Bedarf).
3. **Team-/Owner-Semantik:** Ob `GetBuildingByTypeCount` nur Gebäude des
   **eigenen** Teams zählt oder global — im 1.0-Solo/Freundschaftsspiel
   unkritisch, im Duel/2v2 zu klären (Live-Test).
4. **`Vector`-Layout** (§3.3) ist aus Disasm abgeleitet, nicht live bestätigt;
   für die Count-Variante nicht benötigt.
5. **Readiness/Race** wie #479/#511: Gate vor dem Call, paralleler Zugriff im
   Live-Test beobachten.

---

## 9 · Querverweise

- `docs/research/dedicated-io-thread-model.md` (Thread-SSOT), `#378`/`#436`/`#479`
- `docs/research/dedicated-io-re-findings.md` (carbonium-/HQ-Kette)
- `.agents/skills/riftbreaker-re/SKILL.md` (Tooling, RVA-Konvention)
- `progress-511-hq-native-read.md` (Muster: AOB + vftable-Instanz-Scan)
- `docs/research/508-catalog-of-things.md` (Katalog-Kontext)
- Refs: #548 · #511 · #518 · #543 · #546 · #388 · #479
