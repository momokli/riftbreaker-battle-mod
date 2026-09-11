# RIFT BATTLE — Deployment & Stack

> Ziel-Stack + Deployment-Plan. **Deploy NUR über das Ansible-Playbook in
> `deploy/`** (kein manuelles Gedudel). Umsetzung: Issue #45.

## Ziel-Stack (was IMMER betrieben wird)

| Komponente | Host | Container/Unit | Port | Zweck |
|---|---|---|---|---|
| riftbreaker-dedicated | planet | docker (wine) | 6321/udp | Dev-SP-Server: 1v1 „vs sich selbst" (SP-Mode; rbbattle-Mod + rbbridge) |
| rb-winetest | planet | docker (wine) | 6322/udp | Vanilla-Server (kein Mod, schnelle Test-Joins; gleiches Image wie :6321) |
| tournament-server | planet | systemd (Rust/axum, `tournament/`) | 8080 | Turnier 1v1: Lobby/Ready/GO/Wave-Routing/Score (2 Welten) |
| test-Instanzen | planet | docker, on-demand | frei | Test-Server aller Art (Mod-Tests, Balance, Experimente) |
| Website | planet | statics + Caddy (`mellon-caddy`) | 443 | Landing `/` · `/connectivity.html` · `/solo.html` · `/status.json` · Proxy `/tournament/*` → tournament-server |
| Mod-Download | planet | statics (Caddy) | 443 | `rbbattle.zip` (Paketierung + md5-Parität) |
| rbmods-probe.timer | planet | systemd | — | Connectivity-Checks alle 2 Min → `status.json` |
| rbbridge | in Mod-Containern | Prozess | — | Command-Injection (`exec_cmd_client`, Argument IMMER als EIN gequotierter String) |

## Kanonische Landing

- **`site/` ist die einzige kanonische Landing** (GitHub Pages via
  `pages.yml`, Source-Pfad `site`, kein Build-Schritt). Der Download läuft über
  den **deployten Stand** `https://rift.projectmellon.de/mods/rbbattle.zip`
  (Caddy, vom Deploy atomar ausgetauscht). GitHub-Tags (`v*`) sind seit
  Issue #209 **reine Marker** — keine GitHub-Releases, keine Release-Artefakte.
- `docs/index.html` ist **keine zweite Landing** mehr: ein dünner
  Verweis/Redirect auf die Landing, ohne eigene Download-/Versions-Links.

## Deployment-Plan (Ansible, inventory `planet`)

Rollen in `deploy/roles/` (Details: `deploy/README.md`):

1. **mods-zip** — Mod aus `mod/` paketieren (`scripts/package_bausteine.sh`),
   `rbbattle.zip` nach planet; **md5-Paritäts-Check (Zip == Prod) hart als
   Fehlschlag**.
2. **headless-client-image** — baut `rb-headless-client:<deploy-sha>` IM
   Playbook auf planet aus `tools/headless-client` (gemeinsame Wine-Laufzeit
   für :6321 + :6322; Docker-Layer-Cache → billig/idempotent). Das gerenderte
   Compose pinnt exakt diesen Tag (kein `latest`).
3. **game-content** — Dedicated-Server-Content (Steam-App 4114030) deklarativ
   nach `riftbreaker_game_dir` (SteamCMD anonym; Fallback: idempotenter Sync aus
   kanonischem Cache). Konvergiert nach `rm -rf`; Fehlschlag ist laut.
4. **riftbreaker-server** — Docker-Container + Server-Config (Welt
   `mp_survival`/`jungle`, `disable_steam`, Passwort aus Vault), Mod-Install
   in `<game>/mods/rbbattle`; Restart-Handler bei Mod-/Config-Änderung.
5. **vanilla-server** — zweite Instanz ohne Mods (6322), gleiches Image.
6. **tournament-server** — systemd-Unit, Env-Konfig (`RBBRIDGE_A_URL`/
   `RBBRIDGE_B_URL`), Binary + Web-UI aus `tournament/`.
7. **website** — statische Dateien (`site/*`) nach Docroot, Caddy-Snippet
   (statics + `/tournament/*`-Proxy) + Reload.
8. **probe-timer** — systemd-Timer für `scripts/probe_servers.sh` →
   `status.json`.

Grundsätze:

- **Idempotent** — jeder Lauf konvergiert auf denselben Zustand.
- **Deploy nur via Playbook** —
  `ansible-playbook -i deploy/inventory deploy/site.yml --ask-vault-pass`.
- **Rollback** = vorherige `rbbattle.zip` / vorheriges Binary wieder einspielen.

## Mod-Backups & mods/-Guard (Issue #212)

**Regel:** Mod-Backups liegen **NIE** innerhalb von `<server>/mods/` — weder
als Ordner noch als `.tar.gz`. Zielpfad ist außerhalb, z. B.
`/srv/riftbreaker/backups/` (rollen-seitig `{{ riftbreaker_backup_dir }}`,
Default `{{ riftbreaker_game_dir }}-backups`).

**Warum (Befund planet, 2026-09-10):** Der Dedicated Server scannt **alle**
Unterordner von `<server>/mods/` und lädt jeden Ordner mit einer `*.manifest`
als eigene External-Content-Mod — unabhängig vom Ordnernamen. Ein Backup-Ordner
mit gleicher Content-ID (`rbbattle.bak-<ts>/`) wird **zusätzlich** geladen: die
Versionskonstante wird überschrieben (Log zeigte fälschlich
`event=mod_load version=0.27.3`, obwohl die aktive Datei `0.33.0` war) und beide
Kopien registrieren ihre Handler doppelt →
`[RBBATTLE] event=economy_source source=tick status=fallback
reason=handler_errors err=lua/rbbattle_autoexec.lua:1084: event_unreadable`.
Nach Entfernen des Ordners aus `mods/` + Container-Restart:
`event=mod_load version=0.33.0 status=ok`, keine `handler_errors`.

**Durchgesetzt in drei Stufen** (Rolle `deploy/roles/riftbreaker-server`):

1. **Backups außerhalb** — der alte Mod-Stand wird als
   `{{ riftbreaker_backup_dir }}/rbbattle-<ts>.tar.gz`
   (`tar -C <mods> rbbattle`) gesichert; entpackt wird immer nur nach
   `mods/rbbattle/`.
2. **Guard vor dem Deploy** (idempotenter Ansible-Task) — außer dem Ziel-Mod
   (`rbbattle/`) darf kein weiterer Ordner mit `*.manifest` in `mods/` liegen.
   Fremd-Ordner werden nach `{{ riftbreaker_backup_dir }}/stray-<ts>/`
   weggeschoben (`riftbreaker_mods_guard_autofix: true`, Default); danach prüft
   ein `assert` hart nach. Mit `riftbreaker_mods_guard_autofix: false` bricht
   der Deploy stattdessen sofort ab.
3. **Post-Deploy-Verifikation** — `riftbreaker_mod_log_cmd` (Default:
   `docker exec riftbreaker-dedicated cat /root/exor_logs.txt`) muss genau
   **eine** `event=mod_load`-Zeile mit der erwarteten Version + `status=ok`
   enthalten und **keine** `handler_errors`/`event_unreadable`. **Quelle ist der
   Lua-Log im Container, nicht `docker logs`**: der Dedicated Server schreibt
   `event=mod_load`/`handler_errors` nach `exor_logs.txt` im Wine-Prefix; im
   Prefix ist `drive_c/users/root/Documents` ein Symlink auf `$HOME`
   (`Documents -> /root`) → `/root/exor_logs.txt`. `docker logs` enthält nur die
   Wrapper-Zeilen von `run-server.sh` und damit **nie** eine `mod_load`-Zeile
   (Befund planet 2026-09-11, Issue #224). Schlägt die Verifikation fehl, wertet
   die Rolle den Deploy als fehlgeschlagen und rollt aus dem
   `rbbattle-<ts>.tar.gz` zurück (sofern vorhanden) + startet den Container neu;
   erst dann `fail`. Ein leerer Log ergibt eine klare `fail_msg` (kein
   Ansible-Task-Arg-Crash).

**Kontrollwerkzeug / Regression-Check** (lokal + CI, Exit 1 = Fremd-Ordner):

```bash
python3 tools/mods-guard/check_mods_dir.py /srv/rbgame/mods
```

Siehe [`tools/mods-guard/`](../tools/mods-guard/README.md).

## Continuous Deploy (CD) — Issue #91

Nach jedem Merge auf `main` deployt
[`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml) automatisch auf
den Solo-DEV-Server (planet, Port 6321) — **rolling**, immer der aktuelle Stand
zum Testen. Der Job läuft auf dem self-hosted Runner auf planet und stößt dort
den lokalen **HTTP-Deploy-Hook** an (`rbbattle-deploy-hook`, `127.0.0.1:6323`):
Der Hook macht `git fetch` + Hard-Checkout der Commit-SHA und führt das
Ansible-Playbook aus (`ansible-playbook -i deploy/inventory deploy/site.yml
--vault-password-file …`). **Kein SSH aus CI mehr.** Installation/Migration:
`deploy/README.md` → „CD: HTTP-Deploy-Hook".

Topologie (Momo-Entscheidung, 2026-09-10): **EIN** Server auf `:6321` statt
Steam-/Non-Steam-Dualität; ein Direct-IP-Server (`disable_steam "1"`) deckt
beide Stores ab. Der **Tag→prod-Kanal ist gestrichen**: `tags: ['v*']` sind seit
Issue #209 **reine Marker** (kein Tag-Trigger, keine GitHub-Releases, keine
prod-Umgebung im Workflow). Veröffentlichter Download ist der deployte Stand
`https://rift.projectmellon.de/mods/rbbattle.zip`.

Einziges GitHub-Secret ist `DEPLOY_TOKEN` im Environment `dev` (Bearer-Token
Hook ↔ Workflow). Das Vault-Passwort liegt ausschließlich root-only auf planet
(`/etc/rbbattle-deploy/vault.pass`) und wird nie im Repo oder in Logs ausgegeben.

## Server-Passwort (Vault)

Das Server-Passwort liegt **nie im Klartext** im Repo. Es steht in
`deploy/inventory/host_vars/planet/vault.yml` (Variable
`riftbreaker_server_password`) und wird mit `ansible-vault` verschlüsselt.
Befüllung: siehe `deploy/README.md` → „Vault". Für den CD-Hook liegt das
Vault-Passwort als root-only Datei auf planet — **niemals** auf GitHub.

## Aktueller Zustand (2026-09-10, Issue #209)

`deploy/` **besitzt den Stack**: das Playbook baut das Laufzeit-Image
(`rb-headless-client:<deploy-sha>`) selbst, provisioniert den Steam-Content und
pinnt das Compose auf den Deploy-SHA. Ein from-zero-Aufbau braucht keine
manuellen Schritte auf planet (`deploy/README.md` → „From-zero").

Der alte Community-Stack (`j3n5-group/riftbreaker-docker`, Steam-basiert) unter
`/srv/riftbreaker` ist abgelöst; `/srv/riftbreaker/data/server` bleibt als
**kanonischer Steam-Content-Cache** liegen (Sync-Fallback für `game-content`).
Mod-Instanzen: `/srv/rbgame` (:6321), `/srv/rbgame-vanilla` (:6322); Compose
unter `/opt/rbmods/compose/…`.

## Interim-Deploy :6321 (Issue #156) — historisch, durch #209 überholt

Bis zur CD (#91) wird die Mod auf :6321 manuell eingespielt (reproduzierbar):

```bash
# 1) Mod-Zip aus Repo main bauen (Content-Root = mod/):
bash scripts/package_bausteine.sh          # → dist/rbbattle.zip

# 2) Auf planet kopieren + md5-Parität (lokal == remote):
scp dist/rbbattle.zip planet:/tmp/rbbattle.zip
md5sum dist/rbbattle.zip                   # lokal
ssh planet md5sum /tmp/rbbattle.zip        # remote, muss übereinstimmen

# 3) Spieler-Check VOR jedem Neustart (leer = letzter Log `PauseGame`).
#    Sind Spieler online: NICHT neu starten, im Issue vermerken.
ssh planet 'tail -3 "/srv/riftbreaker/data/wine/drive_c/users/steamuser/Documents/The Riftbreaker/exor_logs.txt"'

# 3b) Guard: in mods/ darf außer rbbattle/ KEIN weiterer Ordner mit *.manifest
#     liegen (sonst lädt der Server Backup-/Fremd-Kopien zusätzlich).
ssh planet 'find /srv/riftbreaker/data/server/mods -name "*.manifest" \
  -printf "%h\n" 2>/dev/null | sort -u | grep -v "/mods/rbbattle$"'   # leer = ok

# 4) Mod ersetzen — Backup AUSSERHALB von mods/, entpacken, Ownership beibehalten:
ssh planet 'mkdir -p /srv/riftbreaker/backups && cd /srv/riftbreaker/data/server/mods && \
  tar -czf /srv/riftbreaker/backups/rbbattle-$(date +%Y%m%d-%H%M%S).tar.gz rbbattle && \
  rm -rf rbbattle && mkdir rbbattle && \
  unzip -q /tmp/rbbattle.zip -d rbbattle && chown -R momo:momo rbbattle'

# 5) Container neu starten:
ssh planet 'cd /srv/riftbreaker && docker compose restart riftbreaker-server'

# 6) Smoke-Test: GENAU EINE mod_load-Zeile mit erwarteter Version + status=ok,
#    KEINE handler_errors/event_unreadable; Container healthy, Port 6321/udp offen.
#    (Andernfalls: Deploy als fehlgeschlagen werten + Backup zurückrollen.)
ssh planet 'grep -a -c mod_load "/srv/riftbreaker/data/wine/drive_c/users/steamuser/Documents/The Riftbreaker/exor_logs.txt"'
ssh planet 'grep -a mod_load "/srv/riftbreaker/data/wine/drive_c/users/steamuser/Documents/The Riftbreaker/exor_logs.txt" | tail -1'
```

Mod-Ordner (Host → Container): `data/server/mods/rbbattle` →
`/opt/riftbreaker/mods/rbbattle`. `exor_logs.txt`:
`data/wine/drive_c/users/steamuser/Documents/The Riftbreaker/`.
Rollback: Backup-`tar.gz` aus `/srv/riftbreaker/backups/` nach
`data/server/mods/` zurückentpacken + Container neu starten.

## Betriebsregeln

- Mod-Parität vor jedem Release prüfen (md5).
- Mod-Backups **nie** in `<server>/mods/` (siehe „Mod-Backups & mods/-Guard").
- Live-Tests nur bei leerem Server.
- Keine Credentials in Repo/Logs; kanonischer Server-Log ist `exor_logs.txt`
  im Container (`/root/exor_logs.txt`, Wine-`Documents -> /root`), `rbbridge.log`
  im Temp. `docker logs` des Dedicated-Servers zeigt nur die `run-server.sh`-Wrapper-Zeilen.
- SSH mesh-first (Tailscale), nie über Public-IPs.
