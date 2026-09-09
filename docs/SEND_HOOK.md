# SEND_HOOK — Feasibility: Sends exakt an den Start der natürlichen DOM-Welle hängen

Stand: 2026-09-09 · Spielversion 2.0.58485 (GOG-Extrakt, `lan:/home/momo/rb-game/lua-src/`)
Zweck: Beantwortung des Feasibility-Gates **vor** jeder Send-Queue-Implementierung
(Issue #25/#27): *Können Sends exakt an den Start der natürlichen DOM-Welle gehängt
werden?* Dieser Befund ist die technische Basis für den späteren Wellen-Routing-Hook;
er ändert **keinen** Spiel-Code und **kein** Mod-Verhalten.

---

## 1. Beleglage

| # | Frage | Befund | Beleg |
|---|---|---|---|
| (a) | Empfängt der Mod Wellen-/Send-Trigger? | Ja — über den Konsolen-Pfad: `exec_cmd_client "rb_wave 3"` → `ConsoleService:ExecuteCommand` → Lua-Command `rb_wave` → `[RBBATTLE] event=wave level=3 status=start/done` (Prod-Log 2026-09-09, 8 Spawns; Issue #18). Das sind **Mod-eigene** Events beim Send-Spawn — kein Engine-"Wave-Event". | Issue #18, mod/README v0.2.0 |
| (b) | Gibt es einen nativen Debug-Wellen-Trigger? | Ja: Lua-Command `debug_dom_manager_spawn_wave_level <level>` → füllt `g_debug_dom_manager_spawn_wave_levels` → `dom_mananger:Update()` ruft pro Level `self:SpawnWavesForDifficultyLevel(level, false)` — **derselbe** Codepfad wie die Naturwelle. | `lua/commands/debug.lua:63`, `lua/missions/v2/dom_manager.lua:200` |
| (c) | Wie spawnt die Naturwelle? | DOM v2 (`dom_mananger`, LuaGraphNode mit State-Machine `self.spawner`). Welle startet im State `spawn`: `OnEnterSpawn` → `SpawnWavesForDifficultyLevel(currentDifficultyLevel, true)` → `SpawnWave(...)` bzw. `SpawnPreparedWave(...)` → pro Attack zufälliger Spawn-Point via `RandomizeSpawnPoint(borderSpawnPointGroupName, waveData)` → `MissionService:ActivateMissionFlow("", waveData.name, "default", self.data)` (`data.spawn_point` = Entity-Name). | `dom_manager.lua:1646–1725` |
| (d) | Was sind die 16 natürlichen Spawner? | Kartenrand-Entities mit Blueprint `logic/spawn_enemy` in den 4 Nicht-Spielbar-Streifen; `mission_base:SelectWaveSpawnPoints` gruppiert sie als `spawn_enemy_border_{west,east,north,south}` und benennt sie (`<gruppe>/<id>`). DOM wählt je Welle 1 Gruppe + 1 Entity daraus. Anzahl je Karte = Generator-Ergebnis (Issue #26 nennt 16). | `mission_base.lua:115–150`, `dom_manager.lua:70–75, 969–1025` |
| (e) | Gibt es ein Engine-Event „Welle beginnt“? | **Nein.** `RegisterHandler`-Grep über 118 eindeutige Event-Namen (LUA_RESEARCH) enthält kein Wave-Start-Event. DOM selbst lauscht nur auf `LuaGlobalEvent` (PauseDOM/ResumeDOM/…), `MissionFlowActivatedEvent/DeactivatedEvent`, `StartUpgradingEvent`, `PlayerDiedEvent`, `RespawnFailedEvent`. `MissionFlowActivatedEvent` feuert für **alle** Flows (Wetter, Objectives, Wellen) — Name-Matching wäre brüchig. | `dom_manager.lua:98–103`, `LUA_RESEARCH.md` |

## 2. Optionen

### Option A — Event-Hook (RegisterGlobalEventHandler)
Auf ein passendes Event lauschen und dort eigene Spawns anstoßen.
- Pro: saubere Kapselung, kein Eingriff in Spiel-Klassen.
- Contra: Es existiert **kein** Wave-Start-Event (1e). `MissionFlowActivatedEvent`
  müsste per Flow-Name gefiltert werden; Wellen-Flow-Namen sind rules-abhängig
  (`rules.waves[<group>]`, `wavesEntryDefinitions`, `prepareAttackDefinitions`,
  `extraWaves`, `bosses`, `multiplayerWaves` — Dutzende Namen), unzuverlässig und
  versionfragil. „Exakt Wellenstart“ ist damit **nicht** garantierbar → verworfen.

### Option B — Function-Wrap (Lua-Monkey-Patch) am `dom_mananger`
Wrapper um eine DOM-Methode; der Mod-Code läuft **im selben Call** wie die Naturwelle.
- Angriffspunkte (in Klammern: was der Wrapper sieht):
  1. `dom_mananger:OnEnterSpawn(state)` — feuert bei **jedem** natürlichen
     Wellenstart (State `spawn` betreten), inkl. Prepared-Attacks. Nicht bei
     HQ-Major-Attacks (eigener `upgradeHQ`-Pfad, `OnHqEnterAttackLogic`).
  2. `dom_mananger:SpawnWavesForDifficultyLevel(level, addToSpawned)` — der
     gemeinsame Chokepoint von Naturwelle (`OnEnterSpawn`, addToSpawned=true) **und**
     Debug-Trigger (Update, false). Ideal für „Send-Queue boostet die nächste Welle“
     (Attack-Count/Pool mutieren) und für Tests via `debug_dom_manager_spawn_wave_level`.
  3. `dom_mananger:SpawnWave(...)` / `PrepareWave(...)` — unterste Spawn-Ebene,
     deckt auch HQ-Major-Pfade ab; viele Call-Sites, feinste Kontrolle.
- Pro: exakter Zeitpunkt (gleiche Call-Tiefe wie die Naturwelle), kein Event-Name-
  Matching, Nutzung von `self` (rules, spawner-State, `data.spawn_point`) möglich.
- Contra/Risiken: (i) Sichtbarkeit der globalen Klasse `dom_mananger` im Autoexec-
  Environment (Klasse entsteht erst beim `MissionService:AddGameRule(...)`-Load —
  Reihenfolge vs. Autoexec unbestätigt) → **lazy idempotenter Patch** beim Mod-Load
  UND bei `PlayerInitializedEvent` (feuert verifiziert nach Missions-Setup) UND vor
  jedem Send-Spawn als Retry; schlägt alles fehl → Log `status=skip reason=no_class`,
  keine Funktionsbeeinträchtigung. (ii) Method-Dispatch: EXOR-class (`class 'dom_mananger'`
  ist Engine-Binding) — Aufrufe erfolgen dynamisch als `self:GetPrepareSpawnTime()` bzw.
  State-Machine-Dispatch `self[enterName]` → Klassen-Tabellen-Patch greift für spätere
  Calls; der Wrapper ruft das Original via gesicherter Referenz. (iii) Die Klasse darf
  **nie** per `require("lua/missions/v2/dom_manager.lua")` aus dem Mod neu geladen
  werden (Re-Execution würde die Klassen-Tabelle ersetzen und bestehende Instanzen
  entkoppeln) — nur Patch bei bereits vorhandenem Global. (iv) Game-Versionen:
  Patch zielt auf v2-API (`GetPrepareSpawnTime`/`SpawnWavesForDifficultyLevel`),
  die alle aktuellen Kampagnen-/Survival-Modi nutzen; v1 (`missions/dom_manager.lua`)
  hat andere Methodennamen → nur patchen, wenn API existiert, sonst Log.
  (v) `PauseDOM`/`SetSuspended(true)` friert die DOM-Update ein → Wrapper läuft dann
  nicht; Send-Queue muss den DOM-Pause-Zustand des Sync-Starts (#22) beachten.

### Option C — Nur nativer Debug-Trigger (`debug_dom_manager_spawn_wave_level`)
Nutzt denselben Codepfad (1b), aber: kein Zugriff auf Queue/self, nur „Level“
(= difficultyLevel) — für E2E-Smoke-Tests der Welle nützlich, nicht für Sends.
→ als Test-/Verifikationswerkzeug notiert, nicht als Hook-Strategie.

## 3. Entscheidung

**Function-Wrap (Monkey-Patch) an `dom_mananger`** — primärer Einhängepunkt für
die Send-Integration (#25/#27):

```lua
-- Skizze (Pseudocode; pcall-gesichert, idempotent, KEIN require des Moduls!)
-- A) Klasse holen (nur wenn schon geladen):
--    local dom = _G.dom_mananger   -- global class table (engine class binding)
-- B) Original sichern + ersetzen:
--    RBB.origOnEnterSpawn = dom.OnEnterSpawn
--    dom.OnEnterSpawn = function(self, state)
--        RBB.origOnEnterSpawn(self, state)      -- Naturwelle UNANGETASTET lassen
--        RBB.SpawnQueuedSends(self)             -- Sends exakt im Wellenstart-Call
--    end
-- C) Queued sends anhaengen (self = dom instance):
--    function RBB.SpawnQueuedSends(self)
--        if RBB.sendQueueEmpty() then return end
--        local group = self.borderSpawnPointGroupNames[RandInt(1, #self.borderSpawnPointGroupNames)]
--        -- z. B. self:SpawnWave(1, group, RBB.sendWavePool, "rbbattle: Send attack : ",
--        --                        true, "", "label_small", 0, self.spawnedAttacks)
--        -- oder Direkt-Spawns an Rand-Spawnern (Pfad v0.3.0, Issue #26)
--    end
-- D) Retry/Patch-Zeitpunkte: Mod-Load, PlayerInitializedEvent, vor jedem rb_wave/rb_send
```

Alternativer/ergänzender Einhängepunkt: `SpawnWavesForDifficultyLevel` wrappen, wenn
die Send-Queue die **nächste Naturwelle mutieren** soll (Anzahl/Pool erhöhen), statt
eigene Extra-Waves zu spawnen — Entscheidung fällt in Issue #25.

## 4. Umsetzungsstand v0.3.0 (diese Foundation)

- Issue #26: Send-Spawns laufen **jetzt** an den natürlichen Rand-Spawnern
  (Gruppen `spawn_enemy_border_*`, zufällige Auswahl) — dieselben Anker wie die
  Naturwelle; DOM-Code unverändert.
- Issue #23: DOM-Timer-Cap 300 s via `GetPrepareSpawnTime`-Wrap (Klasse) —
  Pilotnutzung des Function-Wrap-Musters, liefert zugleich den Live-Beweis für
  die Wrap-Wirksamkeit (Operator prüft „Time left“ via `debug_dom_manager 1`
  bzw. `[RBBATTLE] event=dom_timer patch status=ok`).
- Der eigentliche Wellen-Hook (Queue→Naturwelle) bleibt **bewusst** Issue #25/#27
  vorbehalten — hier nur Befund + Strategie.

## 5. Risiken (Rest)

1. Autoexec-/Missions-Environment-Sichtbarkeit der Klasse — nicht 100 % belegbar
   ohne Live-Test; alle Patch-Versuche loggen ihr Ergebnis (`[RBBATTLE]`), Fehlschlag
   ist harmlos (kein Wrap = Vanilla-Verhalten).
2. Klassen-/Methoden-Dispatch-Semantik des Engine-`class`-Bindings — hohe
   Wahrscheinlichkeit für dynamischen Dispatch (State-Machine ruft per Methoden-Name),
   aber Live-Bestätigung nötig → Operator-Test: Timer-Log + `debug_dom_manager 1`.
3. Game-Update ändert `dom_manager.lua` — Patch bricht graceful ab (API-Check),
   Funktionalität (Sends/Timer) degradiert mit Log statt Crash.
4. „16 Spawner“ ist die Issue-Erwartung; der Mod zählt zur Laufzeit
   (`event=wave_spawners count=N`) und funktioniert mit jeder Anzahl ≥ 1.

## Quellen
- `lan:/home/momo/rb-game/lua-src/` (2.0.58485): `missions/v2/dom_manager.lua`,
  `commands/debug.lua`, `missions/mission_base.lua`, `missions/survival/v2/dom_survival_*_rules_*`,
  `utils/find_utils.lua`
- `lan:/home/momo/rb-game/LUA_RESEARCH.md`, `LOBBY_RESEARCH.md` (2026-09-09)
- Repo: Issue #26/#25/#27/#12/#18, docs/GAME_DESIGN.md („Wellen-Routing“)
