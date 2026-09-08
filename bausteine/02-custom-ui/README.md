# Baustein 02 — Custom-UI (Popup-Test, `rb_ui`)

**Was es testet:** Custom-UI zur Laufzeit über die offizielle Service-API
`GuiService:OpenPopup(entity, template, text)` + globales
`GuiPopupResultEvent`. Der Baustein registriert genau **einen**
Konsolen-Command (`rb_ui`), der ein Popup mit OK-Button am Spieler-Mech
öffnet; Schließen per Button wird geloggt. Kein Wave-Spawn, keine Bindings.

Abgeleitet aus `mod/lua/rbbattle_autoexec.lua` — Experiment B (Custom-UI),
Spike PR #1.

## Inhalt

```
rbbattle_02_customui/                 <- Mod-Ordner (Name = Mod-Name)
└── lua/
    └── rbbattle_02_customui_autoexec.lua
```

## Installation (Windows, Steam)

1. Steam-Bibliothek finden (z. B. `D:\SteamLibrary\steamapps\common\Riftbreaker`).
2. Den Ordner `rbbattle_02_customui/` (komplett) nach
   `<SteamLibrary>\steamapps\common\Riftbreaker\mods\` kopieren →
   Ergebnis: `mods\rbbattle_02_customui\lua\rbbattle_02_customui_autoexec.lua`.
   (`mods\` ggf. neu anlegen.)
3. Spiel starten, Karte laden (Mod lädt automatisch).

## Testschritte

1. In-Game-Konsole öffnen (deutsche Tastatur: `ö`).
2. `rb_ui` eingeben → Popup-Panel **am Spieler-Mech** öffnet sich mit dem
   Text „RBBATTLE — Baustein 02 (Custom-UI)“ und einem OK-Button.
3. Zweites `rb_ui` bei offenem Popup → Meldung „Popup ist bereits offen“
   (kein API-Close dokumentiert; Schließen nur per Button).
4. OK-Button klicken → Popup schließt.
5. Log-Datei prüfen: `<Documents>\The Riftbreaker\exor_logs.txt`
   (oder live via `bausteine/03-log-bridge/tail_events.py`).

## Erwartetes Ergebnis

- Konsole nach Öffnen: `rb_ui: Popup geoeffnet (Button 'OK' schliesst)`.
- Log-Zeilen:

```
[RBBATTLE] event=mod_load version=0.1.0-baustein02 status=ok
[RBBATTLE] event=ui_popup status=opened
[RBBATTLE] event=ui_popup status=closed result=button_ok
```

- `GuiPopupResultEvent` feuert beim Button-Klick (result=`button_ok`).
- Ohne Spieler (Karte nicht geladen): `event=ui_popup status=no_player`
  (graceful no-op).

## Status

- [x] Code abgeleitet aus Spike Experiment B (PR #1)
- [ ] In-Game-Test: `rb_ui` öffnet Popup am Mech (Momo)
- [ ] In-Game-Test: OK-Button schließt, Log `status=closed result=button_ok` (Momo)
- [ ] In-Game-Test: Doppel-`rb_ui` → „bereits offen“-Meldung (Momo)
