# `additionalWaves` — der dritte, bisher unbekannte Wellen-Kanal

**Vorarbeit:** #736 (Hauptpool `rules.waves`), Hard-/Brutal-Doku, `docs/research/creatures-base-difficulty-mechanik.md`
(Anoryxian-Mechanik), #213, #699, #658
**Stand:** 2026-09-18 · **Spiel-Build:** Pack `00_win_data.zip` (lokal, `/srv/rift-local/game/packs`)

**Kern:** Neben `rules.waves` (Hauptpool, #736/Hard/Brutal) und `rules.extraWaves`
("stronger_attack"-Event, siehe Hard-Doku §6) gibt es einen **dritten, komplett
unabhängigen Wellen-Kanal**: `rules.multiplayerWaves`. Er spawnt einen **zufälligen
Elite-Boss** zusätzlich zur regulären Welle — je nach Spieleranzahl und DOM-Level, auch
im Solo-Modus (entgegen dem eigenen Inline-Kommentar im Spielcode).

---

## 1 · Die Mechanik

### 1a. Die Datenstruktur

`rules.multiplayerWaves[level]` hat pro DOM-Level zwei Felder:

```lua
{
    additionalWaves = <-1|0|1>,
    waves = { { name="logic/missions/survival/attack_boss_dynamic.logic", ... } }
}
```

Für **jedes** DOM-Level 1–9, in **jedem** untersuchten Regelwerk (Default/Normal/Hard/Brutal),
enthält `waves` ausschließlich `attack_boss_dynamic.logic` — nie einen anderen Dateinamen.

### 1b. Der Trigger — mit eigenem, im Code dokumentiertem Kommentar

Quelle: `dom_survival_jungle_rules_default.lua`, Zeile 344 (identisch in Normal/Hard/Brutal übernommen):

```lua
additionalWaves = -1, -- Additional Waves count = 1 + additionalWaves - regardless of
                       -- player number. Multiplayer Additional waves are disabled in
                       -- single player mode. Check dom_mananger:GetMultiplayerAttackCount
                       -- for actual code
```

### 1c. Der tatsächliche Code — weicht vom eigenen Kommentar ab

Quelle: `lua/missions/v2/dom_manager.lua:1116-1125`

```lua
function dom_mananger:GetMultiplayerAttackCount( currentDifficultyLevel )
    local playersCounter = self:GetPlayersCounter()   -- #PlayerService:GetConnectedPlayers(), max 4

    if ( playersCounter > 1 ) then
        return Clamp( self.rules.multiplayerWaves[currentDifficultyLevel].additionalWaves + 1, 0, 1 )
    else
        return Clamp( self.rules.multiplayerWaves[currentDifficultyLevel].additionalWaves, 0, 1 )
    end
end
```

Ausgewertet für die drei möglichen `additionalWaves`-Werte:

| `additionalWaves` | Multiplayer (2+ Spieler): `clamp(x+1, 0, 1)` | Solo (1 Spieler): `clamp(x, 0, 1)` |
| --- | --- | --- |
| `-1` | 0 (kein Extra-Boss) | 0 (kein Extra-Boss) |
| `0` | **1** (Extra-Boss) | 0 (kein Extra-Boss) |
| `1` | **1** (Extra-Boss, gecappt) | **1** (Extra-Boss!) |

**Diskrepanz zum Kommentar:** Der Kommentar behauptet, Multiplayer-Zusatzwellen seien
"im Einzelspieler-Modus deaktiviert" — der tatsächliche Code liefert bei
`additionalWaves = 1` aber auch im Solo-Modus `1`, spawnt den Extra-Boss also **auch
für Einzelspieler**, sobald der Wert auf `1` steht. Roh belegt, nicht interpretiert:
möglicherweise ein veralteter Kommentar, keine weitere Erklärung im Scope dieses Dokuments.

---

## 2 · Schwellenwerte je Regelwerk

| Level | Default (roh) | Normal (roh) | Hard/Brutal |
| --- | --- | --- | --- |
| 1 | -1 | -1 | = Default (kein eigenes Override gefunden) |
| 2 | 0 | -1 | = Default |
| 3 | 0 | -1 | = Default |
| 4 | 0 | -1 | = Default |
| 5 | 0 | 0 | = Default |
| 6 | 1 | 0 | = Default |
| 7 | 1 | 1 | = Default |
| 8 | 1 | 1 | = Default |
| 9 | 1 | 1 | = Default |

Übersetzt in "ab welchem Level bekommt man den Extra-Boss":

| Regelwerk | Multiplayer ab Level | Solo ab Level |
| --- | --- | --- |
| Default | **2** | **6** |
| Normal | **5** | **7** |
| Hard | **2** (erbt Default) | **6** (erbt Default) |
| Brutal | **2** (erbt Default) | **6** (erbt Default) |

**Befund:** Normal (das eigene, ausgewählte Regelwerk `dom_survival_jungle_rules_normal.lua`,
nicht die `default.lua`-Basis) ist bei diesem speziellen Mechanismus **milder** als
Hard/Brutal — die verzögern den Extra-Boss beide bis Level 6/2, weil sie
`rules.multiplayerWaves` **nicht** überschreiben und damit auf Defaults (strengere)
Werte zurückfallen, während Normal die Schwelle explizit nach hinten verschiebt.

---

## 3 · Was genau spawnt: `attack_boss_dynamic.logic`

Diese Datei ist biom-agnostisch — sie listet für **jedes** Biom einen `Enemy`-Block
mit `count "1"`, `units_in_one_spawn "1"` (immer exakt ein Boss):

```
blueprint "boss_acid_random"     -- Acid-Biom
blueprint "boss_caverns_random"  -- Caverns-Biom
blueprint "boss_desert_random"   -- Desert-Biom
blueprint "boss_ice_random"      -- Ice-Biom
blueprint "boss_jungle_random"   -- Jungle-Biom (unser Scope)
blueprint "boss_magma_random"    -- Magma-Biom
blueprint "boss_metallic_random" -- Metallic-Biom
blueprint "boss_swamp_random"    -- Swamp-Biom
blueprint "boss_swamp_random_canceroth"
blueprint "drillgor"
```

Jeder `Enemy`-Block hat zusätzlich `spawn_species_difficulty_min/max/offset "0"` —
Parameter eines nativen "Creature Species"-Zufallssystems (kein einzelner Blueprint,
sondern ein gewichteter Pool, aufgelöst über `.kvp`-Dateien).

---

## 4 · `boss_jungle_random` — der gewichtete Zufalls-Pool

Quelle: `scripts/units/creature_species_elite.kvp`, Abschnitt "RANDOM BIOME BOSSES":

```
CreatureSpecies
{
    species_name  "boss_jungle_random"
    creatures_group
    {
        arachnoid_elite            0.40
        arachnoid_elite_acid       0.10
        arachnoid_elite_cryo       0.10
        arachnoid_elite_energy     0.10
        arachnoid_elite_fire       0.10
        baxmoth_elite              0.40
        baxmoth_elite_acid         0.10
        baxmoth_elite_cryo         0.10
        baxmoth_elite_energy       0.10
        baxmoth_elite_fire         0.10
        gnerot_elite               0.40
        gnerot_elite_acid          0.13
        gnerot_elite_cryo          0.13
        gnerot_elite_fire          0.13
        hammeroceros_elite         0.40
        hammeroceros_elite_acid    0.10
        hammeroceros_elite_cryo    0.10
        hammeroceros_elite_energy  0.10
        hammeroceros_elite_fire    0.10
        krocoon_elite              0.40
        krocoon_elite_acid         0.13
        krocoon_elite_cryo         0.13
        krocoon_elite_fire         0.13
        mudroner_elite_acid        0.20
        mudroner_elite_cryo        0.20
        mudroner_elite_energy      0.20
        mudroner_elite_fire        0.20
        phirian_elite              0.40
        phirian_elite_acid         0.10
        phirian_elite_cryo         0.10
        phirian_elite_energy       0.10
        phirian_elite_fire         0.10
    }
}
```

**7 Kreaturen-Familien, 27 gewichtete Einträge** (Basis-Variante + bis zu 4
Elementar-Varianten acid/cryo/energy/fire). Wichtig: **vier dieser Familien sind neu**
für unsere gesamte Recherche-Reihe — `hammeroceros`, `krocoon`, `mudroner`, `phirian`
kommen in keiner der #736/Hard/Brutal-Wellenzusammensetzungen vor (die dortigen Units
sind `canoptrix`, `kafferroceros`, `arachnoid_sentinel`, `gnerot`, `bomogan`, `baxmoth` —
`hammeroceros` ≠ `kafferroceros`, eigene Kreatur).

---

## 5 · Eine dritte, unabhängige HP-Skalierung

Jede der 7 Familien hat eine **eigene, aber untereinander identische** 11-stufige
`min_difficulty_factor`-Health-Leiter (`extra_unit_modifiers.health`), verifiziert für
alle 7 Familien (`arachnoid_elite`, `baxmoth_elite`, `gnerot_elite`,
`hammeroceros_elite`, `krocoon_elite`, `mudroner_elite_acid`, `phirian_elite`):

| `min_difficulty_factor` | HP (identisch für alle 7 Familien) |
| --- | --- |
| 0 | 2 500 |
| 1 | 5 000 |
| 3 | 8 000 |
| 4 | 12 000 |
| 5 | 16 000 |
| 6 | 20 000 |
| 7 | 25 000 |
| 8 | 32 000 |
| 9 | 40 000 |
| 10 | 50 000 |

Diese HP ist ein `extra_unit_modifiers`-**Override**, nicht die `.ent`-Basis-HP der
darunterliegenden Blueprints (z. B. `units/ground/arachnoid_boss_easy` für Tier 0) —
eine **dritte**, komplett eigenständige Difficulty-Skalierung, neben:
1. der statischen `.ent`-HP der Survival-Wellen-Units (#736/Hard/Brutal),
2. der `CreaturesBaseDifficulty`-Tier-Tabelle des Anoryxian-Bosses
   (`docs/research/creatures-base-difficulty-mechanik.md`).

---

## 6 · Kernbefunde

1. **Dritter, bisher unbekannter Wellen-Kanal:** `rules.multiplayerWaves` spawnt einen
   zusätzlichen, zufälligen Elite-Boss parallel zur regulären Welle — unabhängig von
   `rules.waves` (#736) und `rules.extraWaves` (Hard-Doku §6).
2. **Trigger-Formel weicht vom eigenen Code-Kommentar ab:** bei `additionalWaves = 1`
   feuert der Extra-Boss auch im Solo-Modus, entgegen der Kommentar-Behauptung
   "disabled in single player mode".
3. **Normal ist hier milder als Default/Hard/Brutal:** eigener Schwellenwert-Override
   verschiebt den Trigger auf Level 5 (MP) / 7 (Solo) statt Level 2 (MP) / 6 (Solo).
4. **Hard und Brutal überschreiben `rules.multiplayerWaves` nicht** — sie erben
   Defaults strengere Schwellen.
5. **Der Extra-Boss kommt aus einem 27-Einträge-Zufallspool** mit 7 Kreaturen-Familien,
   von denen **vier komplett neu** für unsere gesamte Recherche-Reihe sind
   (`hammeroceros`, `krocoon`, `mudroner`, `phirian`).
6. **Eine dritte, unabhängige HP-Skalierung** (`min_difficulty_factor` → `extra_unit_modifiers.health`,
   2 500 bis 50 000 HP über 11 Stufen) — identisch für alle 7 Familien, unabhängig von
   `.ent`-Basis-HP und der Anoryxian-Tier-Tabelle.

---

## 7 · Offene Punkte

1. **Was den `difficulty_factor`-Wert zur Laufzeit tatsächlich setzt** (der `Enemy`-Node
   in `attack_boss_dynamic.logic` hat `spawn_species_difficulty_min/max/offset` immer
   auf `"0"` — der reale Wert kommt vermutlich aus einer nativen Engine-Quelle,
   wahrscheinlich `currentDifficultyLevel`, aber nicht direkt im Lua-Quelltext belegt).
2. **HP-Ladder der Elementar-Varianten** (`_acid`/`_cryo`/`_energy`/`_fire` je Familie)
   nur stichprobenartig für `arachnoid_elite_acid` bestätigt (identisch zur
   Basis-Variante) — nicht für alle 27 Einträge einzeln verifiziert.
3. **Biom-Zuordnung der `.kvp`-Dateien** (warum `boss_jungle_random` in
   `creature_species_elite.kvp` steht statt in einer biom-eigenen Datei) nicht
   vollständig geklärt.
4. **Andere Biome** (`boss_caverns_random` etc.) nicht ausgewertet — gleiche Methodik
   übertragbar.

**Ref:** #736 · #699 · #658 · #213 · #205 · `docs/research/hard-difficulty-wellen-hp-pool.md` ·
`docs/research/brutal-difficulty-wellen-hp-pool.md` · `docs/research/creatures-base-difficulty-mechanik.md`.
