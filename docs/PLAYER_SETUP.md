# RBBattle — Spieler-Setup (Multiplayer / Coop)

Kurzanleitung für beide Spieler, damit ein Runden-Duell startet. Voraussetzung:
**beide Spieler installieren EXAKT dieselben Mods** (gleicher Stand, gleiche
Dateien). Weicht auch nur ein Mod ab, lehnt der Client die Lobby mit
**„different set of mods“** ab — das Match startet dann nicht.

## 1. Primär-Download (ein Zip für alles)

`rbbattle.zip` ist der einzige Mod, den beide Spieler brauchen (fusioniert
Skeleton + Wave-Spawn + Shop/Queue + Economy + Win-Condition + Reveal-HUD).

- **Immer aktuellste Version:**
  `https://github.com/momokli/riftbreaker-battle-mod/releases/latest/download/rbbattle.zip`
- Beide Spieler laden **denselben** Zip und entpacken ihn in den Mods-Ordner
  (genauer Pfad: `mod/README.md` → „Installation“).

## 2. Gleicher Stand auf beiden Seiten

1. Beide Spieler löschen den alten `rbbattle`-Ordner im Mods-Verzeichnis.
2. Beide entpacken die **dieselbe** `rbbattle.zip` frisch.
3. Kein Spieler darf zusätzlich einzelne Baustein-Zips (`rbb-00` … `rbb-06`)
   oder eine ältere Mod-Version aktiviert haben — sonst „different set of mods“.

## 3. Prüfen (optional)

Beide Spieler laden eine Karte und schauen in
`<Documents>\The Riftbreaker\exor_logs.txt` nach der Zeile:

```
[RBBATTLE] event=mod_load version=<Stand> status=ok ...
```

Steht bei beiden derselbe `version=`-Stand, ist der Mod-Stand synchron.

## Warum das nötig ist

Der Multiplayer gleicht die aktive Mod-Liste beider Clients ab. Jede Abweichung
(andere Datei, andere Version, zusätzlicher/fehlender Mod) führt zu
**„different set of mods“**. Deshalb: **ein Zip, gleicher Stand, keine Extras.**
