# RBBattle Einzel-Mod — Installation & Test (Stand 09.09.2026)

Einzel-Mod **rbbattle** v0.4.0 für den Runden-Duell-Modus („Biter
Battles“-artig, RIFT BATTLE) in *The Riftbreaker*. Nachfolger von
v0.2.0-single (Fusion Baustein 00 + 01). Kein Workshop-Release, keine Garantie.

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
Kartenrand-Spawnern) + DOM-Timer-Deckel + Economy (`rb_convert`, `rb_economy`).
Kein UI, keine Bindings, kein Bridge-Zusatz, **kein io/socket/http**.

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
| `rb_convert <resource> <amount>` | Wandelt gefarmte Ressource **irreversibel** in Send-Währung (Spar-Pool): `rb_convert carbonium 100` → 100 Value → Pool (Faktor-Tabelle `resourceFactors`, z.B. palladium 2×, uranium_ore 3×; unbekannte Ressourcen 1×). Ablehnung bei zu wenig Farm-Menge (`status=insufficient`); kein Rücktausch. Balance = Platzhalter (Tuning #33) |
| `rb_economy` / `rb_economy reset` | Status: Quelle, Pool, farmed/converted/built, Ressourcen-Konten, DB-Status. `reset` = Entwickler-Werkzeug (alles auf 0, inkl. Ressourcen-Keys der DB) |

Erwartete Log-Zeilen in `exor_logs.txt` bei Kartenerstellung:

```
[RBBATTLE] skeleton ok
[RBBATTLE] event=mod_load version=0.4.0 status=ok anchor=border_spawner_groups timer_cap=300 econ_source=none econ_pool=0
[RBBATTLE] event=economy_db status=new db=rbbattle_economy      ← erste Runde
[RBBATTLE] event=economy_source source=resource_obtained status=active   ← erste lesbare Ernte
[RBBATTLE] event=economy_farm source=resource_obtained resource=carbonium amount=100 value=100 farmed=100 built=100
[RBBATTLE] event=convert resource=carbonium amount=100 value=100 pool=100 status=ok irreversible=1
[RBBATTLE] event=dom_timer patch status=ok cap=300        ← nach PlayerInitializedEvent
[RBBATTLE] event=setup difficulty=hard creatures_difficulty=5 timer_cap=300
[RBBATTLE] event=wave level=3 status=start
[RBBATTLE] event=wave_spawners count=16 groups=4          ← Pool der Rand-Spawner
[RBBATTLE] event=spawn ok blueprint=units/ground/baxmoth entity=12345 anchor=spawn_enemy_border_west/...
[RBBATTLE] event=wave level=3 status=done spawned=8 skipped=0 anchor=border spawners=16
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

## Technische Notizen

- **Statische Verifikation (2026-09-09, Pipeline):** `luaparse` (Lua-5.1-Syntax)
  OK; Ausführung in fengari-Lua-VM mit Stub-Services. v0.3.0: 4 Szenarien (16
  Rand-Spawner → 8 Spawns `anchor=spawn_enemy_border_*` ohne Spieler;
  Timer-Wrap 420→300 / Werte <300 bleiben; Fallback Mech-Ring; kein Anker).
  v0.4.0: 3 Szenarien / 30 Checks (Farm-Event-Ladder + Source-Lock;
  Convert irreversibel + Faktoren + Guards; Persistenz-Resume nach Neustart;
  Reset; Fallback tick nach 3 Handler-Fehlern). In-Game-Test steht aus
  (Operator, Prod).
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
