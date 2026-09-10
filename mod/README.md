# RBBattle Einzel-Mod — Installation & Test (Stand 09.09.2026)

Einzel-Mod **rbbattle** v0.29.0 für den Runden-Duell-Modus („Biter
Battles“-artig, RIFT BATTLE) in *The Riftbreaker*. Nachfolger von
v0.2.0-single (Fusion Baustein 00 + 01). Kein Workshop-Release, keine Garantie.

> **Multiplayer:** Beide Spieler müssen **exakt dieselben Mods** installiert
> haben, sonst lehnt der Client die Lobby mit **„different set of mods“** ab.
> Kurzanleitung: [`docs/PLAYER_SETUP.md`](../docs/PLAYER_SETUP.md).

**v0.29.0 — HQ-Erkennung per Entity-TYP + Setup-Phase (Commence-Flow) + HQ-Leak-Filter (Issue #144/#158/#152):**
- *HQ-Erkennung* (#144): `HqAutoDetectEntity()` bindet das HQ jetzt über den Entity-*Typ* `headquarters`
  (`FindService:FindEntitiesByType`, am Spiel-Source belegt: `graph/logic/logic_if_building_health.lua`)
  statt über die leere Gruppe `headquarters`; damit greift der Commence-Flow (#158) und der manuelle
  Fallback `rb_hq entity <id>` bleibt erhalten.
- *Setup-Phase/Commence-Flow* (#158): Das Spiel startet **ohne Auto-HQ**. Solange kein HQ platziert
  ist, hält nur der Wave-*Progress* an (kein Naturwellen-Spawn, kein Runden-Zähler, keine Send-Queue);
  das Spiel selbst läuft frei weiter (kein `debug_dom_pause`). Start-Announce „To commence the game,
  place the headquarter“ (`event=commence status=pending`), HQ erkannt → `event=commence status=ok`
  + Wellenstart (HQ-Erkennung über `HourEvent`-Tick und manuellen Fallback `rb_hq entity <id>`).
- *HQ-Leak-Filter* (#152): `EnteredTriggerEvent` feuert bei jedem Map-Trigger — der Leak zählt jetzt
  nur für eine lesbare **feindliche** Kreatur (`evt:GetEntity()`); eigener Mech/HQ/Team-1-Entities
  werden übersprungen (`hq_leak status=skip reason=no_trigger_entity|own_hq|own_team`). Behebt die
  sofortige Niederlage bei Spielstart.
- *Doku* (#156): Interim-Deploy des Dev-SP-Servers (:6321) reproduzierbar in `docs/DEPLOYMENT.md`
  dokumentiert (Build → scp/md5-Parität → Spieler-Check → Mod ersetzen → Restart → Smoke-Test).

**v0.26.0 — Spar-Pool-Persistenz + Wellen-Presets (Issue #65/#41):**
- *Spar-Pool* (`rb_points`/`econ_pool`) wird an der Rundengrenze (Wellenstart) zusätzlich per
  `EconomyCheckpoint()` in die Global-DB geschrieben und beim Mod-Load geladen — der ersparte Pool
  übersteht so Map-/Session-Reloads (Fix für Issue #65, statisch getestet in `persistence.test.js`).
- *Wellen-Presets* (`RBB.wavePresets`): **A** = Naturwellen alle 8 Min in voller Größe,
  **B** = alle 4 Min in halber Größe; Grundschwierigkeit `normal` (Issue #41). Der Wellen-Takt ist
  nicht mehr fest verdrahtet (`RBB.waveIntervalCapS` folgt dem aktiven Preset). Neue Tests in
  `wave-presets.test.js`.

**v0.25.0 — CI: Builds + Tests auf planet-Self-Hosted-Runner (Issue #117/#136):**
- `CI`-Workflow (`.github/workflows/ci.yml`) läuft für PRs und Pushes auf `main` jetzt auf dem
  Self-Hosted-Runner `[self-hosted, planet]` statt auf GitHub-gehosteten Runnern (schnellere Builds,
  kein Runner-Minuten-Budget). Der Tag-Job (`v*`) bleibt auf `ubuntu-latest`.
- **Mod-Laufzeit unverändert** (nur CI).

**v0.24.1 — CI: Deploy-Dry-Run (Syntax-Check) als Merge-Gate (Issue #138):**
- Neuer Required-Check **`deploy-check`** (`.github/workflows/deploy-check.yml`): `yamllint -c .yamllint deploy/`
  plus `ansible-playbook --syntax-check` für alle Playbooks unter `deploy/`; läuft auf PRs, Push nach `main`
  und per `workflow_dispatch`. Keine Secrets, der Vault wird nie entschlüsselt.
- `.yamllint`-Konfiguration ergänzt. **Mod-Laufzeit unverändert** (nur CI).

**v0.24.0 — Mod-Zip-Version aus Metadaten + Download-Link an deployed Stand (Issue #119):**
- `scripts/mod_version.sh` als Single Source of Truth für die Mod-Version (liest `mod/<GUID>.manifest`, Feld `version`).
- `package_bausteine.sh` erzeugt zusätzlich das versionierte `rbbattle-v<version>.zip`; `rbbattle.zip` bleibt der stabile Alias für die Deploy-Kette.
- Website **dev** (`solo.html`, `connectivity.html`): Download zeigt auf das Same-Origin-Zip `/mods/rbbattle.zip` (deployed `main`-Stand). **prod** (`index.html`) unverändert auf das getaggte Release.
- Deploy-Rolle `mods-zip` kopiert das versionierte Zip zusätzlich nach planet.

**v0.22.0 — CD: Deploy-Parkierung bis 0 Spieler online (Issue #118):**
- Neues eigenständiges Modul `tools/deploy-gate/` (`deploy_gate.py`): `decide()` als reine
  Entscheidungslogik (force / unknown / 0 Spieler / n>0) und `wait_until_empty()` als Poll-Loop,
  der parkt und automatisch ausrollt, sobald alle Spieler disconnected sind.
- `--force` (sofort), `--timeout <s>` (Exit 2 statt Endlos-Hängen), pluggable Spielerzahl via
  `--count-cmd`/`RBM_COUNT_CMD` bzw. `--player-count` (Tests/Override).
- Unit-Tests `tests/…` in `tools/deploy-gate/test_deploy_gate.py` (13 Cases) + CI-Schritt im
  `test`-Job; `lint.yml`/`compileall` deckt jetzt `tools/` mit ab.
- Offene Voraussetzung: verbindliche Spielerzahl-Quelle (RCON/Query) noch nicht festgelegt
  (gehört zur vollständigen CD-Pipeline #91) — Provider ist daher austauschbar ausgelegt.

**v0.21.0 — Send-Boost: nächste Naturwelle prozentual verstärken (Issue #39):**
- Neues Command `rb_boost <stufe|pct>`: kauft einen prozentualen Aufschlag auf die **nächste
  Naturwelle** aus dem Spar-Pool (irreversibel). Stufen `s1` +25% (200), `s2` +50% (400),
  `s3` +100% (800) oder freie pct-Eingabe à 8 Währung/Prozentpunkt. Caps: `maxBoostPct=200`,
  `maxBoostsPerWave=4`.
- Neuer Patch-Hook `PatchSpawnWavesHook`: wrappt `dom_mananger:SpawnWavesForDifficultyLevel`
  (idempotent + pcall = Vanilla-Fallback). Der Boost wird beim nächsten natürlichen Wellenstart
  als difficultyLevel-Delta angewendet und danach zurückgesetzt — genau eine Welle, nicht kumulativ.
  Debug-Trigger (`addToSpawned=false`) bleibt unangetastet; `duel` = Boost-Flush nur in `sp`.
- Anzeige in `rb_status`/`rb_shop`/`rb_balance`; Doku in `docs/GAME_DESIGN.md` + `tests/lua-static/README.md`.
- Neuer Test `tests/lua-static/boost.test.js` (Guards, Kauf, Flush, Caps, duel-Stub).
  Prozent→Level-Delta und Stufen-Preise sind dokumentierte **Annahmen** — **braucht Live-Test** (#33).

**v0.20.0 — Balance & Tuning v1: Preisliste + HQ-HP-Kurve (Issue #33):**
- `RBB.shopCfg`: dokumentierte v1-Preisliste für Tiered Units + Bosse (brabit 100, baxmoth 150,
  artigian 200, canceroth 300, boss 800) — bestehende Shop-Struktur (#25), kein neues Framework.
- HQ-HP-Kurve über Runden: `maxHp(r) = hqHpStart + hqHpPerRound * min(r-1, hqHpRoundCap)`
  (100/120/140/160/180, Deckel ab Runde 4) — `HqMaxHp` setzt bei jedem Wellenstart
  (`OnNaturalWaveStart`) den HQ-HP auf den Runden-Maxwert, solange das HQ nicht zerstört ist.
- Neues read-only Command `rb_balance` (loggt Preisliste + Kurve). `RBB.waveIntervalCapS = 300`
  unverändert (nur dokumentiert, Änderung → #41). `docs/GAME_DESIGN.md` um „Balance & Tuning v1“ ergänzt.
- Neuer Test `tests/lua-static/balance.test.js` (Preis-Invarianten, Kurven-Formel, Wellenstart-Wiring).

**v0.19.0 — Solo-Play-Flow: Verbinden, Dev-Log-Console, Click-HUD (Issues #109, #110, #111):**
- `site/solo.html` + `site/solo-connect.js`: Verbinden-Sektion mit Server-Adresse, Verbinden-Button
  (ok/Fehler-Feedback) und „Spiel starten“ — Start erst nach erfolgreichem Connect (`POST /sp`).
- `site/dev-log.js`: transparente Live-Dev-Log-Console (`#devlog`) — streamt alle Turnier-Events
  (`/events?since=<seq>`) ohne Reload; unbekannte Event-Typen bleiben sichtbar (generischer Fallback).
- Click-HUD (`rb_hud_ui`, `rb_quick`): ein-/ausblendbares HUD-Overlay, wichtigste Send-Aktion per Klick
  („Ja“ kauft die gerüstete Einheit in die Send-Queue via `BuyWave`; „Nein“ schließt ohne Aktion).
- Tests: `tests/live-status/solo-connect.test.js`, `tests/live-status/dev-log.test.js`,
  `tests/lua-static/click-hud.test.js`.

**v0.18.3 — Solo-Landing: Copy gekürzt, Sektionen geschärft (Issue #96):**
- `site/solo.html`: Copy deutlich gekürzt (Hero-Lead, Tags, Typing-Phrasen, How-to-Schritte,
  Command-Tabelle, Hinweis-Karten, Footer-Disclaimer, statisches `nojs`-Replay). Jede Sektion
  hat eine klare Funktion (Verbinden / Spielen / Status), keine Struktur-/ID-/Klassen-Änderung
  — Widget-Verdrahtung (`liveStatus`, `live-status.js`) unverändert.
- Neuer Regressionstest `tests/live-status/solo-content.test.js` (Sektionen-Funktion + Server-Adresse).

**v0.18.0 — Relay liest rbbridge-Antwort (`exec_result`) + Deployment (Issues #73, #45):**
- `bausteine/07-relay/relay.py`: `dispatch_exec` liest nach dem Schreiben auf derselben
  Verbindung die `exec_result`-Antwort der rbbridge und loggt
  `dispatch result cmd_id=… status=ok|error|timeout` (Antwort-Timeout = kein Fehler,
  kein Hänger im Dispatch-Thread). `docs/relay-pipe-contract.md` + `trainer/protocol.md`
  um die Antwortrichtung ergänzt.
- Neu: `deploy/` — Ansible-Playbook für planet (systemd + docker: tournament-server,
  vanilla-/riftbreaker-server, `mods-zip`, `website`, `probe-timer`); Vault-Platzhalter,
  **kein** automatischer Deploy.
- CI/CD (#78/#80) + Repo-Hygiene (#85): Issue-Ref-Pflicht im PR-Body, Follow-up-Issue-System,
  Repo-`AGENTS.md`; Pipeline-Artefakt-Dateien aus dem Repo-Root entfernt.
- Kein Balancing (#33/#39/#40/#41) berührt.

**v0.17.0 — Always-latest Downloads & Mod-Parität + CI/CD-Härtung (Issues #31, #75):**
- Landing (`docs/index.html`): alle Download-Links auf den permanenten Redirect
  `releases/latest/download/<asset>` umgestellt — kein manuelles Link-Fix mehr nach jedem Release.
- Neu: `docs/PLAYER_SETUP.md` („ein Zip, gleicher Stand, keine Extras“) — erklärt die
  Mod-Paritätspflicht beider Spieler und den „different set of mods“-Fehler; verlinkt aus
  `mod/README.md` und der Landing.
- CI/CD: Lint (shellcheck/ruff/actionlint), PR-Quality (Conventional-Commit-Titel),
  Pages-Deploy aus `site/`, wöchentlicher Progress und `!claim`/`!unclaim`-Issue-Claim-System;
  `CONTRIBUTING.md` + README-Badges.
- Kein Balancing (#33/#39/#40/#41) berührt.

**v0.16.0 — Relay-Dispatch auf die rbbridge-Pipe + Live-Status auf solo.html (Issues #60, #62):**
- `bausteine/07-relay/relay.py`: `dispatch_exec` ist kein v0-TODO mehr — ein
  `exec_command` aus der Web-UI wird als `{"cmd":"exec","command":…,"cmd_id":…}`
  auf die rbbridge-Named-Pipe (`\\.\pipe\rbbattle`, via `RBB_PIPE_PATH`) geschrieben.
  Pipe nicht erreichbar → **kein** Ack-Verbrennen, sondern Retry mit Backoff
  (`dispatch failed reason=pipe_unavailable`). Vertrag: `docs/relay-pipe-contract.md`.
- `site/solo.html` (SP-/quasi-PROD-Einstieg): Live-Lobby-Status-Widget verdrahtet
  (analog `site/index.html`) — zeigt Spiel-/Lobby-Status des Turnier-Servers.
- Kein Balancing (#33/#39/#40/#41) berührt.

**v0.15.0 — Einzel-Mod-Paket als Primär-Download (Issue #16):**
- `scripts/package_bausteine.sh` baut zusätzlich `rbbattle.zip` (Content-Root `mod/`:
  `lua/` + `<GUID>.manifest` + `README.md` an der Zip-Wurzel) → läuft über den
  bestehenden `dist/*.zip`-Glob automatisch in Build-Artefakte und Release-Assets.
- Landing (`docs/index.html`): prominenter Primär-Download `rbbattle.zip` (Tag v0.15.0)
  im Hero-Bereich; die 7 Baustein-Zips (`rbb-00`…`rbb-06`) bleiben als Einzelkomponenten.
- Additiv, nicht-breaking: keine bestehenden Downloads/Assets entfernt.

**v0.14.0 — Game-Design-Kern v1 (nur Doku/Website, keine Mod-Laufzeit-Änderung):**
- `docs/GAME_DESIGN.md` wiederhergestellt (PR #68, Issue #20) und gegen den
  implementierten Repo-Stand abgeglichen (Abschnitt „Umsetzungsstand“ mit
  ✅ implementiert / ⚠️ offen).
- Landing (`site/index.html`): „design lesen“ verlinkt wieder auf
  `docs/GAME_DESIGN.md` statt auf den älteren Architektur-Entwurf `docs/concept.md`.
- Kein Balancing — die konkreten Zahlen bleiben bei #33/#39/#40/#41.

**v0.12.0 — Send-Queue & Shop-HUD: Tiered Units + Bosse boosten die nächste Welle (Issue #25):**
- **Shop mit Tier-Struktur (Legion-TD-2-artig) + Boss-Tier:** zentrale
  Preisliste v1 `RBB.shopCfg` (Tier 1/2/3 + Boss). **Struktur/Platzhalter,
  KEIN Balancing** — Blueprints sind Platzhalter aus dem bestehenden
  Wellen-Pool; echte Boss-/Unit-Listen + Tuning folgen in der
  Balance-Session (#33/#12).
- **`rb_buy_wave <unit> [count]` (Kauf-Hook):** kauft Einheiten aus dem Shop
  in die Send-Queue und deduziert den Spar-Pool sofort (irreversibel).
  Guards: unbekannte Unit / zu wenig Pool / Queue voll (maxQueueCreatures).
- **Send-Queue:** ersetzt den MVP-Self-Boost (#42). Die Queue wird vom
  Wellenstart-Hook (`dom_mananger:OnEnterSpawn`) beim nächsten natürlichen
  Wellenstart als Zusatz-Spawns an den eigenen Rand-Spawnern (#26)
  ausgeliefert — Tiered Units + Bosse boosten so die **nächste** Welle.
  Senden jederzeit bis Wellenstart, unbegrenzt oft.
- **Custom-UI-Shop (`rb_shop`):** Popup (GuiService:OpenPopup, Muster
  Baustein 02) mit der Tier-/Preis-Liste + Konsolen-Fallback. `rb_queue`
  zeigt den Queue-Stand. `rb_status` zeigt jetzt die Queue statt des Boost.

**v0.13.0 — Reveal-HUD (Poker): Built-Value + Send-Komposition bei Wellenstart (Issue #27):**
- **Poker-Moment:** Vor dem Wellenstart sind beide Werte verborgen (Gegner-
  Built-Value + WAS kommt); BEIM natürlichen Wellenstart lockt der Mod den
  **eigenen Built-Value** + die **eigene Send-Komposition** (`event=reveal`).
  Die Bridge injiziert die vom Server aufgedeckten Gegner-Werte per
  `rb_reveal <built_opp> <hq_opp> [incoming]` (`event=reveal_opp`).
- **`rb_hud`:** HUD-Standardfelder — Rundennummer, Countdown (DOM-Prepare-Zeit
  gekappt), eigener Pool, HQ-HP beider Teams + Reveal-Zustand
  (`reveal=hidden|revealed`; Gegner-Felder als `hidden` bis zur Aufdeckung).
- **`rb_round_start [n]`:** Bridge-Signal „round steigt“ — verbirgt den Reveal
  wieder für die nächste Build-Phase (TOURNAMENT_API.md Bridge-Tabelle).
- Reine Mod-Logik (kein I/O); das 1v1-Routing + Reveal beider Teams liegt beim
  Tournament-Server (tournament/, Issues #29/#30/#44). Statisch getestet.

**v0.11.0 — Landing Live-Status-Widget (Issue #30, Website/keine Mod-Laufzeit-Änderung):**
- `site/live-status.js` (UMD): `deriveStatus(state)` + Poll-Widget für die
  Landing — „Lobby leer“ / „N Spieler in Lobby“ / „Match läuft: A vs B“ /
  „Solo-Match läuft“ / „Status unbekannt“ (API nicht erreichbar), 5 s Poll,
  kein Poll im Hintergrund-Tab.
- `site/index.html`: Live-Status-Widget im Hero + Dashboard-Link ins
  Spectator-Dashboard.
- `tests/live-status/`: `node --test` (14 Checks) + CI-Schritt.
- Reine Website-/Test-Änderung — **keine Mod-Änderung**.

**v0.10.0 — Tooling/Test-Release (Mod-Laufzeit unverändert, Issues #10/#11):**
- `tools/headless-client/nav-lobby.sh`: Screenshot-gesteuerte Navigation bis in
  die Lobby + Server-Connect (Plan-Datei, `NAV_SERVERS`-Auflösung,
  Render-Check-Abbruch bei schwarzem Frame); in Dockerfile/Compose verdrahtet,
  Trockenlauf-Tests `test-nav-lobby.sh` (14/14). Menü-Koordinaten/-Timings und
  der echte In-Game-Connect sind **OFFEN (Operator, Prod)**.
- `tests/e2e-vollkette/`: Vollketten-E2E `rb_wave 3` (Web-UI → Server → Relay →
  rbbridge → Mod-Spawn, fengari+Stub) als CI-Schritt; die Live-Client-Schritte
  sind explizit als `skip`/OFFEN markiert.
- Reines Tooling-/Test-Release — **keine Mod-Änderung**.

**v0.9.0 — Win-Condition: HQ-HP, Leak-Erkennung, HQ-Tod → Match-Ende (Issue #28):**
- **Mod:** Leaks (feindliche Kreaturen in der Trigger-Zone ums HQ,
  `EnteredTriggerEvent`) senken den HQ-HP (`event=leak`/`event=hq_hp`);
  HQ-Tod — HP ≤ 0 **oder** `RespawnFailedEvent` der HQ-Entity — meldet
  `event=hq_dead` + `event=match_end`. Report läuft als `[RBBATTLE]`-Log-Zeile
  über die Bridge an den Tournament-Server (`POST /report hq_hp`, dort
  bereits implementiert). Operator-/Dev-Kommando `rb_hq` (Status, `leak`,
  `entity <id>`, `reset`).
- **Server (tournament/):** HQ-HP-Buchung, Match-Ende bei HP ≤ 0 (`winner`),
  `match_end`-Feed und Rematch (`POST /rematch`) sind bereits aus
  Issues #29/#30/#44 vorhanden — **keine Server-Änderung** nötig, Tests gruen.
- **Statisch getestet** (fengari/Stub-Services, `tests/lua-static/`): Leak →
  HP-Senkung → Match-Ende, RespawnFailedEvent-Kette, Idempotenz, negative
  Fälle. **OFFEN (kein Live-Spiel):** Trigger-Zone-Asset ums HQ,
  `EnteredTriggerEvent`-Feuerung, HQ-Entity-Identifikation, Sieg-Screen-UI.

**v0.8.0 — Headless-Client bootet bis Hauptmenü (Issue #9, Mod-Laufzeit unverändert):**
- `tools/headless-client/`: Compose-Service (`docker-compose.yml`, shm_size 1 GB,
  `init: true` für saubere SIGTERM-Weiterleitung) plus deterministischer
  Menü-Nachweis `wait-menu.sh` (Graustufen-Standardabweichung > `RENDER_MIN_STD`;
  Timeout sichert letzten Frame und gibt `exit 1`), `run-client.sh` mit
  `--no-verify`/`--screenshot`, Trockenlauf-Tests `test-wait-menu.sh` (5/5) und
  dokumentierter Proton-GE-Fallback (DXVK-on-lavapipe).
- Reines Tooling-/Doku-Release — **keine Mod-Änderung**.
- Landing-Seite: Link-Fix (`docs/concept.md`) + Links zu Solo-/Status-Seite (#56).

**v0.7.0 — rb_wave ohne Spieler (Spawn-Anker alternativ zum Mech, Issue #12):**
- **Anker-Kette OHNE Spieler:** `rb_wave`/`rb_send` wählt den Spawn-Anker in
  3 Stufen — (1) natürliche Kartenrand-Spawner (`spawn_enemy_border_*`, #26),
  (2) **Missions-Spawnpunkte** (Fallback NEU: `FindService:FindPlayerSpawnPoints()`
  + `MapGenerator:GetInitialSpawnPoint()`), (3) Spieler-Mech-Ring (letzter
  Fallback, braucht einen Spieler). Stufe (1) und (2) sind serverseitig
  verfügbar, sobald die Welt gebootet ist — `rb_wave` spawnt damit auf einem
  **leeren (unpausierten) Server ohne Client/Spieler**.
- Log: `event=wave … anchor=border|mission|mech`; Missions-Fallback loggt
  `event=wave … status=no_border_spawners anchor=mission_spawn_point count=N`.

**v0.6.0 — Headless-Client-Tooling (Issues #7/#8), Mod-Laufzeit unverändert:**
- `tools/headless-client/`: Container-Setup (Wine + Xvfb + Mesa-llvmpipe),
  Entrypoint `run-client.sh` und xdotool-Navigation `xdo-nav.sh` (#7).
- Client-Daten-Sync lan→planet: `sync-client-data.sh` (Größencheck → rsync →
  Verifikation inkl. MD5-Stichprobe) + Trockenlauf-Tests (#8).
- **Keine Änderung** an `mod/lua/` oder am Send-/Economy-Verhalten gegenüber
  v0.5.0 — reines Tooling-Release.

**v0.5.0 — MVP Single-Player Self-Send (Sich-selber-senden, Issue #42):**
- **Mod-Mode `rb_mode sp|duel`** (Default `sp`): im `sp`-Mode boostet der
  Send-Pool die **eigene nächste Naturwelle** — der Testmodus zum
  Alleine-Ausprobieren. `duel` ist ein Stub (1v1-Routing folgt, #25/#27).
- **`rb_convert` Calcium-first (#40):** `rb_convert <menge>` konvertiert
  **Calcium** (= Spiel-Ressource `carbonium`, Faktor 1) irreversibel in den
  Send-Pool; `rb_convert <resource> <menge>` bleibt abwärtskompatibel
  (Alias `calcium` ≡ `carbonium`). Pool persistiert (#24).
- **Self-Boost am Wellenstart (Hook aus #36):** Function-Wrap an
  `dom_mananger:OnEnterSpawn` — beim Start der natürlichen Welle wird der Pool
  **greedy (teuerste Kreatur zuerst)** in Zusatz-Spawns an den eigenen
  Rand-Spawnern (#26) umgesetzt und verbraucht. Der %-Stärke-Boost der
  nächsten Welle (#39) bleibt offen, weil die dom_manager-Wave-Strength-API
  unverifiziert ist (`docs/SEND_HOOK.md`) — #26 ist der dokumentierte Fallback.
- **`rb_status`**: zeigt Runde, Pool und den nächsten Boost (Konsolen-Fallback
  für das spätere HUD, #27).

**v0.4.0 — Economy (Duell-Ökonomie, Issue #24):**
- **Issue #24:** Alle **gefarmten Ressourcen** (Carbonium, Cobalt, …) werden als
  **Value** getrackt — Quelle: `ResourceObtainedEvent`/`ResourceChangeEvent`
  (Getter-Ladder, erste lesbare Quelle sperrt; kein Doppel-Zählen).
  **Convert ist bewusst & IRREVERSIBEL**: `rb_convert carbonium 100` wandelt
  gefarmten Wert in **Send-Währung** (Spar-Pool) — kein Rücktausch-Pfad.
- **Spar-Pool persistiert über Runden** (Global-Database `rbbattle_economy`,
  profilgebunden) + **defensiver Checkpoint an der Rundengrenze (#65):**
  `EconomyCheckpoint` schreibt den Pool zusätzlich beim Wellenstart
  (`event=economy_checkpoint`). Die tatsächliche Persistenz über einen echten
  Map-/Session-Reload ist weiterhin In-Game zu verifizieren (AC1, Operator —
  `docs/research/api-deep-dive.md` §3).
- **Built-Value** (= nicht konvertierter Farmwert, GDD: „was gebaut wurde =
  was NICHT gesendet wurde“) wird getrennt geführt — Reveal-Basis für #27.
- **Dokumentierter Fallback:** Fehlt die Ressourcen-Event-API (Getter nicht
  lesbar), schaltet der Mod nach 3 Fehlern dauerhaft auf HourEvent-Tick-
  Einkommen um (Muster Baustein 05; Befund: kein verifizierter Konto-Zugriff,
  `docs/research/api-deep-dive.md` §1).

**v0.3.0 — Mod-Core (Foundation):**
- **Issue #26:** Send-Spawns spawnen an den **16 natürlichen Kartenrand-Spawnern**
  (DOM-Gruppen `spawn_enemy_border_{south,north,east,west}`, zufällige Auswahl je
  Kreatur) statt am Spieler-Mech — **kein Spieler nötig** (Server-only-tauglich).
  DOM-Naturwellen bleiben unangetastet (Basis-Druck). Fallback auf den alten
  Mech-Ring nur, wenn eine Welt keine Rand-Spawner hat.
- **Issue #23:** DOM-Wellen-Vorbereitung auf **300 s** gedeckelt
  (prepareSpawnTime 420→300, 5-Min-Wellen) + Setup-Log (`difficulty`,
  `creatures_difficulty`). Difficulty/Map-Größe/Seed werden beim Server-Start
  gesetzt (C++, kein Lua-Weg) — Ablauf: `docs/DUEL_SETUP.md`.
- Neuer Command-Alias **`rb_send`** (gleiche Logik wie `rb_wave`).
- Konzept-Doku: `docs/SEND_HOOK.md` (Wellen-Hook-Strategie), `docs/SYNC_START.md`
  (Pause/Unpause-Befund #22).

Inhalt des Mod-Ordners: Skeleton-Lebenszeichen-Log beim Laden +
Console-Commands `rb_wave <level>` / `rb_send <level>` (Send-Wellen-Spawning an
Kartenrand-Spawnern) + DOM-Timer-Deckel + Economy (`rb_convert`, `rb_economy`) +
Send-Queue & Shop-HUD (`rb_buy_wave`, `rb_shop`, `rb_queue`, Send-Queue-Hook, #25) +
Win-Condition (`rb_hq`, Leak-/HQ-Tod → Match-Ende, #28).
Custom-UI-Shop-Popup (`rb_shop`), keine Bindings, kein Bridge-Zusatz,
**kein io/socket/http**.

**Mod-Descriptor** (`<GUID>.manifest` im Mod-Root): deklariert Metadaten + die
Spielversion, gegen die der Mod gebaut ist (`game_version "EXE: 1186 DATA: 847"`
für Spiel 2.0.58485). Ohne Descriptor zeigt der Client „Unknown game version“
(Issue #17). Format = `WorkspaceManifest { … }` der EXOR-Workspace-Tools
(Beleg: echte Workshop-Mods, z. B. github.com/lilly1987/Riftbreaker-mods;
Spiel-Regex `EXE: <n> DATA: <n>`).

## Installation (lokaler Mods-Ordner)

Der Inhalt dieses Ordners (`mod/`) ist **eine Mod**: Er spiegelt die
Content-Struktur des Spiels (`lua/`, …) — genau wie es die EXOR-Workspace-Tools
und Workshop-Mods tun (Quelle: fandom „Basic Modding Guide“, Ordner
`<game>/mods/<ModName>/`; echte Mods: github.com/lilly1987/Riftbreaker-mods).

### Windows (Steam)
1. Steam-Bibliothek finden: z.B. `D:\SteamLibrary\steamapps\common\Riftbreaker`
2. Ordner anlegen bzw. kopieren:
   ```
   <SteamLibrary>\steamapps\common\Riftbreaker\mods\rbbattle\
       lua\rbbattle_autoexec.lua
       {96745BE8-78FD-4C30-9718-D57AA40B9C09}.manifest
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
- Dort analog `mods/rbbattle/` anlegen (Ordner ggf. neu erstellen).
- ⚠️ Offiziell heißt es „Steam **und** GamePass **PC** können modden“ —
  **ungetestet auf macOS**, in-game prüfen (siehe FINDINGS). Steam-Ordner muss
  nicht zwingend „common“ heißen — Pfad über Steam → Verwalten → Lokale Dateien
  anzeigen lassen.

### Update per Tool (empfohlen)

Statt manuell zu entpacken: `tools/mod-updater/mod_update.py` findet den
Mods-Ordner, holt das aktuelle `rbbattle.zip`, legt ein Backup der alten
Version an und ersetzt die Mod ([#120](https://github.com/momokli/riftbreaker-battle-mod/issues/120),
Details in `tools/mod-updater/README.md`):

```bash
python3 tools/mod-updater/mod_update.py update
```

## Aktivierung / In-Game-Konsole

- Konsole öffnen mit `` ` `` / `~` / `ö` / `'` (je nach Tastatur-Layout;
  bei deutscher Tastatur: `ö`). Quelle: fandom „Console commands“.
- Falls die Konsole nicht aufgeht: in
  `<Documents>\The Riftbreaker\Conf\initial_config_win` (o.ä.) prüfen —
  `set enable_developer_console 0` → `1` (bei GamePass nötig; Steam meist offen).
- **Log-Datei**: `<Documents>\The Riftbreaker\exor_logs.txt` — dort schreibt
  `LogService:Log`; alle Mod-Zeilen tragen den Präfix `[RBBATTLE]`.

## Commands

| Eingabe | Wirkung |
|---|---|
| `rb_wave 1` … `rb_wave 3` (Konsole/Bridge) | Spawnt Send-Welle an **zufälligen natürlichen Kartenrand-Spawnern** (5 Brabits / +3 Baxmoth / +2 Artigian +1 Canceroth, je Kreatur zufälliger Spawner aus den 4 Gruppen `spawn_enemy_border_*`); ungültige Stufe (`rb_wave 99`) fällt mit Warnung auf Welle 1 zurück. Kein Spieler-Mech nötig (Server-only). Ohne Rand-Spawner: Fallback-Ring um den Mech |
| `rb_send <level>` | Alias für `rb_wave` (Send-Semantik für Shop-/Queue-Integration #25) |
| `rb_wave`-Log-Anker | `anchor=border spawners=N` (bzw. `anchor=fallback_mech`), je Kreatur `anchor=<gruppe>/<id>` im `event=spawn ok`-Log |
| `rb_convert <resource> <amount>` | Wandelt gefarmte Ressource **irreversibel** in Send-Währung (Spar-Pool). **MVP (#40):** Send-Währung ist **NUR Calcium** (`carbonium`, Faktor 1) — `rb_convert <menge>` ≡ `rb_convert calcium 100` ≡ `rb_convert carbonium 100`. Jede andere Ressource wird abgelehnt (`status=not_send_currency`); Mapping zentral in `RBB.economyCfg.sendCurrency` (Ironium-Qualitäts-Split folgt). Ablehnung bei zu wenig Farm-Menge (`status=insufficient`); kein Rücktausch. Balance = Platzhalter (Tuning #33) |
| `rb_economy` / `rb_economy reset` | Status: Quelle, Pool, farmed/converted/built, Ressourcen-Konten, DB-Status. `reset` = Entwickler-Werkzeug (alles auf 0, inkl. Ressourcen-Keys der DB) |
| `rb_buy_wave <unit> [count]` | **Kauf-Hook (#25):** kauft `<unit>` (Shop-Id: `brabit`/`baxmoth`/`artigian`/`canceroth`/`boss`) in die Send-Queue und deduziert den Spar-Pool sofort (irreversibel). `rb_shop` zeigt alle Units/Preise. Guards: unbekannte Unit, zu wenig Pool, Queue voll |
| `rb_shop` | **Custom-UI-Shop (#25):** öffnet ein Popup mit der Tier-/Preis-Liste (Tier 1/2/3 + Boss) + Konsolen-Liste (Fallback ohne Spieler/API) |
| `rb_queue` | **Send-Queue-Status (#25):** Anzahl, Gesamtwert und Blueprint-Liste der für die nächste Welle gekauften Einheiten |
| `rb_boost <stufe|pct>` | **Send-Boost (#39):** kauft einen prozentualen Aufschlag auf die nächste Naturwelle (Stufen `s1`=+25% (200), `s2`=+50% (400), `s3`=+100% (800) oder freie pct-Eingabe à 8 Währung/Prozentpunkt) aus dem Spar-Pool (sofort irreversibel). Beim nächsten natürlichen Wellenstart wird der Boost am `SpawnWavesForDifficultyLevel`-Chokepoint angewendet (difficultyLevel-Delta) und zurückgesetzt — genau eine Welle, nicht kumulativ. Guards: unbekannte Stufe, pct ≤ 0, `maxBoostPct` (200), `maxBoostsPerWave` (4), zu wenig Pool. `duel` = Boost-Flush nur in `sp` |
| `rb_mode sp\|duel` | Modus-Umschaltung: `sp` = Solo-Test (Default, sendet an die eigene nächste Welle), `duel` = 1v1 (Stub, folgt später) |
| `rb_status` | Zeigt `mode`, `runde`, `pool`, die `queue` und den `boost` (für die nächste Welle) — die Kontrollanzeige des Testmodus |
| `rb_hq` / `rb_hq leak [dmg]` / `rb_hq entity <id>` / `rb_hq reset` | Win-Condition-Status + Dev-Werkzeuge (#28): HQ-HP zeigen, manuellen Leak anwenden, HQ-Entity zuordnen, Zustand zurücksetzen (Muster `rb_economy reset`). Die HQ-Entity wird seit #144 zusätzlich automatisch versucht zu binden (`HqAutoDetectEntity`, bei `PlayerInitializedEvent`/jedem `rb_wave`); `rb_hq entity <id>` bleibt der manuelle Fallback, falls die Auto-Erkennung nichts findet |
| `rb_hud` | **Reveal-HUD (#27):** HUD-Standardfelder — Runde, Countdown, eigener Pool, HQ-HP beider Teams + Reveal-Zustand (`reveal=hidden\|revealed`). Gegner-Built/incoming/HQ sind vor Wellenstart `hidden` |
| `rb_reveal <built_opp> <hq_opp> [incoming]` | **Gegner-Injektion (#27):** die Bridge injiziert die vom Server aufgedeckten Gegner-Werte (Built-Value, HQ-HP, eingehende Send-Komposition) → Reveal beider Teams komplett |
| `rb_round_start [n]` | **Build-Phase (#27):** verbirgt den Reveal wieder (Bridge-Signal „round steigt“); `<n>` nur informativ |
| `rb_hud_ui` | **Click-HUD (#99):** öffnet/schließt das HUD-Overlay (2-Button-Popup, Muster `rb_shop`). „Ja“ (`button_yes`) kauft die gerüstete Quick-Send-Einheit in die Send-Queue, „Nein“ (`button_no`) schließt ohne Aktion. Ein-/ausblendbar per erneutem Aufruf |
| `rb_quick [<unit> [count]]` | **Quick-Send rüsten (#99):** legt die per Klick gesendete Einheit fest (Default `brabit` ×1). `<unit>` = Shop-Id (`brabit`/`baxmoth`/`artigian`/`canceroth`/`boss`); `rb_shop` zeigt die Liste |
| `rb_quick_step <+N\|-N\|xN>` | **Send-Menge-Stepper (#147, MVP):** passt die per `rb_quick`/`rb_hud_ui` gerüstete Menge relativ an — `+1`/`-1` für Feinjustierung, `x10`/`x100`/`x1000` für Grobjustierung (Clicker-Stil), ohne eine exakte Zahl zu tippen. Gedeckelt auf `1..maxQueueCreatures` (40). Ein klickbares Stepper-Overlay ist mangels verifizierter Multi-Button-GUI-API noch offen — dieser Command liefert bereits die Logik dafür |

Erwartete Log-Zeilen in `exor_logs.txt` bei Kartenerstellung:

```
[RBBATTLE] skeleton ok
[RBBATTLE] event=mod_load version=0.29.0 status=ok mode=sp anchor=border_spawner_groups timer_cap=300 econ_source=none econ_pool=0
[RBBATTLE] event=economy_db status=new db=rbbattle_economy      ← erste Runde
[RBBATTLE] event=economy_source source=resource_obtained status=active   ← erste lesbare Ernte
[RBBATTLE] event=economy_farm source=resource_obtained resource=carbonium amount=100 value=100 farmed=100 built=100
[RBBATTLE] event=convert resource=carbonium amount=100 value=100 pool=100 status=ok irreversible=1
[RBBATTLE] event=buy_wave unit=brabit tier=t1 count=1 price=100 total=100 pool=0 queue=1 status=ok   ← Kauf-Hook (#25)
[RBBATTLE] event=boost status=ok pct=25 total_pct=25 price=200 pool=1800 buys=1   ← Send-Boost Kauf (#39)
[RBBATTLE] event=wave_hook patch status=ok                       ← Send-Queue-Hook aktiv (#42/#25)
[RBBATTLE] event=boost patch status=ok                           ← Boost-Chokepoint-Hook aktiv (#39)
[RBBATTLE] event=dom_timer patch status=ok cap=300        ← nach PlayerInitializedEvent
[RBBATTLE] event=setup difficulty=hard creatures_difficulty=5 timer_cap=300
[RBBATTLE] event=round round=1 status=start mode=sp pool=0 queue=1   ← natürlicher Wellenstart
[RBBATTLE] event=reveal round=1 status=revealed built_own=3000 built_opp=hidden send_own=units/ground/brabit:2 incoming=hidden   ← Wellenstart-Reveal (#27)
[RBBATTLE] event=send_queue round=1 status=done spawned=1 value=100 anchor=border   ← Send-Queue ausgeliefert (Boost)
[RBBATTLE] event=boost status=flush round=1 pct=25 level_from=2 level_to=3 delta=1 buys=1   ← Send-Boost angewendet (nächste Naturwelle) (#39)
[RBBATTLE] event=wave level=3 status=start
[RBBATTLE] event=wave_spawners count=16 groups=4          ← Pool der Rand-Spawner
[RBBATTLE] event=spawn ok blueprint=units/ground/baxmoth entity=12345 anchor=spawn_enemy_border_west/...
[RBBATTLE] event=wave level=3 status=done spawned=8 skipped=0 anchor=border spawners=16
[RBBATTLE] event=hq_autodetect status=ok group=headquarters entity=54321   ← automatische HQ-Bindung (#144, unverifizierter Gruppen-Name)
[RBBATTLE] event=hq_autodetect status=not_found candidates=headquarters hint=rb_hq_entity   ← kein Treffer -> manueller Fallback noetig (#144)
[RBBATTLE] event=hq_leak status=skip reason=no_hq_entity   ← Leak inaktiv ohne gebundene HQ-Entity (#143, Fix zu #28)
[RBBATTLE] event=hq_leak status=skip reason=no_trigger_entity   ← Trigger ohne lesbare Entity (fremder/generischer Trigger) uebersprungen (#152)
[RBBATTLE] event=hq_leak status=skip reason=own_hq         ← eintretende Entity ist die eigene HQ-Entity (#152)
[RBBATTLE] event=hq_leak status=skip reason=own_team       ← eintretende Entity ist eigener Mech/Team (kein Leak) (#152)
[RBBATTLE] event=leak damage=10 hp_before=100 hp=90        ← feindliche Kreatur erreicht HQ-Zone, NUR mit gebundener Entity (automatisch #144 oder `rb_hq entity <id>`, #28) UND Zone-/Team-Filter (#152)
[RBBATTLE] event=hq_hp hp=90 dead=false                    ← Report → Server (POST /report hq_hp)
[RBBATTLE] event=reveal_opp round=1 built_opp=6400 hq_opp=80 incoming=brabit:2 status=ok   ← Gegner-Werte injiziert (#27)
[RBBATTLE] event=hud round=1 countdown=300 pool=1800 built_own=3000 built_opp=6400 incoming=brabit:2 hq_own=100 hq_opp=80 reveal=revealed   ← HUD-Felder (#27)
[RBBATTLE] event=quick_send status=armed unit=brabit count=1 price=100   ← Quick-Send gerüstet (#99)
[RBBATTLE] event=quick_step status=ok op=x10 unit=brabit before=1 after=10   ← Send-Menge-Stepper (#147 MVP)
[RBBATTLE] event=hud_ui status=opened quick=brabit count=1 round=1 countdown=300 pool=1800 queue=2   ← Click-HUD offen (#99)
[RBBATTLE] event=hud_ui status=closed result=button_yes action=quick_send unit=brabit count=1   ← Klick auf „Ja“ sendet (#99)
[RBBATTLE] event=hq_dead status=match_end hp=0             ← HQ-Tod (HP ≤ 0)
[RBBATTLE] event=match_end reason=hq_destroyed winner=opponent
```

(Die genaue Zahl `count=` hängt von der Karte/Map-Size ab — Issue-Erwartung 16.)

## FINDINGS-Tabelle (Stand Recherche — In-Game-Test des Umbaus offen)

| # | Frage | Ergebnis laut Doku + Original-Spieldaten | Quelle |
|---|---|---|---|
| 1 | Mod-Layout / Einstiegspunkt | Ordner `<game>/mods/<name>/` spiegelt Content-Root; `lua/*_autoexec.lua` läuft bei Map-Erstellung, Zugriff auf alle Services + `RegisterGlobalEventHandler` | exorstudios-Wiki (autoexec.md); lilly1987/Riftbreaker-mods; fandom Basic Modding Guide |
| 2 | Wave-Spawn zur Laufzeit | ✅ `EntityService:SpawnEntity(blueprint, x, y, z, team)` — exakt die Implementierung von EXORs eigenem `debug_spawn_entity` (`lua/commands/cheat.lua`); Blueprints `units/ground/*` gegen `entities/units/ground/*.ent` der Spieldaten verifiziert | OriginalPacksData (PonomarevDmitry/RiftbreakersMods); fandom Console commands |
| 3 | Rand-Spawner finden | ✅ `FindService:FindEntitiesByGroup(group)` — dieselbe API, mit der `dom_manager` (`RandomizeSpawnPoint`) Naturwellen-Anker wählt; Gruppen `spawn_enemy_border_{west,east,north,south}`, Entities werden von `mission_base:SelectWaveSpawnPoints` aus `logic/spawn_enemy`-Entities gruppiert | lua-src 2.0.58485 (`dom_manager.lua`, `mission_base.lua`, `find_utils.lua`) |
| 4 | 5-Min-Timer (#23) | ✅ `dom_mananger:GetPrepareSpawnTime()` liefert rules-Wert (Survival hard/normal: 420); Mod wrappt die Klassen-Methode auf max. 300 s (idempotent, pcall) | lua-src (`dom_survival_*_rules_hard.lua`, `dom_manager.lua:1135`) |
| 5 | Custom Console Commands | ✅ `ConsoleService:RegisterCommand(name, cb)` — offiziell dokumentiert **und** von EXOR selbst so genutzt (cheat.lua: `debug_spawn_entity` …) | exorstudios-Wiki accessing-keyboard-hotkeys.md; fandom Mod service: ConsoleService; OriginalPacksData lua/commands/cheat.lua |
| 6 | Logging | ✅ `LogService:Log(...)` → `exor_logs.txt`; `ConsoleService:Write(...)` → In-Game-Konsole | exorstudios-Wiki debugging-using-lua-services.md |
| 7 | Team-Semantik | Team-String `""` = „Blueprint-Standard“ (EXOR-Cheat nutzt `""`; Spieler-Buildings wie Feinde spawnen korrekt). Team-Ids: Player=1 (Log `GetTeamId` → 1). `"no_team"` existiert für Marker. Für echte Duell-Teams später verifizieren | OriginalPacksData (cheat.lua, wave_ground.lua) |
| 8 | Pause/Unpause | ✅ `debug_dom_pause` **und** `debug_dom_resume` existieren (Lua, debug.lua:73/77); Server-Pause (`debug_pause_server`, `cfg_server_pause_game_when_empty`) ist nativ (C++) | lua-src 2.0.58485; `lan:/home/momo/rb-game/LOBBY_RESEARCH.md` |

**Bekannte offene Punkte für den In-Game-Test:** (1) exakte Spawner-Zahl der
Duell-Karte (Log `event=wave_spawners count=`); (2) Wirksamkeit des
Klassen-Monkey-Patch im echten Autoexec-Environment (Log `event=dom_timer
patch status=ok` = Indiz; Bestätigung über Wellenabstand/`debug_dom_manager 1`);
(3) exakte Feind-Team-Zuordnung bei `SpawnEntity` mit `""` (Blueprint-Standard
erwartet, s. #7); (4) macOS-Mod-Support ungeklärt.
(5) **Win-Condition (#28):** `EnteredTriggerEvent`-Feuerung und das
Trigger-Zone-Asset ums HQ sind nicht belegt (Repo-Recherche hat kein
Trigger-Event, s. api-deep-dive.md) — Handler ist pcall-gesichert registriert,
Log `event=hq_zone status=skip|pending|armed` zeigt den Zustand.
(6) **HQ-Entity-Identifikation (#28, #144):** `HqAutoDetectEntity()` versucht
bei `PlayerInitializedEvent`/jedem `rb_wave`-Aufruf automatisch über
`FindService:FindEntitiesByGroup("headquarters")` zu binden (Gruppen-Name aus
`docs/GAME_DESIGN.md` — dort selbst **unverifiziert**, in `docs/research/`
nicht belegt). Nur ein eindeutiger Treffer wird gebunden
(`event=hq_autodetect status=ok`); bei 0/>1 Treffern bleibt `rb_hq entity <id>`
der verlässliche manuelle Fallback (`event=hq_autodetect status=not_found`,
einmalig geloggt). **Braucht In-Game-Bestätigung**, ob `"headquarters"` der
richtige Gruppen-Name ist — falls nicht, weitere Kandidaten in
`RBB.hqEntityGroupCandidates` ergänzen.

## Technische Notizen

- **Statische Verifikation (2026-09-09, Pipeline):** `luaparse` (Lua-5.1-Syntax)
  OK; Ausführung in fengari-Lua-VM mit Stub-Services. v0.3.0: 4 Szenarien (16
  Rand-Spawner → 8 Spawns `anchor=spawn_enemy_border_*` ohne Spieler;
  Timer-Wrap 420→300 / Werte <300 bleiben; Fallback Mech-Ring; kein Anker).
  v0.4.0: 3 Szenarien / 30 Checks (Farm-Event-Ladder + Source-Lock;
  Convert irreversibel + Faktoren + Guards; Persistenz-Resume nach Neustart;
  Reset; Fallback tick nach 3 Handler-Fehlern). v0.5.0: 2 Szenarien / 22
  Checks (rb_mode Default/Wechsel/usage; rb_convert Calcium-first + Alias +
  Guards; rb_status Runde/Pool/Boost; Self-Boost-Hook: Original-OnEnterSpawn
  zuerst, Runden-Zähler, Pool greedy → Spawn, Rest-Pool). v0.6.0: keine
  Mod-Änderung (Tooling-Release, #7/#8). v0.8.0: keine Mod-Änderung
  (Tooling/Site-Release, #9/#56). v0.7.0: 5 Szenarien /
  14 Checks (Anker-Kette border→mission→mech; Missions-Fallback ohne Spieler
  spawnt 5/8 Kreaturen im Ring um den Spawnpunkt; Initial-Spawnpoint-Fallback;
  kein Anker → Skip). v0.9.0: 18 Checks / 7 Szenarien (Win-Condition:
Leak → HQ-HP-Senkung → Match-Ende bei HP ≤ 0; RespawnFailedEvent-Kette der
HQ-Entity; negative Fälle: andere Entity / ohne Entity-Zuordnung; Idempotenz
nach HQ-Tod). v0.12.0: 1 Szenario / 23 Checks (Send-Queue & Shop-HUD:
rb_shop Tier-Liste + Popup; rb_buy_wave Guards usage/unbekannt/insufficient;
Farm→Convert→Kauf brabit/boss→Queue; rb_queue-Status; Wellenstart → Flush →
send_queue done; Queue danach leer; erneuter Kauf + 2. Welle). v0.18.0:
Reveal-HUD (#27) 1 Szenario / 14 Checks (rb_hud vor Wellenstart reveal=hidden;
Farm→Convert→Kauf → built_own=3000; Wellenstart → event=reveal mit
send_own=brabit:2; rb_reveal → built_opp/incoming/hq_opp; rb_hud beide Teams;
rb_round_start → reveal=hidden; 2. Wellenstart). In-Game-Test
steht aus (Operator, Prod).
- **Economy-Fallback dokumentiert:** Der Mod hat keinen verifizierten Zugriff
  aufs Spieler-Ressourcen-Konto (api-deep-dive.md §1); Value kommt aus
  Ernte-Events (Getter-Ladder). Sind die Events nicht lesbar, schaltet die
  Quelle nach 3 Fehlern dauerhaft auf HourEvent-Tick um (Log
  `event=economy_source source=tick status=fallback reason=handler_errors`).
- Balance-Zahlen (Faktoren, Tick-Wert) sind Platzhalter — zentrale Tabelle
  `RBB.economyCfg` am Economy-Block (Tuning: Issue #33).
- Alle fremden API-Aufrufe sind `pcall`-gesichert: fehlt eine Funktion, kommt
  ein Log statt eines Crashes.
- Blueprint-/Wellen-Definitionen stehen als Konstanten am Dateikopf
  (`RBB.waves`) — dort tunen, wenn der Test läuft.
- Herkunft: v0.2.0-single (feature/single-mod, PR #15); Bausteine 00/01 bleiben
  als Test-Komponenten erhalten.
- Weitere Doku: [`../docs/concept.md`](../docs/concept.md) ·
  [`../docs/findings.md`](../docs/findings.md) ·
  [`../docs/SEND_HOOK.md`](../docs/SEND_HOOK.md) ·
  [`../docs/DUEL_SETUP.md`](../docs/DUEL_SETUP.md) ·
  [`../docs/SYNC_START.md`](../docs/SYNC_START.md)
