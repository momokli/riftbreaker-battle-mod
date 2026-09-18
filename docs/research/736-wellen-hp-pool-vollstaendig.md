# Wellen-Einheiten & HP — vollständiger Pool, Level 1–8 (Issue #736)

**Issue:** #736 (Spike, ausgelöst durch #699) · **Löst:** #658 (Sigma-Werte zu niedrig) ·
**Ziel-Issue:** #205
**Stand:** 2026-09-18 · **Quelle:** `tools/re/rbpack.py cat` gegen `00_win_data.zip`, alle
Non-Biom `attack_level_{1..8}_id_*.logic` im `logic/missions/survival/`-Pool
(30 Dateien; Level 9 hat keine eigene Non-Biom-Basisdatei im Pack, siehe #658).

> Reine Rohdaten-Korrektur + Neuberechnung der #658-Summen. Die eigentliche
> Kosten-Formel bleibt #205 vorbehalten.

## 0 · Die Frage, die diesen Spike ausgelöst hat

Matheo sendete über das Cockpit eine Level-1-Welle (`attack_level_1_id_1.logic`,
fest, kein Zufalls-Pool) und beobachtete **8 Gruppen von je mindestens 15
canoptrix** — obwohl #658s Rohdaten für genau diese Datei nur `canoptrix × 1`
auswiesen. Ursprüngliche Hypothese (#736): `count` könnte "Packs" statt
Einzel-Einheiten zählen.

## 1 · Root Cause: `units_in_one_spawn`, ein bisher übersehenes Feld

`#658` hat aus jedem `Enemy`-Node im FlowGraph nur `blueprint` + `count`
extrahiert. Der vollständige Node hat mehr Felder:

```
Enemy
{
    blueprint "units/ground/canoptrix"
    count "1"
    delay "1.00"
    delay_group "0"
    units_in_one_spawn "100"
}
```

`count` ist die Anzahl der **Spawn-Ticks** (Wiederholungen des Spawn-Ereignisses),
`units_in_one_spawn` die Anzahl der **Einzeleinheiten pro Tick**. Die tatsächliche
Einzeleinheiten-Summe eines `Enemy`-Blocks ist:

```
Einheiten(Enemy-Block) = count × units_in_one_spawn
HP(Enemy-Block)        = count × units_in_one_spawn × max_health(blueprint)
```

**Bezug zum Live-Befund:** `attack_level_1_id_2.logic` hat zwei `Enemy`-Blöcke
mit je `count "4"` und `units_in_one_spawn "30"` → 8 Spawn-Ticks gesamt (4+4),
30 Einheiten pro Tick. Das deckt sich exakt mit "8 Gruppen von je mindestens 15
canoptrix" (30 ≥ 15, 8 Ticks = 8 Gruppen). `attack_level_1_id_1.logic` selbst
(`count "1"`, `units_in_one_spawn "100"`) wäre danach 1 Tick × 100 Einheiten —
vermutlich hat der Live-Test also `id_2` getroffen, nicht `id_1`; welche Variante
genau gesendet wurde, ist außerhalb des Scopes dieses Spikes nicht weiter
aufgeklärt.

## 2 · Ursprüngliche Hypothese widerlegt: kein Pack-/Squad-Konzept

`entities/units/ground/canoptrix.ent` enthält **keine** `squad`/`pack`/
`formation`/`group_size`-Felder — nur ein einzelnes `HealthDesc` für eine
Einzeleinheit. Der Blueprint selbst spawnt also eine Einzeleinheit; die
Vervielfachung passiert ausschließlich über `units_in_one_spawn` im
FlowGraph-`Enemy`-Node, nicht im Blueprint.

Ebenfalls widerlegt: `units_in_one_spawn` ist **keine feste Konstante je
Unit-Typ**. Sie variiert pro Welle/`Enemy`-Block:

| Unit-Typ             | beobachtete `units_in_one_spawn`-Werte | Enemy-Blöcke (Level 1–8) |
| --------------------- | --------------------------------------- | ------------------------- |
| `canoptrix`           | 30, 50, 100                             | 67                         |
| `kafferroceros`       | 3, 5                                    | 61                         |
| `arachnoid_sentinel`  | 3, 5                                    | 18                         |
| `gnerot`               | 1, 2                                    | 17                         |
| `bomogan`              | 2, 3                                    | 13                         |
| `baxmoth`              | 1 (konstant)                            | 9                          |

Vollständiger Roh-Dump aller 30 Dateien (`blueprint`/`count`/
`units_in_one_spawn`/`delay`/`delay_group` je `Enemy`-Block): siehe
[Issue #736, Kommentar "Anhang"](https://github.com/momokli/riftbreaker-battle-mod/issues/736#issuecomment-5728513367).

## 3 · Basis-HP je Unit-Typ

Quelle: `entities/units/ground/<name>.ent` → `HealthDesc.max_health` (aus #658
übernommen, unverändert).

| Unit-Typ             | `max_health` |
| --------------------- | ------------- |
| `canoptrix`            | 5             |
| `arachnoid_sentinel`   | 110           |
| `bomogan`               | 250           |
| `kafferroceros`         | 300           |
| `baxmoth`               | 1000          |
| `gnerot`                | 2000          |

## 4 · Einheiten pro Welle — voller Pool (korrigiert)

Schwarm-Wellen vs. die Solo-Gnerot-Sondervariante, die in jedem Pool ab Level 3
steckt (markiert).

| Level | Pool-Größe | Min | Max | Ø (Schwarm) | Ø (inkl. Gnerot) |
| ----- | ---------- | --- | --- | ----------- | ----------------- |
| 1     | 2          | 100 | 240 | 170         | 170                |
| 2     | 2          | 135 | 245 | 190         | 190                |
| 3     | 3          | 2 (Gnerot) | 173 | 92    | 62                 |
| 4     | 4          | 2 (Gnerot) | 321 | 228   | 172                |
| 5     | 4          | 4 (Gnerot) | 483 | 358   | 270                |
| 6     | 5          | 5 (Gnerot) | 513 | 501   | 402                |
| 7     | 5          | 8 (Gnerot) | 684 | 670   | 538                |
| 8     | 5          | 20 (Gnerot) | 690 | 675  | 544                |

**Befund:** über die Schwarm-Wellen wächst die Einheitenzahl gleichmäßig um
etwa das 3-fache von Level 1 (~100–240) zu Level 8 (~660–690).

## 5 · HP pro Welle — voller Pool (korrigiert)

| Level | Min    | Max    | Ø (Schwarm) | Ø (inkl. Gnerot) |
| ----- | ------ | ------ | ----------- | ----------------- |
| 1     | 500    | 1 200  | 850         | 850                |
| 2     | 1 750  | 2 250  | 2 000       | 2 000              |
| 3     | 3 600  | 4 230  | 3 915       | 3 943              |
| 4     | 4 000 (Gnerot) | 8 490  | 7 880 | 6 910       |
| 5     | 8 000  | 12 590 | 12 397      | 11 298             |
| 6     | 10 000 | 17 700 | 16 498      | 15 198             |
| 7     | 16 000 | 25 200 | 22 950      | 21 560             |
| 8     | 23 160 | **40 000 (Gnerot)** | 24 190 | 27 352 |

**Kipp-Punkt Level 8:** bei Level 3–7 ist die Solo-Gnerot-Variante durchweg die
HP-*schwächste* Welle im Pool. Bei Level 8 kehrt sich das um — `count(gnerot)`
wächst weiter (20 statt 2), `max_health` bleibt konstant → die Solo-Gnerot-Welle
wird mit **40 000 HP** zur HP-*stärksten* Welle im gesamten Pool, trotz nur 20
Einzeleinheiten.

## 6 · Durchschnittliche HP pro Welle

### 6.1 Ø HP pro Einzeleinheit innerhalb einer Welle

HP Σ ÷ Einheiten Σ — wie zäh die Welle im Schnitt pro Gegner ist.

| Level | id_1 | id_2 | id_3 (Gnerot) | id_4 | id_5 |
| ----- | ---- | ---- | -------------- | ---- | ---- |
| 1     | 5.0  | 5.0  | —              | —    | —    |
| 2     | 7.1  | 16.7 | —              | —    | —    |
| 3     | 24.5 | 300.0 | 2000.0        | —    | —    |
| 4     | 44.5 | 24.3 | 2000.0         | 42.5 | —    |
| 5     | 41.6 | 25.2 | 2000.0         | 43.1 | —    |
| 6     | 32.3 | 31.8 | 2000.0         | 35.1 | 32.5 |
| 7     | 32.9 | 33.0 | 2000.0         | 37.2 | 33.8 |
| 8     | 33.6 | 35.4 | 2000.0         | 39.0 | 35.3 |

Die Schwarm-Wellen pendeln sich ab Level 6 bei ~32–39 HP/Einheit ein.

### 6.2 Ø HP je Level-Pool (Erwartungswert bei zufälligem Wellen-Wurf)

| Level | Ø Einheiten | Ø HP Σ   | Ø HP / Einheit |
| ----- | ----------- | -------- | --------------- |
| 1     | 170.0       | 850.0    | 5.00             |
| 2     | 190.0       | 2 000.0  | 10.53            |
| 3     | 62.3        | 3 943.3  | 63.26            |
| 4     | 171.8       | 6 910.0  | 40.23            |
| 5     | 269.8       | 11 297.5 | 41.88            |
| 6     | 401.6       | 15 198.0 | 37.84            |
| 7     | 538.0       | 21 560.0 | 40.07            |
| 8     | 544.2       | 27 352.0 | 50.26            |

## 7 · Kernbefunde

1. `count` ≠ Einzeleinheiten. Echte Formel: `count × units_in_one_spawn`.
   Erklärt die #699-Live-Diskrepanz (8 Gruppen à ≥15 canoptrix = exakt 2
   Enemy-Blöcke mit `count=4`/`units_in_one_spawn=30`).
2. Kein Squad-Blueprint: `canoptrix.ent` hat keine `squad`/`pack`/
   `formation`-Felder — die Vervielfachung sitzt ausschließlich im
   FlowGraph-Node.
3. `units_in_one_spawn` ist keine feste Unit-Konstante — sie variiert je Welle
   (canoptrix: 30/50/100, kafferroceros: 3/5, arachnoid: 3/5, gnerot: 1/2,
   bomogan: 2/3, baxmoth: konstant 1).
4. Die #658-"Einheiten-Sigma"-Werte waren systematisch zu niedrig — reale
   Zahlen liegen um das 10- bis 30-fache höher.
5. Die #213-Richtwertkurve (nur `id_1`) ist mit korrigierten Werten deutlich
   gleichmäßiger (Faktor ~20–25× statt ~1300×).
6. Solo-Gnerot-Sonderfall kippt bei Level 8 von HP-schwächster zu
   HP-stärkster Wellenvariante im Pool.

## 8 · Offene Punkte

- Laufzeit-Skalierung (`creatureDifficultyIncrementPerDOMDifficulty`, #646)
  ist in den `.ent`-Basiswerten oben nicht enthalten.
- Biom-Varianten (`_acid`/`_desert`/`_swamp`/…) und `_alpha`/`_ultra` bewusst
  außerhalb des Scopes.
- Level 9 teilt sich weiterhin den Pool mit Level 8 (siehe #658).
- #213-Formel-Neuberechnung mit korrigierten Werten ist ein #205-Folgeschritt,
  nicht Teil dieser Recherche.
- Die #691-Erklär-Seite (PR #692) nutzt noch die alten #658-Zahlen und
  braucht ein Update mit den hier korrigierten Werten (Follow-up).

## Refs

#736 (dieser Spike) · #699 (auslösende Live-Beobachtung) · #658 (ursprüngliche
Rohdaten, ohne `units_in_one_spawn`) · #646 · #213 · #205
