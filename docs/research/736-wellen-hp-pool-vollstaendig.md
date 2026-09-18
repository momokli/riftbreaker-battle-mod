# Wellen-HP vollständig — Pool-Range je Level (Issue #736, löst #658/#699 auf, aktualisiert #213 §2)

**Issue:** #736 (Spike) · **Vorarbeit:** #658 (Pool-Rohdaten, ohne `units_in_one_spawn`), #699
(Live-Diskrepanz, ausgelöst), #646 (Rohdaten-Methodik) · **Bezugs-Formel:** #213
(`docs/research/213-wave-richtwert.md`) · **Ziel-Issue:** #205
**Stand:** 2026-09-18 · **Spiel-Build:** Pack `00_win_data.zip` (lokal, `/srv/rift-local/game/packs`)

**Kern:** `count` im `Enemy`-FlowGraph-Node ist **nicht** die tatsächliche
Einzeleinheiten-Zahl einer Welle. Es existiert ein zusätzliches, in #658 übersehenes
Feld `units_in_one_spawn`. Echte Einzeleinheiten-Zahl je Enemy-Block:

```
Einheiten(Enemy-Block) = count × units_in_one_spawn
```

Dieses Dokument summiert das für **alle** Non-Biom-`id_*`-Pool-Varianten in Level 1–8
(Level 9 hat keine eigene Non-Biom-Basisdatei — teilt sich den Pool mit Level 8, siehe
#213 §2) und liefert damit die vollständige Einheiten- **und** HP-Spanne je Welle,
nicht nur einen `id_1`-Stichprobenwert.

> Reine Recherche/Datenermittlung (kein Feature, keine Preisformel). Für die
> #205-Preisformel ist dieses Dokument die aktualisierte Datenbasis zu #213 §2–§4.

---

## 1 · Datenquellen

| Quelle | Pfad / Zugriff | Rolle |
| --- | --- | --- |
| Wellen-Zusammensetzung, alle Pool-Varianten | `tools/re/rbpack.py cat logic/missions/survival/attack_level_<L>_id_<N>.logic` (Pack `00_win_data.zip`) | Vollständiger `Enemy`-Node-Block je Welle (nicht nur `blueprint`+`count`) |
| Spawn-Multiplikator | Feld `units_in_one_spawn` im `Enemy`-Node (bisher nicht ausgelesen, #736) | Einzeleinheiten pro Spawn-Tick |
| Basis-HP je Unit | `tools/re/rbpack.py cat entities/units/ground/<name>.ent` → `HealthDesc.max_health` | Stärke-Proxy (wie #213 §3) |
| Blueprint-Einzelcheck | `entities/units/ground/canoptrix.ent` | Bestätigt: kein `squad`/`pack`/`formation`-Feld — Einzeleinheit, kein Gruppen-Blueprint |
| Pool-Größe je Level | `docs/research/213-wave-richtwert.md` §2 (aus `dom_survival_jungle_rules_default.lua:263`) | Wie viele `id_*`-Varianten je Level existieren |

**Abdeckung:** Alle 30 gefundenen Non-Biom-Dateien `attack_level_{1..8}_id_{1..5}.logic`
(Biom-Varianten `_acid/_desert/_swamp/…` und `_alpha`/`_ultra` bewusst außerhalb des
Scopes, wie in #658 festgelegt).

---

## 2 · Basis-HP je Unit-Typ (`.ent`, unverändert zu #213 §3, plus 3 neue Typen aus #658)

| Unit-Typ | `max_health` |
| --- | --- |
| `units/ground/canoptrix` | **5** |
| `units/ground/arachnoid_sentinel` | **110** |
| `units/ground/kafferroceros` | **300** |
| `units/ground/bomogan` | **250** |
| `units/ground/baxmoth` | **1000** |
| `units/ground/gnerot` | **2000** |

`units_in_one_spawn` selbst ist **keine feste Konstante je Unit-Typ** — sie variiert je
Welle/Level für denselben Typ (z. B. canoptrix: 30/50/100 je nach Datei). Der
Multiplikator steckt im FlowGraph-Node, nicht im Blueprint.

---

## 3 · Einheiten pro Welle — vollständiger Pool, Level 1–8

Formel: `Einheiten Σ (Datei) = Σ über alle Enemy-Blöcke von count × units_in_one_spawn`.

| Level | Pool | Varianten (blueprint × Einheiten Σ je Blueprint) | Einheiten Σ (Datei) |
| --- | --- | --- | --- |
| 1 | 2 | id_1: canoptrix×100 · id_2: canoptrix×240 | 100 / 240 |
| 2 | 2 | id_1: arachnoid×5, canoptrix×240 · id_2: arachnoid×15, canoptrix×120 | 245 / 135 |
| 3 | 3 | id_1: arachnoid×18, canoptrix×150, kaffer×5 · id_2: kaffer×12 · id_3: gnerot×2 | 173 / 12 / 2 |
| 4 | 4 | id_1: 191 · id_2: canoptrix×300+kaffer×21 · id_3: gnerot×2 · id_4: bomogan×6+canoptrix×150+kaffer×17 | 191 / 321 / 2 / 173 |
| 5 | 4 | id_1: 303 · id_2: 483 · id_3: gnerot×4 · id_4: 289 | 303 / 483 / 4 / 289 |
| 6 | 5 | id_1: 513 · id_2: 495 · id_3: gnerot×5 · id_4: 504 · id_5: baxmoth×2+canoptrix×450+kaffer×39 | 513 / 495 / 5 / 504 / 491 |
| 7 | 5 | id_1: 684 · id_2: 663 · id_3: gnerot×8 · id_4: 678 · id_5: 657 | 684 / 663 / 8 / 678 / 657 |
| 8 | 5 | id_1: 690 · id_2: 669 · id_3: gnerot×20 · id_4: 684 · id_5: 658 | 690 / 669 / 20 / 684 / 658 |

### Range je Level (Schwarm-Wellen ohne den Solo-Gnerot-Ausreißer / inkl. diesem)

| Level | Schwarm-Range (Ø) | inkl. Solo-Gnerot-Range (Ø) |
| --- | --- | --- |
| 1 | 100–240 (170) | 100–240 (170) |
| 2 | 135–245 (190) | 135–245 (190) |
| 3 | 12–173 (92) | **2**–173 (62) |
| 4 | 173–321 (228) | **2**–321 (172) |
| 5 | 289–483 (358) | **4**–483 (270) |
| 6 | 491–513 (501) | **5**–513 (402) |
| 7 | 657–684 (670) | **8**–684 (538) |
| 8 | 658–690 (675) | **20**–690 (544) |

**Befund:** Über die Schwarm-Wellen skaliert die Einheitenzahl gleichmäßig um etwa das
**3-fache** von Level 1 (~100–240) zu Level 8 (~660–690). Die Solo-Gnerot-Variante
(genau 1 `Enemy`-Block, nur `gnerot`) ist in jedem Level-Pool ab Level 3 vorhanden und
zieht die reine Zähl-Range massiv nach unten (2–20 Einheiten).

---

## 4 · HP pro Welle — vollständiger Pool, Level 1–8

Formel: `HP Σ (Datei) = Σ über alle Enemy-Blöcke von count × units_in_one_spawn × max_health(blueprint)`.

| Level | Varianten (blueprint × HP Σ je Blueprint) | HP Σ (Datei) |
| --- | --- | --- |
| 1 | id_1: canoptrix×100(500) · id_2: canoptrix×240(1200) | 500 / 1200 |
| 2 | id_1: arachnoid×5(550)+canoptrix×240(1200) · id_2: arachnoid×15(1650)+canoptrix×120(600) | 1750 / 2250 |
| 3 | id_1: arachnoid×18(1980)+canoptrix×150(750)+kaffer×5(1500) · id_2: kaffer×12(3600) · id_3: gnerot×2(4000) | 4230 / 3600 / 4000 |
| 4 | id_1: 8490 · id_2: canoptrix×300(1500)+kaffer×21(6300) · id_3: gnerot×2(4000) · id_4: bomogan×6(1500)+canoptrix×150(750)+kaffer×17(5100) | 8490 / 7800 / 4000 / 7350 |
| 5 | id_1: 12590 · id_2: 12150 · id_3: gnerot×4(8000) · id_4: 12450 | 12590 / 12150 / 8000 / 12450 |
| 6 | id_1: 16590 · id_2: 15750 · id_3: gnerot×5(10000) · id_4: 17700 · id_5: baxmoth×2(2000)+canoptrix×450(2250)+kaffer×39(11700) | 16590 / 15750 / 10000 / 17700 / 15950 |
| 7 | id_1: 22500 · id_2: 21900 · id_3: gnerot×8(16000) · id_4: 25200 · id_5: 22200 | 22500 / 21900 / 16000 / 25200 / 22200 |
| 8 | id_1: 23160 · id_2: 23700 · id_3: gnerot×20(**40000**) · id_4: 26700 · id_5: 23200 | 23160 / 23700 / 40000 / 26700 / 23200 |

### HP-Range je Level (Schwarm-Wellen / inkl. Solo-Gnerot-Ausreißer)

| Level | Schwarm-HP-Range (Ø) | inkl. Solo-Gnerot (Ø) |
| --- | --- | --- |
| 1 | 500–1200 (850) | 500–1200 (850) |
| 2 | 1750–2250 (2000) | 1750–2250 (2000) |
| 3 | 3600–4230 (3915) | 3600–4230 (3943) |
| 4 | 7350–8490 (7880) | **4000**–8490 (6910) |
| 5 | 12150–12590 (12397) | 8000–12590 (11298) |
| 6 | 15750–17700 (16498) | 10000–17700 (15198) |
| 7 | 21900–25200 (22950) | 16000–25200 (21560) |
| 8 | 23160–26700 (24190) | 23160–**40000** (27352) |

**Kipp-Punkt Level 8:** Bei Level 3–7 ist die Solo-Gnerot-Variante durchweg die
HP-**schwächste** Welle im Pool (z. B. Level 3: 4000 HP vs. bis zu 4230 HP Schwarm).
Bei Level 8 kehrt sich das um — `gnerot`-`count` wächst weiter (20 statt 2), die HP je
Gnerot bleibt konstant bei 2000 → die Solo-Gnerot-Welle wird mit **40000 HP** zur
HP-**stärksten** Welle im gesamten Pool, trotz nur 20 Einzeleinheiten.

**Gesamtspanne über alle Level 1–8:** 500 HP (Level 1, id_1) bis 40000 HP (Level 8,
Solo-Gnerot) — Faktor **80×**. Ohne den Gnerot-Sonderfall: 500–1200 (Level 1) bis
23160–26700 (Level 8) — Faktor **~20–25×**, deutlich gleichmäßiger als die #213-Formel
(§4, dort nur `id_1`, Faktor 100→135600 durch fehlenden `units_in_one_spawn`-Multiplikator
massiv verzerrt).

---

## 5 · Durchschnittliche HP pro Welle

Zwei unterschiedliche Mittelwerte sind relevant, je nach Frage:

### 5a. Ø HP **pro Einzeleinheit** innerhalb einer Welle (`HP Σ / Einheiten Σ`)

Zeigt, wie "zäh" die Welle im Schnitt pro Gegner ist — unabhängig von der Wellengröße.

| Level | id_1 | id_2 | id_3 | id_4 | id_5 |
| --- | --- | --- | --- | --- | --- |
| 1 | 5.0 | 5.0 | — | — | — |
| 2 | 7.1 | 16.7 | — | — | — |
| 3 | 24.5 | 300.0 | **2000.0** | — | — |
| 4 | 44.5 | 24.3 | **2000.0** | 42.5 | — |
| 5 | 41.6 | 25.2 | **2000.0** | 43.1 | — |
| 6 | 32.3 | 31.8 | **2000.0** | 35.1 | 32.5 |
| 7 | 32.9 | 33.0 | **2000.0** | 37.2 | 33.8 |
| 8 | 33.6 | 35.4 | **2000.0** | 39.0 | 35.3 |

Die Solo-Gnerot-Variante (`id_3`) liegt konstant bei 2000 HP/Einheit (logisch — sie
besteht nur aus Gnerot), während die Schwarm-Wellen bei ~5–45 HP/Einheit liegen. Die
Schwarm-Wellen selbst pendeln sich ab Level 6 bei ~32–39 HP/Einheit ein (Mischung aus
canoptrix/kafferroceros/arachnoid_sentinel wird ab dann relativ stabil).

### 5b. Ø HP **je Level-Pool** (Mittelwert der Wellen-Sigma über alle Pool-Varianten, inkl. Solo-Gnerot — relevant für die erwartete HP-Last bei zufälligem Pool-Wurf)

| Level | Ø Einheiten (Pool) | Ø HP Σ (Pool) | Ø HP/Einheit (Pool-gewichtet) |
| --- | --- | --- | --- |
| 1 | 170.0 | 850.0 | 5.00 |
| 2 | 190.0 | 2000.0 | 10.53 |
| 3 | 62.3 | 3943.3 | 63.26 |
| 4 | 171.8 | 6910.0 | 40.23 |
| 5 | 269.8 | 11297.5 | 41.88 |
| 6 | 401.6 | 15198.0 | 37.84 |
| 7 | 538.0 | 21560.0 | 40.07 |
| 8 | 544.2 | 27352.0 | 50.26 |

Das ist der Wert, den ein Zufalls-Pool-Wurf **im Erwartungswert** liefert (jede
`id_*`-Variante gleich wahrscheinlich) — im Gegensatz zu §4, wo nur die Bandbreite
(min/max) steht.

---

## 6 · Kernbefunde (Zusammenfassung)

1. **`count` ≠ Einzeleinheiten.** Echte Formel: `count × units_in_one_spawn`. Löst
   #736 (Hypothese bestätigt) und erklärt die #699-Live-Diskrepanz (8 Gruppen à ≥15
   canoptrix = 2 Enemy-Blöcke mit `count=4`/`units_in_one_spawn=30` in
   `attack_level_1_id_2.logic`, exakt deckungsgleich).
2. **Kein Squad-Blueprint.** `canoptrix.ent` hat kein `squad`/`pack`/`formation`-Feld —
   die Vervielfachung sitzt ausschließlich im FlowGraph-`Enemy`-Node.
3. **`units_in_one_spawn` ist keine Unit-Konstante**, sondern variiert je Welle
   (canoptrix: 30/50/100; kafferroceros: 3/5; arachnoid_sentinel: 3/5; gnerot: 1/2;
   bomogan: 2/3; baxmoth: konstant 1).
4. **#658-„Einheiten Sigma"-Werte waren systematisch zu niedrig** (fehlender
   Multiplikator) — reale Einheitenzahlen liegen um das **10- bis 30-fache** höher
   als die reinen `count`-Summen aus #658.
5. **Die #213-Richtwert-Kurve (§4, nur `id_1`) ist ebenfalls betroffen** — mit
   korrigierten Werten ist die Kurve über die Schwarm-Wellen deutlich gleichmäßiger
   (Faktor ~20–25× L1→L8) als die alte, `id_1`-only-Kurve suggerierte.
6. **Solo-Gnerot-Sonderfall kippt bei Level 8**: von der HP-schwächsten zur
   HP-stärksten Wellenvariante im Pool, weil `count(gnerot)` skaliert, aber
   `max_health(gnerot)` konstant bleibt.

---

## 7 · Bezug zu #213/#658/#699 — was sich ändert

- **#213 §2** (Einheiten pro Naturwelle, nur `id_1`): **ersetzt** durch §3 hier
  (vollständiger Pool, korrigierter Multiplikator). Die dortige Zeile
  „Level 1: canoptrix ×1 → Einheiten Σ 1" wird durch „Level 1, id_1:
  canoptrix ×100" ersetzt.
- **#213 §4** (Richtwert-Formel, HP-gewichtet): Eingabewerte (`W_raw(L)`) müssen mit
  den §4-Zahlen hier neu berechnet werden — die alte Tabelle basiert auf
  `count`-Summen ohne `units_in_one_spawn` und ist damit **nicht mehr gültig als
  Datenbasis** (die Formel-Logik selbst bleibt unberührt).
- **#658**: Die dort geposteten „Einheiten Sigma"-Tabellen bleiben als Rohdaten zu
  `count` gültig, sind aber **keine** tatsächlichen Einheitenzahlen — dieses Dokument
  liefert die korrigierten Ist-Werte.
- **#699**: Live-Diskrepanz ist durch §3/§6 Punkt 1 erklärt (nicht mehr offen).

---

## 8 · Carbonium-Kostenkurven — 2 Varianten, Level 1 = 100 (Folge-Rechnung für #205)

Auf Basis der §5b-Pool-Durchschnitte (`Ø HP Σ`, inkl. Solo-Gnerot-Ausreißer), normiert
auf `Kosten(1) = 100`. Zwei Varianten, beide HP-basiert (keine reine Zähl-Kurve —
die würde HP komplett ignorieren und beim Level-3-Solo-Gnerot-Einbruch einbrechen,
siehe verworfener Zwischenstand in der #736-Konversation):

```
Normal(L)    = 100 · Ø HP Σ(L) / Ø HP Σ(1)                        (linear zur HP-Last)
Gedaempft(L) = Normal(L) · (1 − ⅓ · t²),   t = (L−1) / 7           (quadratische Dämpfung)
```

Die gedämpfte Kurve ist bewusst so konstruiert, dass sie über die ersten Level fast
deckungsgleich mit der normalen Kurve läuft und erst spät spürbar abweicht — der
Dämpfungsfaktor wächst quadratisch mit dem Level, damit `Gedaempft(8) = ⅔ · Normal(8)`.
Alle Werte auf die Zehnerstelle gerundet.

| Level | Normal (linear zu HP) | Gedämpft (Ende ≈ ⅔) |
| --- | --- | --- |
| 1 | 100 | 100 |
| 2 | 240 | 230 |
| 3 | 460 | 450 |
| 4 | 810 | 760 |
| 5 | 1330 | 1180 |
| 6 | 1790 | 1480 |
| 7 | 2540 | 1920 |
| 8 | 3220 | 2150 |

**Caveat:** Eine dritte, sub-lineare (`√HP`) Kurve wurde im Rahmen dieser Recherche
ebenfalls durchgerechnet, aber verworfen (zu große/zu früh einsetzende Abweichung von
der normalen Kurve — Level 8 landete bei nur 570, ca. ⅙ des Normalwerts). Für #205
sind aktuell nur die zwei obigen Kurven vorgesehen; eine dritte Variante ist offen und
braucht eine konkrete Formel-Vorgabe, bevor sie nachgerechnet wird.

---

## 9 · Offene Punkte / braucht Live-Test

1. **Kreaturen-Stärke-Skalierung zur Laufzeit** (`creatureDifficultyIncrementPerDOMDifficulty`,
   #213 §3) ist in den `.ent`-Basiswerten hier weiterhin nicht enthalten.
2. **Biom-Varianten** (`_acid/_desert/_swamp/…`) und `_alpha`/`_ultra`-Varianten sind
   bewusst außerhalb des Scopes (wie #658/#736) — falls diese im aktuellen Spielmodus
   relevant werden, braucht es einen Folge-Spike.
3. **Level 9** teilt sich weiterhin den Pool mit Level 8 (unverändert zu #213 §2).
4. **#213-Richtwert-Formel-Neuberechnung** mit den hier korrigierten `W_raw`-Werten ist
   bewusst **nicht** Teil dieses Dokuments (reine Datenermittlung) — gehört in einen
   #205-Folgeschritt.
5. **Dritte Carbonium-Kostenkurve** (§8): Formel-Vorgabe steht noch aus.

**Ref:** #736 · #699 · #658 · #646 · #213 (`docs/research/213-wave-richtwert.md`) · #205.
