# Dedicated IO — Thread-Modell (Ist-Stand `main`)

**Single Source of Truth** für die Frage „auf welchem Thread läuft was" im
Dedicated-IO-Pfad (`rbbridge.dll` → `pipe_bridge` → Cockpit/Backend).
Stand: Build 2.0.58485, `main` (Commit-Stand 2026-09-15).

Dieses Dokument ersetzt die verstreuten/veralteten Thread-Aussagen in
`dedicated-io-write-functions.md`, `dedicated-io-re-findings.md`,
`io-write-poc.md`, `database-object-re-findings.md`, `docs/INGRESS_IO.md` und
`server/README.md`.

## Was `main` **nicht mehr** hat (historisch)

Der frühere Game-Thread-Marshal aus #376 ist **nicht mehr im Code**:

- **kein `exec`-Kommando** in `handle_line` / **kein `/exec`-Endpoint** in
  `pipe_bridge.c`,
- **kein `g_pending_cmd`**-Puffer,
- **kein vtable-Detour** auf `ConsoleService::Update` (`0x1C1FBA0`),
- **kein `lua_*`** und keine Lua-Skript-Ausführung in `rbbridge.c`.

Entfernt mit dem Umbau „C++ direct only":

- `c4db642` — `refactor(io): remove Lua exec + DOM-state capture (C++ direct only)`
  (Refs #376): Drop von generic `exec` (`ConsoleService::ExecuteCommand` → Lua),
  der exec-gerouteten WRITE-Kommandos (`pause_dom`/`resume_dom`/`end_game`) und
  der DOM-State-Capture (`capture_dom_state_game_thread` + Lua-C-API +
  `dom_mananger`-Resolver + `world_ready`).
- `f022f35` — `revert(#387): Lua-DOM-Pfad entfernen (pause_dom/resume_dom,
  dom_ref, LuaGraphNode-Resolver)` (#446).

Die in `dedicated-io-re-findings.md` „Phase C"/„Phase D" beschriebene
Lua-Architektur bzw. der `g_pending_cmd`-Detour sind damit **historisch** (RE-/
Machbarkeits-Nachweis), **nicht** der `main`-Stand.

## Ist-Stand `main`: alles läuft inline im Pipe-Thread

Es gibt **keinen** Marshal. Jeder Game-Call der Bridge wird **direkt und inline
auf dem Pipe-Thread** ausgeführt (jeweils „guarded", d. h. nur bei aufgelöster
Anbindung, sonst `ok:false` ohne Aufruf):

| Operation | Einstieg | nativer Call |
|---|---|---|
| `get_state` (Resources/HQ) | `dispatch_get_state` | `Riftbreaker::GetPlayerAccount(World*, …)` `0xC60050` → `GetPlayerTeam` `0xC60F50` |
| `add_resource` | `dispatch_add_resource` | `PlayerService::AddResourceAmount` `0xF1E3D0` |
| `activate_mission_flow` | `dispatch_activate_mission_flow` | `MissionService::ActivateMissionFlow` `0xF93280` |
| `deactivate_mission_flow` | `dispatch_deactivate_mission_flow` | `MissionService::DeactivateMissionFlow` `0xF960E0` |
| `creatures_difficulty` (set/inc/dec) | `dispatch_creatures_difficulty` | `CampaignService` Getter/Setter/Inc/Dec |
| `restart_map` | `dispatch_restart_map` | natives Kommando (#423) |

Der einzige empirisch als pipe-thread-safe belegte Mutator ist
`PlayerService::AddResourceAmount` `0xF1E3D0` (`io-write-poc.md` §B6,
Live-Beweis 2026-09-13). Alle übrigen Game-Calls sind auf dem Pipe-Thread ein
**offenes Live-Risiko** (siehe unten).

## Readiness-Gate (**#479**)

Der Pipe-Thread ruft Game-Funktionen, die eine **fertig initialisierte Welt**
voraussetzen — teils **zu früh** und **off-thread**.

- Belegter Crash (#479, Minidumps 2026-09-15, beide identisch): erster
  `get_state` **während der Map-Initialisierung** → `0xc0000005`, read at `0x36`,
  Stack `dispatch_get_state` → `GetPlayerAccount` `0xC60050` → `GetPlayerTeam`
  `0xC60F50` → `EcsContext::FindIt` `0x1DD0370` (`boost::unordered_map`).
- Der bisherige Guard `no_world` (`rbbridge.c:3047`) prüft nur `world != 0` — der
  Zeiger war **non-NULL**, die Welt aber noch nicht fertig → Guard greift nicht.

**Soll:** Readiness-Gate **vor jedem Game-Call** (nicht nur `world != 0`): Welt
fertig = z. B. `InstantiateMap took` / `NavigationGraph::Generate - Graph
generated` im exor-Log oder ein game-seitiges Ready-Flag; bis dahin
`ok:false, reason:"world_not_ready"` (graceful, kein Call).

## #436-Beleg (Live-Crash auf `planet`)

Auf `planet` (`riftbreaker-dedicated`, Mount `/opt/rbmods/rbtools-drift`) crashte
das Spiel **~3–4 s nach dem ersten schweren Game-Call** über die Bridge
(2026-09-15, #436):

```
[server]  CRASH:
wine: Unhandled page fault on read access to 0000000000000000 ...
wine: Unhandled page fault on read access to 00000000000001E0 ...
```

Betroffen: `get_state`, `activate_mission_flow` **und**
`deactivate_mission_flow` — jeder Call, der World/PlayerService/MissionService
anfasst. Kein #389-Regression (A/B identisch). Der gracefulte Pfad **ohne**
Game-Call (leerer Flow → `missing_flow`) läuft sauber durch. Der Container wurde
zusätzlich extern in kurzen Abständen neu gestartet (RestartCount=0,
`StartedAt` wechselt).

⇒ Dieselbe Klasse wie #479: **off-thread / zu früh** — der Pipe-Thread fasst
Game-Objekte an, für die es weder ein Readiness-Gate noch einen Marshal gibt.

## Marshal-Optionen (wenn wieder gebaut)

Ein Game-Thread-Marshal ist heute **nicht implementiert**. Das Muster aus #376
(Queue `g_pending_cmd` + vtable-Detour, Drain per `ConsoleService::Update`)
bleibt die dokumentierte Option — **aber** mit der **#378-Korrektur**:

- `ConsoleService::Update` (`0x1C1FBA0`) läuft auf einem **Worker**-Thread, nicht
  auf dem Main-/Game-Thread. Für **reine C++-Reads/Writes** ist das als
  Drain-Punkt brauchbar; für **`lua_*`-Aufrufe** ist es **untauglich** (Lua
  braucht den Main-Thread → crash).
- Da `main` kein `lua_*` mehr in `rbbridge.c` hat, laufen alle aktuellen Calls
  ohnehin auf dem Pipe-Thread; ein künftiger Marshal müsste entweder einen
  echten Main-Thread-Drain-Punkt finden oder – für die C++-Primitive – den
  Worker-Thread-Drain genügen lassen.
- Referenz-Muster: #376 (`g_pending_cmd` + Detour, in `c4db642`/`f022f35`
  entfernt). Aktueller Bedarf/Stand: **#479**.

## Offene Punkte (Kurz)

- **Kein Marshal in `main`** — alle Game-Calls inline im Pipe-Thread.
- **Queued**: Readiness-Gate + (ggf.) Game-Thread-Marshal — **#479**.
- **Live-Risiko**: alle Game-Calls außer `AddResourceAmount` — siehe **#436**.
- **`lua_*` ist raus** (C++-only, #387/#446): der #378-Thread-Hinweis betrifft
  nur noch einen hypothetischen künftigen Lua-Pfad.

Refs: #376, #378, #387, #436, #446, #479, #482.
