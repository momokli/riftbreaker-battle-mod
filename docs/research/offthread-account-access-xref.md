# Off-Thread-Zugriff auf Game-Container — xref-Analyse (#486, Build 2.0.58485)

Frage: Ruft das Spiel **selbst** `GetPlayerAccount`/Account-Zugriffe aus
Worker-Threads (`Exor::TaskWorldExecutor`-Systemen) auf? → Entscheidungsgrundlage
für #479 (Readiness-Gate, „Deferral nötig: ja/nein").

Methode (alles statisch gegen
`/srv/rbgame/bin/riftbreaker_dll_win_release.dll`, md5 `ddfa768c…`):

- Symbol-Tabelle aus `llvm-pdbutil dump -publics` (556.337 Symbole, 322.406
  Funktionen), RVA = Section-Base + dezimaler `addr`-Offset (Skill-Doku).
- **Alle** `call rel32` (E8) linear im `.text` (47.844.352 B) gescannt →
  910.013 direkte Calls; Call-Graph + Rückwärtsgraph lokal (`capstone`+`pefile`).
- Enthaltende Funktion je Call-Site über sortierte PDB-Publics aufgelöst.
- **Thread-Kontext** je Aufrufer: RTTI-Klassenhierarchie (`??_R3<Class>@@8` →
  `_RTTIClassHierarchyDescriptor` → Basisklassen-Array → `??_R0`-Typnamen)
  geparst. Ist `Exor::System` in der Basisliste ⇒ die Klasse läuft als
  `SystemTask` auf dem `TaskWorldExecutor` (Worker-Pool; Thread-Modell #378).

## 1. Aufrufer von `GetPlayerAccount` / `GetPlayerTeam`

`GetPlayerAccount(World*, uint)` RVA `0xC60050` — **77 Call-Sites, 75 Aufrufer**.
`GetPlayerAccount(World*, TeamId)` RVA `0xC600F0` — 12 Sites, 11 Aufrufer.
`GetPlayerTeam(World*, uint)` RVA `0xC60F50` — 70 Sites, 58 Aufrufer.

Aufrufer-Typen (RTTI-basiert):

| Ziel | `Exor::System` (Worker) | non-System (UI/Service) | ohne RTTI (frei/Lambda) |
|---|---|---|---|
| `GetPlayerAccount(World*,uint)` | **18** | 36 | 21 |
| `GetPlayerTeam` | **23** | 21 | 14 |
| `GetPlayerAccount(World*,TeamId)` | **5** | 2 | 4 |

`Exor::System`-Aufrufer von `GetPlayerAccount(World*, uint)` (Auszug, jeweils
RTTI-verifiziert, `ResourceSystem` → Basis `?AVSystem@Exor@@`):

```
ResourceSystem::OnStartRunning / RecreateAllBuildings / Refill /
                 UpdateConvertResource / OnResourceConverterRemoved
ResearchSystem::AddToResearch
BuildingSystem::BuildBuilding / ComponentCreated / FillWithFloors /
                 OnSellBuildingRequest / OnSellFloorRequest /
                 OnUpgradeBuildingRequest / RepairBuilding /
                 OnBuildingComponentRemoved
DiscoverableSystem::Update(float, SystemExecutionContext&)
RiftbreakerDebugSystem::Update(float)
ResourceClientSystem::UpdateSoundEffects
PersistentCampaignSystem::OnSellOutpostRequest
```

`GetPlayerTeam` zusätzlich u. a. `AnnouncementSystem` (8 Event-Handler),
`LootContainerSystem`, `PlayerSpawnSystem`, `MechClientSystem`.

> **Thread-Nachweis:** `ResourceSystem` erbt von `Exor::System` (RTTI), wird also
> als `SystemTask` geführt — `TaskWorldExecutor::Queue(System*)`
> (`0x2220B60`) konstruiert `SystemTask`-Objekte in den Executor-Vektor
> (Disasm: Aufruf `construct<SystemTask>` + Append). `SubmitSystemTasks`
> (`0x2224920`) / `TryScheduleTask` (`0x22259C0`) verplanen sie; nach #378 laufen
> die System-`Update`s auf Worker-Threads. `ResourceSystem::UpdateConvertResource`
> (`0xB35480`) wird z. B. direkt aus `ResourceSystem::Update`
> (`0xB33B90`, virtuelle Executor-Update-Signatur) gerufen — der
> `GetPlayerAccount`-Call `0x180B356FB` sitzt also im Worker-Pfad.

**Ergebnis:** Account-Zugriffe sind **nicht** auf Main-/Lua-Thread beschränkt —
das Spiel ruft `GetPlayerAccount`/`GetPlayerTeam` aus Executor-Systemen.

## 2. Schreibseite (Gegenprobe)

- `PlayerService::AddResourceAmount` (`0xF1E3D0`): 2 Aufrufer = die eigenen
  Convenience-Overloads; diese sind **nur** per `PlayerService::RegisterLua`
  als Adresse referenziert (lea-Scan) ⇒ öffentliche API ist **Lua-gebunden**.
- `MissionService::ActivateMissionFlow` (`0xF93280`, + 4 Overloads): ebenso nur
  via `MissionService::RegisterLua` ⇒ **Lua-gebunden**.
- `ConsoleService::ExecuteCommand` (`0x1C0BEF0`): nur via
  `ConsoleService::RegisterLua` ⇒ **Lua-gebunden** (deckt #378).
- **Aber:** Account-**Mutation** intern läuft sehr wohl auf Workern:
  `ResourceAccount::Increase` (`0x2D6670`) / `ResourceBasket::Increase`
  werden aus `ResourceSystem::RecreateAllBuildings/Refill`,
  `ResearchSystem::AddToResearch/CheckAndFillGlobalResources`,
  `CraftingSystem::StartCreating`, `BuildingSystem::OnSell*Request`,
  `PipesSystem::RemoveConnection/SplitConnection`,
  `Beam/BurstWeaponSystem::CheckAmmoExistence` gerufen (alle `Exor::System`).

## 3. Locks / Guards (Disasm)

| Funktion | `lock`-Instruktionen |
|---|---|
| `GetPlayerAccount(World*,uint)` `0xC60050` | **0** |
| `GetPlayerAccount(World*,TeamId)` `0xC600F0` | **0** |
| `GetPlayerTeam` `0xC60F50` | **0** |
| `EcsContext::FindIt` `0x1DD0370` | **0** |
| `AddResourceAmount` `0xF1E3D0` | 5 (`lock inc` / `lock xadd` on `[+8]`/`[+0xc]` = SharedPtr-Refcounts, **keine** State-Guards) |
| `ActivateMissionFlow(DB)` `0xF93280` | **0** |

Der Lese-Pfad ist `GetPlayerAccount → GetPlayerTeam → Ecs::Find<> →
Ecs::GetComponent` — durchgehend **ohne** Synchronisation. Sicherheit beruht also
auf der Scheduling-Ordnung des Executors, nicht auf Locks.

## 4. Entscheidung

- **Reads: Deferral nicht nötig.** `GetPlayerAccount`/`GetPlayerTeam` werden vom
  Spiel selbst aus `TaskWorldExecutor`-Systemen gerufen (18 bzw. 23
  RTTI-verifizierte `Exor::System`-Aufrufer) — der Zugriff ist nicht
  Main-Thread-exklusiv. Zusammen mit dem crash-freien Prod-Lauf (#479: ~15 h,
  `get_state` alle ~2,5 s) ⇒ **kein Beleg** für ein Threading-Problem nach dem
  Readiness-Gate; kein Umbau auf Detour/Marshaling für Reads.
- **Writes: schwächere Datenlage.** Die öffentlichen Write-APIs
  (`AddResourceAmount`, `ActivateMissionFlow`, `ExecuteCommand`) sind
  Lua-/Main-Thread-gebunden; off-thread aufgerufen hat nur `dispatch_add_resource`
  (Pipe-Thread). Account-Mutation *an sich* läuft im Spiel aber auch auf Workern
  (`Increase`), daher **kein zwingender** Deferral-Grund — die spezifische
  PlayerService-API bleibt aber ohne Off-Thread-Präzedenz.
- **Empfehlung:** #479 bleibt beim Readiness-Gate (keine Threading-Maschinerie).
  Ein evtl. Restrisiko der Writes weiter über #480/#481 messen; keinen
  vtable-Detour ohne Thread-Nachweis (#479-Regel).

## Belege / Reproduktion

- Symbole: `llvm-pdbutil dump -publics /srv/rbgame/bin/riftbreaker_dll_win_release.pdb`
- Call-Sites: E8-Scan über `.text` + Resolve über sortierte Publics (capstone).
- RTTI: `??_R3<Class>@@8` (numBaseClasses, BaseClass-Array, `??_R0`-Namen).
