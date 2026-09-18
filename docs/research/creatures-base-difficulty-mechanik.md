# `creatureDifficultyIncrementPerDOMDifficulty` — die Mechanik entschlüsselt

**Vorarbeit:** #213 (offener Punkt), #736, `docs/research/hard-difficulty-wellen-hp-pool.md`,
`docs/research/brutal-difficulty-wellen-hp-pool.md` (alle drei führten dieses Feld als
ungeklärten Caveat)
**Stand:** 2026-09-18 · **Spiel-Build:** Pack `00_win_data.zip` (lokal, `/srv/rift-local/game/packs`)

**Kern:** Das Feld ist kein Multiplikator auf die Survival-Wellen-Units. Es ist ein
globaler, persistenter Difficulty-Zähler, den **ausschließlich ein einziger
DLC2-Kampagnen-Boss** ausliest — und dieser Boss kommt im Survival-Modus **nachweislich
nie vor**. Damit ist der Caveat aus #213/#736/Hard/Brutal für die dort untersuchten
Units (canoptrix, kafferroceros, arachnoid_sentinel, gnerot, bomogan, baxmoth)
vollständig geschlossen.

---

## 1 · Der Mechanismus

### 1a. Ein globaler Float-Zähler im nativen Engine-Code

`CampaignService` (C++, nicht Lua) verwaltet einen einzelnen persistenten Wert:

```lua
CampaignService:GetCreaturesBaseDifficulty()
CampaignService:SetCreaturesBaseDifficulty( value )
CampaignService:IncreaseCreaturesBaseDifficulty( delta )
CampaignService:DecreaseCreaturesBaseDifficulty( delta )
```

### 1b. Erhöht bei jeder DOM-Level-Eskalation, indiziert nach Spieleranzahl

Quelle: `lua/missions/v2/dom_manager.lua`

```lua
function dom_mananger:IncreaseCreaturesBaseDifficulty()
    if ( self.rules.creatureDifficultyIncrementPerDOMDifficulty ~= nil ) then
        local playersCounter = self:GetPlayersCounter()
        local index = Clamp( playersCounter, 1, #self.rules.creatureDifficultyIncrementPerDOMDifficulty )
        CampaignService:IncreaseCreaturesBaseDifficulty(
            self.rules.creatureDifficultyIncrementPerDOMDifficulty[index][self.currentDifficultyLevel]
        )
    end
end

function dom_mananger:GetPlayersCounter()
    local playersCount = #PlayerService:GetConnectedPlayers();
    if playersCount > 4 then return 4 end
    return playersCount;
end
```

**Das löst das Rätsel der vier Teiltabellen `[1]`–`[4]`** aus #213/#736/Hard/Brutal:
sie sind exakt nach **Spieleranzahl** indiziert (1–4 Spieler, ab 5 gecappt auf 4) —
nicht nach Biom, nicht nach einem unbekannten Modifikator.

Es gibt daneben `RevertCreaturesBaseDifficulty()` (zieht die Summe aller Increments bis
zum aktuellen Level wieder ab) und `UpdateCreaturesBaseDifficulty()` (baut den Zähler
beim (Wieder-)Einstieg durch Aufsummieren aller bisherigen Level-Increments neu auf) —
beide iterieren `for i = 1, self.currentDifficultyLevel`, akkumulieren also **additiv**
über alle bisher erreichten DOM-Level.

### 1c. Der einzige Konsument im gesamten Spiel

Corpus-weiter Grep über **alle** `lua/units/`-Dateien nach `GetCreaturesBaseDifficulty`
und `healthSetup`/`SetupDifficulty`: **ausschließlich**
`lua/units/ground/anoryxian_boss_base.lua` (+ Subklassen `anoryxian_boss.lua`,
`anoryxian_boss_alpha.lua`) liest den Wert. Keiner der sechs in #736/Hard/Brutal
untersuchten Survival-Units (`canoptrix`, `kafferroceros`, `arachnoid_sentinel`,
`gnerot`, `bomogan`, `baxmoth`) referenziert `GetCreaturesBaseDifficulty` — verifiziert
per direktem Grep auf jede einzelne `.lua`-Datei dieser sechs Units (keine Treffer).

### 1d. Anwendung: absolute HP-Lookup-Tabelle, kein Multiplikator

Quelle: `lua/units/ground/anoryxian_boss_base.lua:168-190`

```lua
function anoryxian_base:SetupDifficulty()
    local creaturesDifficulty = CampaignService:GetCreaturesBaseDifficulty()
    local playersCount = #PlayerService:GetConnectedPlayers();

    local diff = math.max( 1, math.min( math.floor( creaturesDifficulty ), 10 ) )
    local players = math.max( 1, math.min( playersCount, 4 ) )

    local newHealth = self.healthSetup[diff][players]

    HealthService:SetMaxHealth( self.entity, newHealth )
    HealthService:SetHealth( self.entity, newHealth )
end
```

Der akkumulierte Float wird **abgerundet und auf 1–10 geklemmt** (`diff`), die
Spieleranzahl auf 1–4 geklemmt (`players`) — daraus wird eine **absolute** HP direkt
aus einer 10×4-Tabelle gelesen, kein Prozent- oder Multiplikator-Wert.

---

## 2 · Die konkreten HP-Tabellen

Quelle: `lua/units/ground/anoryxian_boss.lua` / `anoryxian_boss_alpha.lua`
(Format laut Inline-Kommentar im Spielcode selbst: `healthSetup[difficulty][playerCount]`)

### Anoryxian (Basis, `anoryxian_boss.lua`)

| Difficulty-Tier | 1 Spieler | 2 Spieler | 3 Spieler | 4 Spieler |
| --- | --- | --- | --- | --- |
| 1 | 40 000 | 70 000 | 90 000 | 110 000 |
| 2 | 45 000 | 79 000 | 101 000 | 124 000 |
| 3 | 50 000 | 88 000 | 113 000 | 138 000 |
| 4 | 55 000 | 96 000 | 124 000 | 151 000 |
| 5 | 60 000 | 105 000 | 135 000 | 165 000 |
| 6 | 65 000 | 114 000 | 146 000 | 179 000 |
| 7 | 70 000 | 123 000 | 158 000 | 193 000 |
| 8 | 75 000 | 131 000 | 169 000 | 206 000 |
| 9 | 80 000 | 140 000 | 180 000 | 220 000 |
| 10 | 85 000 | 149 000 | 191 000 | 234 000 |

### Anoryxian Alpha (`anoryxian_boss_alpha.lua`)

| Difficulty-Tier | 1 Spieler | 2 Spieler | 3 Spieler | 4 Spieler |
| --- | --- | --- | --- | --- |
| 1 | 60 000 | 105 000 | 135 000 | 165 000 |
| 2 | 65 000 | 114 000 | 146 000 | 179 000 |
| ... | ... | ... | ... | ... |
| 10 | 105 000 | 184 000 | 236 000 | 289 000 |

**Auffällig:** `Alpha[Tier N] == Basis[Tier N+5]` exakt — die Alpha-Variante ist
schlicht die Basis-Tabelle um 5 Tiers nach oben verschoben, dieselbe Reskin-Logik wie
bei den Survival-Wellen-Units (#736/Hard/Brutal), nur hier als komplette
Zusatz-Lookup-Tabelle statt als HP-Multiplikator auf einen Blueprint.

---

## 3 · Wo taucht der Anoryxian-Boss tatsächlich auf?

`attack_boss_dynamic.logic` (der Boss-Trigger in `rules.wavesEntryDefinitions` bei
DOM-Level 8/9, siehe #736 §Kontext) spawnt **keinen festen Boss**, sondern eine
zufällige "Species"-Auswahl über Blueprints wie `boss_jungle_random`,
`boss_caverns_random`, `boss_desert_random`, etc. (`spawn_type="creature_species"`,
`spawn_species_difficulty_min/max`-Parameter — eine native Engine-Zufallsauswahl, keine
direkte `.ent`-Referenz).

Die dahinterliegenden Species-Pools liegen in `.kvp`-Dateien
(`scripts/units/creature_species*.kvp`: `creature_species.kvp`, `_caverns`, `_elite`,
`_elite_spawners`, `_elite_alien_towers`, `_ice`, `_metallic`, `_swamp`) — **in keiner
dieser Dateien kommt `anoryxian` vor** (per Grep über jede einzelne Datei verifiziert).

**Endgültige Bestätigung:** ein Grep nach `anoryxian` über **alle 1184** Dateien unter
`logic/missions/survival/` liefert **0 Treffer**. Der Anoryxian-Boss ist eine rein
skriptete Begegnung der DLC2-Höhlen-Kampagne (`entryLogic = "logic/missions/campaigns/dlc_2/caverns_boss_intro.logic"`,
siehe `anoryxian_boss.lua:_OnInit`) und im Survival-Modus (Jungle, egal ob Default/Hard/Brutal)
**strukturell unerreichbar**.

---

## 4 · Bezug zu #213/#736/Hard/Brutal — Caveat geschlossen

- **#213 §3 Caveat** ("Kreaturen-Stärke skaliert zusätzlich über
  `creatureDifficultyIncrementPerDOMDifficulty`, braucht Live-Test"): **geklärt.** Die
  Skalierung betrifft nachweislich nur den Anoryxian-Boss, nicht die in #213/#736
  untersuchten Naturwellen-Units.
- **#736 §8, Hard-Doku §7d, Brutal-Doku §6c** (Rohwerte der Tabelle verglichen, Mechanik
  "bewusst nicht entschlüsselt"): **jetzt entschlüsselt**, siehe §1–§2 hier. Die dort
  dokumentierten Rohwert-Unterschiede zwischen Default/Hard/Brutal bleiben als Beleg
  korrekt, betreffen aber ausschließlich einen im Survival-Modus nicht spawnbaren Boss
  — für die Preisformel (#205) sind sie **irrelevant**.
- **Alle HP-Zahlen in #736, Hard- und Brutal-Dokument bleiben unverändert gültig** —
  die `.ent`-Basis-HP (ggf. mit `_alpha`/`_ultra`-Blueprint-Wechsel) ist die vollständige,
  finale Antwort für die sechs Survival-Wellen-Units. Kein weiterer Laufzeit-Multiplikator
  wirkt zusätzlich auf sie.

---

## 5 · Praktischer Nutzen: Debug-Befehl

Für Live-Tests existiert ein Konsolenbefehl (`lua/commands/debug.lua`):

```lua
ConsoleService:RegisterCommand( "debug_set_creatures_difficulty_level", function( args )
    CampaignService:SetCreaturesBaseDifficulty( tonumber( args[1] ) )
end)
```

Direkt nutzbar, um `CreaturesBaseDifficulty` manuell zu setzen und (in einer DLC2-Höhlen-
Kampagnen-Session mit Anoryxian) die Tier-Sprünge in §2 live zu verifizieren. Für den
Survival-Modus hat der Befehl mangels Konsument keine sichtbare Wirkung.

---

## 6 · Kernbefunde

1. **`creatureDifficultyIncrementPerDOMDifficulty[1]`-`[4]` sind nach Spieleranzahl
   indiziert** (1–4 Spieler) — Rätsel aus #213/#736/Hard/Brutal gelöst.
2. **Einziger Konsument im ganzen Spiel: der Anoryxian-Boss** (DLC2-Höhlen-Kampagne).
3. **Anwendung ist eine absolute 10×4-HP-Lookup-Tabelle**, kein Prozent-/Multiplikator-Wert.
4. **Anoryxian ist im Survival-Modus (Jungle, jede Difficulty) nicht spawnbar** — 0 Treffer
   über 1184 Survival-Missionsdateien, kein Eintrag in irgendeinem `creature_species*.kvp`-Pool.
5. **Der Caveat aus #213/#736/Hard/Brutal ist damit geschlossen** — alle dortigen
   HP-Zahlen für die sechs Survival-Units sind vollständig und unverändert gültig.
6. **Alpha-Boss-Tabelle = Basis-Tabelle um 5 Tiers verschoben** — dieselbe Reskin-Logik
   wie bei den `_alpha`/`_ultra`-Wellen-Units, nur als komplette Zusatztabelle statt
   Blueprint-Wechsel umgesetzt.

---

## 7 · Verbleibend offen

1. Ob `creature_species_elite*.kvp` (Elite-Spawner/-Türme) über einen anderen Pfad
   doch mit Anoryxian oder ähnlichen skriptgebundenen Bossen interagieren — nicht
   geprüft, da außerhalb des ursprünglichen Scopes (Naturwellen-HP).
   Nicht relevant für #205.
2. Das genaue Startverhalten von `CreaturesBaseDifficulty` (Startwert 0 oder 1 zu
   Kampagnenbeginn) wurde nicht isoliert verifiziert — durch das `math.max(1, ...)`-Clamp
   in `SetupDifficulty()` praktisch irrelevant (Tier ist ohnehin mindestens 1).

**Ref:** #213 · #736 · #699 · #658 · #205 · `docs/research/hard-difficulty-wellen-hp-pool.md` ·
`docs/research/brutal-difficulty-wellen-hp-pool.md`.
