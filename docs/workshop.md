# Steam-Workshop-Upload — Anleitung (nur Lua-Mod!)

> ⚠️ **Grundregel: NUR der Lua-Mod (`mod/`, `bausteine/00–03`) geht in den
> Workshop. Der Trainer (`trainer/`, `bausteine/04-trainer-io`) wird NIE
> hochgeladen** — In-Process-Injection verstößt gegen die Plattform-Regeln
> und bleibt private Distribution (Entscheidung 08.09.2026,
> `docs/concept.md` → „Steam-Kompatibilität“).

Stand: 08.09.2026. Aktuell ist der Mod noch nicht workshop-reif (Bausteine-
Tests offen) — diese Anleitung dokumentiert den späteren Ablauf und die
AppID-Verifikation.

## AppID: The Rift Breaker = `780310`

Verifiziert **2026-09-08** über zwei unabhängige Quellen:

| Quelle | Ergebnis |
|---|---|
| Steam Store API (`store.steampowered.com/api/appdetails?appids=780310`) | `"name": "The Riftbreaker"`, `"steam_appid": 780310` |
| SteamDB via Kagi-Suche (`steamdb.info/app/780310/`) | The Riftbreaker |

Zusätzlicher interner Beleg: der Workshop-Content-Pfad in `mod/README.md`
(`...\steamapps\workshop\content\780310\<modid>\`) nutzt dieselbe AppID.

---

## Vorbereitung

1. **SteamCMD** installieren: <https://developer.valvesoftware.com/wiki/SteamCMD>
2. **Content-Ordner** bauen — Inhalt = Content-Root des Mods (genau die
   Struktur, die unter `<game>/mods/<ModName>/` liegt, nur `lua/`-Teile):

   ```
   D:\rbbattle_workshop\rbbattle_mod\
   ├── lua\
   │   └── rbbattle_autoexec.lua      <- (finaler Mod, noch nicht der Spike)
   └── (keine Trainer-/DLL-/Python-Dateien!)
   ```

   Vorbereiten per Repo-Skript oder manuellem Kopieren aus `mod/`:
   ```bash
   scripts/package_mod.sh              # erzeugt dist/rbbattle-mod-<version>.zip
   ```
   (das ZIP enthält nur den Mod — zum Entpacken in den Content-Ordner.)

3. **Preview-Bild** (z. B. `preview.png`, 512×512 empfohlen).

## workshop.vdf (Beispiel)

```vdf
"workshopitem"
{
    "appid"            "780310"
    "publishedfileid"  ""
    "contentfolder"    "D:\rbbattle_workshop\rbbattle_mod"
    "previewfile"      "D:\rbbattle_workshop\preview.png"
    "visibility"       "1"
    "title"            "RBBattle — Runden-Duell-Modus (Lua-Mod)"
    "description"      "Rundenbasiertes 1v1-Duell (Biter-Battles-artig) fuer The Rift Breaker. Nur Lua-Mod - keine externen Tools enthalten. Install: Inhalt nach <Spielordner>\\mods\\rbbattle_mod\\ entpacken."
    "changenote"       "Erster Upload (friends-only Test)"
}
```

**`visibility`:** `0` = öffentlich, `1` = nur Freunde (friends-only),
`2` = versteckt. Für den ersten Test: `1`.

## Upload-Ablauf (friends-only)

```bat
steamcmd +login <steamaccount> +workshop_build_item D:\rbbattle_workshop\workshop.vdf +quit
```

- SteamCMD fragt nach Passwort + Steam-Guard-Code (2FA).
- Erwartete Ausgabe beim ersten Upload:
  ```
  Creating new Workshop item...
  Preparing content...
  Uploading content...
  Uploading preview image...
  Committing update... Success.
  ```
- **Wichtig:** SteamCMD gibt beim ersten Upload die neue
  `publishedfileid` aus (bzw. trägt sie in die vdf ein). Für **Updates**
  diese ID in die `workshop.vdf` übernehmen (`"publishedfileid" "<id>"`),
  dann denselben Befehl erneut laufen lassen.
- Kontrolle im Steam-Client: Workshop → Deine Dateien → Sichtbarkeit
  „Nur Freunde“; Freunde zum Testen einladen.

## Vor jedem Upload prüfen (Checkliste)

- [ ] Content-Ordner enthält **nur** Lua-Mod-Dateien (kein `trainer/`,
      keine DLL, kein `pipe_client.py`, kein `scan/`)
- [ ] `visibility` korrekt gesetzt (Test: `1`)
- [ ] `appid` = `780310` (The Riftbreaker)
- [ ] Mod vorher lokal getestet (`mod/README.md`, Bausteine 00–03)
- [ ] Game-Update seit letztem Test? → API-Änderungen prüfen
      (`docs/findings.md`)

## Warum der Trainer nie in den Workshop darf

- Workshop-Regeln verbieten Prozess-Manipulation/Injection-Inhalte.
- Der Trainer ist ein „für uns“-Tool (runtime-only Injection, kein
  Datei-Eingriff) — Distribution ausschließlich privat (z. B. direktes
  ZIP von `trainer/` bzw. `bausteine/04-trainer-io/`), nie über Steam.
- Der Lua-Mod bleibt dadurch ein normaler, update-fester Workshop-Mod.

## Referenzen

- SteamCMD: <https://developer.valvesoftware.com/wiki/SteamCMD>
- Upload-Guide SteamCMD (visibility-Werte): Steam-Community-Guide
  „Uploading workshop items via SteamCMD“
  (<https://steamcommunity.com/sharedfiles/filedetails/?id=1881151743>)
- AppID-Verifikation: Steam Store API (`appdetails?appids=780310`) +
  <https://steamdb.info/app/780310/>
