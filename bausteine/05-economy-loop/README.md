# Baustein 05 — Economy-Loop (`rb_points`, `rb_buy_wave`, `rb_status`)

**Was es testet:** Der geschlossene Economy-Kreis als Fundament des
Runden-Duells: Punkte **verdienen** (Kills der eigenen Kampfwellen bzw.
Survival-Zeit-Tick als Fallback), Punkte **ausgeben** (Kreaturen-Welle gegen
die eigene Basis kaufen, Kosten 10/25/50) und den Kontostand **anzeigen**
(mit Persistenz-Spiegel in einer Global-Database).

Abgeleitet aus Baustein 01 (Wave-Spawn, in-game verifiziert 08.09.2026) +
Recherche `docs/research/api-deep-dive.md` (08.09.2026).

**Wichtig:** Punkte sind ein **eigenes Konto des Mods**, nicht die
Spiel-Ressourcen (carbonium & Co.) — einen verifizierten Lua-Zugriff aufs
Spieler-Ressourcen-Konto gibt es nicht (Deep-Dive §1). Damit bleibt die
Basis-Ökonomie unangetastet und das Konto ist später 1:1 auf den
Tournament-Server (Baustein 06, Scoreboard) übertragbar.

## Inhalt

```
rbbattle_05_economy/                <- Mod-Ordner (Name = Mod-Name)
└── lua/
    └── rbbattle_05_economy_autoexec.lua
```

## Commands

| Command | Bedeutung |
|---|---|
| `rb_points` | Guthaben + Zähler anzeigen (Konsole + Log) |
| `rb_points reset` | Konto auf 0 zurücksetzen (auch in der Global-DB) — für wiederholbare Tests |
| `rb_buy_wave <1..3>` | Welle kaufen (Kosten **10 / 25 / 50**), spawnen im Ring um den Spieler (Team = Blueprint-Standard, d. h. hostil) |
| `rb_status` | Details: Modus (`auto`/`kill`/`tick`), Punkte, getrackte Kreaturen, DB-Status, Kosten-/Wert-Tabelle |

## Punktequellen (Dual-Mode)

| Quelle | Ereignis | Wert | Sicherheit |
|---|---|---|---|
| Kill (Hauptweg) | `EntityKilledEvent` — nur für Kreaturen, die `rb_buy_wave` gespawnt hat (Entity-ID-Tracking, kein Farmen an Wildtieren) | brabit 1 · baxmoth 2 · artigian 3 · canceroth 5 | wahrscheinlich (Event + Mechanik belegt, Getter per Konvention — **Laufzeit-Selbsttest im Mod**) |
| Zeit-Tick (Fallback) | `HourEvent` (globaler Spielzeit-Takt) | +1 je Tick | wahrscheinlich |

Der Mod startet im Modus `auto`: Das Tick-Einkommen läuft sofort
(„Fallback automatisch aktiv, solange die Kill-API nicht verifiziert ist“).
Der **erste fehlerfreie Kill** schaltet auf Kill-Einkommen um
(Log `mode=kill status=kill_event_verified`). **Jeder Fehler** im
Kill-Handler (alles pcall-gesichert) schaltet dauerhaft auf Tick-Einkommen
zurück (Log `mode=fallback_tick reason=kill_handler_error`). Kein
Einkommen doppelt: Im Kill-Modus liefert der HourEvent-Handler nichts.

## Persistenz

Punkte/Zähler liegen im Speicher (pro Map-Session) und werden als
**Spiegel** in die Global-Database `rbbattle_05_economy`
(`PlayerService:GetOrCreateGlobalDatabase`, API: `docs/misc/database-class.md`)
geschrieben und beim Mod-Load zurückgelesen. **Ob die DB einen
Map-/Spiel-Neustart überlebt, ist nicht dokumentiert → In-Game-Test
(nächster Punkt).** DB-Ausfälle sind toleriert (Mod läuft dann rein im
Speicher, Log `event=db_load status=unavailable`). `rb_points reset` löscht
die DB-Schlüssel.

## Installation (Windows, Steam)

1. Steam-Bibliothek finden (z. B. `D:\SteamLibrary\steamapps\common\Riftbreaker`).
2. Den Ordner `rbbattle_05_economy/` (komplett) nach
   `<SteamLibrary>\steamapps\common\Riftbreaker\mods\` kopieren →
   Ergebnis: `mods\rbbattle_05_economy\lua\rbbattle_05_economy_autoexec.lua`.
3. Spiel starten, **Survival-Karte** laden (Mod + Commands laden automatisch).

## Testcheckliste

1. **Install & Load:** Karte laden → Log enthält
   `event=mod_load version=0.1.0-baustein05 mode=auto points=0 status=ok`
   (+ `event=db_load status=new` beim ersten Mal, `status=resume` danach).
2. **Konto:** `rb_status` → zeigt `mode=auto`, Punkte 0, `wave_costs=1:10 2:25 3:50`.
3. **Kauf-Flow:** `rb_buy_wave 1` → Konsole: „Welle 1 gekauft (-10 Punkte), 5 Kreaturen
   unterwegs. Punkte: 0“; Log `event=buy_wave level=1 cost=10 spawned=5 ... status=done`;
   5 Brabits spawnen im Ring 8–20 m um den Spieler.
4. **Kill-Einkommen:** Die Basis/der Mech tötet die Brabits → Log
   `event=points_add source=kill blueprint=units/ground/brabit pts=1 points=1`,
   beim ersten Kill zusätzlich `event=points mode=kill status=kill_event_verified`;
   `rb_points` zeigt `source=kill` und das Guthaben.
5. **Insuffizienz:** Bei Guthaben < Kosten (`rb_buy_wave 3` mit 0 Punkten) → Konsole warnt
   „nur X Punkte, Welle 3 kostet 50“, Log `event=buy_wave ... status=insufficient_points`.
6. **Ungültiges Level:** `rb_buy_wave 99` → Konsole „level 99 ungueltig (1..3)“,
   Log `status=invalid_level`.
7. **Kein Spieler-Mech:** `rb_buy_wave 1` ohne geladene Karte/ohne Mech → Erstattung:
   Log `status=refunded reason=no_spawn`, Konsole „konnte nicht gespawnt werden →
   X Punkte erstattet“.
8. **Tick-Fallback:** Solange `mode=auto` läuft, sollte mit der Spielzeit
   `event=points_add source=hour_tick pts=1` erscheinen (Frequenz notieren!).
   Kill-Handler-Fehler (falls Getter nicht existieren) → Log
   `mode=fallback_tick reason=kill_handler_error`, `rb_status` zeigt `mode=tick`.
9. **Persistenz:** Punkte sammeln → Map verlassen & neu laden → `rb_points`:
   überleben die Punkte den Neustart? (Unverifiziert, s. o. — Ergebnis in
   Status-Checkboxen + api-deep-dive.md §3 eintragen.)
10. **Log-Format:** Alle Zeilen in `<Documents>\The Riftbreaker\exor_logs.txt`
    mit `[RBBATTLE] event=... key=value` — live parsebar via
    `bausteine/03-log-bridge/tail_events.py`.

## Erwartetes Verhalten / Edge-Cases

- **Kein Doppel-Einkommen:** Modus `kill` → keine `hour_tick`-Punkte mehr.
- **Nur eigene Kreaturen zählen:** ambient getötete Wildtiere bringen nichts
  (nicht getrackt); erst nach `rb_buy_wave` gespawnte IDs sind getrackt
  (Cap 500, Prune-Log `event=track prune reason=max_tracked`).
- **„Gegen die eigene Basis“:** Die Welle spawnt im Ring um den Spieler —
  im Test also **am Standort der eigenen Basis** aufhalten, damit die
  Basis-Verteidigung kämpft. (Später im Duell übergibt der Tournament-Server
  das Spawn-Ziel, s. concept.md.)
- **Balance (Testwerte):** Voll-Clear-Payback ca. 50 % (W1: 5/10, W2: 11/25,
  W3: 21/50) → Kettenkäufe nur mit überlebtem Tick-Einkommen. Werte sind
  Config am Dateikopf (`RBB.cfg`), nicht hartkodiert im Ablauf.
- **Fehlerbilder:** unbekannte Blueprints → `reason=not_found`-Skip (kein
  Crash; Namen gegen Original-Spieldaten verifiziert, Baustein 01);
  Service-Ausfälle → `event=spawn api_error`/`status=no_player`/`no_position`;
  DB weg → `db_ok=false`, Mod läuft im Speicher weiter.

## Status

- [x] Code abgeleitet aus Baustein 01 (Spawn, in-game verifiziert) + Deep-Dive
- [x] Lua-Syntax geprüft (luaparse, Lua 5.1-Grammatik) — kein In-Game-Lauf möglich
- [ ] In-Game-Test: Install + `mod_load` + `db_load`
- [ ] In-Game-Test: `rb_buy_wave` 1/2/3 (Kosten, Spawn, Log-Muster)
- [ ] In-Game-Test: Kill-Einkommen + Umschaltung `auto` → `kill` (`kill_event_verified`)
- [ ] In-Game-Test: Insuffizienz / ungültiges Level / Refund ohne Mech
- [ ] In-Game-Test: `HourEvent`-Frequenz + Fallback-Pfad (falls Kill-API fehlschlägt)
- [ ] In-Game-Test: Persistenz über Map-Neustart (`rb_points` nach Reload)
