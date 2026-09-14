# Resource Hash Map (StringHash -> Name)

Stand: 2026-09-14 · Build 2.0.58485 (GOG == Dedi, byte-identisch mac + planet)

Issues: #371 (unbekannte `StringHash`-Werte mappen) · #379 (PoC `add_resource mythium -10`)

## Methode

Ressourcen sind im `ResourceAccount`/`ResourceBasket` über den **FNV-1a-32-Bit-Hash** ihres Namens keyed (`StringHash`):

- Offset-Basis: `0x811c9dc5`
- Primzahl: `0x01000193`
- `h = 0x811c9dc5; for byte in name: h = (h ^ byte) * 0x01000193`

Die Implementierung wurde gegen die sechs bereits live-bekannten Hashes verifiziert (alle treffen exakt, siehe Tabelle). Kandidatennamen stammen aus den `GameplayResourceDef`-Blöcken der Spiel-Daten (`scripts/resources/*.resource` in den Daten-Packs unter `extracted/base/packs/*_data.zip`, Quelle `planet:/home/momo/rb-game/`); das `id`-Feld ist der Name, der gehasht wird. DLC-Packs (`extracted/DLCs/*`) liefern **keine** zusätzlichen Ressourcennamen.

## Pack-Zugriff (zip64)

**Für Packs > 4 GB `tools/re/rbpack.py` benutzen, nicht `unzip`.** Info-ZIPs
`unzip` findet im Haupt-Pack `00_win_data.zip` (~7.7 GB, zip64) das Central
Directory nicht („start of central directory not found; zipfile corrupt“).
Pythons stdlib `zipfile` (der Motor von `rbpack.py`) liest dasselbe Pack
problemlos: 67628 Member, Central Directory in < 1 s.

```bash
python3 tools/re/rbpack.py list scripts/resources/                # Namen filtern
python3 tools/re/rbpack.py cat  scripts/resources/iron.resource   # Inhalt -> stdout
python3 tools/re/rbpack.py grep 'id\s+"steel"' --in scripts/resources/
```

Details: [`tools/re/README.md`](../../tools/re/README.md).

## Ergebnis

Alle 13 beobachteten Einträge des Account-Baskets sind zugeordnet — **kein Hash bleibt offen**. `resources[]` in `get_state` ist ein Roh-Dump dieses Baskets (Account-Array: 16-Byte-Einträge `{u32 StringHash, i64 ResourceValue}`).

| Hash         | Name                       | Typ             | Quelle (Spiel-Daten)                   |
| ------------ | -------------------------- | --------------- | -------------------------------------- |
| `0x659cc791` | `carbonium`                | global          | `scripts/resources/carbon.resource`    |
| `0x0d01a504` | `steel`                    | global          | `scripts/resources/iron.resource`      |
| `0x1b9f8256` | `titanium`                 | global          | `scripts/resources/titanium.resource`  |
| `0x666d2128` | `palladium`                | global          | `scripts/resources/palladium.resource` |
| `0x6ddeafbe` | `uranium`                  | global          | `scripts/resources/uranium.resource`   |
| `0x9c6fc222` | `cobalt`                   | global          | `scripts/resources/cobalt.resource`    |
| `0xc73e9a95` | `uranium_ore`              | global          | `scripts/resources/uranium.resource`   |
| `0x88504036` | `ammo_tower_liquid`        | ammo_tower      | `scripts/resources/ammo.resource`      |
| `0xbd8cd785` | `ammo_tower_explosive`     | ammo_tower      | `scripts/resources/ammo.resource`      |
| `0x3ee7cbc3` | `ammo_tower_low_caliber`   | ammo_tower      | `scripts/resources/ammo.resource`      |
| `0xea6f06d5` | `ammo_tower_high_caliber`  | ammo_tower      | `scripts/resources/ammo.resource`      |
| `0xa4c40a93` | `biomass_plant`            | loot            | `scripts/resources/loot.resource`      |
| `0x0afcacdd` | `specimen_bush_ray_flower` | loot (specimen) | `scripts/resources/specimens.resource` |

## mythium

- FNV-1a(`"mythium"`) = **`0xc772bed0`**.
- Dieser Hash **matcht keinen** der 13 Account-Hashes; `"mythium"` taucht in den Spiel-Daten/Lua überhaupt nicht auf (grep über `lua-src/` und `extracted/` -> 0 Treffer).

**`mythium` ist keine echte Spiel-Ressource**, sondern ein Platzhalter im PoC (#379). `add_resource mythium -10` greift daher ins Leere: `PlayerService::AddResourceAmount` löst den Namen über `StringHash` auf und findet keinen passenden `GameplayResourceDef` (kein Basket-Eintrag, der Betrag wird nicht gebucht).

## Konsequenz für #379

Das PoC-Backend muss eine echte Ressource verwenden, z. B. `carbonium` (`0x659cc791`) oder `uranium_ore` (`0xc73e9a95`). Der `add_resource`-Pfad in `rbbridge.c` hartkodiert aktuell ohnehin `"carbonium"` (Write-PoC #373).

## Hinweise

- `steel` ist der interne Name der „Eisen“-Ressource; das Lua-Kommando `cheat_add_resource` akzeptiert zusätzlich den Alias `ironium` (`lua/commands/cheat.lua`). Gehasht wird der interne Name `steel`.
- `uranium_ore` ist eine eigene globale Ressource (Erz), getrennt vom veredelten `uranium`.
- Die sieben `global`-Ressourcen sind die spielökonomischen Kernressourcen; die restlichen sechs Einträge (Turm-Munition, Biomasse, Pflanzenspecimen) stehen ebenfalls im Account-Basket und erscheinen daher in `resources[]`.
