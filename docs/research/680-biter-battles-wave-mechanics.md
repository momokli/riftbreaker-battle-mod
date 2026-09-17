# Biter Battles: Wellen-Mechanik als Referenz für Sandbox-Mode (Issue #680)

**Issue:** #680 (Recherche) · **Refs:** #205 · #199 (ECO-6) · #670 (Preiskurven-Kandidaten, aktuelles System)
**Stand:** 2026-09-17 · **Quelle:** `github.com/Factorio-Biter-Battles/factorio-biter-battles` (Open Source,
direkt geklont und gelesen — nicht nur Wiki/Foren-Beschreibungen)

> Reine Recherche/Dokumentation (kein Feature). Für einen zukünftigen Sandbox-Mode ist angedacht, Wellen eher
> wie bei diesem Factorio-Mod spawnen zu lassen — dieses Dokument hält die tatsächlichen Formeln und
> Mechanismen fest, damit sie für den späteren Umbau nicht neu recherchiert werden müssen.

---

## 1 · Warum das relevant ist

Unser aktuelles System (Preiskurve für "Wave senden", #670) nutzt feste `.logic`-Dateien pro Difficulty-Level
(Pool aus mehreren vorgefertigten Kompositionen, siehe #658). Für die Zukunft ist ein Sandbox-Mode geplant, in
dem Wellen dynamischer/prozeduraler gespawnt werden — näher an **Biter Battles**, dem bekannten Factorio-PvP-Mod.

## 2 · Kernunterschied: Threat-Budget statt fester Wellen-Pool

Unser System wählt pro Difficulty-Level zufällig eine von mehreren **fertig vorgefertigten** `.logic`-Dateien
(z. B. `attack_level_6_id_3.logic` = kafferroceros×13 + baxmoth×2 + canoptrix×9, fest im Spiel hinterlegt).
Keine Variation innerhalb einer Datei — entweder diese Mischung oder eine der anderen im Pool.

Biter Battles macht das anders: Es gibt **keine vorgefertigten Wellen**. "Threat" ist eine laufende Währung,
die **einzeln Einheiten kauft**, bis ein Budget leer ist. Beim Wellen-Spawn zieht das Spiel Einheit für Einheit
aus einem gewichteten Zufalls-Pool (verfügbare Stärken hängen von Evolution ab, siehe §3), zieht jeweils den
Threat-Wert dieser Einheit vom Budget ab, und hört auf sobald das Budget aufgebraucht ist. Jede Welle ist
dadurch einzigartig zusammengesetzt.

**Bildlich:** unser System ist "wähle eines von 5 fertigen Menüs", Biter Battles ist "du hast X Budget, kauf
dir einzeln zusammen was reinpasst".

## 3 · Zwei getrennte Werte: Evolution vs. Threat

- **Evolution (evo)** = Qualität. Bestimmt, welche Einheiten-Tiers überhaupt spawnbar sind (klein/mittel/groß/
  behemoth) und wie stark Boss-Einheiten skalieren. Steigt permanent leicht von selbst, zusätzlich beim Senden
  von Wissenschaft.
- **Threat** = Menge. Die "Kaufkraft" für die nächste Angriffswelle. Steigt beim Senden, sinkt wieder wenn der
  Gegner Biter tötet.

Genau die Trennung "Preis" vs. "wie schwer wird die Welle", die bei unserer #670-Diskussion aufkam — Biter
Battles hat sie strikt durchgezogen, unser aktuelles System (noch) nicht.

## 4 · Die Feed-Formel

Aus `maps/biter_battles_v2/feeding_calculations.lua`, wortwörtlich:

```
food = anzahl_flaschen * mutagen_wert_pro_flasche
```

Wissenschafts-Pakete ("Mutagen-Stärke" pro Flasche), aus `tables.lua`:

| Science Pack | Wert |
| --- | --- |
| automation (rot) | 0,0011 |
| logistic (grün) | 0,0028 |
| military (grau) | 0,0095 |
| chemical (blau) | 0,0345 |
| production (lila) | 0,1150 |
| utility (gelb) | 0,2205 |
| space (weiß) | 0,4375 |

`food` wird in einer Schleife gleichzeitig in **Evo-Zuwachs** (mit abnehmendem Ertrag, je höher Evo schon ist —
dasselbe Dominanz-Prinzip wie bei unserer #670-Kurve) und **Threat-Zuwachs** umgewandelt.

**Wichtiger Design-Kontrast zu unserem Ansatz:** Über 100 % Evo kehrt sich das um. Ein fest im Code stehender
Faktor (`threat_scale_factor_past_evo100 = 3`, `config.lua`) sorgt dafür, dass jede Flasche über 100 % Evo
**3× so viel Threat** gibt wie dieselbe Flasche bei 99 %. Das ist bewusst das Gegenteil unseres "darf nicht
relativ billiger werden"-Prinzips — Biter Battles belohnt explizit "alles reinkloppen wenn Evo schon hoch ist",
um das Spiel gegen Ende zu eskalieren, statt es zu bremsen.

## 5 · Von Threat zu Wellen

Aus `ai.lua` und der Tick-Tabelle in `main.lua`:

- Alle ~2 Minuten wird das gespeicherte Threat beider Teams verglichen und per Verhältnis auf **7 Wellen**
  verteilt: `wellen_anzahl = ceil(threat_verhaeltnis * 7)`.
- Pro Welle geht die Hälfte des Threat-Budgets an normale Biter, die Hälfte an Bosse. Einheiten werden
  **einzeln** aus einem gewichteten Zufalls-Pool gezogen und vom Budget abgezogen, bis nichts mehr übrig ist.
- Boss-HP-Multiplikator ist fix im Code: **26×** normale HP (`20 * 1,3`, `config.lua`).
- **Wichtiger Rückkopplungs-Unterschied zu uns:** Getötete Biter ziehen Threat **wieder ab**
  (`Public.subtract_threat`). Verteidigung drainiert direkt den Threat-Pool des Angreifers. Bei uns ist eine
  gesendete Welle ein einmaliger, abgeschlossener Kauf — bei Biter Battles ist Threat ein **persistenter,
  abbaubarer** Pool, der über Zeit und Kills schwankt.

## 6 · Was das für unseren Sandbox-Plan bedeuten würde

Architektur-Sprung, kein Feintuning:

- **Heute:** wir aktivieren eine fertige `.logic`-Datei direkt per nativem C++-Aufruf (`ActivateMissionFlow`,
  siehe `server/dll/rbbridge.c`) — ein Aufruf, feste Zusammensetzung.
- **Biter-Battles-Stil:** prozedural einzelne Einheiten an Spawnpunkten erzeugen, bis ein Threat-Budget leer
  ist — viele Einzel-Spawns, dynamische Zusammensetzung, plus ein laufendes Threat/Evo-Buchhaltungssystem, das
  über die Zeit persistiert (nicht nur pro Sende-Klick).

Deutlich mehr Bauaufwand als die #670-Preiskurve — aber die Grundbausteine (native Einheiten-Spawns, Ressourcen
lesen/schreiben über die Bridge) existieren teilweise schon aus der bisherigen RE-Arbeit.

## 7 · Offene Diskussionsfragen

1. Wollen wir das volle prozedurale Budget-System übernehmen, oder nur einzelne Ideen daraus (z. B. Evo/Threat-
   Trennung) in unser bestehendes Pool-System einbauen — ohne den kompletten Umbau?
2. Biter Battles belohnt "mehr senden wird effektiver, je höher Evo schon ist" (3×-Threat-Bonus über 100 %
   Evo). Wollen wir diese Eskalations-Logik, oder bleiben wir bei unserem "darf nie relativ billiger werden"-
   Prinzip?
3. Der Rückkopplungs-Mechanismus (getötete Biter senken das Threat-Budget des Gegners) ist strategisch
   interessant — lohnt sich das für uns, oder macht es das System zu komplex für Solo-/2-Spieler-Runden?
4. Zeitrahmen: explizit Sandbox-Mode-Zukunftsmusik, nicht nötig für den #670-Playtest der Preiskurve. Wann
   wollen wir das ernsthaft angehen?

## Refs

#205 · #199 (ECO-6) · #670 · #658 (Wellen-Pool-Daten, aktuelles System) ·
[Factorio-Biter-Battles/factorio-biter-battles](https://github.com/Factorio-Biter-Battles/factorio-biter-battles)
