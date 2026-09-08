# RBBattle Spike-Mod — Installation & Test (Stand 08.09.2026)

Spike-Mod für den Runden-Duell-Modus („Biter Battles“-artig). Kein Workshop-
Release, keine Garantie — Ziel ist die **In-Game-Validierung** der Mod-API
(Experimente A/B/C). Spieltest macht Momo; dieses README ist die Anleitung.

## Installation (lokaler Mods-Ordner — Dev-Pfad)

Der Inhalt dieses Ordners (`mod/`) ist **eine Mod**: Er spiegelt die
Content-Struktur des Spiels (`lua/`, …) — genau wie es die EXOR-Workspace-Tools
und Workshop-Mods tun (Quelle: fandom „Basic Modding Guide“, Ordner
`<game>/mods/<ModName>/`; echte Mods: github.com/lilly1987/Riftbreaker-mods).

### Windows (Steam)
1. Steam-Bibliothek finden: z.B. `D:\SteamLibrary\steamapps\common\Riftbreaker`
2. Ordner anlegen bzw. kopieren:
   ```
   <SteamLibrary>\steamapps\common\Riftbreaker\mods\riftbreaker_battle_mod\
       lua\rbbattle_autoexec.lua
   ```
   (`mods\` ggf. neu anlegen; der Spiel-Ordner `mods` ist identisch mit dem
   Workspace-Ordner, den die „Riftbreaker Tools“ verwenden.)
3. Spiel starten, beliebige Karte laden (Kampagne oder Survival).
   Die Datei `*_autoexec.lua` wird **bei Kartenerstellung** automatisch
   ausgeführt — kein weiterer Aktivierungsschritt (Steam) nötig.

### Alternativen (Download-Pfade des Spiels, falls später nötig)
- Steam Workshop: `<SteamLibrary>\steamapps\workshop\content\780310\<modid>\`
- mod.io: `C:\Users\Public\mod.io\3951\mods\<modid>\`

### macOS (Steam)
- Install-Pfad: `~/Library/Application Support/Steam/steamapps/common/Riftbreaker/`
- Dort analog `mods/riftbreaker_battle_mod/` anlegen (Ordner ggf. neu erstellen).
- ⚠️ Offiziell heißt es „Steam **und** GamePass **PC** können modden“ —
  **ungetestet auf macOS**, in-game prüfen (siehe FINDINGS). Steam-Ordner muss
  nicht zwingend „common“ heißen — Pfad über Steam → Verwalten → Lokale Dateien
  anzeigen lassen.

## Aktivierung / In-Game-Konsole

- Konsole öffnen mit `` ` `` / `~` / `ö` / `'` (je nach Tastatur-Layout;
  bei deutscher Tastatur: `ö`). Quelle: fandom „Console commands“.
- Falls die Konsole nicht aufgeht: in
  `<Documents>\The Riftbreaker\Conf\initial_config_win` (o.ä.) prüfen —
  `set enable_developer_console 0` → `1` (bei GamePass nötig; Steam meist offen).
- **Log-Datei**: `<Documents>\The Riftbreaker\exor_logs.txt` — dort schreibt
  `LogService:Log`; alle Mod-Zeilen tragen den Präfix `[RBBATTLE]`.

## Hotkeys & Commands (Experiment A/B/C)

| Eingabe | Wirkung |
|---|---|
| `rb_wave 1` … `rb_wave 3` (Konsole) | Spawnt kleine Kreaturen-Welle um den Spieler (Experiment A) und schreibt Bridge-Logs (C) |
| `rb_wave 2` / F7 / F8 | F7 = Welle 1, F8 = Welle 3 (Bindings; in `rbbattle_autoexec.lua` änderbar) |
| `rb_ui` / F9 | Öffnet ein Popup-Panel mit Button (Experiment B, Custom-UI) |
| Konsole: `ConsoleService:Write` | Bestätigung jeder Aktion direkt in der In-Game-Konsole |

Erwartete Log-Zeilen in `exor_logs.txt`:

```
[RBBATTLE] event=mod_load version=0.1.0-spike status=ok
[RBBATTLE] event=init status=commands_registered
[RBBATTLE] event=wave level=3 status=start
[RBBATTLE] event=spawn ok blueprint=units/ground/baxmoth entity=12345
[RBBATTLE] event=wave level=3 status=done spawned=8 skipped=0
[RBBATTLE] event=ui_popup status=opened
```

## FINDINGS-Tabelle (Spike, Stand Recherche — In-Game-Test offen)

| # | Frage | Ergebnis laut Doku + Original-Spieldaten | Quelle |
|---|---|---|---|
| 1 | Mod-Layout / Einstiegspunkt | Ordner `<game>/mods/<name>/` spiegelt Content-Root; `lua/*_autoexec.lua` läuft bei Map-Erstellung, Zugriff auf alle Services + `RegisterGlobalEventHandler` | exorstudios-Wiki (autoexec.md); lilly1987/Riftbreaker-mods; fandom Basic Modding Guide |
| 2 | Wave-Spawn zur Laufzeit (A) | ✅ `EntityService:SpawnEntity(blueprint, x, y, z, team)` — exakt die Implementierung von EXORs eigenem `debug_spawn_entity` (`lua/commands/cheat.lua`); Blueprints `units/ground/*` gegen `entities/units/ground/*.ent` der Spieldaten verifiziert | OriginalPacksData (PonomarevDmitry/RiftbreakersMods); fandom Console commands |
| 3 | Custom-UI (B) | ✅ `GuiService:OpenPopup(entity, "gui/popup/popup_template_1button", text)` + `GuiPopupResultEvent` — offizielles Wiki-Beispiel; Template-Datei in Spieldaten vorhanden. ⚠️ `GuiService:ShowHudText(id, …)` existiert (HUD-Node `interface_hud_show_text.lua`), aber gültige `id`s sind undokumentiert → Popup gewählt | exorstudios-Wiki gui-popup.md; OriginalPacksData gui/popup/ |
| 4 | Custom Console Commands (C) | ✅ `ConsoleService:RegisterCommand(name, cb)` + `ExecuteCommand('bind f7 "cmd"')` — offiziell dokumentiert **und** von EXOR selbst so genutzt (cheat.lua: `debug_spawn_entity` …) | exorstudios-Wiki accessing-keyboard-hotkeys.md; fandom Mod service: ConsoleService; OriginalPacksData lua/commands/cheat.lua |
| 5 | Logging (C) | ✅ `LogService:Log(...)` → `exor_logs.txt`; `ConsoleService:Write(...)` → In-Game-Konsole | exorstudios-Wiki debugging-using-lua-services.md |
| 6 | Team-Semantik | Team-String `""` = „Blueprint-Standard“ (EXOR-Cheat nutzt `""`; Spieler-Buildings wie Feinde spawnen korrekt). Team-Ids: Player=1 (Log `GetTeamId` → 1). `"no_team"` existiert für Marker (`effects/messages_and_markers/wave_marker`). Für echte Duell-Teams später verifizieren | OriginalPacksData (cheat.lua, wave_ground.lua) |
| 7 | In-Game-Konsole | ✅ Vorhanden; Tasten ´/ö/'/ñ/ù/`~`/`; GamePass: `enable_developer_console 1` in Conf/initial_config_win | fandom Console commands |
| 8 | Fehlende/unklare Doku | Offizielle exorstudios-Wiki-Serviceseiten sind weitgehend leere Stubs; vollständige Signaturen nur über Fandom „Mod service:*“-Dumps + extrahierte Spieldaten | — |

**Bekannte offene Punkte für den In-Game-Test:** (1) macOS-Mod-Support
ungeklärt; (2) Popup-Verhalten ohne Fokus/mehrere Popups; (3) ob `bind` dauerhaft
in der Config persistiert (unschädlich — Commands werden pro Map neu registriert);
(4) exakte Feind-Team-Zuordnung bei `SpawnEntity` mit `""` (Blueprint-Standard
erwartet, s. #6).

## Technische Notizen

- **Statische Verifikation:** `luaparse` (Lua-5.1-kompatibler Parser) über
  `mod/lua/rbbattle_autoexec.lua` — Syntax OK. Ein echter `luac -p` stand im
  Container nicht zur Verfügung; In-Game-Test steht aus.
- Alle fremden API-Aufrufe sind `pcall`-gesichert: fehlt eine Funktion, kommt
  `status=api_missing` ins Log statt eines Crashes.
- Blueprint-/Wellen-Definitionen stehen als Konstanten am Dateikopf
  (`RBB.waves`) — dort tunen, wenn der Test läuft.

Architektur & Gesamtkonzept: [`../docs/concept.md`](../docs/concept.md) ·
Findings-Überblick: [`../docs/findings.md`](../docs/findings.md)
