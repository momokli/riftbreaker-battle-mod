# `Database*`-Objekt (Mission-Flow-Payload) — RE-Befunde (Issue #386)

Beleg für das **C++-only Payload-Objekt** des Mission-Flow-Aufrufs:
`MissionService::ActivateMissionFlow(…, Database*)`. Ergänzt
`docs/research/dedicated-io-re-findings.md` (das auf dieses Dokument verweist).

Quelle: `/srv/rbgame/bin/riftbreaker_dll_win_release.{dll,pdb}`, Build
2.0.58485 (GOG == Dedi), read-only auf `planet` verifiziert (2026-09-15).
Tools: `llvm-pdbutil-18 dump -publics`, capstone/pefile über
`/opt/rb-re/venv/bin/python` (Bildbasis der Abbildung = `0x180000000`).
RVA = Section-Base + **dezimaler** `addr`-Offset (`.text` = `0x1000`).

## 1. KERNBEFUND: es gibt einen `Database*`-Overload (kein Lua nötig)

`ActivateMissionFlow` existiert **fünfmal** (Public-Symbole). Das Demangling
zeigt die Formen; nur der `Database*`-Overload (RVA `0xF93280`) ist der
C++-direkte Ziel-Pfad.

| RVA | 4. Argument / Form | Rolle |
|---|---|---|
| `0xF93130` | `(UtfString const&, UtfString const&, UtfString const&)` | ohne `data` |
| `0xF93160` | `(…, luabind::object const&)` | **Lua-Pfad** (heutiges `dom_mananger.data`) |
| **`0xF93280`** | **`(UtfString const&, UtfString, UtfString, Database*)`** | **Ziel-Overload — C++-only** |
| `0xF93350` | `(UtfString const&, UtfString)` | Zwischen-Overload |
| `0xF93420` | `(UtfString const&)` | Kurzform |

Demangled (byte-exakt aus dem PDB-Public-Stream):

```
??_ActivateMissionFlow@MissionService@Riftbreaker@@QEAA?AV?$UtfString@D…@@AEBV34@00PEAVDatabase@4@@Z   (0xF93280)
?ActivateMissionFlow@MissionService@Riftbreaker@@QEAA?AV?$UtfString@D…@@AEBV34@00AEBVobject@adl@luabind@@@Z (0xF93160, Lua)
```

→ `PEAVDatabase@4@` = `Database*` als 4. (letztes) Parameter. Kein
luabind-Objekt, kein `lua_State*`, kein Lua-Thread-Zwang.

### 1.1 Disasm-Beleg `0xF93280` → Kern `0x33A7E0`

`tools`-Disasm (VA `0x180F93280`):

```
0x180F93280  mov   [rsp+8], rbx
0x180F93285  mov   [rsp+0x18], rbp
0x180F9328A  mov   [rsp+0x20], rsi
0x180F9328F  push  rdi
0x180F93290  sub   rsp, 0x50
0x180F93294  mov   rbp, r9          ; arg2 (UtfString) sichern
0x180F93297  mov   rsi, r8          ; arg1 (UtfString)
0x180F9329A  mov   rdi, rdx         ; sret-Ziel (Rueckgabe UtfString)
0x180F9329D  mov   rbx, [rcx+8]     ; this+0x8
0x180F932A1  call  0x182573DC0      ; rel32 (Build-gebunden)
…
0x180F932C1  mov   rdx, [rsi+0x18]  ; UtfString size
0x180F932C5  lea   rcx, [rsi+8]     ; UtfString inline-data
0x180F932C9  cmp   [rcx+0x18], 0xF  ; SSO-capacity-Check
…
0x180F932DD  mov   rax, [rsp+0x88]  ; Stack-Arg4 = Database*
0x180F932E5  mov   [rsp+0x28], rax
0x180F932EA  mov   rax, [rsp+0x80]  ; Stack-Arg3 = UtfString
0x180F932F2  mov   [rsp+0x20], rax
0x180F932F7  mov   r9, rbp          ; arg2
0x180F932FA  lea   r8, [rsp+0x38]
0x180F932FF  mov   rdx, rdi
0x180F93302  mov   rcx, r10
0x180F93305  call  0x18033A7E0      ; MissionSystem::ActivateMissionFlow (Kern)
0x180F9330A  jmp   0x180F93337
```

Damit ist belegt:

* **Rückgabe `UtfString` by value** → sret-Zeiger in **RDX** (ret: `mov rax,rdi`).
* **Parameter 1/2** in **R8/R9** (beides `UtfString`, per hidden-Pointer), **3/4**
  auf dem Stack (`[rsp+0x80]=arg3`, `[rsp+0x88]=arg4` = **`Database*`**).
  Zur Laufzeit wird arg4 unverändert an den Kern durchgereicht (Stack-Slot
  `[rsp+0x28]`) — `data` bleibt ein **Zeiger**.
* Die SSO-Prüfung (`[+8]` Daten, `[+0x18]` Size, `[+0x20]` Capacity ≤ 0xF) im
  Wrapper bestätigt das `UtfString`-Layout aus `docs/research/dedicated-io-write-functions.md`.

> Korrektur zu einer früheren Notiz („`data`=R9"): R9 ist **arg2** (UtfString),
> `data` ist der **4. Param auf dem Stack**. Die ABI-Form folgt mechanisch aus
> dem demangled Symbol (4 Parameter + sret).

**Rohbytes Prolog `0xF93280`** (nur das `E8`-rel32 @ `0xF932A1` maskiert):

```
48 89 5C 24 08 48 89 6C 24 18 48 89 74 24 20 57 48 83 EC 50
49 8B E9 49 8B F0 48 8B FA 48 8B 59 08 E8 1A 0B 5E 01 48 8B
```

## 2. `Exor::Database`-Klassen-Layout

* **KEIN vftable**: `??_7Database@Exor@@6B@` existiert **nicht** (bestätigt) →
  nicht polymorph → **kein vftable-Instanz-Scan** möglich.
  RTTI-TypeDescriptor `.?AVDatabase@Exor@@` @ RVA `0x3F91D30` existiert (aber
  ohne vftable kein RTTI-Walk).
* **Größe = 0x60 Byte**: `Database::Database()` @ `0x2C6550` initialisiert
  **drei** eingebettete Container à `0x20` Byte bei `+0x00`, `+0x20`, `+0x40`.
  Jeder Container = `{ allocator* @+0, begin @+8, size @+0x10, capacity @+0x18 }`.
  Der Allocator-Pointer kommt dreimal aus dem lazy Default-Allocator
  (`0x253DEE0` → jmp `0x2547640`, Ausgabe in `+0`/`+0x20`/`+0x40`).
  Copy-Ctor `0x26C590` spiegelt exakt dieselben Offsets.
* `new Database`-Call-Sites im Spiel: `mov ecx,0x60` → `call 0x253DAC0`
  (`operator new`) → 0x60-Byte-zeroing → `call 0x2C6550`. → `malloc(0x60)`
  ist die korrekte Allokationsgröße.

**Rohbytes `Database::Database()` `0x2C6550`** (`E8`-rel32 @ Index 23 maskiert):

```
48 89 5C 24 18 48 89 74 24 20 48 89 4C 24 08 57 48 83 EC 20
48 8B F9 E8 74 79 27 02 48 89 07
```

**Rohbytes `Database::SetString` `0x25956B0`** (kein Call im Prefix → exakt):

```
4C 89 44 24 18 53 48 83 EC 30 49 8B D8 48 8B 42 18 48 83 C2 08
48 83 7A 18 0F 76 03 48 8B 12 48
```

Disasm `SetString` (`RCX=this`, `RDX=&key`, `R8=&value`):

```
0x1825956B0  mov   [rsp+0x18], r8
0x1825956B5  push  rbx
0x1825956B6  sub   rsp, 0x30
0x1825956BA  mov   rbx, r8            ; value-UtfString*
0x1825956BD  mov   rax, [rdx+0x18]    ; key->size
0x1825956C1  add   rdx, 8
0x1825956C5  cmp   [rdx+0x18], 0xF   ; key SSO?
0x1825956DE  call  0x18258CD60       ; Map-Insert
```

→ `SetString(key, value)` mit zwei `UtfString`-Argumenten, beide als Zeiger.

### 2.1 RVA-Tabelle `Exor::Database` (Build 2.0.58485)

| Symbol | RVA |
|---|---|
| `Database::Database()` (Default-Ctor) | `0x2C6550` |
| `Database::~Database()` | `0x272B70` |
| `Database::Database(Database const&)` | `0x26C590` |
| `Database::Database(Database&&)` | `0x470CF0` |
| `Database::SetString(UtfString const&, UtfString)` | `0x25956B0` |
| `Database::SetFloat(UtfString const&, float)` | `0x2595550` |
| `Database::SetInt(UtfString const&, int)` | `0x2595600` |
| `Database::SetVector(UtfString const&, Vector3 const&)` | `0x2596100` |
| `Database::Get(StringHash)` → `Variant*` | `0x25911E0` |
| `Database::GetString(UtfString const&)` → `UtfString const&` | `0x2591FD0` |
| `Database::GetStringKeys()` → `Vector<UtfString>` | `0x2592060` |

**Ramifikation:** Ein eigenes Payload-Objekt = `malloc(0x60)` + Default-Ctor +
`SetString("spawn_point", v)` — alles pures C++ (Heap/STL-Container),
thread-agnostisch. Im Laufzeitpfad werden alle drei Adressen per **AOB**
aufgelöst (`RBBRIDGE_DB_CTOR_SIG`, `RBBRIDGE_DB_SETSTRING_SIG`,
`RBBRIDGE_AMF_SIG`), **nie** als feste RVA aufgerufen.

## 2.2 AOB-Eindeutigkeit gegen `.text` (LIVE, planet, 2026-09-15)

Gegenprobe jeder Laufzeit-Signatur gegen die echte `.text`
(`/srv/rbgame/bin/riftbreaker_dll_win_release.dll`, Build 2.0.58485,
`pefile`; `.text` VA `0x1000`, VirtualSize `0x2DA0A93`). Gezählt wird die
Trefferzahl des maskierten Musters; rel32-Operanden der E8-Calls sind
Wildcards.

| Signatur | Länge | Treffer | RVAs |
|---|---|---|---|
| `RBBRIDGE_ACTIVATE_SIG` (ActivateMissionFlow, Database*) | 33 B | **1** | `0xF93280` |
| `RBBRIDGE_DB_SETSTRING_SIG` (SetString) | 26 B | **1** | `0x25956B0` |
| `RBBRIDGE_DB_GETSTRING_SIG` (GetString) | 31 B | **1** | `0x2591FD0` |
| `RBBRIDGE_DB_CTOR_SIG` (Database::Database()) | 28 B | **258** | erster Treffer `0x26ADA0` (≠ Ctor) |
| `RBBRIDGE_DB_CTOR_SIG` (rel32 maskiert, 48 B) | 48 B | **46** | u. a. `0x26BB40`, `0x2C6550` |
| `RBBRIDGE_DB_CTOR_SIG` (voller 106-B-Body) | 106 B | **2** | `0x2C6550`, `0xDB7610` |
| `RBBRIDGE_DB_GETKEYS_SIG` (GetStringKeys) | 27 B | **3** | `0x2591BB0`, `0x2591D80`, `0x2592060` |

**Befund (Blocker für den naiven Prolog-Scan):** `Database::Database()`
(`??0Database@Exor@@QEAA@XZ`, `0x2C6550`) ist **nicht** per AOB eindeutig.
Der komplette 106-Byte-Body ist **byte-identisch** mit
`??0EntityStatComponent@Riftbreaker@@QEAA@XZ` (`0xDB7610`) — PDB-verifiziert
— und ~8 weitere `3×0x20`-Container-Ctors teilen den Prolog. Ein Prolog-Scan
würde also (erster Treffer `0x26B9A0` = `??0CampaignMissionSaveInfo@…`) die
**falsche Funktion** aufrufen.

**Lösung — `new 0x60`-Call-Site als Anker** (statt Prolog):
`mov ecx,0x60` (`B9 60 00 00 00`) → `call operator new` (`E8`) → 0x60-B-
Nullung → `mov rcx,rax` (`48 8B C8`) → `call Database::Database()` (`E8`).
Das rel32-Ziel des **zweiten** E8 ist der Ctor. Die auflösende Funktion
(`resolve_db_ctor_fn`) nimmt das rel32-Ziel aller `new 0x60`-Sites, verlangt
**genau ein** unterschiedliches Ziel (sonst `NULL`, kein Aufruf) und prüft es
gegen den Prolog `RBBRIDGE_DB_CTOR_SIG` gegen. `GetStringKeys` ist ebenfalls
nicht eindeutig (3 Schwestern) und wurde entfernt (unbenutzt).

Die präzise, build-gebundene Nachprüfung ersetzt die frühere Annahme
„Prolog-Signatur genügt" aus dem Review-Minor m2.

## 3. `MissionService`-Auflösung (RTTI-Kette)

| Symbol | RVA |
|---|---|
| `MissionService` vftable `??_7MissionService@Riftbreaker@@6B@` | `0x2E962A0` |
| RTTI-TypeDescriptor `??_R0?AVMissionService@Riftbreaker@@@8` | `0x405E6D0` |
| Complete-Object-Locator `??_R4MissionService@Riftbreaker@@6B@` | `0x391CB10` |
| RTTI-Name-String `.?AVMissionService@Riftbreaker@@` (inline ab TD+0x10) | `0x405E6E0` |

Resolve-Kette (identisch zum `ConsoleService`-Resolver):

1. RTTI-Namensstring `. ?AVMissionService@Riftbreaker@@` (inkl. NUL) suchen →
   TypeDescriptor = Fund − `0x10`.
2. pTypeDescriptor-Feld (`COL+0xC`) mit `td_rva` suchen; `COL.signature == 1`
   und `COL.pSelf (COL+0x14) == col_rva` prüfen.
3. QWORD == Modulbasis + `col_rva` scannen (`vftable-8`) → `vftable = Fund + 8`
   (Erwartung `0x2E962A0`).
4. Instanz: 8-Byte-aligniertes QWORD == vftable in committeter, lesbarer
   Region (kein `PAGE_GUARD`) → erster Treffer = `this`.

Nicht-Fund an **jeder** Stufe → `NULL` + `dbg()`, **kein** Aufruf.

## 4. Thread-Modell & Risiken (ehrlich)

* `Database`-Builder (malloc/Ctor/SetString): pures C++, thread-agnostisch.
* `ActivateMissionFlow` mutiert Mission-Flow-/MissionSystem-State → **bevorzugt
  Game-Thread** (Marshal-Detour `ConsoleService::Update`-Muster, #376). Der
  solcher Detour ist im Branch `feat/386-database-payload` **nicht** vorhanden; bis
  dahin läuft der **guarded Direktaufruf** aus dem Pipe-Thread — als
  **Live-Risiko** markiert (Story 8, nur mit Player).
* `data`-Lifetime: der Kern `0x33A7E0` reicht den Zeiger durch → das Payload
  muss bis zur Flow-Deaktivierung am Leben bleiben (kein `free` nach dem Call).
* `UtfString`-Rückgabe (sret) kann Heap allokieren → derzeit bewusst geleakt
  (kein `UtfString`-Dtor aufgelöst); offener Punkt, geringe Menge pro Aufruf.
* Read des **echten** aktiven Flow-`Database*` aus `MissionService` ist bislang
  nicht aufgelöst → `get_state.mission_flow` liest das **geparkte** Payload
  (C++-Accessor-Pfad end-to-end belegt); Misserfolg → Feld `null`.

## 5. Bausteine / Integration (Branch `feat/386-database-payload`)

* `rbbridge.c`: `resolve_db_setstring_fn` / `resolve_db_getstring_fn` /
  `resolve_db_ctor_fn` (AOB, `.text`-first), `build_database_payload`,
  `database_get_string`; `dispatch_activate_mission_flow` baut das Payload bei
  gesetztem `spawn_point` und reicht es als `data` durch; `get_state` liefert
  `mission_flow_payload{spawn_point}`.
* `pipe_bridge.c`: `POST /activate_mission_flow` reicht ein optionales
  `spawn_point`-Feld durch (Default = kein Payload, `data=NULL`).
* `cockpit.html`: mission-flow-Section zeigt `payload.spawn_point` (Read) und
  sendet ihn als `spawn_point` (Write).
* Host-Test: `tests/rbbridge-hosttest` deckt die drei AOB-Resolver + die
  Negativfaelle (`NULL`, kein Aufruf) ab.

## 6. Verweise

* `docs/research/dedicated-io-re-findings.md` — READ/WRITE-Pfade, Thread-Modell
  (`ConsoleService::Update`-Detour), Lua-Hook.
* `docs/research/io-write-poc.md` — `add_resource`-C++-Write, `UtfString`-Layout.
* `docs/research/dedicated-io-write-functions.md` — Write-Funktions-RVAs.