# SYNC_START — Pause/Unpause-Befund für Ready-Check & GO (Issue #22)

Stand: 2026-09-09 · **Vorbereitungs-Doku — kein Live-Test** (Live-Test der
Unpause-Mechanik macht der Operator separat auf Prod; diese Pipeline hat kein
laufendes Game).

Ziel (Issue #22): Beide Welten booten pausiert; erst wenn beide Spieler
„ready“ sind, feuert der Tournament-Server **GO** synchron an beide Bridges
(Transport: `exec_cmd_client "<command>"` — Quoting beachten, Issue #18).

## Pause-Ebenen (3 getrennte Mechanismen!)

| Ebene | Mittel | Beleg | Wirkung |
|---|---|---|---|
| 1. Server-Pause (ganze Simulation) | natives Command `debug_pause_server`; Config `cfg_server_pause_game_when_empty` (im Research auch als „server_pause_game_when_empty“ ohne cfg_-Präfix notiert) | C++-seitig: `PlayerCheatSystem::InitConsoleCommands` (942 native Commands), Config-Variablen aus `globals_dll.txt` (`cfg_server_pause_game_when_empty`) — `lan:/home/momo/rb-game/LOBBY_RESEARCH.md`; **kein Lua-Beleg** (im lua-src nicht vorhanden) | pausiert die Server-Simulation („Server pausiert, wenn leer“) |
| 2. DOM-Pause (nur Wellen-Manager) | Lua-Command `debug_dom_pause` → `QueueEvent("LuaGlobalEvent", event_sink, "PauseDOM", {})` → `dom_mananger:PauseDOM()` → `SetSuspended(true)` + `CampaignService:OperateDOMPlanetaryJump(true)` | **statisch verifiziert** `lua/commands/debug.lua:73` + `lua/missions/v2/dom_manager.lua:787` (Spiel 2.0.58485) | DOM-State-Machine eingefroren (keine Naturwellen-Timer), Spieler können weiter bauen |
| 3. DOM-Resume | Lua-Command `debug_dom_resume` → LuaGlobalEvent `ResumeDOM` → `dom_mananger:ResumeDOM()` → `SetSuspended(false)` | **statisch verifiziert** `lua/commands/debug.lua:77` + `dom_manager.lua:793` | DOM läuft weiter |

## WICHTIGE Korrektur zur bisherigen Annahme

> „KEIN Unpause-Command bekannt“ — **widerlegt für die DOM-Ebene**:
> `debug_dom_resume` existiert (debug.lua:77, verifiziert 2026-09-09 gegen
> lua-src 2.0.58485). Damit ist der GO-Pfad **ohne** Client-Join-Fallback
> möglich: `debug_dom_resume` auf beiden Welten.
> Nur die **Server-Ebene** (debug_pause_server/cfg_server_pause_game_when_empty)
> bleibt C++-seitig ohne Lua-Beleg — dort gilt weiterhin der Fallback
> „ResumeGame bei Client-Join“ (String `resume_game`/`resume_game`-Action ist im
> DLL-String-Dump belegt, Kontext GUI-Action **oder** natives Command; Klärung
> per `dump_console_commands` auf dem Ziel-Server).

## Empfohlener Ablauf (Design GO, Live-Test durch Operator)

1. **Boot pausiert**: Beide Welten mit `cfg_server_pause_game_when_empty 1`
   starten (Server pausiert ohne Clients) — zusätzlich/alternativ
   `debug_pause_server` nach Start. DOM-Ebene: `debug_dom_pause`.
2. **Join**: Spieler verbinden sich (Welt bleibt pausiert; wenn die
   Server-Pause bei Join automatisch aufhebt → danach sofort `debug_dom_pause`
   setzen, falls DOM laufen soll — Reihenfolge im Live-Test klären).
3. **Ready**: beide Clients melden „ready“ an den Tournament-Server (Web-UI).
4. **GO (synchron)**: Tournament-Server feuert an **beide** Bridges:
   - `exec_cmd_client "debug_dom_resume"` (DOM-Ebene, verifiziert vorhanden)
   - falls Server-Pause aktiv war: natives Resume (Name via
     `dump_console_commands` bestätigen; Kandidaten aus Research:
     `resume_game`) bzw. Fallback Client-Join.
5. **Nachweis**: beide Welten loggen ab GO den DOM-Takt
   (`[RBBATTLE] event=dom_timer patch status=ok cap=300` in rbbattle v0.3.0;
   Wellenstart-Zeitstempel im Log vergleichen → Sync-Beweis).

## Verifikations-Befehle (live, Prod)

| Zweck | Befehl | Beleg |
|---|---|---|
| Alle nativen Commands listen | `dump_console_commands` | LOBBY_RESEARCH §4 (DLL-String Console.cpp) |
| DOM-Pause setzen | `debug_dom_pause` | debug.lua:73 (Lua, verifiziert) |
| DOM-Pause aufheben | `debug_dom_resume` | debug.lua:77 (Lua, verifiziert) |
| Beliebigen LuaGlobalEvent-Namen feuern (Test) | `debug_send_lua_event <name>` | debug.lua:31 |
| DOM-State + Timer anzeigen | `debug_dom_manager 1` | debug.lua:47; dom_manager.lua:228 („Time left“ je State) |
| Server-Pause (nativ) | `debug_pause_server` | LOBBY_RESEARCH §2 (native Lambdas) |
| Config setzen | `set cfg_server_pause_game_when_empty 1` | LOBBY_RESEARCH §3 (cfg_-Variablen) |

## Risiken / offene Punkte

1. **Reihenfolge-Effekt**: Server-Pause (Ebene 1) und DOM-Pause (Ebene 2) sind
   unabhängig — GO muss beide korrekt abräumen; Live-Test der Kombination nötig.
2. `debug_dom_resume` wirkt nur, wenn die DOM-Klasse/Instanz der Welt läuft
   (Map geladen). Pre-Map-Ausführung ist wirkungslos (Event wird nirgends
   empfangen) — GO erst nach „Welt geladen“-Signal der Mods feuern.
3. `SetSuspended(true)` friert nur den DOM-Graph-Knoten ein — Naturwellen-Timer
   stehen, **Spieler-Aktionen nicht**. Falls volle Welt-Pause (inkl. Bauen) vor
   GO gewünscht ist → Ebene 1 (Server-Pause) nutzen; Verhalten live prüfen.
4. Transport über Bridge erfordert gequotete Argumente (`exec_cmd_client
   "debug_dom_resume"` als EIN String; Issue #18).
5. Ob `resume_game` ein natives Console-Command oder GUI-Action-String ist,
   bleibt bis zum `dump_console_commands`-Live-Test offen.

## Quellen
- `lan:/home/momo/rb-game/lua-src/lua/commands/debug.lua` (Z. 31/47/73/77)
- `lan:/home/momo/rb-game/lua-src/lua/missions/v2/dom_manager.lua`
  (OnLuaGlobalEvent Z. 524 ff., PauseDOM Z. 787, ResumeDOM Z. 793,
  DumpDomData Z. ~640, Debug-Overlay Z. 228)
- `lan:/home/momo/rb-game/LOBBY_RESEARCH.md` (2026-09-09): native Commands,
  cfg_-Variablen, DLL-Strings, RCON/Transport
- Repo: Issue #22/#18, docs/GAME_DESIGN.md „Setup/Start“, docs/DUEL_SETUP.md
