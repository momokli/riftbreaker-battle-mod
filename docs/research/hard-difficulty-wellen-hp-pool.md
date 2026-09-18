# Hard-Difficulty — Wellen-Pool, HP & Mechanik-Unterschiede zu Normal

**Vorarbeit:** #736 (`docs/research/736-wellen-hp-pool-vollstaendig.md`, Normal/Default-Pool,
`units_in_one_spawn`-Fix) · #658 · #699 · #213 (`docs/research/213-wave-richtwert.md`)
**Stand:** 2026-09-18 · **Spiel-Build:** Pack `00_win_data.zip` (lokal, `/srv/rift-local/game/packs`)

**Kern:** Die Spiel-Difficulty (Easy/Normal/Hard/Brutal) ist kein globaler Multiplikator,
sondern ein eigenes Lua-Regelwerk (`dom_survival_jungle_rules_<difficulty>.lua`), das per
`require("...default.lua")()` die Basis lädt und gezielt einzelne Tabellen überschreibt.
Dieses Dokument liefert für **Hard** dieselbe Analyse wie #736 für Normal — plus eine
Gegenüberstellung, was sich zwischen den beiden Difficulties tatsächlich unterscheidet.

> Reine Recherche/Datenermittlung. Für die #205-Preisformel ist dieses Dokument die
> Hard-Difficulty-Ergänzung zu #736.

---

## 1 · Architektur: wie Difficulty-Regelwerke aufgebaut sind

```
dom_survival_jungle_rules_hard.lua:
    local rules = require("dom_survival_jungle_rules_default.lua")()
    rules.timeToNextDifficultyLevel = { ... }         -- teils überschrieben
    rules.prepareSpawnTime = { ... }                  -- überschrieben
    rules.creatureDifficultyIncrementPerDOMDifficulty = { ... }  -- überschrieben
    rules.maxAttackCountPerDifficulty = { ... }        -- überschrieben
    rules.waves["default"] = { ... }                   -- komplett neu definiert
    rules.extraWaves = { ... }                         -- überschrieben (aber inhaltsgleich zu Normal)
    return rules
```

Easy/Normal/Brutal folgen demselben Muster, überschreiben aber jeweils andere Teilmengen.
Normal überschreibt z. B. `rules.waves` **nicht** — der Hauptpool bleibt identisch zu Default
(= #736). Hard dagegen schreibt `rules.waves["default"]` komplett neu.

---

## 2 · Alpha/Ultra sind eigene Blueprints, keine Biom-Varianten

Bisherige Annahme (#213/#658/#736-Scope): `_alpha`/`_ultra` = Biom- oder DLC-Suffixe,
bewusst ausgeklammert. **Korrektur:** Es sind **eigene, stärkere Unit-Blueprints** mit
eigener `.ent`-Basis-HP:

| Unit | Basis | Alpha | Ultra |
| --- | --- | --- | --- |
| `canoptrix` | 5 | 25 (5×) | 125 (25×) |
| `arachnoid_sentinel` | 110 | 330 (3×) | 990 (9×) |
| `kafferroceros` | 300 | 900 (3×) | 1800 (6×) |
| `gnerot` | 2000 | 3000 (1,5×) | 4000 (2×) |
| `bomogan` | 250 | 300 (1,2×) | 400 (1,6×) |
| `baxmoth` | 1000 | 1300 (1,3×) | 1600 (1,6×) |

`count`/`units_in_one_spawn` sind zwischen Plain- und Alpha/Ultra-Pendant **identisch** —
nur der `blueprint`-Name (und damit die `.ent`-HP) ändert sich. Das ist der zentrale
Hebel, mit dem Hard schwerer wird: nicht mehr Gegner, sondern zähere.

---

## 3 · Hard überschreibt den Haupt-Pool (`rules.waves`)

Pro Level mischt Hard die `_alpha`-Version **zusätzlich** zur Plain-Version in denselben
Pool, teils mit Gewichtung durch doppelte Einträge (erhöht die Zieh-Wahrscheinlichkeit).
Level 9 bekommt bei Hard einen **komplett eigenen** Pool (27 Slots, inkl. `_ultra`) statt
wie bei Default/Normal einfach Level 8 zu teilen (#213 §2).

| Level | Pool-Slots (Hard) | Unique Kompositionen | Auffälligkeit |
| --- | --- | --- | --- |
| 1 | 4 | 4 | Plain ×2 + Alpha ×2 |
| 2 | 4 | 4 | Plain ×2 + Alpha ×2 |
| 3 | 4 | 4 | Plain ×2 + Alpha ×2 |
| 4 | 6 | 6 | Plain ×3 + Alpha ×3 |
| 5 | 8 | 8 | Plain ×4 + Alpha ×4 |
| 6 | 12 | 10 | 2 Einträge doppelt gewichtet |
| 7 | 12 | 10 | 2 Einträge doppelt gewichtet |
| 8 | 12 | 10 | 2 Einträge doppelt gewichtet |
| 9 | 27 | 15 | **Eigener Pool**, inkl. `_ultra`, massiv gewichtet |

### Rohdaten je Level (Komposition × Gewicht: Einheiten-Σ / HP-Σ)

```
Level 1: id_1(×1: 100u/500hp) · id_1_alpha(×1: 100u/2500hp)
         id_2(×1: 240u/1200hp) · id_2_alpha(×1: 240u/6000hp)

Level 2: id_1(×1: 245u/1750hp) · id_1_alpha(×1: 245u/7650hp)
         id_2(×1: 135u/2250hp) · id_2_alpha(×1: 135u/7950hp)

Level 3: id_1(×1: 173u/4230hp) · id_1_alpha(×1: 173u/14190hp)
         id_2(×1: 12u/3600hp)  · id_2_alpha(×1: 12u/10800hp)

Level 4: id_1(×1: 191u/8490hp) · id_1_alpha(×1: 191u/26970hp)
         id_2(×1: 321u/7800hp) · id_2_alpha(×1: 321u/26400hp)
         id_3(×1: 2u/4000hp)   · id_3_alpha(×1: 3u/9000hp)

Level 5: id_1(×1: 303u/12590hp) · id_1_alpha(×1: 303u/40270hp)
         id_2(×1: 483u/12150hp) · id_2_alpha(×1: 483u/40950hp)
         id_3(×1: 4u/8000hp)    · id_3_alpha(×1: 5u/11000hp)
         id_4(×1: 289u/12450hp) · id_4_alpha(×1: 297u/36850hp)

Level 6: id_1(×2: 513u/16590hp) · id_1_alpha(×1: 513u/54270hp)
         id_2(×2: 495u/15750hp) · id_2_alpha(×1: 495u/51750hp)
         id_3(×1: 5u/10000hp)   · id_3_alpha(×1: 7u/17000hp)
         id_4(×1: 504u/17700hp) · id_4_alpha(×1: 504u/50400hp)
         id_5(×1: 491u/15950hp) · id_5_alpha(×1: 491u/48650hp)

Level 7: id_1(×2: 684u/22500hp) · id_1_alpha(×1: 684u/73500hp)
         id_2(×2: 663u/21900hp) · id_2_alpha(×1: 663u/71700hp)
         id_3(×1: 8u/16000hp)   · id_3_alpha(×1: 11u/31000hp)
         id_4(×1: 678u/25200hp) · id_4_alpha(×1: 675u/69900hp)
         id_5(×1: 657u/22200hp) · id_5_alpha(×1: 657u/67200hp)

Level 8: id_1(×2: 690u/23160hp) · id_1_alpha(×1: 690u/75480hp)
         id_2(×2: 669u/23700hp) · id_2_alpha(×1: 669u/77100hp)
         id_3(×1: 20u/40000hp)  · id_3_alpha(×1: 15u/45000hp)
         id_4(×1: 684u/26700hp) · id_4_alpha(×1: 684u/72150hp)
         id_5(×1: 658u/23200hp) · id_5_alpha(×1: 658u/68800hp)

Level 9: id_1(×3: 690u/23160hp) · id_1_alpha(×2: 690u/75480hp) · id_1_ultra(×1: 690u/207840hp)
         id_2(×3: 669u/23700hp) · id_2_alpha(×2: 669u/77100hp) · id_2_ultra(×1: 669u/199200hp)
         id_3(×2: 20u/40000hp)  · id_3_alpha(×2: 15u/45000hp)  · id_3_ultra(×1: 15u/59000hp)
         id_4(×2: 684u/26700hp) · id_4_alpha(×2: 684u/72150hp) · id_4_ultra(×1: 684u/183300hp)
         id_5(×2: 658u/23200hp) · id_5_alpha(×2: 658u/68800hp) · id_5_ultra(×1: 658u/178600hp)
```

Quelle: `lua/missions/survival/v2/dom_survival_jungle_rules_hard.lua` (`rules.waves["default"]`),
Enemy-Block-Rohdaten via `tools/re/rbpack.py cat` (gleiche Methode wie #736 §1/§3/§4).

---

## 4 · Einheiten & HP pro Welle — Pool-Durchschnitte Hard, Level 1–9

| Level | Pool-Slots | Ø Einheiten | Ø HP-Σ | Ø HP/Einheit |
| --- | --- | --- | --- | --- |
| 1 | 4 | 170.0 | 2 550 | 15.0 |
| 2 | 4 | 190.0 | 4 900 | 25.8 |
| 3 | 4 | 92.5 | 8 205 | 88.7 |
| 4 | 6 | 171.5 | 13 777 | 80.3 |
| 5 | 8 | 270.9 | 21 783 | 80.4 |
| 6 | 12 | 418.8 | 27 533 | 65.7 |
| 7 | 12 | 560.6 | 38 792 | 69.2 |
| 8 | 12 | 566.3 | 43 513 | 76.8 |
| 9 | 27 | 553.7 | 67 607 | 122.1 |

Level 9 enthält den extremsten Einzelwert im gesamten Datensatz (Normal + Hard):
`attack_level_8_id_1_ultra` mit **207 840 HP** bei nur 690 Einheiten.

---

## 5 · Vergleich Hard vs. Normal

| Level | Ø Einheiten Normal | Ø Einheiten Hard | Ø HP Normal | Ø HP Hard | HP-Faktor |
| --- | --- | --- | --- | --- | --- |
| 1 | 170.0 | 170.0 | 850 | 2 550 | **3.00×** |
| 2 | 190.0 | 190.0 | 2 000 | 4 900 | **2.45×** |
| 3 | 62.3 | 92.5 | 3 943 | 8 205 | **2.08×** |
| 4 | 171.8 | 171.5 | 6 910 | 13 777 | **1.99×** |
| 5 | 269.8 | 270.9 | 11 298 | 21 783 | **1.93×** |
| 6 | 401.6 | 418.8 | 15 198 | 27 533 | **1.81×** |
| 7 | 538.0 | 560.6 | 21 560 | 38 792 | **1.80×** |
| 8 | 544.2 | 566.3 | 27 352 | 43 513 | **1.59×** |

**Kernbefund:** Die **Anzahl** der Gegner ändert sich zwischen Normal und Hard kaum
(± wenige Prozent, weil `count`/`units_in_one_spawn` bei Alpha-Blueprints unverändert
bleiben). Der Unterschied ist fast ausschließlich **HP pro Gegner** — mit dem größten
Sprung bei Level 1 (canoptrix dominiert dort und hat mit 5× den höchsten Alpha-Multiplikator
aller Units), abnehmend Richtung Level 8, weil sich der Mix Richtung kafferroceros/gnerot
verschiebt, deren Alpha-Multiplikator milder ist (3× bzw. 1,5×).

---

## 6 · `rules.extraWaves` — "stronger_attack"-Event (geteilt zwischen Normal & Hard)

Konsumiert über `dom_mananger:GetExtraWavePool()` (`lua/missions/v2/dom_manager.lua`),
ausgelöst durch das Event `"stronger_attack"` (`lua/missions/v2/event_manager.lua`):
`self.extraAttacks = amount` — vermutlich die native Twitch-Integration des Spiels, auf
der dieser Mod aufbaut. **Kein passiver Difficulty-Bestandteil**, sondern ein aktiv von
außen getriggerter Zusatz-Wellen-Spawn.

Pool-Inhalt ist **identisch für Normal und Hard** (reine Alpha-Dateien, verifiziert per
Diff der beiden Lua-Quellen):

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

## 7 · Weitere Mechanik-Unterschiede (Timing, Attack-Count, Creature-Increment)

### 7a. `maxAttackCountPerDifficulty` (Wellen pro Angriffsrunde)

| Level | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Default | 1 | 2 | 2 | 3 | 3 | 3 | 3 | 3 | 4 |
| Hard | 1 | 2 | 2 | **2** | **2** | 3 | 3 | 3 | 4 |

Bei Level 4–5 feuert Hard eine Welle **weniger** pro Runde als Default.

### 7b. `prepareSpawnTime` (Vorwarnzeit vor jeder Welle)

| | Default | Hard |
| --- | --- | --- |
| Alle Level | 360 s konstant | **420 s konstant** |

Gegenintuitiv: Hard gibt **mehr** Vorbereitungszeit, nicht weniger.

### 7c. `timeToNextDifficultyLevel` (wann DOM eskaliert)

Identisch zwischen Default und Hard: `200, 600, 600, 600, 600, 600, 600, 600, 600`.
Die Eskalationsgeschwindigkeit selbst ändert sich nicht — nur der Inhalt der Wellen.

### 7d. `creatureDifficultyIncrementPerDOMDifficulty` (Laufzeit-HP-Boost, Mechanik NICHT entschlüsselt)

Vier Teiltabellen `[1]`–`[4]` (vermutlich je Spieleranzahl), Wert pro DOM-Level 1–9.
Rohwerte unterscheiden sich zwischen Default und Hard, **was genau der Wert bewirkt ist
bewusst offen gelassen** (wie in #213/#736 als Caveat vermerkt — kein Teil dieser
Zahlen, on top der `.ent`-Basis-HP):

| Sub-Tabelle | Default (Level 1→9) | Hard (Level 1→9) |
| --- | --- | --- |
| [1] | 0, 0, 0, 0, 0, 0.5, 0.5, 0.5, 1.5 | 0, 0, 0, **0.5, 0.5, 0.5, 0.5**, 0.5, 1.5 |
| [2] | 0, 2, 0, 1, 0, 1, 0, 1, 0 | 2, 0, 1, 1, 0, 1, 0, 1, 1 |
| [3] | 2, 0, 1, 0, 1, 0, 1, 0, 1 | 3, 0, 1, 0, 1, 0, 1, 1, 1 |
| [4] | 2, 1, 0, 1, 0, 1, 0, 1, 1 | 3, 0, 1, 1, 1, 0, 1, 1, 1 |

Auffällig: `[1]` setzt den `0.5`-Bump bei Hard schon ab Level 4 statt erst ab Level 6 bei
Default — zwei Level früher.

---

## 8 · Carbonium-Kostenkurven auf Hard-Basis

Gleiche zwei Formeln wie #736 §8, jetzt mit den Hard-Pool-Durchschnitten (§4) statt
Normal, normiert auf Level 1 = 100:

```
Normal(L)    = 100 · Ø HP Σ(L) / Ø HP Σ(1)
Gedaempft(L) = Normal(L) · (1 − ⅓ · t²),   t = (L−1) / 7
```

| Level | Normal (Hard-Basis) | Gedämpft (Hard-Basis) |
| --- | --- | --- |
| 1 | 100 | 100 |
| 2 | 190 | 190 |
| 3 | 320 | 310 |
| 4 | 540 | 510 |
| 5 | 850 | 760 |
| 6 | 1080 | 900 |
| 7 | 1520 | 1150 |
| 8 | 1710 | 1140 |

Zum Vergleich die #736-Kurven auf Normal-Basis: Level 8 lag dort bei 3220 (normal) /
2150 (gedämpft) — das liegt **höher** als Hard hier, weil beide Kurven-Sets unabhängig
auf ihrer jeweiligen Level-1-Basis normiert sind (100), nicht auf eine gemeinsame Skala.
Ein direkter Preisvergleich zwischen Normal- und Hard-Kosten bräuchte eine gemeinsame
Normierung — offener Punkt für #205, falls Sends über Difficulties hinweg vergleichbar
bepreist werden sollen.

---

## 9 · Kernbefunde (Zusammenfassung)

1. **`_alpha`/`_ultra` sind eigene Blueprints mit eigener `.ent`-HP**, keine Biom-Varianten
   (Korrektur zu #213/#658/#736-Scope-Annahme).
2. **Hard ändert die Gegner-Anzahl kaum** — `count`/`units_in_one_spawn` bleiben zwischen
   Plain- und Alpha-Pendant identisch, nur der Blueprint (und damit die HP) wechselt.
3. **HP-Faktor Hard/Normal sinkt mit dem Level**: 3,0× bei Level 1 auf 1,6× bei Level 8,
   weil canoptrix (5× Alpha-Multiplikator) bei niedrigen Leveln dominiert.
4. **Level 9 ist bei Hard kein Alias mehr für Level 8** (anders als Default/Normal),
   sondern ein eigener, 27 Slots großer Pool mit `_ultra`-Tier — der stärkste Einzelwert
   im gesamten Datensatz (207 840 HP, `attack_level_8_id_1_ultra`).
5. **`rules.extraWaves` ("stronger_attack") ist kein Hard-Feature** — Pool ist identisch
   zu Normal, ausgelöst durch ein Event im Code, nicht durch Difficulty-Wahl.
6. **Hard gibt mehr Vorbereitungszeit** (420s vs. 360s), obwohl die Wellen stärker sind.
7. **`maxAttackCountPerDifficulty` ist bei Level 4–5 niedriger** (2 statt 3 Wellen/Runde).
8. **`creatureDifficultyIncrementPerDOMDifficulty` unterscheidet sich numerisch**
   zwischen Default und Hard — Mechanik bewusst nicht entschlüsselt (offener Punkt).

---

## 10 · Offene Punkte

1. **`creatureDifficultyIncrementPerDOMDifficulty`-Mechanik** (§7d): welche Semantik die
   Werte haben (additiver HP-Bonus? Prozent? Spawn-Chance?) und wie die 4 Teiltabellen
   `[1]`–`[4]` (vermutlich Spieleranzahl) ausgewählt werden — offen, wie in #213/#736.
2. **Gemeinsame Normierung der Carbonium-Kurven** über Difficulties hinweg (§8) — für
   #205 zu klären, falls Sends difficulty-übergreifend gleich bepreist werden sollen.
3. **Easy/Brutal** wurden für dieses Dokument nicht ausgewertet — gleiche Methodik wäre
   direkt übertragbar, falls benötigt.
4. **Biom-Varianten weiterhin außerhalb des Scopes** (wie #658/#736), jetzt zusätzlich
   auch für die neuen Alpha/Ultra-Dateien.

**Ref:** #736 · #699 · #658 · #213 (`docs/research/213-wave-richtwert.md`) · #205.
