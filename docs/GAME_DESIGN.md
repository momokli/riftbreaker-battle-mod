# RIFT BATTLE — Game Design v1

1v1-Duell-Mod für The Riftbreaker (Biter-Battles-/Legion-TD-2-inspiriert). Zwei Spieler, zwei Welten, gleiche Karte, synchroner Wellen-Takt: Wer sein HQ verliert, verliert das Match.

## Status
- **Finalisiert** nach Design-Interview mit Momo (2026-09-09, 18 Entscheidungen in 6 Runden).
- Kern-Idee v0.2 ("natural waves + send boost") bestätigt und präzisiert.

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
| Start | Beide Welten pausiert bis GO (Pause: `debug_pause_server`/`debug_dom_pause`, Fallback-Unpause: ResumeGame bei Client-Join) |

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
| Sync-Start | `debug_pause_server`/`debug_dom_pause`; Fallback ResumeGame bei Join |
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
