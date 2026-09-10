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

1. Mod lädt, Version 0.10.0, HQ initialisiert (`hq_hp=100 hq_dead=false`).
2. Leak-Flow: `EnteredTriggerEvent` → `event=leak` → `event=hq_hp`.
3. HQ-Tod durch Leaks (HP ≤ 0) → `event=hq_dead` + `event=match_end` (genau einmal).
4. Idempotenz: weiterer Leak nach Tod ändert nichts.
5. HQ-Tod-Kette: `RespawnFailedEvent` der getrackten HQ-Entity → `match_end`.
6. `RespawnFailedEvent` eines anderen Gebäudes → **kein** Match-Ende.
7. `RespawnFailedEvent` ohne zugeordnete Entity → nur Hinweis (`event=hq_respawn`).

## Grenzen (ehrlich dokumentiert)

Die Stub-Services ersetzen die Spiel-Engine; **nicht** live-verifizierbar sind
daher: die tatsächliche Feuerung von `EnteredTriggerEvent`/`RespawnFailedEvent`
im Spiel, das Trigger-Zone-Asset ums HQ und die HQ-Entity-Identifikation. Diese
Punkte sind im Mod-Kopf (`mod/lua/rbbattle_autoexec.lua`) und in `mod/README.md`
als OFFEN markiert. Die reine Win-Condition-Logik (Leak → HP → Match-Ende) ist
vollständig statisch getestet.
