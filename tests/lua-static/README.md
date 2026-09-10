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

1. Mod lädt, Version 0.28.2, HQ initialisiert (`hq_hp=100 hq_dead=false`).
2. Leak-Flow: `EnteredTriggerEvent` → `event=leak` → `event=hq_hp`.
3. Zone-/Team-Filter (#152): Leak nur für feindliche Kreaturen — Trigger ohne
   Entity (`no_trigger_entity`), eigene HQ-Entity (`own_hq`), eigener Mech
   oder eigene Team-Id 1 (`own_team`) werden übersprungen.
4. HQ-Tod durch Leaks (HP ≤ 0) → `event=hq_dead` + `event=match_end` (genau einmal).
5. Idempotenz: weiterer Leak nach Tod ändert nichts.
6. HQ-Tod-Kette: `RespawnFailedEvent` der getrackten HQ-Entity → `match_end`.
7. `RespawnFailedEvent` eines anderen Gebäudes → **kein** Match-Ende.
8. `RespawnFailedEvent` ohne zugeordnete Entity → nur Hinweis (`event=hq_respawn`).

`send-queue.test.js` deckt Issue #25 (Send-Queue & Shop-HUD) ab:

1. Mod lädt, Version 0.28.2; `rb_buy_wave`/`rb_shop`/`rb_queue` registriert,
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

`reveal-hud.test.js` deckt Issue #27 (Reveal-HUD / Poker) ab:

1. Mod lädt; `rb_reveal`/`rb_round_start`/`rb_hud` registriert.
2. Vor Wellenstart: `rb_hud` → `reveal=hidden`, Gegner-Built/incoming/HQ
   verborgen (`hidden`).
3. Farm → Convert → Kauf: Built-Value 3000, Queue bereit.
4. Bei Wellenstart: `event=reveal` lockt eigenen Built-Value + eigene
   Send-Komposition (`built_own=3000 send_own=units/ground/brabit:2`).
5. `rb_reveal` injiziert Gegner-Werte (`built_opp`/`hq_opp`/`incoming`).
6. `rb_hud` danach: Built beider Teams + WAS kommt + HQ beider Teams sichtbar.
7. `rb_round_start` verbirgt den Reveal wieder (`reveal=hidden`); zweiter
   Wellenstart lockt erneut.

`click-hud.test.js` deckt Issue #99 (Click-HUD / Senden per Klick) ab:

1. Mod lädt, Version 0.28.2; `rb_hud_ui`/`rb_quick` registriert,
   `GuiPopupResultEvent`-Handler aktiv.
2. `rb_quick` ohne Args → `status=usage` (Default `brabit` ×1).
3. `rb_quick brabit 2` → `status=armed`; `rb_quick unbekannt` → `unknown_unit`.
4. Farm → Convert → Pool 2000.
5. `rb_hud_ui` öffnet das Overlay (Template `popup_ingame_2buttons`)
   → `event=hud_ui status=opened`; erneuter Aufruf → `already_open`.
6. Klick „Ja“ (`button_yes`) → `BuyWave` kauft `brabit` ×2 in die Queue
   (`event=buy_wave … queue=2`) + `event=hud_ui status=closed action=quick_send`.
7. Klick „Nein“ (`button_no`) → schließt ohne Kauf (Queue unverändert).
8. Fremdes Popup (z. B. `rb_shop`, `open=false`) → Guard ignoriert den Klick
   (kein zusätzlicher Kauf).

`balance.test.js` deckt Issue #33 (Balance & Tuning v1) ab:

1. Mod lädt, Version 0.28.2 (kein Version-Bump); `rb_balance` registriert.
2. `rb_balance` legt die v1-Preisliste offen: 5 Units / 4 Tiers / 1 Boss mit
   den dokumentierten Preisen (brabit 100, baxmoth 150, artigian 200,
   canceroth 300, boss 800).
3. Preis-Invarianten (nicht trivial): eindeutige Unit-Ids, genau 1 Boss,
   positive Integer-Preise, strikt steigend t1 < t2 < t3 < boss.
4. HQ-HP-Kurve: Formel-Konstanten (start=100, per_round=20, cap=4) + Stichproben
   r1..r6 (100/120/140/160/180/180) — monoton nicht-fallend, gedeckelt.
5. Wellenstart setzt den HQ-HP auf den Runden-Maxwert (Runde 1→100, 2→120,
   3→140); die Kurve heilt zurücks aufs Runden-Max.

`boost.test.js` deckt Issue #39 (Send-Boost) ab:

1. Mod lädt; `rb_boost` registriert; Boost-Chokepoint-Hook aktiv
   (`event=boost patch status=ok`).
2. Guards: usage / unbekannte Stufe / pct ≤ 0 / zu wenig Pool.
3. Farm → `rb_convert` → Pool; `rb_boost s1` ×2 → `total_pct=50`, `buys=2`
   (Preis 200 je Stufe, Pool sofort deduziert).
4. Debug-Trigger (`SpawnWavesForDifficultyLevel(..., false)`) verbraucht den
   Boost **nicht**; Naturwelle (`addToSpawned=true`) wendet ihn an: 50% @ Level 2
   → `delta=1` → Level 3 (`event=boost status=flush`), danach zurückgesetzt.
5. Freie pct-Eingabe (`rb_boost 30` → linearer Preis 240); Caps (`maxBoostPct`=200,
   `maxBoostsPerWave`=4); `duel`-Modus: Boost-Flush nur in `sp`.

`send-currency.test.js` deckt Issue #40 (Send-Währung Calcium-only) ab:

1. Mod lädt (kein Version-Bump); `rb_convert`/`rb_economy` registriert.
2. `rb_convert <menge>` (ein Arg) konvertiert Calcium (`carbonium`) → Pool.
3. `rb_convert calcium <menge>` (Alias) ≡ `rb_convert carbonium <menge>`.
4. Nicht-Calcium (`steel`) wird abgelehnt (`not_send_currency`), auch wenn es
   gefarmt wurde — kein Convert-Vorgang, Pool unverändert.
5. Währung entscheidet VOR der Mengen-Prüfung: ungefarmtes `ironium` →
   `not_send_currency` (nicht `insufficient`).
6. Pool bleibt durch die Ablehnungen unangetastet (`economy_show`).

`wave-presets.test.js` deckt Issue #41 (Wellen-Takt + Grundschwierigkeit) ab:

1. Mod lädt (kein Version-Bump); `RBB.wavePresets` als Preset-Konfiguration
   vorhanden (`baseDifficulty="normal"`, `active="A"`).
2. Preset-Werte: A = 8 Min (480 s) volle Größe (strengthPct 100), B = 4 Min
   (240 s) halbe Größe (strengthPct 50); `RBB.waveIntervalCapS` = 480 (Preset A).
3. Point-of-Switch `RBB.wavePresets.active`: unbekannte ID fällt auf A zurück;
   aktives B liefert intervalS 240.
4. Wellen-Stärke-Skalierung (`ScaleWaveLevel`): `floor(level * pct/100)`, min. 1
   (100 % = unverändert, 50 % = halbiert).
5. Integration am `SpawnWavesForDifficultyLevel`-Chokepoint: Variante A lässt die
   Naturwelle unverändert (Level 4 → 4), Variante B halbiert (4 → 2, 3 → 1, 1 → 1);
   Debug-Trigger (`addToSpawned=false`) bleibt unangetastet.
6. `rb_balance` legt die Presets als Log-Fläche offen (`wave_preset`/`wave_preset_cfg`);
   das Setup-Log führt Preset + Grundschwierigkeit mit.

`persistence.test.js` deckt Issue #65 (Persistenz des Spar-Pools) ab:

1. Phase 1 (frischer Run): Farm 2000 carbonium → `rb_convert 1500` → Pool 1500;
   Wellenstart (`dom_mananger.OnEnterSpawn`) → `event=economy_checkpoint round=1
   pool=1500 status=ok` + Stub-DB enthält `pool=1500` (Checkpoint schreibt in die
   Global-DB).
2. Phase 2 (Reload): zweite frische Lua-VM mit vorbefüllter DB → `EconomyLoad`
   resumed (`status=resume pool=1500`) und `mod_load … econ_pool=1500`.

AC1 (In-Game-Bestätigung: echter Map-/Session-Reload) ist hier NICHT abbildbar —
kein Spiel-Zugriff; bleibt Operator-Lauf (Prod).

`commence.test.js` deckt Issue #158 (Setup-Phase / Commence-Flow) ab:

1. Mod lädt in der Setup-Phase (`commenced=false`) + Start-Announce
   "To commence the game, place the headquarter" als Log
   (`event=commence status=pending hint=place_hq`) und In-Game-Konsole.
2. Ohne HQ hält der Wellenstart (`dom_mananger.OnEnterSpawn`) AN: kein
   Original-Spawn, kein Runden-Zähler; `event=commence status=held reason=no_hq`
   genau einmal (Spam-Guard). Kein debug_dom_pause (Spiel läuft frei weiter).
3. HQ platziert → `FindEntitiesByGroup("headquarters")` erkennt es über den
   HourEvent-Tick (periodische Erkennung) → `event=commence status=ok` +
   Commence-Announce.
4. Nach Commence läuft der Wellenstart normal (Spawn + `round=1`).
5. Commence idempotent; manueller Fallback `rb_hq entity <id>` commencet ebenfalls.

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

Für #27 gilt analog: die Reveal-Logik (hidden → Lock bei Wellenstart →
Gegner-Injektion → `rb_hud`) ist statisch getestet; die Gegner-Werte kommen
zur Laufzeit von der Bridge (kein eigener I/O-Kanal des Mods) und das
1v1-Routing/der Reveal beider Teams liegt beim Tournament-Server — beides ist
nicht live-verifizierbar (Operator, Prod).

Für #99 gilt analog: die Klick-/Overlay-Logik (Toggle, `button_yes` → `BuyWave`,
`button_no` → schließen, Fremd-Popup-Guard) ist statisch getestet; das tatsächliche
Pop-up-Rendering des 2-Button-Templates (`popup_ingame_2buttons`) und die
`GuiPopupResultEvent`-Feuerung im Spiel sind live-verifizierbar (Operator, Prod).

Für #33 gilt analog: Preisliste und HQ-HP-Kurve sind reine Daten-/Formel-Logik und
statisch getestet; das **Gefühl** ("War Level 3 zu brutal?", richtige Preise,
richtige HP-Kurve) ist ausschließlich live verifizierbar (Operator, Test-Duell
Momo vs. Matheo) — alle Werte sind als "braucht Live-Test" markiert.

Für #39 gilt analog: der DOM-Hebel (Naturwellen-Stärke diskret über
`difficultyLevel` 1..9, indiziert `GetWavePool`/`GetAttackCount`) ist am
lan-lua-src (Spiel 2.0.58485) **verifiziert**; die Boost-Zahlen (Stufen-Preise,
Caps, Prozent→Level-Delta `ceil(level*pct/100)`) sind dokumentierte Annahmen
und brauchen Live-Test — die tatsächliche Wrap-Wirksamkeit am
`SpawnWavesForDifficultyLevel`-Chokepoint ist live verifizierbar (Operator).

Für #41 gilt analog: die Preset-Konfiguration (Takt + Grundschwierigkeit) und
die Wellen-Stärke-Skalierung (`floor(level * strengthPct/100)`, min. 1) sind
reine Daten-/Formel-Logik und statisch getestet; welcher Takt das richtige
**Gefühl** trifft (selten+voll vs. häufig+halb) ist ausschließlich live
verifizierbar (Operator, Test-Duell Momo vs. Matheo) — alle Werte sind als
„braucht Live-Test“ markiert. `baseDifficulty="normal"` ist eine Server-seitige
Einstellung (docs/DUEL_SETUP.md), die der Mod nicht selbst setzt, sondern nur
dokumentiert/loggt.

Für #158 gilt analog: die Setup-Phase-/Commence-Logik (pending → held → ok,
Gate des Wellenstarts an `OnEnterSpawn`, periodische HQ-Erkennung über den
`HourEvent`-Tick, idempotenter Commence) ist statisch getestet; die tatsächliche
Wrap-Wirksamkeit am `OnEnterSpawn` im DOM-State-Machine-Kontext (ob der
prepare→spawn-Zyklus beim Halten sauber weiterläuft) und die echte
HQ-Platzierung über das Build-Menü (`FindEntitiesByGroup("headquarters")`)
sind live-verifizierbar (Operator, E2E auf :6321).
