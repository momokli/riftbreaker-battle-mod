# RIFT BATTLE — Game Design v1

1v1-Duell-Mod für The Riftbreaker (Biter-Battles-/Legion-TD-2-inspiriert). Zwei Spieler, zwei Welten, gleiche Karte, synchroner Wellen-Takt: Wer sein HQ verliert, verliert das Match.

## Status
- **Finalisiert** nach Design-Interview mit Momo (2026-09-09, 18 Entscheidungen in 6 Runden).
- Kern-Idee v0.2 ("natural waves + send boost") bestätigt und präzisiert.
- **Repo-Stand rbbattle v0.24.2**: Der Design-Kern ist implementiert — Abgleich unten im Abschnitt „Umsetzungsstand“; dort sind alle offenen Abweichungen explizit als `offen` markiert.

## Match-Flow
1. **Lobby** (Web-UI/Tournament-Server): Beide Spieler registrieren sich.
2. **Ready-Check**: Beide Welten (2 Dedicated Server) booten pausiert; gleicher Seed; erst wenn beide ready → GO.
3. **GO (synchron)**: Beide Welten starten gleichzeitig den Runden-Loop.
4. **Runden-Loop** bis **HQ-Tod**.
5. **Sieg-Screen** → **Rematch**.

## Setup
| Punkt | Wert |
|---|---|
| Format | 1v1 (2v2-ready: Shared Base + gemeinsamer Send-Pool) |
| Server | 2× Dedicated (je Welt ein Duellant) + Tournament-Server (Referee) + Web-UI |
| Karte | Gleicher Seed für beide Welten (Fairness), Größe "large" (Cap: `difficulty_max_map_size`) |
| Schwierigkeit | Preset `normal` (leichter als `hard`; Sends erhöhen den Druck) |
| Naturwellen | Preset-abhängig (#41): Variante A = 8 Min volle Größe, Variante B = 4 Min halbe Größe (`RBB.wavePresets`); Default folgt Testergebnis |
| Start | Beide Welten pausiert bis GO — DOM-Ebene: `debug_dom_pause` → GO = `debug_dom_resume` (verifiziert, docs/SYNC_START.md); Server-Ebene: `debug_pause_server`/`cfg_server_pause_game_when_empty` (nativ, Resume-Kommando per `dump_console_commands` offen) |

## Runden-Loop (der Herzschlag)
```
BUILD-PHASE (Preset-Takt: 8 Min / 4 Min, #41)
  ├─ Farmen (Erz) + Defensen aufbauen
  ├─ SENDEN: jederzeit bis Wellenstart, unbegrenzt oft
  │    Ressourcen → SEND-WÄHRUNG [IRREVERSIBEL = Commitment]
  │    Shop: Tiered Units + Bosse (Legion-TD-2-artig)
  │    Sends landen in der Queue → boosten die NÄCHSTE Naturwelle
  └─ Sparen erlaubt (Pool bleibt über Runden stehen)
WELLENSTART = LOCK + REVEAL
  ├─ Naturwelle + eingesendete Kreaturen spawnen an den
  │    16 natürlichen Kartenrand-Spawnern der Gegner-Welt
  └─ HUD deckt auf: Built-Value beider Teams + WAS kommt
LEAKS → Schaden am HQ
HQ-TOD → Match verloren
```

## Economy
- **Value-Quellen**: Alle gefarmten Ressourcen (Carbonium, Eisen, Seltene…).
- **Convert**: Bewusste, **irreversible** Umwandlung in Send-Währung ("ich schicke 100 Carbon"). Abbauen allein erzeugt noch keinen Send — nur die explizite Konvertierung.
- **Built-Value**: Team-Wert = was gebaut wurde (= was NICHT gesendet wurde). Wird bei Wellenstart aufgedeckt.

## Der Poker-Kern
1. **Commitment**: Convert ist endgültig — wer convertet, sendet wirklich.
2. **Blind bis Wellenstart**: Kein Team sieht den Send-Pool des Gegners vorher.
3. **Reveal bei Wellenstart**: HUD zeigt beide Built-Values + die konkrete eingehende Send-Komposition.
4. **Bluff**: Sparen ist erlaubt — Pool ansparen und in einer Runde alles geben.

## Reveal (bei Wellenstart)
- Built-Value pro Team (Legion-TD-artige Team-Anzeige).
- WAS kommt: Unit-Komposition der eingehenden Sends.

## Win-Condition
- Leaks (Kreaturen erreichen das HQ) schaden dem HQ.
- HQ-Tod = Match-Ende (nativ: `RespawnFailedEvent` → `ReportGameFailed`).
- Keine Comeback-Mechanik — HQ-HP ist der Balancer.
- Keine Extra-Boss-Runden: Bosse kommen mit den Naturwellen mit; Sends sind die Schippe drauf.
- Endlos: Kein Runden-Cap, die Skalierung entscheidet.

## HUD (In-Game, Custom-UI)
- Immer sichtbar: Rundennummer, Countdown bis Wellenstart, eigener Send-Pool.
- Bei Wellenstart (Reveal): Built-Value beider Teams, eingehende Send-Komposition, HQ-HP beider Teams.

## 2v2 (später)
- Beide Teammates teilen sich EINE Welt: gemeinsame Defense, gemeinsamer Send-Pool (Legion-TD-artig).

## Technik-Mapping (research-verifiziert 2026-09-09)
| Feature | Mechanik |
|---|---|
| Sync-Start | `debug_pause_server`/`debug_dom_pause`; GO = `debug_dom_resume` (DOM-Ebene, verifiziert) |
| Wellen-Takt | `prepareSpawnTime` (DOM-Rules) 420 → Preset-Intervall (A=480 s / B=240 s) |
| Schwierigkeit | Difficulty-Presets `rules_normal` (leichter als hard) + Wellen-Stärke-Skalierung |
| HQ | Entity `headquarters`, `HealthService`, `ReportHeadquaterDamage` |
| HQ-Tod | `RespawnFailedEvent` → `ReportGameFailed` + `ShowEndGameHud` |
| Leak-Detection | `EnteredTriggerEvent` (Trigger-Zone um HQ, Team-/BP-Filter) |
| Send-Spawn | 16 natürliche `spawner`-Entities an Kartenrändern |
| Shop-HUD | Custom-UI-Baustein (02), Command-Hook `rb_buy_wave` |
| Wellen-Routing | DOM-Wellenstart-Hook (Send-Queue mutiert nächste Welle) |
| State-Egress | `send_state` (Issue #13) → Tournament-Server |

## Balance & Tuning v1 (Issue #33)

Erste, zentral dokumentierte Balance-Werte — **alle brauchen Live-Test** (kein
Spieltest durch den Agenten; die Werte sind eine dokumentierte Annahme, keine
verifizierte Kurve).

### Preisliste v1 (Tiered Units + Bosse)

Zentrale Datenbasis: `RBB.shopCfg` in `mod/lua/rbbattle_autoexec.lua` (Preise in
Send-Währung = 1 Carbonium-Value, Faktor 1). Lesbar per `rb_balance`/`rb_shop`.

| Tier | Unit | Preis | Anmerkung |
|---|---|---|---|
| Tier 1 | brabit | 100 | billigster Basis-Send |
| Tier 1 | baxmoth | 150 | |
| Tier 2 | artigian | 200 | |
| Tier 3 | canceroth | 300 | |
| Boss | boss | 800 | 8× billigster Tier-1, Single-Slot |

Prinzip: monoton steigende Preise über die Tiers, Boss als teuerste Einheit.
Blueprints sind weiterhin Platzhalter (echte Unit-/Boss-Listen folgen separat);
die **Preis-Relationen** sind die hier festgelegte Balance-Größe. **braucht
Live-Test** (Test-Duell Momo vs. Matheo).

### HQ-HP-Kurve über Runden

Dokumentierte Formel (Konstante + Cap in `RBB.hqCfg`):

```
maxHp(r) = hqHpStart + hqHpPerRound * min(r-1, hqHpRoundCap)   (r >= 1)
         = 100       + 20           * min(r-1, 4)
```

| Runde | 1 | 2 | 3 | 4 | 5+ |
|---|---|---|---|---|---|
| maxHP | 100 | 120 | 140 | 160 | 180 |

Bei jedem Wellenstart (`OnNaturalWaveStart`) wird der HQ-HP auf diesen
Runden-Maxwert gesetzt (solange das HQ nicht zerstört ist). `hqHpStart` muss dem
Server-Default `TOURNAMENT_HQ_HP` entsprechen. **braucht Live-Test** — ob
20 HP/Runde das richtige Gefühl trifft und ob das Deckel bei Runde 4 passt, ist
offen.

### Send-Boost (Issue #39)

Zusätzlicher, reinerer Hebel neben der Shop-Composition (#25): der Send verstärkt
die **nächste Naturwelle prozentual** statt Kreaturen zu kaufen. Kauf per
`rb_boost <stufe|pct>` (irreversibel vom Spar-Pool abgezogen); beim nächsten
natürlichen Wellenstart wird der akkumulierte Boost auf die Welle angewendet
und zurückgesetzt — genau eine Welle, nicht kumulativ.

Zentrale Datenbasis: `RBB.boostCfg` in `mod/lua/rbbattle_autoexec.lua` (Preise in
Send-Währung = 1 Carbonium-Value, Faktor 1). Lesbar per `rb_balance`/`rb_status`.

| Stufe | Boost | Preis | Anmerkung |
|---|---|---|---|
| s1 | +25% | 200 | |
| s2 | +50% | 400 | |
| s3 | +100% | 800 | |
| freie pct-Eingabe | +N% | N × 8 | linearer Preis (`pricePerPct`) |

Caps: `maxBoostPct = 200` (kumulierte Summe je Welle), `maxBoostsPerWave = 4`
(Schutz vor Endlos-Spam, analog `shopCfg.maxQueueCreatures`).

**Technischer Hebel (verifiziert am lan-lua-src, Spiel 2.0.58485):** die
Naturwellen-Stärke ist diskret über `difficultyLevel` (1..9) indiziert —
`dom_mananger:SpawnWavesForDifficultyLevel(level, addToSpawned)` → `GetWavePool`
(`rules.waves[group][level]`) + `GetAttackCount`
(`rules.maxAttackCountPerDifficulty[level]`). Der prozentuale Aufschlag wird als
Level-Delta approximiert: `delta = ceil(level * pct/100)`, min. 1, gedeckelt auf
`maxDifficultyLevel`. Der Boost hängt am Chokepoint
`SpawnWavesForDifficultyLevel` (Naturwelle = `addToSpawned=true`; Debug-Trigger
`false` bleibt unangetastet). **braucht Live-Test** — Prozent→Level-Delta und
Stufen-Preise sind eine dokumentierte Annahme, keine verifizierte Kurve.

### Wellen-Takt & Grundschwierigkeit (Issue #41, Test-Varianten)

Der Wellen-Takt ist nicht mehr fest verdrahtet, sondern als explizite,
dokumentierte Konfigurations-Presets in `RBB.wavePresets` gefasst
(`mod/lua/rbbattle_autoexec.lua`). Zwei Varianten werden im Test-Duell
verglichen:

| Variante | Takt | Größe | `intervalS` | `strengthPct` |
|---|---|---|---|---|
| A | alle 8 Min | volle Größe | 480 | 100 |
| B | alle 4 Min | halbe Größe | 240 | 50 |

- `RBB.waveIntervalCapS` wird aus dem aktiven Preset abgeleitet (Patch
  `dom_mananger:GetPrepareSpawnTime`, `prepareSpawnTime` 420 → Preset-Intervall).
- Die Wellen-Stärke ist diskret über `difficultyLevel` indiziert (1..9);
  „halbe Größe“ wird als `floor(level * strengthPct/100)`, min. 1, approximiert
  am Chokepoint `dom_mananger:SpawnWavesForDifficultyLevel` (vor dem Send-Boost
  #39).
- Grundschwierigkeit: `baseDifficulty = "normal"` (leichter als der bisherige
  Default `hard`) — Sends erhöhen die Schwierigkeit zusätzlich, der Basis-Druck
  soll niedriger sein. Server-seitig gesetzt (docs/DUEL_SETUP.md).
- Umschaltung vor Map-Start über `RBB.wavePresets.active` (`"A"`|`"B"`).

**braucht Live-Test** — welcher Takt (selten+voll vs. häufig+halb) das richtige
Gefühl trifft, entscheidet der Test-Duell (Momo vs. Matheo, Feedback-Protokoll);
der Default folgt dem Testergebnis.

## Offene Tuning-Punkte (nach Interview, Stand nach #33-v1)
- ~~Preisliste (Tiered Units + Bosse)~~ → v1 dokumentiert (Tabelle oben) — **braucht Live-Test**.
- ~~Send-Boost (nächste Welle %-verstärken)~~ → v1 dokumentiert (Stufen/Caps oben) — **braucht Live-Test** (Prozent→Level-Delta ist Annahme).
- Naturwellen-Gefühl: "War Level 3 zu brutal?" (Live-Test 16:32: 8 Kreaturen, Momo gestorben) — **braucht Live-Test** (offen).
- ~~HQ-HP-Kurve über Runden~~ → v1 dokumentiert (Formel/Tabelle oben) — **braucht Live-Test**.

## Umsetzungsstand (Repo, rbbattle v0.24.2)
Abgleich des Design-Kerns gegen den implementierten Mod-/Server-/Site-Stand.
`✅` = implementiert · `⚠️ offen` = noch nicht im Repo umgesetzt.

| Design-Element | Stand | Issue | Beleg / Command |
|---|---|---|---|
| Sync-Start / GO (Ready-Check) | ✅ implementiert (DOM-Ebene; Server-Ebene offen) | #22 | `debug_dom_pause`/`debug_dom_resume`; docs/SYNC_START.md |
| Economy: Farm-Value | ✅ implementiert | #24 | `ResourceObtainedEvent`/`ResourceChangeEvent` (Getter-Ladder); Fallback HourEvent-Tick |
| Economy: Convert irreversibel | ✅ implementiert | #24/#40 | `rb_convert` (Calcium/carbonium **only**, Faktor 1); andere Ressourcen → `not_send_currency`; Pool persistiert (DB `rbbattle_economy`) |
| Built-Value (getrennt geführt) | ✅ implementiert | #24/#27 | Reveal-Basis |
| Send-Queue & Shop-HUD (Tiered Units + Boss) | ✅ implementiert | #25 | `rb_buy_wave`/`rb_shop`/`rb_queue`; Queue-Flush bei `dom_mananger:OnEnterSpawn` |
| Send-Boost (nächste Welle %-verstärken) | ✅ implementiert | #39 | `rb_boost <stufe|pct>`; Flush am `SpawnWavesForDifficultyLevel`-Chokepoint (`event=boost`) |
| Sends → Gegner-Welt (1v1-Routing) | ⚠️ offen | #25/#27/#29 | aktuell `rb_mode sp` (Self-Send an eigene Rand-Spawner); `duel` = Stub |
| Reveal-HUD (Built-Value + WAS kommt) | ✅ implementiert | #27 | `rb_hud`/`rb_reveal`/`rb_round_start`; `event=reveal`/`reveal_opp` |
| Win-Condition HQ-Tod (Logik) | ✅ implementiert | #28 | `rb_hq`; Leak → HQ-HP; `hq_dead`/`match_end` (Server-Buchung vorhanden) |
| Win-Condition (Trigger-Zone, HQ-Entity-ID, Sieg-Screen) | ⚠️ offen | #28 | `EnteredTriggerEvent`-Feuerung + Trigger-Zone-Asset unbelegt; HQ-Entity via `rb_hq entity <id>` |
| Live-Status (Landing) | ✅ implementiert (Website) | #30 | `site/live-status.js` + Landing-Widget + Dashboard-Link |
| Balancing (Preisliste v1, HQ-HP-Kurve) | 🟡 v1 dokumentiert (braucht Live-Test) | #33 | `rb_balance`/`rb_shop`; `RBB.shopCfg` + `RBB.hqCfg` (Formel + Cap) |
| Wellen-Takt + Grundschwierigkeit (Presets A/B) | 🟡 v1 dokumentiert (braucht Live-Test) | #41 | `RBB.wavePresets` (A=480 s voll, B=240 s halb); `rb_balance`; Skalierung am `SpawnWavesForDifficultyLevel`-Chokepoint |

Hinweis: Diese Doku hält den Design-Kern fest. Die Balancing-Zahlen v1
(Shop-Preise + HQ-HP-Kurve + Boost-Stufen) stehen oben im Abschnitt
„Balance & Tuning v1 (Issue #33)“; die übrigen Werte (Ressourcen-Faktoren)
bleiben den Tuning-Issues #40/#41 vorbehalten.
