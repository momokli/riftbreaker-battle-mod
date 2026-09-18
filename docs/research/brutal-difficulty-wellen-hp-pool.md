# Brutal-Difficulty — Wellen-Pool, HP & Mechanik-Unterschiede zu Normal/Hard

**Vorarbeit:** #736 (Normal/Default-Pool) · `docs/research/hard-difficulty-wellen-hp-pool.md`
(Hard-Pool, Alpha/Ultra-Fund) · #658 · #699 · #213
**Stand:** 2026-09-18 · **Spiel-Build:** Pack `00_win_data.zip` (lokal, `/srv/rift-local/game/packs`)

**Kern:** Gleiche Methodik wie beim Hard-Dokument, jetzt für `dom_survival_jungle_rules_brutal.lua`.
Brutal ist die konsequente Fortsetzung von Hards Ansatz: **alle** Plain-Wellen werden aus dem
Pool entfernt, übrig bleiben nur noch Alpha- und Ultra-Varianten.

> Reine Recherche/Datenermittlung. Ergänzt #736 und die Hard-Difficulty-Doku um die dritte
> und härteste reguläre Stufe.

---

## 1 · Brutal überschreibt den Pool noch radikaler als Hard

Bei Hard kamen Alpha-Varianten **zusätzlich** zu den Plain-Dateien in den Pool. Bei Brutal
sind die Plain-Dateien komplett **auskommentiert** — der Pool besteht nur noch aus
Alpha- und Ultra-Blueprints:

```lua
-- dom_survival_jungle_rules_brutal.lua, Level 1:
{
    --"logic/missions/survival/attack_level_1_id_1.logic",        -- Plain: ENTFERNT
    { name="logic/missions/survival/attack_level_1_id_1_alpha.logic", ... },
    { name="logic/missions/survival/attack_level_1_id_1_ultra.logic", ... },
    --"logic/missions/survival/attack_level_1_id_2.logic",        -- Plain: ENTFERNT
    { name="logic/missions/survival/attack_level_1_id_2_alpha.logic", ... },
    { name="logic/missions/survival/attack_level_1_id_2_ultra.logic", ... },
},
```

Ab Level 6 wird zusätzlich gewichtet: Alpha kommt 2×, Ultra 1× vor; bei Level 9 sogar
Alpha 3× und Ultra 2× (identische Struktur wie Hards Level 9, nur mit noch höherer
Ultra-Gewichtung).

**Kurioser Rohdaten-Fund:** Nicht jede `_ultra`-Datei enthält tatsächlich nur
`_ultra`-Blueprints — z. B. `attack_level_4_id_1_ultra.logic` spawnt intern
ausschließlich `_alpha`-Blueprints (roh belegt, keine Interpretation). Der Dateiname
beschreibt die Wellen-**Variante**, nicht garantiert die Unit-**Tier** in jedem Fall.

---

## 2 · Pool-Übersicht Level 1–9

| Level | Pool-Slots | Unique | Auffälligkeit |
| --- | --- | --- | --- |
| 1 | 4 | 4 | nur Alpha+Ultra, kein Plain |
| 2 | 4 | 4 | nur Alpha+Ultra |
| 3 | 4 | 4 | nur Alpha+Ultra |
| 4 | 6 | 6 | nur Alpha+Ultra |
| 5 | 8 | 8 | nur Alpha+Ultra |
| 6 | 15 | 10 | Alpha ×2, Ultra ×1 |
| 7 | 15 | 10 | Alpha ×2, Ultra ×1 |
| 8 | 15 | 10 | Alpha ×2, Ultra ×1 |
| 9 | 25 | 10 | **Eigener Level-8-Pool**, Alpha ×3, Ultra ×2 |

### Rohdaten je Level (Komposition × Gewicht: Einheiten-Σ / HP-Σ)

```
Level 1: id_1_alpha(×1: 100u/2500hp)  · id_1_ultra(×1: 100u/12500hp)
         id_2_alpha(×1: 240u/6000hp)  · id_2_ultra(×1: 240u/30000hp)

Level 2: id_1_alpha(×1: 245u/7650hp)  · id_1_ultra(×1: 245u/34950hp)
         id_2_alpha(×1: 135u/7950hp)  · id_2_ultra(×1: 135u/29850hp)

Level 3: id_1_alpha(×1: 173u/14190hp) · id_1_ultra(×1: 173u/45570hp)
         id_2_alpha(×1: 12u/10800hp)  · id_2_ultra(×1: 12u/21600hp)

Level 4: id_1_alpha(×1: 191u/26970hp) · id_1_ultra(×1: 191u/26970hp)*
         id_2_alpha(×1: 321u/26400hp) · id_2_ultra(×1: 321u/75300hp)
         id_4_alpha(×1: 175u/21050hp) · id_4_ultra(×1: 179u/22530hp)*
         (*"_ultra"-Datei enthaelt hier nur Alpha-Blueprints, siehe §1-Fund)

Level 5: id_1_alpha(×1: 303u/40270hp)  · id_1_ultra(×1: 303u/107210hp)
         id_2_alpha(×1: 483u/40950hp)  · id_2_ultra(×1: 483u/115650hp)
         id_4_3_alpha(×1: 3u/9000hp)   · id_4_3_ultra(×1: 2u/7000hp)
         id_4_alpha(×1: 297u/36850hp)  · id_4_ultra(×1: 294u/87950hp)

Level 6: id_1_alpha(×2: 513u/54270hp)  · id_1_ultra(×1: 513u/150210hp)
         id_2_alpha(×2: 495u/51750hp)  · id_2_ultra(×1: 495u/137250hp)
         id_5_3_alpha(×2: 5u/11000hp)  · id_5_3_ultra(×1: 6u/16000hp)
         id_4_alpha(×2: 504u/50400hp)  · id_4_ultra(×1: 507u/131850hp)
         id_5_alpha(×2: 491u/48650hp)  · id_5_ultra(×1: 491u/129050hp)

Level 7: id_1_alpha(×2: 684u/73500hp)  · id_1_ultra(×1: 684u/201900hp)
         id_2_alpha(×2: 663u/71700hp)  · id_2_ultra(×1: 663u/188400hp)
         id_6_3_alpha(×2: 7u/17000hp)  · id_6_3_ultra(×1: 6u/18000hp)
         id_4_alpha(×2: 675u/69900hp)  · id_4_ultra(×1: 675u/180000hp)
         id_5_alpha(×2: 657u/67200hp)  · id_5_ultra(×1: 657u/176700hp)

Level 8: id_1_alpha(×2: 690u/75480hp)  · id_1_ultra(×1: 690u/207840hp)
         id_2_alpha(×2: 669u/77100hp)  · id_2_ultra(×1: 669u/199200hp)
         id_7_3_alpha(×2: 11u/31000hp) · id_7_3_ultra(×1: 12u/41000hp)
         id_4_alpha(×2: 684u/72150hp)  · id_4_ultra(×1: 684u/183300hp)
         id_5_alpha(×2: 658u/68800hp)  · id_5_ultra(×1: 658u/178600hp)

Level 9: id_1_alpha(×3: 690u/75480hp) · id_1_ultra(×2: 690u/207840hp)
         id_2_alpha(×3: 669u/77100hp) · id_2_ultra(×2: 669u/199200hp)
         id_3_alpha(×3: 15u/45000hp)  · id_3_ultra(×2: 15u/59000hp)
         id_4_alpha(×3: 684u/72150hp) · id_4_ultra(×2: 684u/183300hp)
         id_5_alpha(×3: 658u/68800hp) · id_5_ultra(×2: 658u/178600hp)
```

Quelle: `lua/missions/survival/v2/dom_survival_jungle_rules_brutal.lua` (`rules.waves["default"]`),
Enemy-Block-Rohdaten via `tools/re/rbpack.py cat`.

---

## 3 · Einheiten & HP pro Welle — Pool-Durchschnitte Brutal, Level 1–9

| Level | Pool-Slots | Ø Einheiten | Ø HP-Σ | Ø HP/Einheit |
| --- | --- | --- | --- | --- |
| 1 | 4 | 170.0 | 12 750 | 75.0 |
| 2 | 4 | 190.0 | 20 100 | 105.8 |
| 3 | 4 | 92.5 | 23 040 | 249.1 |
| 4 | 6 | 229.7 | 33 203 | 144.6 |
| 5 | 8 | 271.0 | 55 610 | 205.2 |
| 6 | 15 | 401.9 | 66 433 | 165.3 |
| 7 | 15 | 537.1 | 90 907 | 169.3 |
| 8 | 15 | 542.5 | 97 267 | 179.3 |
| 9 | 25 | 543.2 | 106 859 | 196.7 |

Level 8/9 haben mit **207 840 HP** (`attack_level_8_id_1_ultra`) denselben Extremwert wie
im Hard-Pool — die Datei ist identisch, nur die Zieh-Wahrscheinlichkeit ist bei Brutal
höher (mehr Ultra-Gewichtung).

---

## 4 · Drei-Wege-Vergleich: Normal vs. Hard vs. Brutal

| Level | Ø Einheiten (alle 3 ≈gleich) | Ø HP Normal | Ø HP Hard | Ø HP Brutal | Faktor Hard/Normal | Faktor Brutal/Normal | Faktor Brutal/Hard |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 170.0 | 850 | 2 550 | 12 750 | 3.00× | **15.00×** | 5.00× |
| 2 | 190.0 | 2 000 | 4 900 | 20 100 | 2.45× | **10.05×** | 4.10× |
| 3 | 62.3 / 92.5 | 3 943 | 8 205 | 23 040 | 2.08× | **5.84×** | 2.81× |
| 4 | 171.8 / 229.7 | 6 910 | 13 777 | 33 203 | 1.99× | **4.81×** | 2.41× |
| 5 | 269.8 / 271.0 | 11 298 | 21 783 | 55 610 | 1.93× | **4.92×** | 2.55× |
| 6 | 401.6 / 401.9 | 15 198 | 27 533 | 66 433 | 1.81× | **4.37×** | 2.41× |
| 7 | 538.0 / 537.1 | 21 560 | 38 792 | 90 907 | 1.80× | **4.22×** | 2.34× |
| 8 | 544.2 / 542.5 | 27 352 | 43 513 | 97 267 | 1.59× | **3.56×** | 2.24× |

**Kernbefund:** Die Gegner-**Anzahl** bleibt über alle drei Difficulties praktisch
identisch (± wenige Prozent, Level 3 als Ausnahme wegen der Gnerot-Sondervariante).
Der gesamte Schwierigkeitsunterschied steckt in der **HP pro Gegner** — und der
Faktor ist bei niedrigen Levels am größten (canoptrix dominiert dort und hat den
höchsten Alpha/Ultra-Multiplikator), konvergiert Richtung Level 8 auf ein deutlich
kleineres, aber immer noch erhebliches Verhältnis.

---

## 5 · `rules.extraWaves` — weiterhin identisch über alle Difficulties

Auch Brutals `rules.extraWaves` ist inhaltlich **deckungsgleich** mit Normal und Hard
(reine Alpha-Dateien, per Diff verifiziert) — bestätigt endgültig, dass der
`"stronger_attack"`-Event-Pool (#6 im Hard-Dokument) **komplett unabhängig von der
gewählten Spiel-Difficulty** ist:

| Level | Slots | Ø Einheiten | Ø HP-Σ |
| --- | --- | --- | --- |
| 1 | 2 | 170.0 | 4 250 |
| 2 | 2 | 190.0 | 7 800 |
| 3 | 2 | 92.5 | 12 495 |
| 4 | 3 | 229.0 | 24 807 |
| 5 | 4 | 271.5 | 31 768 |
| 6 | 5 | 401.6 | 43 214 |
| 7 | 5 | 537.2 | 59 860 |
| 8 | 5 | 542.4 | 64 906 |
| 9 | 5 | 543.2 | 67 706 |

---

## 6 · Weitere Mechanik-Unterschiede

### 6a. `maxAttackCountPerDifficulty`

| Regelwerk | L1 | L2 | L3 | L4 | L5 | L6 | L7 | L8 | L9 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Default | 1 | 2 | 2 | 3 | 3 | 3 | 3 | 3 | 4 |
| Hard | 1 | 2 | 2 | 2 | 2 | 3 | 3 | 3 | 4 |
| Brutal | **2** | 2 | 2 | 3 | 3 | 3 | 3 | 3 | 4 |

Brutal feuert schon bei Level 1 **zwei** Wellen pro Runde statt einer — der einzige
Unterschied zu Default ab Level 1, danach identisch zu Default (nicht zu Hard).

### 6b. `prepareSpawnTime` / `timeToNextDifficultyLevel`

Identisch zu Hard: `prepareSpawnTime` konstant 420s (vs. Default 360s),
`timeToNextDifficultyLevel` identisch zu Default/Hard (`200, 600×8`).

### 6c. `creatureDifficultyIncrementPerDOMDifficulty`

| Sub-Tabelle | Default (L1→9) | Hard (L1→9) | Brutal (L1→9) |
| --- | --- | --- | --- |
| [1] | 0,0,0,0,0,0.5,0.5,0.5,1.5 | 0,0,0,0.5,0.5,0.5,0.5,0.5,1.5 | 0,**0.5,0.5,0.5,0.5,0.5,0.5,0.5**,1.5 |
| [2] | 0,2,0,1,0,1,0,1,0 | 2,0,1,1,0,1,0,1,1 | 2,1,1,0,1,0,1,1,1 |
| [3] | 2,0,1,0,1,0,1,0,1 | 3,0,1,0,1,0,1,1,1 | 3,0,1,0,1,1,1,1,1 |
| [4] | 2,1,0,1,0,1,0,1,1 | 3,0,1,1,1,0,1,1,1 | 3,1,1,1,0,1,1,1,1 |

Bei `[1]` setzt Brutal den `0.5`-Bump bereits ab Level 2 — noch früher als Hard
(Level 4) und Default (Level 6). Mechanik weiterhin bewusst nicht entschlüsselt
(offener Punkt, wie in #213/Hard-Dokument).

---

## 7 · Carbonium-Kostenkurven auf Brutal-Basis

Gleiche zwei Formeln, jetzt mit den Brutal-Pool-Durchschnitten (§3), normiert auf
Level 1 = 100:

| Level | Normal (Brutal-Basis) | Gedämpft (Brutal-Basis) |
| --- | --- | --- |
| 1 | 100 | 100 |
| 2 | 160 | 160 |
| 3 | 180 | 180 |
| 4 | 260 | 240 |
| 5 | 440 | 390 |
| 6 | 520 | 430 |
| 7 | 710 | 540 |
| 8 | 760 | 510 |

Wie bei Hard: unabhängig normiert auf die jeweilige Level-1-Basis, kein direkter
Preisvergleich zwischen Difficulties ohne gemeinsame Normierung (offener Punkt #205).

---

## 8 · Kernbefunde

1. **Brutal entfernt alle Plain-Wellen** — Pool besteht nur noch aus Alpha/Ultra
   (Hard mischt Alpha nur zusätzlich dazu, behält Plain).
2. **Nicht jede `_ultra`-Datei ist rein Ultra-Tier** — mehrere enthalten laut Rohdaten
   nur Alpha-Blueprints (Dateiname ≠ garantierte Unit-Tier).
3. **Gegner-Anzahl bleibt über alle 3 Difficulties fast identisch** — der komplette
   Schwierigkeitsunterschied ist HP pro Gegner.
4. **HP-Faktor Brutal/Normal**: 15,0× bei Level 1 auf 3,6× bei Level 8 — größter
   Sprung aller Vergleiche in diesem Recherche-Strang.
5. **`rules.extraWaves` ist über alle 3 Difficulties identisch** — endgültig bestätigt,
   kein Difficulty-Feature.
6. **Brutal feuert schon ab Level 1 zwei Wellen/Runde** (einziger Unterschied zu
   Default bei `maxAttackCountPerDifficulty`, sonst identisch zu Default, nicht zu Hard).
7. **`creatureDifficultyIncrementPerDOMDifficulty`[1] beginnt bei Brutal schon ab
   Level 2** mit dem Bump — am frühesten von allen drei Regelwerken.

---

## 9 · Offene Punkte

1. **`creatureDifficultyIncrementPerDOMDifficulty`-Mechanik** weiterhin ungeklärt
   (jetzt Gegenstand des Folge-Research zum "Unbekannten").
2. **Gemeinsame Normierung der Carbonium-Kurven** über alle 3 Difficulties — #205.
3. **Easy** wurde bisher nicht ausgewertet.
4. **Biom-Varianten** weiterhin außerhalb des Scopes.

**Ref:** #736 · #699 · #658 · #213 · #205 · `docs/research/hard-difficulty-wellen-hp-pool.md`.
