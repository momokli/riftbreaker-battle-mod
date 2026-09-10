# RBBattle Einzel-Mod — Installation & Test (Stand 09.09.2026)

Einzel-Mod **rbbattle** v0.9.0 für den Runden-Duell-Modus („Biter
Battles“-artig, RIFT BATTLE) in *The Riftbreaker*. Nachfolger von
v0.2.0-single (Fusion Baustein 00 + 01). Kein Workshop-Release, keine Garantie.

**v0.9.0 — Win-Condition: HQ-HP, Leak-Erkennung, HQ-Tod → Match-Ende (Issue #28):**
- **Mod:** Leaks (feindliche Kreaturen in der Trigger-Zone ums HQ,
  `EnteredTriggerEvent`) senken den HQ-HP (`event=leak`/`event=hq_hp`);
  HQ-Tod — HP ≤ 0 **oder** `RespawnFailedEvent` der HQ-Entity — meldet
  `event=hq_dead` + `event=match_end`. Report läuft als `[RBBATTLE]`-Log-Zeile
  über die Bridge an den Tournament-Server (`POST /report hq_hp`, dort
  bereits implementiert). Operator-/Dev-Kommando `rb_hq` (Status, `leak`,
  `entity <id>`, `reset`).
- **Server (tournament/):** HQ-HP-Buchung, Match-Ende bei HP ≤ 0 (`winner`),
  `match_end`-Feed und Rematch (`POST /rematch`) sind bereits aus
  Issues #29/#30/#44 vorhanden — **keine Server-Änderung** nötig, Tests gruen.
- **Statisch getestet** (fengari/Stub-Services, `tests/lua-static/`): Leak →
  HP-Senkung → Match-Ende, RespawnFailedEvent-Kette, Idempotenz, negative
  Fälle. **OFFEN (kein Live-Spiel):** Trigger-Zone-Asset ums HQ,
  `EnteredTriggerEvent`-Feuerung, HQ-Entity-Identifikation, Sieg-Screen-UI.

**v0.8.0 — Headless-Client bootet bis Hauptmenü (Issue #9, Mod-Laufzeit unverändert):**
- `tools/headless-client/`: Compose-Service (`docker-compose.yml`, shm_size 1 GB,
  `init: true` für saubere SIGTERM-Weiterleitung) plus deterministischer
  Menü-Nachweis `wait-menu.sh` (Graustufen-Standardabweichung > `RENDER_MIN_STD`;
  Timeout sichert letzten Frame und gibt `exit 1`), `run-client.sh` mit
  `--no-verify`/`--screenshot`, Trockenlauf-Tests `test-wait-menu.sh` (5/5) und
  dokumentierter Proton-GE-Fallback (DXVK-on-lavapipe).
- Reines Tooling-/Doku-Release — **keine Mod-Änderung**.
- Landing-Seite: Link-Fix (`docs/concept.md`) + Links zu Solo-/Status-Seite (#56).

**v0.7.0 — rb_wave ohne Spieler (Spawn-Anker alternativ zum Mech, Issue #12):**
- **Anker-Kette OHNE Spieler:** `rb_wave`/`rb_send` wählt den Spawn-Anker in
  3 Stufen — (1) natürliche Kartenrand-Spawner (`spawn_enemy_border_*`, #26),
  (2) **Missions-Spawnpunkte** (Fallback NEU: `FindService:FindPlayerSpawnPoints()`
  + `MapGenerator:GetInitialSpawnPoint()`), (3) Spieler-Mech-Ring (letzter
  Fallback, braucht einen Spieler). Stufe (1) und (2) sind serverseitig
  verfügbar, sobald die Welt gebootet ist — `rb_wave` spawnt damit auf einem
  **leeren (unpausierten) Server ohne Client/Spieler**.
- Log: `event=wave … anchor=border|mission|mech`; Missions-Fallback loggt
  `event=wave … status=no_border_spawners anchor=mission_spawn_point count=N`.

**v0.6.0 — Headless-Client-Tooling (Issues #7/#8), Mod-Laufzeit unverändert:**
- `tools/headless-client/`: Container-Setup (Wine + Xvfb + Mesa-llvmpipe),
  Entrypoint `run-client.sh` und xdotool-Navigation `xdo-nav.sh` (#7).
- Client-Daten-Sync lan→planet: `sync-client-data.sh` (Größencheck → rsync →
  Verifikation inkl. MD5-Stichprobe) + Trockenlauf-Tests (#8).
- **Keine Änderung** an `mod/lua/` oder am Send-/Economy-Verhalten gegenüber
  v0.5.0 — reines Tooling-Release.

**v0.5.0 — MVP Single-Player Self-Send (Sich-selber-senden, Issue #42):**
- **Mod-Mode `rb_mode sp|duel`** (Default `sp`): im `sp`-Mode boostet der
  Send-Pool die **eigene nächste Naturwelle** — der Testmodus zum
  Alleine-Ausprobieren. `duel` ist ein Stub (1v1-Routing folgt, #25/#27).
- **`rb_convert` Calcium-first (#40):** `rb_convert <menge>` konvertiert
  **Calcium** (= Spiel-Ressource `carbonium`, Faktor 1) irreversibel in den
  Send-Pool; `rb_convert <resource> <menge>` bleibt abwärtskompatibel
  (Alias `calcium` ≡ `carbonium`). Pool persistiert (#24).
- **Self-Boost am Wellenstart (Hook aus #36):** Function-Wrap an
  `dom_mananger:OnEnterSpawn` — beim Start der natürlichen Welle wird der Pool
  **greedy (teuerste Kreatur zuerst)** in Zusatz-Spawns an den eigenen
  Rand-Spawnern (#26) umgesetzt und verbraucht. Der %-Stärke-Boost der
  nächsten Welle (#39) bleibt offen, weil die dom_manager-Wave-Strength-API
  unverifiziert ist (`docs/SEND_HOOK.md`) — #26 ist der dokumentierte Fallback.
- **`rb_status`**: zeigt Runde, Pool und den nächsten Boost (Konsolen-Fallback
  für das spätere HUD, #27).

**v0.4.0 — Economy (Duell-Ökonomie, Issue #24):**
- **Issue #24:** Alle **gefarmten Ressourcen** (Carbonium, Cobalt, …) werden als
  **Value** getrackt — Quelle: `ResourceObtainedEvent`/`ResourceChangeEvent`
  (Getter-Ladder, erste lesbare Quelle sperrt; kein Doppel-Zählen).
  **Convert ist bewusst & IRREVERSIBEL**: `rb_convert carbonium 100` wandelt
  gefarmten Wert in **Send-Währung** (Spar-Pool) — kein Rücktausch-Pfad.
- **Spar-Pool persistiert über Runden** (Global-Database `rbbattle_economy`,
  profilgebunden; überlebt Welt-/Mod-Neustart).
- **Built-Value** (= nicht konvertierter Farmwert, GDD: „was gebaut wurde =
  was NICHT gesendet wurde“) wird getrennt geführt — Reveal-Basis für #27.
- **Dokumentierter Fallback:** Fehlt die Ressourcen-Event-API (Getter nicht
  lesbar), schaltet der Mod nach 3 Fehlern dauerhaft auf HourEvent-Tick-
  Einkommen um (Muster Baustein 05; Befund: kein verifizierter Konto-Zugriff,
  `docs/research/api-deep-dive.md` §1).

**v0.3.0 — Mod-Core (Foundation):**
- **Issue #26:** Send-Spawns spawnen an den **16 natürlichen Kartenrand-Spawnern**
  (DOM-Gruppen `spawn_enemy_border_{south,north,east,west}`, zufällige Auswahl je
  Kreatur) statt am Spieler-Mech — **kein Spieler nötig** (Server-only-tauglich).
  DOM-Naturwellen bleiben unangetastet (Basis-Druck). Fallback auf den alten
  Mech-Ring nur, wenn eine Welt keine Rand-Spawner hat.
- **Issue #23:** DOM-Wellen-Vorbereitung auf **300 s** gedeckelt
  (prepareSpawnTime 420→300, 5-Min-Wellen) + Setup-Log (`difficulty`,
  `creatures_difficulty`). Difficulty/Map-Größe/Seed werden beim Server-Start
  gesetzt (C++, kein Lua-Weg) — Ablauf: `docs/DUEL_SETUP.md`.
- Neuer Command-Alias **`rb_send`** (gleiche Logik wie `rb_wave`).
- Konzept-Doku: `docs/SEND_HOOK.md` (Wellen-Hook-Strategie), `docs/SYNC_START.md`
  (Pause/Unpause-Befund #22).

Inhalt des Mod-Ordners: Skeleton-Lebenszeichen-Log beim Laden +
Console-Commands `rb_wave <level>` / `rb_send <level>` (Send-Wellen-Spawning an
Kartenrand-Spawnern) + DOM-Timer-Deckel + Economy (`rb_convert`, `rb_economy`) +
Self-Send-MVP (`rb_mode`, `rb_status`, Self-Boost-Hook) +
Win-Condition (`rb_hq`, Leak-/HQ-Tod → Match-Ende, #28).
Kein UI, keine Bindings, kein Bridge-Zusatz, **kein io/socket/http**.

**Mod-Descriptor** (`<GUID>.manifest` im Mod-Root): deklariert Metadaten + die
Spielversion, gegen die der Mod gebaut ist (`game_version "EXE: 1186 DATA: 847"`
für Spiel 2.0.58485). Ohne Descriptor zeigt der Client „Unknown game version“
(Issue #17). Format = `WorkspaceManifest { … }` der EXOR-Workspace-Tools
(Beleg: echte Workshop-Mods, z. B. github.com/lilly1987/Riftbreaker-mods;
Spiel-Regex `EXE: <n> DATA: <n>`).

## Installation (lokaler Mods-Ordner)

Der Inhalt dieses Ordners (`mod/`) ist **eine Mod**: Er spiegelt die
Content-Struktur des Spiels (`lua/`, …) — genau wie es die EXOR-Workspace-Tools
und Workshop-Mods tun (Quelle: fandom „Basic Modding Guide“, Ordner
`<game>/mods/<ModName>/`; echte Mods: github.com/lilly1987/Riftbreaker-mods).

### Windows (Steam)
1. Steam-Bibliothek finden: z.B. `D:\SteamLibrary\steamapps\common\Riftbreaker`
2. Ordner anlegen bzw. kopieren:
   ```
   <SteamLibrary>\steamapps\common\Riftbreaker\mods\rbbattle\
       lua\rbbattle_autoexec.lua
       {96745BE8-78FD-4C30-9718-D57AA40B9C09}.manifest
   ```
   (`mods\` ggf. neu anlegen; der Spiel-Ordner `mods` ist identisch mit dem
   Workspace-Ordner, den die „Riftbreaker Tools“ verwenden.)
3. Spiel starten, beliebige Karte laden (Kampagne oder Survival).
   Die Datei `*_autoexec.lua` wird **bei Kartenerstellung** automatisch
   ausgeführt — kein weiterer Aktivierungsschritt (Steam) nötig.

### Alternativen (Download-Pfade des Spiels, falls später nötig)
- Steam Workshop: `<SteamLibrary>\steamapps\workshop\content\780310\<modid>\`
- mod.io: `C:\Users\Public\mod.io\3951\mods\<modid>\`

### macOS (Steam)
- Install-Pfad: `~/Library/Application Support/Steam/steamapps/common/Riftbreaker/`
- Dort analog `mods/rbbattle/` anlegen (Ordner ggf. neu erstellen).
- ⚠️ Offiziell heißt es „Steam **und** GamePass **PC** können modden“ —
  **ungetestet auf macOS**, in-game prüfen (siehe FINDINGS). Steam-Ordner muss
  nicht zwingend „common“ heißen — Pfad über Steam → Verwalten → Lokale Dateien
  anzeigen lassen.

## Aktivierung / In-Game-Konsole

- Konsole öffnen mit `` ` `` / `~` / `ö` / `'` (je nach Tastatur-Layout;
  bei deutscher Tastatur: `ö`). Quelle: fandom „Console commands“.
- Falls die Konsole nicht aufgeht: in
  `<Documents>\The Riftbreaker\Conf\initial_config_win` (o.ä.) prüfen —
  `set enable_developer_console 0` → `1` (bei GamePass nötig; Steam meist offen).
- **Log-Datei**: `<Documents>\The Riftbreaker\exor_logs.txt` — dort schreibt
  `LogService:Log`; alle Mod-Zeilen tragen den Präfix `[RBBATTLE]`.

## Commands

| Eingabe | Wirkung |
|---|---|
| `rb_wave 1` … `rb_wave 3` (Konsole/Bridge) | Spawnt Send-Welle an **zufälligen natürlichen Kartenrand-Spawnern** (5 Brabits / +3 Baxmoth / +2 Artigian +1 Canceroth, je Kreatur zufälliger Spawner aus den 4 Gruppen `spawn_enemy_border_*`); ungültige Stufe (`rb_wave 99`) fällt mit Warnung auf Welle 1 zurück. Kein Spieler-Mech nötig (Server-only). Ohne Rand-Spawner: Fallback-Ring um den Mech |
| `rb_send <level>` | Alias für `rb_wave` (Send-Semantik für Shop-/Queue-Integration #25) |
| `rb_wave`-Log-Anker | `anchor=border spawners=N` (bzw. `anchor=fallback_mech`), je Kreatur `anchor=<gruppe>/<id>` im `event=spawn ok`-Log |
| `rb_convert <resource> <amount>` | Wandelt gefarmte Ressource **irreversibel** in Send-Währung (Spar-Pool). **MVP:** `rb_convert <menge>` konvertiert **Calcium** (`carbonium`, Faktor 1); `rb_convert calcium 100` ≡ `rb_convert carbonium 100` ≡ `rb_convert 100`. Weitere Ressourcen (Faktor-Tabelle `resourceFactors`, z.B. palladium 2×, uranium_ore 3×) via 2-Arg-Form. Ablehnung bei zu wenig Farm-Menge (`status=insufficient`); kein Rücktausch. Balance = Platzhalter (Tuning #33) |
| `rb_economy` / `rb_economy reset` | Status: Quelle, Pool, farmed/converted/built, Ressourcen-Konten, DB-Status. `reset` = Entwickler-Werkzeug (alles auf 0, inkl. Ressourcen-Keys der DB) |
| `rb_mode sp\|duel` | Modus-Umschaltung: `sp` = Solo-Test (Default, self-send), `duel` = 1v1 (Stub, folgt später) |
| `rb_status` | Zeigt `mode`, `runde`, `pool` und den `boost` (nächste Welle) — die Kontrollanzeige des Testmodus |
| `rb_hq` / `rb_hq leak [dmg]` / `rb_hq entity <id>` / `rb_hq reset` | Win-Condition-Status + Dev-Werkzeuge (#28): HQ-HP zeigen, manuellen Leak anwenden, HQ-Entity zuordnen, Zustand zurücksetzen (Muster `rb_economy reset`) |

Erwartete Log-Zeilen in `exor_logs.txt` bei Kartenerstellung:

```
[RBBATTLE] skeleton ok
[RBBATTLE] event=mod_load version=0.8.0 status=ok mode=sp anchor=border_spawner_groups timer_cap=300 econ_source=none econ_pool=0
[RBBATTLE] event=economy_db status=new db=rbbattle_economy      ← erste Runde
[RBBATTLE] event=economy_source source=resource_obtained status=active   ← erste lesbare Ernte
[RBBATTLE] event=economy_farm source=resource_obtained resource=carbonium amount=100 value=100 farmed=100 built=100
[RBBATTLE] event=convert resource=carbonium amount=100 value=100 pool=100 status=ok irreversible=1
[RBBATTLE] event=wave_hook patch status=ok                       ← Self-Boost-Hook aktiv (#42)
[RBBATTLE] event=dom_timer patch status=ok cap=300        ← nach PlayerInitializedEvent
[RBBATTLE] event=setup difficulty=hard creatures_difficulty=5 timer_cap=300
[RBBATTLE] event=round round=1 status=start mode=sp pool=100   ← natürlicher Wellenstart
[RBBATTLE] event=self_boost round=1 pool_before=100 spent=100 spawned=1 pool_after=0   ← Self-Boost
[RBBATTLE] event=wave level=3 status=start
[RBBATTLE] event=wave_spawners count=16 groups=4          ← Pool der Rand-Spawner
[RBBATTLE] event=spawn ok blueprint=units/ground/baxmoth entity=12345 anchor=spawn_enemy_border_west/...
[RBBATTLE] event=wave level=3 status=done spawned=8 skipped=0 anchor=border spawners=16
[RBBATTLE] event=leak damage=10 hp_before=100 hp=90        ← Kreatur erreicht HQ-Zone (#28)
[RBBATTLE] event=hq_hp hp=90 dead=false                    ← Report → Server (POST /report hq_hp)
[RBBATTLE] event=hq_dead status=match_end hp=0             ← HQ-Tod (HP ≤ 0)
[RBBATTLE] event=match_end reason=hq_destroyed winner=opponent
```

(Die genaue Zahl `count=` hängt von der Karte/Map-Size ab — Issue-Erwartung 16.)

## FINDINGS-Tabelle (Stand Recherche — In-Game-Test des Umbaus offen)

| # | Frage | Ergebnis laut Doku + Original-Spieldaten | Quelle |
|---|---|---|---|
| 1 | Mod-Layout / Einstiegspunkt | Ordner `<game>/mods/<name>/` spiegelt Content-Root; `lua/*_autoexec.lua` läuft bei Map-Erstellung, Zugriff auf alle Services + `RegisterGlobalEventHandler` | exorstudios-Wiki (autoexec.md); lilly1987/Riftbreaker-mods; fandom Basic Modding Guide |
| 2 | Wave-Spawn zur Laufzeit | ✅ `EntityService:SpawnEntity(blueprint, x, y, z, team)` — exakt die Implementierung von EXORs eigenem `debug_spawn_entity` (`lua/commands/cheat.lua`); Blueprints `units/ground/*` gegen `entities/units/ground/*.ent` der Spieldaten verifiziert | OriginalPacksData (PonomarevDmitry/RiftbreakersMods); fandom Console commands |
| 3 | Rand-Spawner finden | ✅ `FindService:FindEntitiesByGroup(group)` — dieselbe API, mit der `dom_manager` (`RandomizeSpawnPoint`) Naturwellen-Anker wählt; Gruppen `spawn_enemy_border_{west,east,north,south}`, Entities werden von `mission_base:SelectWaveSpawnPoints` aus `logic/spawn_enemy`-Entities gruppiert | lua-src 2.0.58485 (`dom_manager.lua`, `mission_base.lua`, `find_utils.lua`) |
| 4 | 5-Min-Timer (#23) | ✅ `dom_mananger:GetPrepareSpawnTime()` liefert rules-Wert (Survival hard/normal: 420); Mod wrappt die Klassen-Methode auf max. 300 s (idempotent, pcall) | lua-src (`dom_survival_*_rules_hard.lua`, `dom_manager.lua:1135`) |
| 5 | Custom Console Commands | ✅ `ConsoleService:RegisterCommand(name, cb)` — offiziell dokumentiert **und** von EXOR selbst so genutzt (cheat.lua: `debug_spawn_entity` …) | exorstudios-Wiki accessing-keyboard-hotkeys.md; fandom Mod service: ConsoleService; OriginalPacksData lua/commands/cheat.lua |
| 6 | Logging | ✅ `LogService:Log(...)` → `exor_logs.txt`; `ConsoleService:Write(...)` → In-Game-Konsole | exorstudios-Wiki debugging-using-lua-services.md |
| 7 | Team-Semantik | Team-String `""` = „Blueprint-Standard“ (EXOR-Cheat nutzt `""`; Spieler-Buildings wie Feinde spawnen korrekt). Team-Ids: Player=1 (Log `GetTeamId` → 1). `"no_team"` existiert für Marker. Für echte Duell-Teams später verifizieren | OriginalPacksData (cheat.lua, wave_ground.lua) |
| 8 | Pause/Unpause | ✅ `debug_dom_pause` **und** `debug_dom_resume` existieren (Lua, debug.lua:73/77); Server-Pause (`debug_pause_server`, `cfg_server_pause_game_when_empty`) ist nativ (C++) | lua-src 2.0.58485; `lan:/home/momo/rb-game/LOBBY_RESEARCH.md` |

**Bekannte offene Punkte für den In-Game-Test:** (1) exakte Spawner-Zahl der
Duell-Karte (Log `event=wave_spawners count=`); (2) Wirksamkeit des
Klassen-Monkey-Patch im echten Autoexec-Environment (Log `event=dom_timer
patch status=ok` = Indiz; Bestätigung über Wellenabstand/`debug_dom_manager 1`);
(3) exakte Feind-Team-Zuordnung bei `SpawnEntity` mit `""` (Blueprint-Standard
erwartet, s. #7); (4) macOS-Mod-Support ungeklärt.
(5) **Win-Condition (#28):** `EnteredTriggerEvent`-Feuerung und das
Trigger-Zone-Asset ums HQ sind nicht belegt (Repo-Recherche hat kein
Trigger-Event, s. api-deep-dive.md) — Handler ist pcall-gesichert registriert,
Log `event=hq_zone status=skip|pending|armed` zeigt den Zustand.
(6) **HQ-Entity-Identifikation (#28):** kein verifizierter Blueprint/Lookup —
Operator ordnet die Entity per `rb_hq entity <id>` zu; sonst greift nur der
HP≤0-Pfad (Leak) als Match-Ende.

## Technische Notizen

- **Statische Verifikation (2026-09-09, Pipeline):** `luaparse` (Lua-5.1-Syntax)
  OK; Ausführung in fengari-Lua-VM mit Stub-Services. v0.3.0: 4 Szenarien (16
  Rand-Spawner → 8 Spawns `anchor=spawn_enemy_border_*` ohne Spieler;
  Timer-Wrap 420→300 / Werte <300 bleiben; Fallback Mech-Ring; kein Anker).
  v0.4.0: 3 Szenarien / 30 Checks (Farm-Event-Ladder + Source-Lock;
  Convert irreversibel + Faktoren + Guards; Persistenz-Resume nach Neustart;
  Reset; Fallback tick nach 3 Handler-Fehlern). v0.5.0: 2 Szenarien / 22
  Checks (rb_mode Default/Wechsel/usage; rb_convert Calcium-first + Alias +
  Guards; rb_status Runde/Pool/Boost; Self-Boost-Hook: Original-OnEnterSpawn
  zuerst, Runden-Zähler, Pool greedy → Spawn, Rest-Pool). v0.6.0: keine
  Mod-Änderung (Tooling-Release, #7/#8). v0.8.0: keine Mod-Änderung
  (Tooling/Site-Release, #9/#56). v0.7.0: 5 Szenarien /
  14 Checks (Anker-Kette border→mission→mech; Missions-Fallback ohne Spieler
  spawnt 5/8 Kreaturen im Ring um den Spawnpunkt; Initial-Spawnpoint-Fallback;
  kein Anker → Skip). v0.9.0: 18 Checks / 7 Szenarien (Win-Condition:
Leak → HQ-HP-Senkung → Match-Ende bei HP ≤ 0; RespawnFailedEvent-Kette der
HQ-Entity; negative Fälle: andere Entity / ohne Entity-Zuordnung; Idempotenz
nach HQ-Tod). In-Game-Test steht aus (Operator, Prod).
- **Economy-Fallback dokumentiert:** Der Mod hat keinen verifizierten Zugriff
  aufs Spieler-Ressourcen-Konto (api-deep-dive.md §1); Value kommt aus
  Ernte-Events (Getter-Ladder). Sind die Events nicht lesbar, schaltet die
  Quelle nach 3 Fehlern dauerhaft auf HourEvent-Tick um (Log
  `event=economy_source source=tick status=fallback reason=handler_errors`).
- Balance-Zahlen (Faktoren, Tick-Wert) sind Platzhalter — zentrale Tabelle
  `RBB.economyCfg` am Economy-Block (Tuning: Issue #33).
- Alle fremden API-Aufrufe sind `pcall`-gesichert: fehlt eine Funktion, kommt
  ein Log statt eines Crashes.
- Blueprint-/Wellen-Definitionen stehen als Konstanten am Dateikopf
  (`RBB.waves`) — dort tunen, wenn der Test läuft.
- Herkunft: v0.2.0-single (feature/single-mod, PR #15); Bausteine 00/01 bleiben
  als Test-Komponenten erhalten.
- Weitere Doku: [`../docs/concept.md`](../docs/concept.md) ·
  [`../docs/findings.md`](../docs/findings.md) ·
  [`../docs/SEND_HOOK.md`](../docs/SEND_HOOK.md) ·
  [`../docs/DUEL_SETUP.md`](../docs/DUEL_SETUP.md) ·
  [`../docs/SYNC_START.md`](../docs/SYNC_START.md)
