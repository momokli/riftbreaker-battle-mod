# RIFT BATTLE — Deployment & Stack

> Ziel-Stack + Deployment-Plan. **Deploy NUR über das Ansible-Playbook in
> `deploy/`** (kein manuelles Gedudel). Umsetzung: Issue #45.

## Ziel-Stack (was IMMER betrieben wird)

| Komponente | Host | Container/Unit | Port | Zweck |
|---|---|---|---|---|
| riftbreaker-dedicated (**release**) | planet | docker (wine) | 6321/udp (extern) | Release-/Spieler-Server, an einen Git-Tag gebunden (Direct-IP-Join, `disable_steam "1"`) |
| riftbreaker-dedicated-dev (**dev**) | planet | docker (wine) | 6322/udp (extern) | Rolling DEV-Server (`main`), immer der aktuelle Stand zum Testen |
| tournament-server | planet | systemd (Rust/axum, `tournament/`) | 8081 | Turnier 1v1 für die **release**-Instanz: Lobby/Ready/GO/Wave-Routing/Score |
| tournament-server-dev | planet | systemd (Rust/axum, `tournament/`) | 8082 | Turnier 1v1 für die **dev**-Instanz |
| test-Instanzen | planet | docker, on-demand | frei | Boot-Test/Experimente — eigene Ports (z. B. 6323), **nie** Prod-Ports |
| Website | planet | statics + Caddy (`mellon-caddy`) | 443 | Landing `/` · `/connectivity.html` · `/solo.html` · `/status.json` · Proxy `/tournament/*` → tournament-server |
| Mod-Download | planet | statics (Caddy) | 443 | `rbbattle.zip` (Paketierung + md5-Parität) |
| rbmods-probe.timer | planet | systemd | — | Connectivity-Checks alle 2 Min → `status.json` (eine Zeile je Instanz) |
| rbbridge | in Mod-Containern | Prozess | — | Command-Injection (`exec_cmd_client`, Argument IMMER als EIN gequotierter String) |

## Server-Modell: Multi-Instanz (Issue #290)

Auf planet laufen **zwei koexistierende Dedicated-Server-Instanzen**, beide per
Direct-IP (`65.21.27.234:<port>`, `disable_steam "1"`) von außen erreichbar. Jede
Instanz hat einen eigenen Stand-Kanal, eigene Container-/Unit-/Pfad-/Volume-
Namen und einen eigenen UDP-Port — kein Clash.

**Instanz-Schema** (Quelle: `deploy/instances/<name>.yml`; Auswahl via
`-e riftbreaker_instance=<name>`, Default `release`):

| Instanz | Kanal | Game-UDP (extern) | Bridge (nur 127.0.0.1) | Tournament | Container | Game-Dir | Compose | Unit |
|---|---|---|---|---|---|---|---|---|
| `release` | Git-Tag `vX.Y.Z` | 6321 | 9001 | 8081 | `riftbreaker-dedicated` | `/srv/rbgame` | `/opt/rbmods/compose/riftbreaker-dedicated` | `tournament-server` |
| `dev` | `main` (rolling) | 6322 | 9004 | 8082 | `riftbreaker-dedicated-dev` | `/srv/rbgame-dev` | `/opt/rbmods/compose/riftbreaker-dedicated-dev` | `tournament-server-dev` |
| `test` (Boot-Test) | PR-SHA | 6323 | 9003 | 8091 | `riftbreaker-dedicated-test` | `/srv/rbgame-test` | `/opt/rbbattle-deploy/test/rb` | `tournament-server-test` |

Regeln:

- **Game-Content getrennt pro Instanz** (`/srv/rbgame` vs. `/srv/rbgame-dev`),
  beide gespiegelt aus dem kanonischen Steam-Cache
  (`/srv/riftbreaker/data/server`, `game-content`, Modus `sync`). Der
  `mods/`-Guard gilt je Instanz.
- **Bridge-Ports** sind nur an `127.0.0.1` des Hosts publiziert (intern); der
  Container-Port ist konstant `9001`, nur das Host-Mapping variiert je Instanz.
  `tournament_bridge_a_url` zeigt auf den Host-Port der eigenen Instanz.
- **Nur die externen Instanzen** (`release`, `dev`) brauchen offene UDP-Ports.
  UFW ist host-seitig (nicht Repo-owned): z. B. `ufw allow 6321/udp` und
  `ufw allow 6322/udp` (einmalig auf planet, bewusst außerhalb des Deploys).
- **Boot-Test** nutzt 6323/9003/8091 und kollidiert nicht mit laufenden
  Prod-Instanzen (`release` 6321/9001/8081, `dev` 6322/9004/8082).

**Deploy** (Details: `deploy/README.md`):

```bash
# eine Instanz (Default release):
ansible-playbook -i deploy/inventory deploy/site.yml -e riftbreaker_instance=release --ask-vault-pass
# alle Instanzen in einem Lauf:
ansible-playbook -i deploy/inventory deploy/fleet.yml --ask-vault-pass
```

Offene Punkte/Risiken (Ressourcen 2× Wine+Dedi, Engine-Mehrfach-Instanz,
Steam-Verhalten) stehen unter `Refs #290` im Issue.

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
2. **dedicated-server-image** — baut `rb-dedicated:<deploy-sha>` IM
   Playbook auf planet aus `tools/dedicated-server` (Wine-Laufzeit,
   Community-Rezept; Docker-Layer-Cache → billig/idempotent). EIN Image für
   alle Instanzen. Das gerenderte Compose pinnt exakt diesen Tag (kein `latest`).
3. **game-content** — Dedicated-Server-Content (Steam-App 4114030) deklarativ
   nach `riftbreaker_game_dir`. **Standard: idempotenter Sync aus dem
   kanonischen Cache** (`/srv/riftbreaker/data/server`, in Backups) — der
   SteamCMD-Modus ist deaktiviert (hängt an `lib32gcc-s1`, siehe
   `vars.yml`). Konvergiert nach `rm -rf`; Fehlschlag ist laut.
4. **riftbreaker-server** — Docker-Container + Server-Config (Welt
   `mp_survival`/`jungle`, `disable_steam`, Passwort aus Vault), Mod-Install
   in `<game>/mods/rbbattle`; Restart-Handler bei Mod-/Config-Änderung.
5. **tournament-server** — systemd-Unit, Env-Konfig (`RBBRIDGE_A_URL`/
   `RBBRIDGE_B_URL`), Binary + Web-UI aus `tournament/`.
6. **website** — statische Dateien (`site/*`) nach Docroot, Caddy-Snippet
   (statics + `/tournament/*`-Proxy) + Reload.
7. **probe-timer** — systemd-Timer für `scripts/probe_servers.sh` →
   `status.json` (eine Endpoint-Zeile je Instanz, aus `deploy/instances/*.yml`).

Grundsätze:

- **Idempotent** — jeder Lauf konvergiert auf denselben Zustand.
- **Instanz-Profile** — die instanz-spezifischen Werte stehen in
  `deploy/instances/<name>.yml` (Auswahl `-e riftbreaker_instance=<name>`,
  Default `release`); host-weite Werte in `host_vars/planet/vars.yml`.
- **Deploy nur via Playbook** —
  `ansible-playbook -i deploy/inventory deploy/site.yml --ask-vault-pass`
  (eine Instanz) bzw. `deploy/fleet.yml` (alle Instanzen).
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
3. **Post-Deploy-Verifikation** — **zweistufig** (Issue #226), weil der
   Dedicated Server im Idle (siehe „Log-Quelle & Timing" unten) **keinen**
   Lua-Log schreibt:
   1. **Artefakt-Check (hart, idle-sicher):** Manifest-Version *und*
      `RBB.version` im deployten Lua-Stand unter `{{ riftbreaker_mod_dir }}`
      müssen exakt der erwarteten Version (`mod_version` aus dem Mod-Manifest)
      entsprechen. Das ist der maßgebliche Gate.
   2. **Runtime-Log-Check (best effort):** existiert `exor_logs.txt`, muss er
      genau **eine** `event=mod_load`-Zeile mit der erwarteten Version +
      `status=ok` und **keine** `handler_errors`/`event_unreadable` enthalten.
      Fehlt der Log im Idle, ist das **kein Fehler** (die Artefakt-Prüfung gilt).

   Schlägt eine der Prüfungen fehl, wertet die Rolle den Deploy als
   fehlgeschlagen und rollt aus dem `rbbattle-<ts>.tar.gz` zurück (sofern
   vorhanden) + startet den Container neu; erst dann `fail`. Fehlende
   Versionen/leere Trefferlisten ergeben eine klare `fail_msg` (kein
   Ansible-Task-Arg-Crash).

### Log-Quelle & Timing (Befund planet 2026-09-11, Issue #226)

- **Quelle ist der Lua-Log im Container, nicht `docker logs`:** `docker logs`
  des Dedicated-Servers enthält nur die zwei `run-server.sh`-Wrapper-Zeilen
  (`[run-server] starte Xvfb …` / `[run-server] starte: wine bin/DedicatedServer.exe …`)
  und damit **nie** eine `mod_load`-Zeile.
- **Pfad:** `exor_logs.txt` im Wine-Prefix; `drive_c/users/root/Documents` ist
  ein Symlink auf `$HOME` (`Documents -> /root`) → `/root/exor_logs.txt`.
  Abruf: `docker exec riftbreaker-dedicated cat /root/exor_logs.txt`.
- **Timing:** Der Log entsteht erst, wenn der Server eine Map lädt und Lua
  ausführt. Mit `server_pause_game_when_empty=1` und **keinen Spielern** (Idle)
  passiert das **nicht** — nach einem Restart existiert `exor_logs.txt` im Idle
  gar nicht (Empirie: auch nach >5 Min kein Log, `Running=true`,
  `RestartCount=0`).
- Der Log liegt im **Container-Writable-Layer** (kein Bind-Mount des
  Wine-Prefix) und ist nach `docker compose up -d --force-recreate` ohnehin weg.
- Deshalb ist der **Artefakt-Check** (deployter Stand auf der Platte) im CD der
  harte Gate; der Runtime-Log-Check greift nur, wenn zum Prüfzeitpunkt
  tatsächlich eine Map geladen wurde.

**Kontrollwerkzeug / Regression-Check** (lokal + CI, Exit 1 = Fremd-Ordner):

```bash
python3 tools/mods-guard/check_mods_dir.py /srv/rbgame/mods
```

Siehe [`tools/mods-guard/`](../tools/mods-guard/README.md).

## Continuous Deploy (CD) — Issue #91

Nach jedem Merge auf `main` deployt
[`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml) automatisch auf
die **dev-Instanz** (planet, Port 6322) — **rolling**, immer der aktuelle Stand
zum Testen. Der Job läuft auf dem self-hosted Runner auf planet und verbindet
sich per **SSH als dedizierter deploy-User** (`ssh rbd "<sha> <ref> dev"`); die
forced command (`deploy/deploy-ssh.sh`) validiert SHA **und Instanz-Token**, macht
`git fetch` + Hard-Checkout und führt das Ansible-Playbook als root aus
(enges sudoers). Kein Token, kein Polling. Installation/Migration:
`deploy/README.md` → „CD: SSH-Deploy" und „Multi-Instanz-Deploy".

Die **release-Instanz** (planet, Port 6321) wird NICHT rolling deployt, sondern
aus einem Git-Tag über
[`.github/workflows/deploy-release.yml`](../.github/workflows/deploy-release.yml)
(`workflow_dispatch` + Tag `vX.Y.Z`, Umgebung `prod`) — der bewusst wieder
eingeführte **Tag→prod-Kanal** (Issue #209 hatte ihn gestrichen; mit dem
zweiten Deploy-Target ist er wieder sinnvoll). Damit ist der Live-/Spieler-Stand
an einen Tag gebunden, während `main` auf der dev-Instanz rollt.

Topologie (Issue #290): **zwei** Direct-IP-Server (`disable_steam "1"` deckt
Steam- und GOG-Client ab) — `release` :6321 (stabil, Tag) und `dev` :6322
(rolling). Veröffentlichter Download ist der deployte Stand
`https://rift.projectmellon.de/mods/rbbattle.zip` (aus dem jeweiligen
Deploy-Stand).

Der HTTP-Hook ist seit 2026-09-11 durch den SSH-Deploy abgelöst (Issue #235);
das `DEPLOY_TOKEN`-Secret im Environment `dev` wurde gelöscht — **es gibt kein
GitHub-Secret mehr**. Das Vault-Passwort liegt ausschließlich root-only auf
planet (`/etc/rbbattle-deploy/vault.pass`) und wird nie im Repo oder in Logs
ausgegeben.

## Server-Passwort (Vault)

Das Server-Passwort liegt **nie im Klartext** im Repo. Es steht in
`deploy/inventory/host_vars/planet/vault.yml` (Variable
`riftbreaker_server_password`) und wird mit `ansible-vault` verschlüsselt.
Befüllung: siehe `deploy/README.md` → „Vault". Für den CD-Hook liegt das
Vault-Passwort als root-only Datei auf planet — **niemals** auf GitHub.

## Aktueller Zustand (2026-09-10, Issue #209)

`deploy/` **besitzt den Stack**: das Playbook baut das Laufzeit-Image
(`rb-dedicated:<deploy-sha>`) selbst, provisioniert den Steam-Content und
pinnt das Compose auf den Deploy-SHA. Ein from-zero-Aufbau braucht keine
manuellen Schritte auf planet (`deploy/README.md` → „From-zero").

Der alte Community-Stack (`j3n5-group/riftbreaker-docker`, Steam-basiert) unter
`/srv/riftbreaker` ist abgelöst; `/srv/riftbreaker/data/server` bleibt als
**kanonischer Steam-Content-Cache** liegen (Sync-Fallback für `game-content`).
Mod-Instanzen: `/srv/rbgame` (:6321, release) und `/srv/rbgame-dev` (:6322,
dev); Compose unter `/opt/rbmods/compose/riftbreaker-dedicated{,-dev}`.

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
  `exor_logs.txt` entsteht erst bei Map-Load; im Idle **ohne Spieler**
  (`server_pause_game_when_empty=1`) gar nicht (Issue #226) → im CD ist der
  idle-sichere Artefakt-Check (#226) maßgeblich, nicht der Runtime-Log.
- SSH mesh-first (Tailscale), nie über Public-IPs.
