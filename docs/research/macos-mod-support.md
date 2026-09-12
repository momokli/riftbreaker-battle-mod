# macOS-Mod-Support — Verifikations-Vorbereitung (Issue #208)

Stand: 12.09.2026. Reine Recherche/statische Verifikation — **kein** Mac-Test
durchgeführt, **kein** In-Game-Beleg. Dieses Dokument ist **nicht** als
„macOS funktioniert“ zu werten; der Warnhinweis in `mod/README.md` bleibt
bestehen.

## Schlussfolgerung

**The Riftbreaker hat keine native macOS-Version.** „macOS-Mod-Support“ ist
damit nativ nicht möglich — die im Issue genannte Installation
(`~/Library/Application Support/Steam/steamapps/common/Riftbreaker/mods/rbbattle/`)
kann auf einem Mac **nie** existieren (Steam für macOS installiert keine
Windows-only-Titel). Die einzige Mac-Route ist Windows-Kompatibilität
(GPTK/CrossOver/Whisky — alle Wine-basiert — oder Parallels/Windows-VM); dort
gelten die **Windows**-Pfade im Wine-/VM-`drive_c`. Ob die Mod unter diesen
Schichten tatsächlich lädt, ist ein **offener Punkt** (echter Mac-Test durch
Momo/Matheo).

Konfidenz: **hoch** (Steam-API ist die maßgebliche Quelle für
Plattform-Verfügbarkeit).

## Verifizierbar ohne Mac (belegte Fakten)

| # | Fakt | Quelle | Konfidenz |
|---|---|---|---|
| 1 | Keine native macOS-Version: Steam-App 780310 → `platforms: { windows: true, mac: false, linux: false }` | Steam-API `store.steampowered.com/api/appdetails?appids=780310` (abgefragt 12.09.2026) | hoch |
| 2 | Steam-Systemanforderungen nennen nur Windows (min. 8.1, empfohlen 10); kein macOS/Linux-Abschnitt | Steam-Store-Seite App 780310 (12.09.2026) | hoch |
| 3 | Steam für macOS installiert keine Windows-only-Titel → `~/Library/Application Support/Steam/steamapps/common/Riftbreaker` entsteht nie | Steam-Client-Verhalten (Windows-only-Titel sind in der Mac-Bibliothek nicht installierbar) | hoch |
| 4 | Das offizielle exorstudios-Wiki dokumentiert **keine** macOS-Pfade und erwähnt macOS **nicht**; die Modding-Doku ist plattform-agnostisch (Mod-Struktur, Services, Tools) | exorstudios/riftbreaker-wiki (Clone 12.09.2026; grep `macOS|darwin|apple` → 0 Treffer) | hoch |
| 5 | Das Mod-Format selbst ist plattform-agnostisch: Ordner + `lua/*_autoexec.lua` + `<GUID>.manifest`; die Engine lädt es unabhängig vom OS, **sofern** das Spiel läuft | exorstudios-Wiki (autoexec), lilly1987/Riftbreaker-mods (bereits in `docs/findings.md`) | hoch |
| 6 | Repo-Tooling ist bereits cross-platform: `tools/mod-updater` erkennt den macOS-Steam-Root `~/Library/Application Support/Steam` — generische Erkennung, findet für dieses Spiel aber nie einen Treffer (Windows-only) | `tools/mod-updater/mod_update.py` (`steam_roots`, `system == "Darwin"`) + `test_mod_update.py` | hoch |
| 7 | „GamePass PC“ = Xbox-App auf Windows → ebenfalls **keine** Mac-Route; Game Pass Ultimate (Cloud) streamt, erlaubt aber keine lokalen Mods | Microsoft Game Pass / xCloud-Modell | mittel |

## Plattform-Unterschiede (Mac-Routen)

- **Kein nativer Pfad.** Mod-Install auf macOS gibt es nur über
  Windows-Kompatibilität: **Apple Game Porting Toolkit (GPTK), CrossOver,
  Whisky** (alle Wine-basiert) oder **Parallels** (Windows-VM).
- **Proton ist nicht relevant** (Proton = Steam Deck/Linux). Auf macOS ist
  Wine (via GPTK/CrossOver/Whisky) die Entsprechung. Der Hinweis
  „Wine/Proton nicht relevant auf macOS“ gilt also nur für **Proton**;
  **Wine-basiertes Tooling ist genau die macOS-Route**.
- **Pfade im Wine-/VM-Kontext:** der Mod-Ordner und alle Spiel-Pfade
  (`Conf/initial_config_win`, `exor_logs.txt` unter `Documents\The Riftbreaker`)
  liegen im `drive_c` des Bottles/der VM, **nicht** unter
  `~/Library/Application Support/Steam/...`.
- **Konsole:** `enable_developer_console` in `Conf/initial_config_win` (im
  Bottle-`Documents`-Ordner). Die Konsolen-Taste `~`/`` ` `` liegt auf
  Mac-Tastaturen physisch anders (Backtick nahe der Umschalttaste, `§` oben
  links) — im Bottle gilt das Windows-Tastatur-Layout.
- **Trainer-DLL:** Named Pipe (`\\.\pipe\rbbattle`) + Runtime-Injection sind
  Windows-spezifisch. Unter Wine/VM = Windows-Pfad (vermutlich funktionsfähig,
  ungetestet); nativ macOS = nicht vorhanden (SIP + Hardened Runtime,
  in `docs/concept.md` bewusst vertagt).

## Offene Punkte (braucht Mac-Test — nicht erledigt)

1. **Lädt die Mod unter GPTK/CrossOver/Whisky/Parallels?** — In-Game-Test mit
   echter Mac-Hardware (Momo/Matheo). Erwartbar, da es ein Windows-Build im
   Bottle ist, aber **unverifiziert**.
2. **Konsole/Aktivierung unter Wine:** `enable_developer_console` +
   Konsolen-Taste im Bottle — unverifiziert.
3. **Trainer/Relay unter Wine:** Named-Pipe-Brücke zur Trainer-DLL im Bottle —
   unverifiziert; ggf. eigenes Issue.

## Empfehlung

- **Doku korrigieren** (dieses Ergebnis): `mod/README.md` (macOS-Abschnitt) und
  `docs/findings.md` von „ungetestet auf macOS“ auf „keine native
  macOS-Version; nur Wine/VM“ umstellen (PR `Refs #208`).
- **Warnhinweis NICHT entfernen** (kein Erfolg verifiziert).
- **Mac-Test** (GPTK/CrossOver/Whisky/Parallels) als separaten, expliziten
  Test-Punkt bei Momo/Matheo nachziehen — nicht hier als „erledigt“ markieren.
- Kein Merge, keine Code-Änderung an Spiel-Inhalten (reine Doku/Recherche).
