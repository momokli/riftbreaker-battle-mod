# Wellen-Richtwert — Datenbasis & Formel (Issue #213, Baseline für #205)

**Issue:** #213 (Recherche, Milestone 1.0) · **Input:** Design-Interview #199 (ECO-6) · **Ziel-Issue:** #205
**Stand:** 2026-09-16 · **Spiel-Build:** 2.0.58485 (GOG == Dedi, byte-identisch)
**Kern:** Der %-Boost-Preis für Sends soll nicht flach sein, sondern an einem
**Richtwert pro Naturwelle** hängen: eine feste Calcium-Menge kauft einen festen
**absoluten** Richtwert-Zuwachs, die resultierende %-Verstärkung schrumpft dadurch
automatisch, je größer/später die Welle ist (Zitat ECO-6, s. #213).

> Reine Recherche/Datenermittlung (kein Feature). Die eigentliche Preisformel wird
> in #205 implementiert, nicht hier.

---

## 0 · Ablage-Hinweis (Abweichung vom Issue-Text)

Das Issue nennt als Ablageort `docs/GAME_DESIGN.md`. Diese Datei existierte bis
Commit `ca58561` („docs: drop legacy Lua-mod design…“, Green-Field-Refactor
#378/#380) und wurde dort **absichtlich entfernt** — zusammen mit `mod/README.md`
und `ROADMAP.md`. Auch das frühere #213-Ergebnis (PR #216, Commit `d7c0e13`)
lag in `docs/GAME_DESIGN.md` und ist damit aus dem Baum verschwunden; nur der
Git-Verlauf hält es noch. Die aktuelle, etablierte Ablage für Wellen-/Send/RE-
Recherche ist `docs/research/` (Muster: `docs/research/508-catalog-of-things.md`,
das die offene Calcium-Formel ausdrücklich als #205-Punkt führt, §5.2 und §7.3.4).

Dieses Dokument etabliert die #213-Recherche deshalb in `docs/research/` neu und
macht sie selbst-tragend (die früher in `GAME_DESIGN.md` referenzierten
Baseline-Werte #33 sind unten mit Quelle aus dem Git-Verlauf wiederholt).

---

## 1 · Datenquellen

| Quelle | Pfad / Zugriff | Rolle |
| --- | --- | --- |
| Wellen-Pool je Difficulty-Level | `/home/momo/rb-game/lua-src/lua/missions/survival/v2/dom_survival_jungle_rules_default.lua:263` (`rules.waves`) | Welche `.logic`-Wellen je Level gepoolt sind |
| Angriffe je Level | ebd. `:228` (`rules.maxAttackCountPerDifficulty`) + `_normal.lua` | Wie viele Wellen pro Attack-Runde |
| Konkrete Wellen-Zusammensetzung | `tools/re/rbpack.py cat logic/missions/survival/attack_level_<N>_id_1.logic` (Pack `00_win_data.zip`) | Blueprint + `count` je Welle (echte Spieldaten) |
| Stärke je Einheit | `tools/re/rbpack.py cat entities/units/ground/<name>.ent` → `HealthDesc.max_health` | Basis-HP je Unit-Typ (echte Spieldaten) |
| Spawner-Struktur | `docs/PLAYTEST_1.0.md:398` (`event=wave_spawners count=<N>`) | 16 Rand-Spawner in 4 Gruppen |
| v1-Preisliste + HQ-HP-Kurve (#33) | Git-Verlauf: `clanker-git show da99b89:docs/GAME_DESIGN.md` (bzw. `ca58561^:docs/GAME_DESIGN.md`) | Repo-etablierter Wert-Proxy (Mod-Units) |
| Mod-Wellenkomposition (`rb_wave`) | `bausteine/01-wave-spawn/...autoexec.lua` (`RBB.waves`), `bausteine/05-economy-loop/...` (`killPoints`) | Repo-eigene Platzhalter-Wellen |

**Methodik:** Die `.logic`-Dateien sind Text (FlowGraph v5) und über
`tools/re/rbpack.py cat` lesbar (zip64-sicher) — belegt in
`docs/research/508-catalog-of-things.md` §0/§1.4. Alle Zahlen unten sind daraus
extrahiert, nicht geschätzt. Nicht-Extrahierbares ist explizit markiert.

---

## 2 · Einheiten pro Naturwelle (Quelle: `.logic`, echte Spieldaten)

Wellen-Pool des `default`-Bioms, je Difficulty-Level `L` (1..9) — `rules.waves["default"][L]`.
Spalte „Pool" = Anzahl der wählbaren `.logic`-Varianten (DOM würfelt je Welle eine).

| Level | Pool (`attack_level_*_id_*`) | id_1-Zusammensetzung (blueprint × count) | Einheiten Σ |
| --- | --- | --- | --- |
| 1 | 2 | canoptrix ×1 | **1** |
| 2 | 2 | arachnoid_sentinel ×1, canoptrix ×8 | **9** |
| 3 | 2 | kafferroceros ×1, arachnoid_sentinel ×6, canoptrix ×3 | **10** |
| 4 | 3 | kafferroceros ×5, arachnoid_sentinel ×8, canoptrix ×3 | **16** |
| 5 | 4 | kafferroceros ×9, arachnoid_sentinel ×8, canoptrix ×5 | **22** |
| 6 | 5 | kafferroceros ×13, arachnoid_sentinel ×8, canoptrix ×9 | **30** |
| 7 | 5 | kafferroceros ×18, arachnoid_sentinel ×10, canoptrix ×12 | **40** |
| 8 | 5 | kafferroceros ×18, arachnoid_sentinel ×12, canoptrix ×12 | **42** |
| 9 | 5 | = Pool aus Level 8 (`attack_level_8_id_1..5`) → id_1 wie L8 | **42** |

**Caveats (belegt):**
- Gezählt ist jeweils `id_1` (repräsentativ, nicht die einzige Variante) — DOM wählt
  zufällig aus dem Pool der Stufe. Andere `id_*` haben abweichende Mischungen;
  eine Voll-Inventarisierung (die ~1078 `attack_level*`-Member unter
  `logic/missions/survival/`, inkl. Biome-/DLC-Varianten `_acid/_desert/_swamp/…` und `_alpha/_ultra`) ist ein
  möglicher Folge-Spike.
- Level 9 teilt sich die Wellen-Definition mit Level 8 (`rules.waves[9]` verweist auf
  `attack_level_8_id_*`); die Eskalation ab L9 kommt über `maxAttackCountPerDifficulty = 4`.
- „16 Rand-Spawner / 4 Gruppen" ist **kein** Multiplikator der Zusammensetzung: DOM
  wählt je Welle 1 Gruppe + 1 Spawnpunkt; die `.logic` liefert die Mischung
  (`docs/SEND_HOOK.md`, `docs/research/508-catalog-of-things.md` §1.4).

---

## 3 · Stärke pro Einheit (Quelle: `.ent`, echte Spieldaten)

Stärke-Proxy je Typ = **Basis-HP** (`HealthDesc.max_health`). Reale Naturwellen-Units:

| Unit-Typ | max_health | Beleg |
| --- | --- | --- |
| `units/ground/canoptrix` | **5** | `entities/units/ground/canoptrix.ent` |
| `units/ground/arachnoid_sentinel` | **110** | `entities/units/ground/arachnoid_sentinel.ent` |
| `units/ground/kafferroceros` | **300** | `entities/units/ground/kafferroceros.ent` |

Zum Vergleich die Units des **Mod-Platzhalter-Shops** (`#33`-Preisliste) — gleiche
Methode, aber **anderer Unit-Satz** (nicht die Naturwellen-Units):

| Unit-Typ | max_health | #33-Preis | `killPoints` (bausteine/05) |
| --- | --- | --- | --- |
| `brabit` | 120 | 100 | 1 |
| `baxmoth` | 1000 | 150 | 2 |
| `artigian` | 2000 | 200 | 3 |
| `canceroth` | 4500 | 300 | 5 |
| `boss` | — | 800 | — |

> **Wichtig:** `RBB.shopCfg`/die #33-Preise beziehen sich auf die **Mod-Shop-Units**
> (brabit/baxmoth/…), die Naturwellen bestehen aber aus canoptrix/arachnoid_sentinel/
> kafferroceros. Ein direkter 1:1-Preis-Proxy auf die Naturwelle geht daher **nicht**;
> HP ist die gemeinsame, belegbare Stärke-Einheit. `RBB.shopCfg` selbst ist seit
> #378/#380 aus dem Lua-Mod entfernt (Business-Logik → Backend), die Preisliste ist
> nur noch über den Git-Verlauf (`da99b89`) belegbar.

**Caveat:** Die `.ent`-HP ist der *Basis*-Wert. Zur Laufzeit skaliert die DOM die
Kreaturenstärke zusätzlich über `rules.creatureDifficultyIncrementPerDOMDifficulty`
(`dom_manager.lua:1204` `UpdateCreaturesBaseDifficulty`). Für eine absolute
Richtwert-Kurve müsste dieser Faktor mit einbezogen werden — **braucht Live-Test**
(bzw. Auswertung der Increment-Tabellen je Biom/Difficulty).

---

## 4 · Richtwert-Formel

Definition (ECO-6): Der Richtwert einer Welle ist die Summe der Stärke aller
spawnenden Einheiten.

```
W_raw(L)        = Σ_u  strength(u)  für alle Units u der Welle in Level L
strength(u)     = max_health(u)                       (Basis-HP, §3)

Richtwert(L)    = k · W_raw(L)                        (k = Kalibrier-Konstante)
```

`k` ist frei wählbar — er legt nur die **Skala** fest, nicht die Kurvenform.
Beispiel-Normierung auf `Richtwert(1) = 100` (d. h. `k = 100 / W_raw(1) = 20`):

| Level | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `W_raw` (Σ HP) | 5 | 150 | 975 | 2 395 | 3 605 | 4 825 | 6 560 | 6 780 | 6 780 |
| `Richtwert` (k=20, L1=100) | 100 | 3 000 | 19 500 | 47 900 | 72 100 | 96 500 | 131 200 | 135 600 | 135 600 |
| Einheiten Σ (Zähl-Proxy) | 1 | 9 | 10 | 16 | 22 | 30 | 40 | 42 | 42 |

**Angriffe je Runde:** DOM setzt pro Difficulty-Level `maxAttackCountPerDifficulty[L]`
Wellen ab. Default `[1,2,2,3,3,3,3,3,4]`, `_normal`-Variante `[1,2,2,2,2,2,3,3,3]`
(`dom_survival_jungle_rules_default.lua:228`, `_normal.lua`). Richtwert pro
Attack-**Runde** = `attackCount(L) · Richtwert(L)` (Default: 100 → 6 000 → 39 000 →
… → 542 400 bei L9).

**Befund:** HP-gewichtet ist die Kurve extrem steil (L1→L2 ≈ ×30), weil (a) die HP
der Typen weit auseinanderliegen (5 vs. 110 vs. 300) und (b) die Mischung ab L3 auf
die teuren Typen umschwenkt. Der Preis-/`killPoints`-Proxy der Mod-Units wäre flacher,
passt aber nicht auf die Naturwellen-Units (§3). Für #205 ist daher zu entscheiden:
HP-gewichtet (physikalisch treu, aber steil), Zähl-basiert (robust, aber ignoriert
Typwechsel), oder eine sub-lineare Gewichtung (z. B. `strength = sqrt(max_health)`).
**Offene Design-Entscheidung — gehört in #205.**

---

## 5 · Calcium → Richtwert (Umrechnungsfaktor)

ECO-6: eine feste Calcium-Menge = ein fester **absoluter** Richtwert-Zuwachs,
unabhängig von der Welle. Die %-Verstärkung folgt dann relativ zur Welle:

```
Richtwert-Zuwachs   = calcium · calciumPerRichtwert      (Faktor)
%-Verstärkung(Welle) = Richtwert-Zuwachs / Richtwert(L) · 100
```

**Platzhalter-Konstante (braucht Live-Test):** `calciumPerRichtwert = 1`
(1 Calcium = 1 Richtwert-Punkt; ECO-6-Beispiel „100 Calcium = +100 Richtwert").

Mit der Normierung aus §4 (L1=100):

| Calcium | L1 (100) | L3 (19 500) | L5 (72 100) | L8 (135 600) |
| --- | --- | --- | --- | --- |
| 100 | +100 % | +0,51 % | +0,14 % | +0,074 % |

**Genau das ist gewünscht** (Calcium relativ wertloser, je später/größer die Welle).
Der **absolute** Faktor `calciumPerRichtwert` ist willkürlich skaliert (hängt an `k`
aus §4) und **braucht Live-Test** — kalibriert wird er über #205, gestützt auf die
tatsächliche Ökonomie (Calcium-Zufluss je Runde, #40).

---

## 6 · Bezug zum Repo-Establishment (und was das frühere #213-Ergebnis war)

Das frühere, gemergte #213-Ergebnis (PR #216 / `d7c0e13`, dann via `ca58561` gelöscht)
dokumentierte eine **reine Platzhalter-Formel** `Richtwert(level) = 100 · level`, weil
die Wellen-Zusammensetzung „ohne Live-Spiel/RE nicht enumerierbar" schien. Dieses
Dokument **ersetzt die Annahme durch echte Spieldaten** (§2/§3) — die `.logic`-Mischungen
und `.ent`-HP sind ohne Live-Spiel aus dem Pack lesbar.

Früherer Mess-Fallback aus #216 (bleibt gültig als Live-Gegenprobe): Instrumentierung
des Chokepoints `SpawnWavesForDifficultyLevel` loggt `event=richtwert_sample
level=%d before=%d after=%d delta=%d` sowie `event=richtwert_sample_types …`. Damit
lässt sich am laufenden Spiel prüfen, ob die hier dokumentierte Statik
(Level → Menge/Typen) der Spawn-Realität entspricht.

Ebenfalls betroffen (aus dem gelöschten `GAME_DESIGN.md`, hier mit Quelle gesichert):
- **HQ-HP-Kurve (#33):** `maxHp(r) = 100 + 20 · min(r-1, 4)` — Muster für „Formel + Cap".
- **#33-Preisliste:** s. §3 (Mod-Units, 100/150/200/300/800).
- **Send-Boost (#39):** `boostCfg.pricePerPct` = linear, wird durch #205 ersetzt.

---

## 7 · Offene Punkte / braucht Live-Test

1. **Stärke-Definition** (§4): HP-gewichtet vs. zähl-basiert vs. sub-linear — **Design-Entscheidung für #205** (Kurven-Steilheit).
2. **Kalibrier-Konstante `k` / `calciumPerRichtwert`** (§4/§5): absoluter Faktor willkürlich, braucht Ökonomie-Kalibrierung (#40/Live-Test).
3. **Kreaturen-Stärke-Skalierung** (§3): `creatureDifficultyIncrementPerDOMDifficulty` ist in der Basis-HP **nicht** enthalten — bei absoluter Kurve nachziehen.
4. **Pool-Vollständigkeit** (§2): nur `id_1` je Level ausgewertet; andere `id_*` und Biome-/`_alpha`/`_ultra`-Varianten offen.
5. **Live-Gegenprobe:** `event=richtwert_sample*` (aus #216) gegen die Statik prüfen.
6. **Level-9-Ausreißer:** teilt Wellen-Definition mit L8; Eskalation nur über `attackCount=4`.

**Ref:** #213 · #199 (ECO-6) · #205 (Send-Mechanik vereinfachen) · #33 (Balance-Formeln) ·
#39/#40/#41 · `docs/research/508-catalog-of-things.md` · `docs/SEND_HOOK.md` ·
PR #216 (früheres Ergebnis) · Commit `ca58561` (Doku-Entfernung).
