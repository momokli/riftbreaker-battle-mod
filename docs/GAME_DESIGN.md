# RIFT BATTLE — Game Design v1

1v1-Duell-Mod für The Riftbreaker (Biter-Battles-/Legion-TD-2-inspiriert). Zwei Spieler, zwei Welten, gleiche Karte, synchroner Wellen-Takt: Wer sein HQ verliert, verliert das Match.

## Status
- **Finalisiert** nach Design-Interview mit Momo (2026-09-09, 18 Entscheidungen in 6 Runden).
- Kern-Idee v0.2 ("natural waves + send boost") bestätigt und präzisiert.
- **Repo-Stand rbbattle v0.18.0**: Der Design-Kern ist implementiert — Abgleich unten im Abschnitt „Umsetzungsstand“; dort sind alle offenen Abweichungen explizit als `offen` markiert.

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
| Schwierigkeit | Preset `hard` |
| Naturwellen | Alle 5 Minuten (Patch `prepareSpawnTime` 420→300 s), endlos skalierend |
| Start | Beide Welten pausiert bis GO — DOM-Ebene: `debug_dom_pause` → GO = `debug_dom_resume` (verifiziert, docs/SYNC_START.md); Server-Ebene: `debug_pause_server`/`cfg_server_pause_game_when_empty` (nativ, Resume-Kommando per `dump_console_commands` offen) |

## Runden-Loop (der Herzschlag)
```
BUILD-PHASE (5 Min)
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
| 5-Min-Takt | `prepareSpawnTime` (DOM-Rules) 420→300 s |
| Schwierigkeit | Difficulty-Presets `rules_hard` |
| HQ | Entity `headquarters`, `HealthService`, `ReportHeadquaterDamage` |
| HQ-Tod | `RespawnFailedEvent` → `ReportGameFailed` + `ShowEndGameHud` |
| Leak-Detection | `EnteredTriggerEvent` (Trigger-Zone um HQ, Team-/BP-Filter) |
| Send-Spawn | 16 natürliche `spawner`-Entities an Kartenrändern |
| Shop-HUD | Custom-UI-Baustein (02), Command-Hook `rb_buy_wave` |
| Wellen-Routing | DOM-Wellenstart-Hook (Send-Queue mutiert nächste Welle) |
| State-Egress | `send_state` (Issue #13) → Tournament-Server |

## Offene Tuning-Punkte (nach Interview)
- Preisliste (Tiered Units + Bosse) — braucht erste Test-Runde mit Momo+Matheo.
- Naturwellen-Gefühl: "War Level 3 zu brutal?" (Live-Test 16:32: 8 Kreaturen, Momo gestorben).
- HQ-HP-Kurve über Runden.

## Umsetzungsstand (Repo, rbbattle v0.18.0)
Abgleich des Design-Kerns gegen den implementierten Mod-/Server-/Site-Stand.
`✅` = implementiert · `⚠️ offen` = noch nicht im Repo umgesetzt.

| Design-Element | Stand | Issue | Beleg / Command |
|---|---|---|---|
| Sync-Start / GO (Ready-Check) | ✅ implementiert (DOM-Ebene; Server-Ebene offen) | #22 | `debug_dom_pause`/`debug_dom_resume`; docs/SYNC_START.md |
| Economy: Farm-Value | ✅ implementiert | #24 | `ResourceObtainedEvent`/`ResourceChangeEvent` (Getter-Ladder); Fallback HourEvent-Tick |
| Economy: Convert irreversibel | ✅ implementiert | #24/#40 | `rb_convert` (Calcium/carbonium first, Faktor 1); Pool persistiert (DB `rbbattle_economy`) |
| Built-Value (getrennt geführt) | ✅ implementiert | #24/#27 | Reveal-Basis |
| Send-Queue & Shop-HUD (Tiered Units + Boss) | ✅ implementiert | #25 | `rb_buy_wave`/`rb_shop`/`rb_queue`; Queue-Flush bei `dom_mananger:OnEnterSpawn` |
| Sends → Gegner-Welt (1v1-Routing) | ⚠️ offen | #25/#27/#29 | aktuell `rb_mode sp` (Self-Send an eigene Rand-Spawner); `duel` = Stub |
| Reveal-HUD (Built-Value + WAS kommt) | ✅ implementiert | #27 | `rb_hud`/`rb_reveal`/`rb_round_start`; `event=reveal`/`reveal_opp` |
| Win-Condition HQ-Tod (Logik) | ✅ implementiert | #28 | `rb_hq`; Leak → HQ-HP; `hq_dead`/`match_end` (Server-Buchung vorhanden) |
| Win-Condition (Trigger-Zone, HQ-Entity-ID, Sieg-Screen) | ⚠️ offen | #28 | `EnteredTriggerEvent`-Feuerung + Trigger-Zone-Asset unbelegt; HQ-Entity via `rb_hq entity <id>` |
| Live-Status (Landing) | ✅ implementiert (Website) | #30 | `site/live-status.js` + Landing-Widget + Dashboard-Link |
| Balancing (Preisliste, Faktoren, HQ-HP-Kurve) | ⚠️ offen | #33/#39/#40/#41 | Platzhalter — bewusst **nicht** in dieser Doku gesetzt |

Hinweis: Diese Doku hält den Design-Kern fest und spiegelt **keine** Balancing-Zahlen.
Die konkreten Werte (Shop-Preise, Ressourcen-Faktoren, HQ-HP-Kurve, Wellen-Stärke-Boost)
bleiben den Tuning-Issues #33/#39/#40/#41 vorbehalten.
