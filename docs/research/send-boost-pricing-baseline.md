# Send-Boost-Preismodell — erster Arbeits-Entwurf (für #205)

**Vorarbeit:** #736 (Normal-Wellen-HP), `docs/research/hard-difficulty-wellen-hp-pool.md`,
`docs/research/brutal-difficulty-wellen-hp-pool.md` (Hard/Brutal-Wellen-HP), #213, #199 (ECO-6)
**Stand:** 2026-09-18

> **Status: Erstentwurf, kein Feature.** Die Zahlen hier sind eine Kalibrierungs-Baseline
> für die #205-Preisformel, keine fertige Balance. Sie werden durch Playtesting und die
> tatsächliche Carbonium-Einkommensrate (noch nicht recherchiert/gemessen) nachjustiert.

**Design-Ziel (aus Chat-Diskussion, ECO-6-Kontext):**
- **Durchschnittliches Sende-Verhalten** (regelmäßig, kleine Beträge) soll die Wellen-HP
  knapp unter das Niveau von **Hard**-Difficulty heben.
- **Sparen/seltener, dafür größer senden** soll die Wellen-HP deutlich über Hard, aber
  unter **Brutal** heben.
- Die Kosten sollen **mit jedem Level garantiert steigen** — kein Level darf günstiger
  sein als das vorherige.

---

## 1 · Warum die naive Methode (additive Interpolation) bricht

Erster Versuch: `Ziel(L) = Normal(L) + Index · (Hard(L) − Normal(L))`. Ergebnis bei
Index 0,85 bzw. 1,4 (Position zwischen Normal/Hard/Brutal):

| Level | Kosten Ø-Senden (additiv) | Kosten Sparen (additiv) |
| --- | --- | --- |
| 5 | 1 048 | 2 825 |
| 6 | 1 234 | 3 282 |
| 7 | 1 723 | **4 480** |
| 8 | **1 616** ⚠️ Rückgang! | **4 431** ⚠️ Rückgang! |

**Ursache:** Der Faktor Hard/Normal schrumpft mit dem Level (3,00× bei L1 → 1,59× bei L8,
siehe Hard-Doku §4/§5). Absolute HP-Differenzen einer schrumpfenden Ratio sind nicht
automatisch monoton — additive Interpolation zwischen Difficulty-Tiers bricht deshalb bei
L7→L8. Bekanntes Anti-Pattern bei Powercurve-Balancing (vgl. Itemization-Systeme mit
Tier-Sprüngen): additive Interpolation ist nur sicher, wenn die Ratio zwischen den Tiers
über die gesamte Skala konstant bleibt — hier ist sie es nicht.

---

## 2 · Der Fix: fester Ziel-Multiplikator statt Pro-Level-Ratio

Statt der (schwankenden) Pro-Level-Ratio wird ein **einziger, fixer Multiplikator** über
das geometrische Mittel der Hard/Normal- bzw. Brutal/Normal-Ratio aller 8 Level bestimmt
(robuster als ein Einzellevel, nicht verzerrt durch den Solo-Gnerot-Ausreißer bei Level 3):

```
Hard/Normal-Ratio (geom. Mittel, L1-L8)   = 2.04
Brutal/Normal-Ratio (geom. Mittel, L1-L8) = 5.85

M_avg   = 1 + 0.85 · (2.04 − 1) = 1.89   -- Ø-Senden-Zielmultiplikator
M_saved = 2.04 + 0.4 · (5.85 − 2.04)     -- Sparen-Zielmultiplikator
        = 3.57

Ziel(L)   = Normal(L) · M
Kosten(L) = k · ( Ziel(L) − Normal(L) ) = k · Normal(L) · (M − 1)
```

**Warum das nicht mehr brechen kann:** `Normal(L)` ist über alle 8 Level streng monoton
steigend (verifiziert, keine Ausnahme). `k · (M − 1)` ist eine positive Konstante.
Eine monoton steigende Größe multipliziert mit einer positiven Konstante bleibt
zwangsläufig monoton steigend — das ist keine empirische Beobachtung mehr, sondern eine
mathematische Garantie der Formel selbst.

---

## 3 · Der Kalibrierungs-Anker: 300 Carbonium Startkapital

Bisheriger Anker (#213/#736 §8) war willkürlich (`Level 1 = 100 Carbonium`). Es gibt
einen echten, doppelt bestätigten Anker:

- **Statisch:** `logic/missions/survival/default.logic` (Node `entity_add_resource_0`)
  gewährt beim Rundenstart `carbonium = 300.00` (und `steel = 300.00`) — vor Biom-Zweigen,
  also vor der ersten Welle.
- **Live bestätigt:** Commits `f7ea0a4`/`496e2a6` (`docs(#363)`/`docs(#365)`, dieses Repo)
  haben `carbonium 300.0` bereits an einem laufenden Server live ausgelesen.

**Kalibrierung:** Die Kosten für das Ø-Send-Ziel bei Level 1 werden exakt auf 300
Carbonium gesetzt — die komplette Starthabe reicht bei Level 1 gerade für das
Ø-Send-Ziel, nicht mehr und nicht weniger. Daraus:

```
k = 300 / ( Normal(1) · (M_avg − 1) ) = 300 / ( 850 · 0.89 ) ≈ 0.3977 Carbonium/HP
```

---

## 4 · Ergebnis-Tabelle

| Level | Normal-HP (natürlich) | Ziel Ø-Senden | Kosten Ø-Senden | Ziel Sparen | Kosten Sparen |
| --- | --- | --- | --- | --- | --- |
| 1 | 850 | 1 604 | **300** | 3 030 | 867 |
| 2 | 2 000 | 3 775 | **706** | 7 130 | 2 040 |
| 3 | 3 943 | 7 443 | **1 392** | 14 058 | 4 023 |
| 4 | 6 910 | 13 043 | **2 439** | 24 635 | 7 049 |
| 5 | 11 298 | 21 324 | **3 987** | 40 277 | 11 524 |
| 6 | 15 198 | 28 687 | **5 364** | 54 183 | 15 503 |
| 7 | 21 560 | 40 695 | **7 609** | 76 865 | 21 993 |
| 8 | 27 352 | 51 627 | **9 654** | 97 514 | 27 902 |

Beide Kosten-Spalten sind über alle 8 Level streng monoton steigend (per Formel
garantiert, siehe §2).

---

## 5 · Offene Punkte (blockieren die Feinjustierung, nicht das Grundmodell)

1. **Carbonium-Einkommensrate** (pro Zeit/Runde) — existiert laut Nutzer, aber noch nicht
   recherchiert oder gemessen. Ohne sie lässt sich nicht abschließend prüfen, ob die
   Kosten in §4 in der verfügbaren Zeit bis zur jeweiligen Welle überhaupt aufbringbar
   sind (weder für Ø-Senden noch für Sparen).
2. **Einmal- vs. laufender Betrag:** unklar, ob die 300 Carbonium nur einmalig beim
   Rundenstart gewährt werden oder ob es zusätzlich einen laufenden Zufluss
   (Fabriken/Research) gibt, der on top kommt.
3. **M_avg/M_saved sind Erstschätzungen** (0,85 bzw. 1,4 als Position zwischen den
   Difficulties) — explizit als Playtesting-Stellschrauben gedacht, keine finalen Werte.
4. **k ist nur bei Level 1 exakt kalibriert** — ob der gleiche `k` über alle Level hinweg
   das gewünschte Spielgefühl trifft oder levelabhängig nachjustiert werden muss, ist
   offen (das Modell erlaubt eine level-abhängige Anpassung von `k`, solange `k > 0`
   bleibt — Monotonie bliebe erhalten, nur die Steilheit würde sich ändern).

**Ref:** #205 · #199 (ECO-6) · #736 · #213 · `docs/research/hard-difficulty-wellen-hp-pool.md` ·
`docs/research/brutal-difficulty-wellen-hp-pool.md`.
