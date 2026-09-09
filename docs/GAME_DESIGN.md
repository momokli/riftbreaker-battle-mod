# Game Design v1

Design-Kern für den Runden-Duell-Modus von *The Rift Breaker* — „Biter Battles × Legion TD 2“.
Festgehalten am 2026-09-09 (Entscheid von Momo, Issue #20). Wo dieses Dokument der älteren
Konzept-Skizze (`docs/concept.md`) widerspricht, gilt der Stand hier.

## Kernprinzipien

### 1. Spawns: zufällig an 1–N Kartenrändern

Wellen spawnen nicht mehr am Player-Mech, sondern zufällig an 1–N Kartenrändern der Map.
Dafür hält die Map 16 Spawner-Entities vor (Boot-Log „* spawner(16)“, Befund über das
Kommando `dump_map_layout`).

*Begründung:* Wellen sind nicht mehr an den Spieler gebunden (Server-only-Betrieb ohne
Mech-Anker), die vorhandenen Spawner-Entities sind der dafür vorgesehene Anker. Zufällige
Ränder machen jede Welle unvorhersehbar und zwingen zum Verteidigen in alle Richtungen.

### 2. Natürliche Wellen (Base Pressure) + Sends von außen

Natürliche Wellen (DOM/Survival) bleiben als Base Pressure aktiv — Sends kommen von außen
ZUSÄTZLICH dazu.

*Begründung:* Die natürlichen Wellen halten die Partie ohne Zutun in Bewegung und geben
dem Verteidiger eine planbare Grundlast; gegnerische Sends entscheiden das Duell, weil
sie zusätzlich zur Naturwelle kommen.

### 3. Send-Timing: Send-Boost auf die nächste Welle

Punkte sammeln sich passiv an (Biter-Battles-Stil). Sends boosten die NÄCHSTE natürliche
Welle (Mod hält Send-Queue, angehängt am Wellenstart — Legion-TD-Style) → dadurch immer
eine Build-Phase.

*Begründung:* Angehängte Sends statt Instant-Wirkung geben dem Verteidiger zwischen den
Wellen eine feste Build-Phase und machen Send-Timing zur Entscheidung (jetzt senden oder
für die nächste Welle sparen) — ohne tote Wartezeit durch Reisezeiten.

### 4. Send-Inhalt: VALUE-SHOP

Items/Erze werden zu Credits gutgeschrieben; aus Credits werden Einheiten gekauft —
Preistabelle je Unit, Kauf über das Kommando `rb_buy_wave`.

*Begründung:* Der Shop übersetzt den passiven Punktestand in bewusste Entscheidungen
(eine teure Elite-Welle oder mehrere billige?) und ist damit das Steuerungsinstrument des
Duells.

### 5. Inspiration: Legion TD 2

Ziel ist das Build-/Defense-Duell-Gefühl von Legion TD 2: Beide Seiten bauen ihre
Verteidigung aus und versuchen, die des Gegners mit besser getimten und komponierten Sends
zu überwinden.

*Begründung:* Legion TD 2 belegt, dass symmetrische Build-Duelle ohne direkte
Spieler-Interaktion spannend und fair sind — genau die Form, die sich auf zwei getrennten
Dedicated Servern abbilden lässt.

### 6. Win-Condition: LEAKS AUF HQ

Kreaturen, die die Verteidigung durchbrechen (Leaks), schaden dem HQ. Wer zuerst bei 0 ist,
verliert.

*Begründung:* Ein einzelner, klarer Lebens-Pool (HQ) macht Verteidigungsqualität direkt
messbar und gibt dem Duell ein eindeutiges Ziel, ohne dass eine Basis physisch zerstört
werden muss.

## Architektur

Verifizierte Gesamtstruktur (Vollkette am 2026-09-09 auf Prod bewiesen):

- **2× Dedicated Server** — jeder Duellant spielt auf einer eigenen Server-Instanz mit
  eigener Map und eigener Economy. `pause_when_empty=0` erlaubt Server-only-Betrieb ohne
  Mech-Anker.
- **rbbridge** — koppelt jede Server-Instanz nach außen: empfangene Sends/Befehle werden
  im Spiel ausgeführt, Spielzustand wird exportiert.
- **Tournament-Server** — zentrale Instanz für Lobby, Runden, Wave-Routing und Punkte;
  leitet Sends eines Duellanten als Welle auf die Instanz des Gegners und verwaltet den
  Spielstand (HQ-Leben, Sieg/Niederlage).
- **Client-Rolle** — der Spieler-Client wird nur zum aktiven Spielen benötigt. Für
  Server-Tests genügt der Fake-Client (`debug_spawn_fake_client`); die Headless-Client-
  Umgebung ist damit für Server-Tests nicht mehr erforderlich.

## Runden-Struktur

1. **Build-Phase** — Verteidigung bauen/upgraden, Sends entscheiden.
2. **Naturwelle + Send-Boost** — die natürliche Welle startet, die in der Send-Queue
gesammelten gegnerischen Sends hängen an.
3. **Aufräumen** — Leaks abrechnen (HQ-Schaden), Economy gutschreiben.
4. **Nächste Runde** — zurück zu Schritt 1.

## Offene Fragen

- Runden synchron (Tournament-Timer) oder freilaufend?
- Build-Phase-Länge: DOM-Kadenz oder eigener Timer?
- Level-/Tuning-Werte: HQ-Leben, Schaden pro Leak, Wellen-Größen und Schwierigkeitsstufen
- Preistabelle je Unit: Kosten in Credits, Freischaltung, Angebotsumfang
- Runden-Länge und Phasen-Taktung (Planung vs. Angriff)
- Economy-Rate: passiver Punkte-Zufluss pro Zeiteinheit
- Spawn-Verteilung: exakte Bedeutung von „1–N Kartenrändern“ (Anzahl, Gewichtung, Ränder)

## Status

- **2026-09-09:** Design-Kern v1 entschieden und dokumentiert (Issue #20).
- **Verifiziert:** Vollkette auf Prod bewiesen — Bridge → Konsole → Lua-Mod → Spawn
  (`event=wave level=3 status=done spawned=8`), Fake-Client statt echtem Client,
  Auto-Weltstart des Dedicated Servers, Mod-Gleichstand erzwungen.
- **Umsetzung folgt** als Issues: Rand-Spawner-Umbau, Send-Queue + Send-Boost statt DOM-Abschaltung, Value-Shop
  (05-Economy), HQ-Leak-Research, Tournament-Kopplung.
