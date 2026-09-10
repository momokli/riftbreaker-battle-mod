# Lua Static Tests (fengari + luaparse)

Statische Verifikation des Lua-Mods **ohne Spielausführung** — die einzige
CI-fähige Testebene für `mod/lua/rbbattle_autoexec.lua`, da der Mod keinen
eigenen I/O-Kanal hat und In-Game-Läufe nur der Operator auf den Dedi-Instanzen
macht (siehe `mod/README.md` → „Technische Notizen“).

- **`luaparse`** prüft die Lua-5.1-Grammatik (Spiel-VM = Lua 5.1).
- **`fengari`** (Lua-VM in JS) führt den Mod mit **Stub-Services** aus und
  assertiert auf die `[RBBATTLE]`-Log-Zeilen — die Vertragsfläche für
  Bridge/Server (siehe `docs/TOURNAMENT_API.md`).

## Aufruf

```bash
cd tests/lua-static
npm install     # einmalig
npm test        # = node --test
```

## Abgedeckte Szenarien

`win-condition.test.js` deckt Issue #28 (Win-Condition) ab:

1. Mod lädt, Version 0.12.0, HQ initialisiert (`hq_hp=100 hq_dead=false`).
2. Leak-Flow: `EnteredTriggerEvent` → `event=leak` → `event=hq_hp`.
3. HQ-Tod durch Leaks (HP ≤ 0) → `event=hq_dead` + `event=match_end` (genau einmal).
4. Idempotenz: weiterer Leak nach Tod ändert nichts.
5. HQ-Tod-Kette: `RespawnFailedEvent` der getrackten HQ-Entity → `match_end`.
6. `RespawnFailedEvent` eines anderen Gebäudes → **kein** Match-Ende.
7. `RespawnFailedEvent` ohne zugeordnete Entity → nur Hinweis (`event=hq_respawn`).

`send-queue.test.js` deckt Issue #25 (Send-Queue & Shop-HUD) ab:

1. Mod lädt, Version 0.12.0; `rb_buy_wave`/`rb_shop`/`rb_queue` registriert,
   Wellenstart-Hook aktiv (`event=wave_hook patch status=ok`).
2. `rb_shop` listet 4 Tiers (inkl. Boss) + öffnet das Custom-UI-Popup
   (Template `popup_template_1button`).
3. `rb_buy_wave`-Guards: usage / unbekannte Unit / zu wenig Pool.
4. Farm → `rb_convert` → Spar-Pool; Kauf Tier-1 (`brabit` ×2) + Boss.
5. `rb_queue`-Status (Anzahl/Wert/Pool).
6. Wellenstart (`dom_mananger.OnEnterSpawn`) → Original zuerst (Naturwelle
   unangetastet), dann `event=send_queue status=done` (Boost der nächsten
   Welle) — Queue danach leer, Pool unangetastet.
7. Erneuter Kauf + zweiter Wellenstart (Senden jederzeit, beliebig oft).

## Grenzen (ehrlich dokumentiert)

Die Stub-Services ersetzen die Spiel-Engine; **nicht** live-verifizierbar sind
daher: die tatsächliche Feuerung von `EnteredTriggerEvent`/`RespawnFailedEvent`
im Spiel, das Trigger-Zone-Asset ums HQ und die HQ-Entity-Identifikation. Diese
Punkte sind im Mod-Kopf (`mod/lua/rbbattle_autoexec.lua`) und in `mod/README.md`
als OFFEN markiert. Die reine Win-Condition-Logik (Leak → HP → Match-Ende) ist
vollständig statisch getestet.

Für #25 gilt analog: die Shop-/Queue-Logik (Preisliste, Kauf-Guards, Queue-Flush
am Wellenstart) ist statisch getestet; die tatsächliche `OnEnterSpawn`-Wrap-
Wirksamkeit im Autoexec-Environment und das Shop-Popup-Rendering sind
live-verifizierbar (Operator, Prod) — die Boss-/Unit-Blueprints sind bewusst
Platzhalter (Balance-Session #33/#12).
