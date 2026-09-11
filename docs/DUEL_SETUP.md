# DUEL_SETUP — Welt-Setup für den synchronen Runden-Takt (Issue #23)

Stand: 2026-09-09 · Ziel: **5-Min-Wellen, Schwierigkeit hard, Map large,
identischer Seed auf beiden Welten** (GDD „Setup“, Issue #23).

## Was der Mod leistet (rbbattle v0.3.0)

| Punkt | Umsetzung | Ort |
|---|---|---|
| prepareSpawnTime 420→300 s | `dom_mananger:GetPrepareSpawnTime` wird zur Laufzeit auf **max. 300 s** gedeckelt (Function-Wrap, pcall-gesichert, idempotent; Retry bei Mod-Load, `PlayerInitializedEvent` und jedem `rb_wave`/`rb_send`) | `mod/lua/rbbattle_autoexec.lua` |
| Verifikations-Log | `[RBBATTLE] event=dom_timer patch status=ok cap=300` + `event=setup difficulty=<name> creatures_difficulty=<n> timer_cap=300` | dito |
| Effektiv-Abstand | Zwischen zwei Naturwellen liegt zusätzlich `cooldownAfterAttacks` (Survival-rules 60–240 s je DOM-Level) + `idleTime` (hard: 0) + ggf. Streaming. **Beide Welten laufen identisch** (gleiche Rules/Seed), Fairness bleibt; exakt-300-s-Runden wären nur mit zusätzlichem Cooldown-Patch möglich (Folge-Tuning, bewusst nicht Teil von #23) | dom_manager v2 State-Machine |

## Was NICHT der Mod setzen kann (Beleg)

Difficulty, Kartengröße und Seed werden **vor der Kartengenerierung** gesetzt
und sind C++-seitig (Lobby/Dedicated-Server-Options):

- `*_autoexec.lua` läuft **bei Kartenerstellung** — die Welt-/Map-Generierung
  (`CreateWorld`/`GenerateMap`/`StartGame`) liegt davor und ist reines C++
  (`GameServerState::StartGame`, `GameServerOptions` mit Feldern wie
  `campaign_name`, `mission_name`, `difficulty`, `max_players` …).
  Kein Lua-Console-Command erzeugt eine Welt aus der Lobby
  (Beleg: `lan:/home/momo/rb-game/LOBBY_RESEARCH.md`, 2026-09-09).
- Difficulty-Rules-Auswahl beim Map-Start: `GetRulesForDifficulty()` hängt am
  `DifficultyService`-Postfix (`GetDomRulesScriptPostfix()` bzw.
  `GetCurrentDifficultyName()`); Rules-Dateien `dom_survival_*_rules_hard.lua`
  (prepareSpawnTime=420, Beleg lua-src) — gesetzt über die Server-/Lobby-Difficulty.
- Mod-seitig gibt es **keinen** Lua-Setter für Difficulty/Seed/Map-Size; die
  CVar-/Config-Namen (`difficulty_max_map_size` etc.) kommen aus dem
  Command-/CVar-Dump des laufenden Servers (Research 143 Commands/742 CVars;
  native C++-Registrierung `PlayerCheatSystem::InitConsoleCommands`).

→ Deshalb: **Server-Start-Konfiguration ist Operator-Aufgabe** (beide Welten
identisch); der Mod **loggt** die aktiv wirksame Difficulty zur Verifikation
(`event=setup difficulty=hard …`).

## Operator-Checkliste (beide Welten identisch!)

1. **Dedicated Server je Welt** starten (headless: `cli=1 config=config.cfg` — vollständige Startup-Config nötig, sonst hängt die App und bindet nie den UDP-Port, s. Issue #239;
   DedicatedServer.exe lädt `riftbreaker_dll_win_release.dll`).
2. **Gleiche Konfiguration** in beiden Servern (GUI „New game“ bzw.
   Server-Options/Config; eine **gemeinsame Config-Quelle** — z. B. dieselbe
   `server.cfg`/GameServerOptions-Datei mit festem Seed — damit kein
   Drift zwischen den Welten entsteht):
   - Spielmodus/Karte: identische `campaign_name`/`mission_name`/`mission_id`
   - Schwierigkeit: **hard** → wählt `dom_survival_<biome>_rules_hard.lua`
     (bzw. Kampagnen-Äquivalent `*_rules_hard`)
   - Map-Größe: **large** (Cap gemäß CVar-Dump: `difficulty_max_map_size`)
   - **Seed: ein fester Wert** (gleiche Zahl in beiden Welten → identische
     Karte, identische Spawner-/Ressourcen-Verteilung, identischer DOM-Takt)
3. **Mod rbbattle v0.3.0** in beiden Welten installiert
   (`<game>/mods/rbbattle/lua/rbbattle_autoexec.lua`).
4. **Live-Verifikation** (Operator, Prod):
   - Log beider Welten: `[RBBATTLE] event=dom_timer patch status=ok cap=300`
     und `event=setup difficulty=hard …`
   - `debug_dom_manager 1` → „Time left“ im `prepare_spawn`-State ≤ 300 s
   - Wellenstart-Zeitstempel beider Welten vergleichen (Log-Sync)
   - `dump_console_commands` + Config-Dump, um die tatsächlichen
     CVar-Namen für Seed/Map-Size zu bestätigen (Karte/Version abhängig)

## SP-Mode / Mod-Parität (Issue #44) — Client joint ohne Mod

Im SP-Mode läuft der Mod **nur auf dem Server**; der Client joint **ohne** Mod.
Da der Mod reine Lua ist (keine neuen Entities/Assets/UI, `mod/lua/`), muss ein
Vanilla-Client nichts rendern. Die Engine-Content-Prüfung (`ContentMismatch`,
„different set of mods“) verhindert den Join aber als Paritäts-Gate — Lösung aus
dem Research (Befund im Issue #44): die CVar
**`debug_disable_content_version_check 1`** serverseitig setzen.

**Server-Konfiguration (Operator-Aufgabe, wie Difficulty/Seed):**

```text
set debug_disable_content_version_check 1
```

- Als Console-Command nach Server-Start **oder** als Start-/Config-Argument des
  Dedicated Servers setzen (C++-CVar `cfg_debug_disable_content_version_check`,
  offiziell registriert — Beleg `console_commands.txt` Z. 269).
- **Nur der Server** braucht die Einstellung — der Client bleibt vanilla.
- Verifikation: Vanilla-Client joint den SP-Server ohne „different set of mods“.

Fallback (falls die CVar im Ziel-Build nicht greift): No-Op-Client-Mod installieren
(dieselbe Content-Liste, client-seitig wirkungslos) — erst im Live-Join-Test prüfen
(siehe Research-Empfehlung 2 im Issue). `external_content`-Mounting (Empfehlung 3)
bleibt als letzter Ausweg offen.

## Bekannte Grenzen / offene Punkte

- Exakte CVar-Namen für Seed & Map-Size sind **nicht statisch belegbar**
  (nur im C++/Dump); Live-Dump auf dem Ziel-Server nötig (Schritt 4).
- Effektiver Wellenabstand = 300 s + cooldownAfterAttacks (60–240 s je
  DOM-Level) → „5-Min-Wellen“ = Deckel der Vorbereitungsphase; ob zusätzlich
  der Cooldown gedeckelt wird, entscheidet der Balance-Test (Issue #33).
- `PlayerInitializedEvent`-Pfad setzt einen initialisierenden Spieler pro Welt
  voraus (Duell: gegeben); ohne Spieler bleibt der Timer ungepatcht (Log fehlt
  dann → Operator erkennt es sofort).

## Quellen
- `lan:/home/momo/rb-game/lua-src/`: `missions/v2/dom_manager.lua` (GetPrepareSpawnTime,
  cooldownAfterAttacks), `missions/survival/v2/dom_survival_*_rules_{default,hard,normal}.lua`,
  `utils/rules_utils.lua` (GetRulesForDifficulty)
- `lan:/home/momo/rb-game/LOBBY_RESEARCH.md` (C++-Startkette, GameServerOptions,
  cfg-Variablen, headless-Argumente)
- Repo: docs/GAME_DESIGN.md „Setup“, Issue #23
