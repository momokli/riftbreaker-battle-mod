# RIFT BATTLE — Deployment & Stack

> Ziel-Stack + Deployment-Plan. **Deploy NUR über das Ansible-Playbook in
> `deploy/`** (kein manuelles Gedudel). Umsetzung: Issue #45.
>
> Wie groß muss die Kiste sein? → [`SERVER_SIZING.md`](SERVER_SIZING.md)
> (CPU/RAM/Storage je Betriebs-Szenario, gemessen auf planet).

## Ziel-Stack (was IMMER betrieben wird)

| Komponente                       | Host              | Container/Unit                                                     | Port                 | Zweck                                                                                          |
| -------------------------------- | ----------------- | ------------------------------------------------------------------ | -------------------- | ---------------------------------------------------------------------------------------------- |
| riftbreaker-dedicated            | planet            | docker (wine)                                                      | 6321/udp             | Dev-SP-Server: 1v1 „vs sich selbst" (SP-Mode; rbbattle-Mod + rbbridge)                         |
| tournament-server                | planet            | systemd (Rust/axum, `tournament/`)                                 | 8081                 | Turnier 1v1: Lobby/Ready/GO/Wave-Routing/Score (2 Welten)                                      |
| test-Instanzen                   | planet            | docker, on-demand                                                  | frei                 | Test-Server aller Art (Mod-Tests, Balance, Experimente)                                        |
| Operator-Cockpit + Tournament-UI | planet            | **eigener** Caddy (`rift-caddy`, plain HTTP) hinter `mellon-caddy` | 443 → 127.0.0.1:8787 | `/contract/*` → IO-Bridge (basic_auth) · `/tournament/*` → tournament-server                   |
| rbmods-image-retention.timer     | planet            | systemd                                                            | —                    | Alte Mod-Image-Tags aufräumen (Rollback-Stand + laufendes Image bleiben)                       |
| rbmods-host-hygiene.timer        | planet            | systemd                                                            | —                    | wöchentlich dangling Docker-Images aufräumen (`docker image prune`, **kein** `-a`; Issue #308) |
| rbmods-crash-collector           | planet            | systemd                                                            | —                    | Crash-Artefakte (Minidump + Trace + Log) sichern, Minidump parsen (Meta) + Retention (Issue #462/#481) |
| rbbridge                         | in Mod-Containern | Prozess                                                            | —                    | Command-Injection (`exec_cmd_client`, Argument IMMER als EIN gequotierter String)              |

## Deployment-Plan (Ansible, inventory `planet`)

Rollen in `deploy/roles/` (Details: `deploy/README.md`):

1. **mods-zip** — Mod aus `mod/` paketieren (`scripts/package_bausteine.sh`),
   `rbbattle.zip` nach planet; **md5-Paritäts-Check (Zip == Prod) hart als
   Fehlschlag**.
2. **dedicated-server-image** — baut `rb-dedicated:<deploy-sha>` IM
   Playbook auf planet aus `tools/dedicated-server` (Wine-Laufzeit
   für :6321, Community-Rezept; Docker-Layer-Cache → billig/idempotent). Das
   gerenderte Compose pinnt exakt diesen Tag (kein `latest`).
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
6. **website** — eigener **`rift-caddy`** (plain HTTP: Landing + `/mod.zip` +
   Cockpit `/contract/*` + `/tournament/*`) und **ZWEI** Einträge im geteilten
   Host-Caddy (`mellon-caddy`, Landing- + Cockpit-Domain). Details: „Website-Pfad“ unten.
7. **image-retention** — systemd-Timer für
   `scripts/docker_image_tag_retention.sh`: entfernt alte
   `rb-dedicated`/`rb-headless-client`-Tags, behält das laufende Image und den
   Rollback-Stand (Issue #309, siehe unten).
8. **host-hygiene** — wöchentlicher systemd-Timer (Issue #308): entfernt
   dangling Docker-Images (`docker image prune`, **kein** `-a`; der getaggte
   Rollback-Stand bleibt erhalten). Installiert `scripts/host_hygiene.sh` +
   Unit/Timer; automatische Variante der manuellen Aufräum-Befehle in
   [`SERVER_SIZING.md`](SERVER_SIZING.md).
9. **crash-collector** — systemd-*Dauer*-Dienst (Issue #462/#481): beobachtet
   `docker logs -f` des Dedicated-Servers auf Crash-Marker (`CRASH:`,
   `page fault`) und sichert das neueste `crash_info/<uuid>.{dmp,log,trace}`
   als Bundle nach `/opt/rbmods/crashes/<ts>-<uuid>/` — zusammen mit
   `context.log` (letzte N Container-Zeilen) und `meta.json`. Der Minidump wird
   dabei von `rbmods-minidump-meta.py` minimal geparst (siehe „Crash-Bundles &
   meta.json“ unten): Exception-Code/-Adresse, Modul, Modulbasis, Fault-RVA,
   Fault-Thread und Stack-RVAs kommen aus dem Dump; fehlt/kaputt der Dump,
   bleiben diese Felder `null` und `module_base`/`fault_address` fallen auf die
   `module_range`-/`page fault`-Zeile DIESES Bundles zurück. Retention
   (Default 20 Bundles) begrenzt auch das crash_info-Wachstum im Wine-Volume
   (#462). Prod wird als **eigener Zwilling** mitbeobachtet (Unit
   `rbmods-crash-collector-prod`, Bundle-Dir `/opt/rbmods/crashes-prod`,
   Container `riftbreaker-dedicated-prod`, Issue #481).

Grundsätze:

- **Idempotent** — jeder Lauf konvergiert auf denselben Zustand.
- **Deploy nur via Playbook** —
  `ansible-playbook -i deploy/inventory deploy/site.yml --ask-vault-pass`.
- **Rollback** = vorherige `rbbattle.zip` / vorheriges Binary wieder einspielen.
  Für ein **Image**-Rollback bleibt das getaggte `rb-dedicated:<alte-sha>`
  erhalten — die Rolle `host-hygiene` entfernt nur dangling Images (#308).

## Crash-Bundles & meta.json (Issue #462/#481)

Der Collector (`scripts/crash_collector.sh`, Unit `rbmods-crash-collector` bzw.
`rbmods-crash-collector-prod`) legt je Crash ein Bundle
`<crash_collector_dir>/<ts>-<uuid>/` an: `<uuid>.{dmp,log,trace}`,
`context.log` (letzte N Container-Zeilen) und `meta.json`. Beide Skripte sind
planetfrei pruefbar — kein Docker, kein Wine, kein Netz:

- `tests/shell/crash-collector.test.sh` — Bundle/Retention/Fallback (Fake-Docker)
- `tests/shell/minidump_meta.test.sh` — Parser-Unit (synthetische MDMP-Fixtures)
- `deploy/tests/crash-collector/run.sh` — Render der Unit (dev + prod)

### meta.json-Felder

Basis (Bestand): `collected_at`, `uuid`, `bundle`, `container`, `image`,
`git_sha`, `container_started_at`, `container_uptime_seconds`, `crash_marker`,
`crash_line`, `context_lines`, `files`.

Aus dem Minidump (#481) — `null`, wenn der Dump fehlt oder kaputt ist:
`exception_code`, `exception_address`, `module`, `module_base`, `fault_rva`,
`fault_thread`, `stack_rvas`. Adressen/Offsets sind Hex-Strings **ohne** `0x`.

**Same-Boot-Regel:** `module_base`/`fault_address` stammen bevorzugt aus dem
Dump des Bundles; fehlt der Dump, aus der `module_range`-/`page fault`-Zeile
**desselben** Bundles (`context.log`). Nie ein Wert aus einem anderen Boot.

### Parser (`scripts/minidump_meta.py`)

Nur stdlib (`struct`/`json`/`sys`/`os`) — keine Symbole, kein PDB, kein Netz.
CLI: `minidump_meta.py [--json] <dmp>` -> JSON. Genutzte Streams:
`Exception(6)`, `ModuleList(4)`, `ThreadList(3)`, `MemoryList(5)`;
`Memory64List(9)` best-effort. Jeder Parsefehler (falsche Magic, truncated,
fehlender Stream, RVA ausserhalb) -> `{"_ok": false, "reason": ...}` und
**rc=0** (kein Traceback) — der Collector stirbt nie an einem kaputten Dump.

Grenzen (bewusst, nicht geraten):

- **Pointer-Breite 8 Byte** (64-bit Wine): ein 32-bit-Dump liefert falsche
  Stack-Kandidaten — die Modulbasis-Breite wird nicht erkannt.
- **`stack_rvas`**: nur gegen das **Fault-Modul** relativiert, dedupliziert,
  aufsteigend, **Cap 32**; liegt der Stack nicht in der MemoryList, bleibt die
  Liste leer (kein Fehler).
- **RVA-Bounds** werden geprueft; out-of-bounds -> `_ok:false`.

### prod-Instanz (#481)

`deploy/prod-vars.yml`: `crash_collector_unit: rbmods-crash-collector-prod`,
`crash_collector_dir: /opt/rbmods/crashes-prod`,
`crash_collector_container: riftbreaker-dedicated-prod`; `deploy/deploy-prod.yml`
bindet die Rolle `crash-collector` ein (Tag `crash`). Dev bleibt unveraendert
(`rbmods-crash-collector`, `/opt/rbmods/crashes`) — kein Clash. Der Nachweis ist
hermetisch (Render-Test + Fixture-Dumps); die echte prod-Beobachtung gilt erst
nach einem prod-Deploy.

## Image-Tag-Retention (Issue #309)

**Problem:** Der CD-Build taggt jedes Mal neu (`rb-dedicated:<deploy-sha>`),
entfernt aber nie alte Tags. Live-Messung auf planet (2026-09-12):
`rb-dedicated` **63 Tags bei 12 Image-IDs**, `rb-headless-client` **46 Tags
bei 6 IDs** — je neue ID ~**1,56 GB unique** (`docker system df -v`).

**Lebenszyklus eines Tags:**

1. Ein Deploy baut/verwendet `rb-dedicated:<deploy-sha>` und pinnt genau diesen
   Tag im gerenderten Compose (kein `latest`).
2. Der Timer `rbmods-image-retention.timer` läuft **täglich** und entfernt
   ältere Tags per `docker rmi <repo>:<tag>` — **nicht** per
   `docker image prune -a` (das würde den Rollback-Stand mitnehmen).
3. Es bleiben je Repo erhalten:
   - **jeder Tag, den ein Container als `Config.Image` trägt** (laufendes Image
     — auch gestoppte Container),
   - der **aktuelle Deploy-Tag** (`RB_PROTECTED_TAGS` = `dedicated_server_image`),
   - die **letzten 2 Rollback-Tags** (`image_retention_rollback_tags`).
4. Pro Kandidat prüft ein Guard vor jedem `rmi` per
   `docker ps -aq --filter ancestor=<repo>:<tag>`, ob ein (auch gestoppter)
   Container dieses Image benutzt → dann bleibt der Tag erhalten; ein benutztes
   Image wird nie untagged oder löschbar. Der Filter löst die Referenz zur
   Image-ID auf und greift daher **auch**, wenn ein Container aus einer nackten
   Image-ID gestartet wurde (`Config.Image` ist dann die kurze ID, kein
   `repo:tag`). Zusätzlich prüft der Guard die exakte `Config.Image`-Gleichheit.

> **Hinweis:** `RB_ROLLBACK_TAGS` zählt **Tags**, nicht distinkte Image-IDs.
> Trägt eine ID mehrere Tags, können nach dem Lauf weniger als N verschiedene
> Images als Rollback übrig bleiben — dafür ist jedes benutzte Image garantiert
> getaggt.

**Rollback geht nach dem Cleanup noch:** die letzten 2 Tags bleiben als
vollständige Images vorhanden und sind mit `docker image inspect` prüfbar.

### Dry-Run / Verifikation

Das Skript kann ohne Änderung zeigen, was es täte:

```bash
# Auf planet, read-only: nichts wird entfernt. Die Zeilen erscheinen auf
# stdout; RB_IMAGE_RETENTION_LOG nur setzen, wenn zusaetzlich in eine DATEI
# geschrieben werden soll (kein /dev/stdout — `>>` scheitert ohne regulaere Datei).
sudo /usr/local/bin/rbmods-image-retention.sh --dry-run

# Zähler vorher/nachher:
docker image ls rb-dedicated | wc -l
```

Der manuelle Lauf einer Timer-Runde (nach dem Dry-Run-Blick):

```bash
sudo systemctl start rbmods-image-retention.service
journalctl -u rbmods-image-retention.service -n 40 --no-pager
```

Details zu den Schaltern (`--keep N`, `--repo NAME`, ENV-Variablen):
`scripts/docker_image_tag_retention.sh --help`. Der hermetische
Red/Green-Test (kein Docker nötig) liegt in
`tests/shell/image-retention.test.sh` und läuft in CI (`lint.yml`).

**Nicht in diesem Issue:** ungetaggte Dangling-Layer (→ #308) und weniger Müll
erzeugen (→ #247, reproduzierbare Builds in GHCR).

## Website-Pfad — eigener Rift-Caddy + ZWEI Host-Einträge (Landing + Cockpit, Issue #322)

Pro Environment („Twin“) betreibt der Rift-Stack einen **eigenen `rift-caddy`**
(plain HTTP, `network_mode: host`), der ZWEI Hostnames bedient — die statische
**Landing** (+ `/mod.zip`) und das **Operator-Cockpit** (`/contract/*` →
IO-Bridge, `/tournament/*` → tournament-server, `/server/*` →
server-control-Agent):

```text
www.<env>.projectmellon.de    ┐
cockpit.<env>.projectmellon.de┤→ Host-Caddy (mellon-caddy, hostet viele Domains)
                              │     └─ reverse_proxy 127.0.0.1:<rift_caddy_port> (je Domain)
                              └──────────────┘
                                   └─ rift-caddy (eigener Container, plain HTTP, net=host)
                                        ├─ file_server (Landing + /mod.zip)
                                        ├─ handle_path /contract/*   → 127.0.0.1:<bridge> (basic_auth)
                                        ├─ handle_path /tournament/* → 127.0.0.1:8081
                                        └─ handle /server/*          → 127.0.0.1:<server_control_port>
                                             (basic_auth + Bearer-Injektion, #454)
```

Eigenschaften:

- **Genau ZWEI** Einträge im geteilten Host-Caddy (`landing_domain` +
  `cockpit_domain` → `reverse_proxy 127.0.0.1:<rift_caddy_port>`), idempotent
  via `blockinfile` (per-Domain-Marker `managed by deploy/roles/website`). Kein
  `Caddyfile.d`-Mount, keine Snippet-Import-Zeile mehr. Verwaiste
  `operator.*`-/manuelle Rift-Blöcke entfernt die Rolle (kein Parallel-Block).
- **rift-caddy** ist ein eigener Container (`caddy:2`, `network_mode: host`) und
  lauscht ausschließlich auf `127.0.0.1:<rift_caddy_port>`. TLS terminiert
  weiterhin der Host-Caddy.
- `/contract/*` wird auf die IO-Bridge (`riftbreaker_bridge_port`) proxyt und
  per `basic_auth` (operator) geschützt; `/tournament/*` geht unverändert an den
  tournament-server.
- `/server/*` (Server-Control-Agent, Plane B, #424) liegt seit #454 hinter
  **derselben** Operator-`basic_auth` wie der Cockpit-Root (Fix #456) — der
  Browser schickt die Credentials automatisch mit (gleicher Realm). Den Bearer
  des Agenten kann kein Browser senden; der rift-caddy injiziert ihn per
  `header_up Authorization "Bearer <server_control_token>"`. Nur bei gesetztem
  Token: ist der Wert leer, entfällt der `header_up` (der Agent lehnt ein
  literales `Bearer ` ab und antwortet 401). Der Token kommt aus dem Vault
  (`vault_server_control_token`) und verlässt nie den Host.
- Variablen: `deploy/inventory/host_vars/planet/vars.yml` (dev) bzw.
  `deploy/prod-vars.yml` (prod) — `landing_domain`, `cockpit_domain`,
  `rift_caddy_*`, `riftbreaker_bridge_port`; Umsetzung: `deploy/roles/website/`.

Akzeptanz-Beleg (Play-Test-Preflight **P4**, `docs/PLAYTEST_1.0.md`):

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://www.drift.projectmellon.de/                     # 200 (Landing)
curl -s -o /dev/null -w '%{http_code}\n' https://cockpit.drift.projectmellon.de/tournament/health # 200 (Cockpit)
curl -s -o /dev/null -w '%{http_code}\n' https://www.rift.projectmellon.de/                      # 200 (Landing)
```

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
   1. **Artefakt-Check (hart, idle-sicher):** Manifest-Version _und_
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

### Log-Quelle & Timing

**Update (Issue #245, Community-Rezept #241):** Seit dem Umstieg auf das
Community-Dedicated-Server-Image ist die Quelle wieder **`docker logs`** —
`tools/dedicated-server/scripts/entrypoint.sh` (`follow_server_logs`) tailt
`exor_logs.txt` selbst nach stdout, daher landen `[RBBATTLE] event=...`-Zeilen
jetzt in `docker logs {{ riftbreaker_server_container }}`. `riftbreaker_mod_log_cmd`
in der Rolle spiegelt das. Der Rest dieses Abschnitts (Befund planet
2026-09-11, Issue #226, altes Wine-Client-Image) bleibt als historischer
Kontext stehen, warum der Artefakt-Check der harte Gate ist und nicht der
Runtime-Log:

- **Altes Image (bis #241):** `docker logs` enthielt nur die zwei
  `run-server.sh`-Wrapper-Zeilen (`[run-server] starte Xvfb …` / `[run-server]
starte: wine bin/DedicatedServer.exe …`) und **nie** eine `mod_load`-Zeile;
  der Log lag im Container-Writable-Layer unter `/root/exor_logs.txt`
  (Wine-`Documents -> /root`) und war nur per `docker exec ... cat` erreichbar.
- **Timing (weiterhin relevant):** Der Log entsteht erst, wenn der Server eine
  Map lädt und Lua ausführt. Ob/wann das ohne Spieler passiert, hängt von
  `riftbreaker_server_pause_game_when_empty` ab (aktuell `0` — die Welt läuft
  headless weiter, Issue #265/#269).
- Deshalb bleibt der **Artefakt-Check** (deployter Stand auf der Platte,
  idle-sicher) der harte Gate; der Runtime-Log-Check ist weiterhin nur
  best-effort und greift, wenn zum Prüfzeitpunkt tatsächlich eine Map geladen
  wurde.

**Kontrollwerkzeug / Regression-Check** (lokal + CI, Exit 1 = Fremd-Ordner):

```bash
python3 tools/mods-guard/check_mods_dir.py /srv/rbgame/mods
```

Siehe [`tools/mods-guard/`](../tools/mods-guard/README.md).

## Disk-Space-Gate — Deploy-Bremse vor voller Platte (Issue #310)

planet baut, sichert und deployt auf **derselben Platte** (`/dev/md2`, 904 GB),
die der Stack vollschreibt. Läuft sie voll, kann sich der Deploy **nicht mehr
selbst herausrollen**: Image-Build, Zip-Kopie und Backup-Tarball brauchen selbst
Platz. Deshalb prüft ein **Preflight** den freien Platz auf `/` **bevor**
`dedicated-server-image` baut und **bevor** der Mod-Backup-Tarball entsteht.

- **Task:** `deploy/roles/riftbreaker-server/tasks/disk-preflight.yml` —
  `assert` auf den Fact `ansible_mounts` (read-only → `--check`-fest, ändert
  nichts).
- **Aufruf:** als `include_role … tasks_from: disk-preflight` in den
  `pre_tasks` von `deploy/site.yml` **und** `deploy/test-deploy.yml` — also
  **vor** den Rollen (nicht innerhalb der Rolle, die erst nach dem Image-Build
  läuft). Läuft damit auch unter `--tags server,website` (deploy-check).
- **Schwelle konfigurierbar:** `riftbreaker_disk_min_free_gb` (Default **10** GB),
  Mount via `riftbreaker_disk_mount` (Default `/`). Allein übersteuern, z. B.
  `-e riftbreaker_disk_min_free_gb=20`.
- **Abbruch:** mit klarer `fail_msg` (nennt geforderten **und** tatsächlichen
  freien Platz + nächsten Schritt: erst aufräumen, siehe #301, dann erneut
  deployen).

Das Gate ist ein **Not-Aus**, kein Ersatz fürs Aufräumen: erst Sichtbarkeit,
dann Gate, plus Timer/Automatik — beides gehört zusammen (#301).

**Selbsttest (hermetisch, ohne Host/Prod-Zugriff):**

```bash
bash deploy/tests/disk-gate/run.sh
```

Er injiziert synthetische `ansible_mounts` (100 GB frei → läuft durch, 1 GB frei
→ Abbruch mit `PLATZ-GATE`, Schwelle 0 → durch) und ruft die **echte**
Preflight-Task-Datei auf. Läuft zusätzlich in `deploy-check-local` auf dem
GitHub-Hosted-Runner.

## Continuous Deploy (CD) — Issue #91

Nach jedem Merge auf `main` deployt
[`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml) automatisch auf
den Solo-DEV-Server (planet, Port 6321) — **rolling**, immer der aktuelle Stand
zum Testen. Der Job läuft auf dem self-hosted Runner auf planet und verbindet
sich per **SSH als dedizierter deploy-User** (`ssh rbd "<sha> <ref>"`); die
forced command (`deploy/deploy-ssh.sh`) validiert die SHA, macht
`git fetch` + Hard-Checkout und führt das Ansible-Playbook als root aus
(enges sudoers). Kein Token, kein Polling. Installation/Migration:
`deploy/README.md` → „CD: SSH-Deploy".

Topologie (Stand Issue #328): **zwei** Instanzen, ein Dedi-Port. **DEV** läuft
rolling auf `:6321` (dieser CD-Workflow, `deploy/site.yml`); **PROD** ist eine
koexistierende zweite Instanz auf `:6322`, öffentlich erreichbar über den
**Satellite-Relay** (eigene IPv4, inbound `:6321` → DNAT → planet `:6322`;
`deploy/deploy-prod.yml` + `prod-vars.yml`). Ein Direct-IP-Server
(`disable_steam "1"`) deckt beide Stores ab; der Client ist effektiv auf Port
`:6321` hardgewired, daher der zweite öffentliche Zugang über eine zweite
**Adresse** (den Satellite) statt eines zweiten Ports.

Der **Tag→prod-Kanal ist weiterhin nicht verdrahtet** (Follow-up):
`tags: ['v*']` sind seit Issue #209 **reine Marker** (kein Tag-Trigger, keine
GitHub-Releases, keine prod-Umgebung im Workflow). Das zweite Deploy-Target
existiert seit #328 (`deploy-prod.yml`) — der Tag-Trigger darauf wird als eigenes
Folge-Issue angeschlossen.

Der HTTP-Hook ist seit 2026-09-11 durch den SSH-Deploy abgelöst (Issue #235);
das `DEPLOY_TOKEN`-Secret im Environment `dev` wurde gelöscht — **es gibt kein
GitHub-Secret mehr**. Das Vault-Passwort liegt ausschließlich root-only auf
planet (`/etc/rbbattle-deploy/vault.pass`) und wird nie im Repo oder in Logs
ausgegeben.

**Deploy-Gate (Issue #238):** Vor dem SSH-Deploy parkt der Lauf, bis **0
Spieler online** sind (Provider `tools/deploy-gate/player_count.py`, Quelle =
Container-Log). `workflow_dispatch` mit `force=true` deployt sofort; ein
Timeout (Default 1800 s) bricht rot ab, statt unbegrenzt zu hängen. Betrieb +
Troubleshooting: `deploy/README.md` → „CD: SSH-Deploy"; Details:
`tools/deploy-gate/README.md`.

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
Mod-Instanz: `/srv/rbgame` (:6321); Compose
unter `/opt/rbmods/compose/…`.

## Interim-Deploy :6321 (Issue #156) — historisch, durch #209 überholt

> ⚠️ **Nicht mehr ausführen.** Die Befehle unten gehören zum Layout **vor**
> dem Community-Rezept (#241): Mods als Host-Ordner unter
> `/srv/riftbreaker/data/server/mods`, Wine-Prefix als Bind-Mount unter
> `/srv/riftbreaker/data/wine`. Beides existiert so nicht mehr — der
> Wine-Prefix ist heute ein **benanntes Volume** (`rb-wine`), und
> `exor_logs.txt` wird vom Entrypoint nach `docker logs` getailt.
> Aktuelle Pfade und Abrufe: „Log-Quelle & Timing" oben. Der Block bleibt
> als Referenz stehen, weil die Reihenfolge (Spieler-Check → Guard → Backup
> außerhalb `mods/` → Neustart → Smoke-Test) die ist, die die Ansible-Rolle
> heute automatisiert.

Bis zur CD (#91) wurde die Mod auf :6321 manuell eingespielt:

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
  im Wine-Prefix (benanntes Volume, kein Bind-Mount). Seit dem
  Community-Rezept (#241) tailt der Entrypoint ihn selbst nach stdout, also
  zeigt `docker logs {{ riftbreaker_server_container }}` die
  `[RBBATTLE] event=...`-Zeilen wieder mit (s. „Log-Quelle & Timing" oben) —
  aber erst, sobald eine Map geladen ist. Direkt nach einem
  `--force-recreate` (frisch gestarteter Container) ist das **nicht** der
  Fall, egal ob Spieler online sind oder nicht → im CD bleibt der
  idle-/boot-sichere Artefakt-Check (#226/#245) maßgeblich, nicht der
  Runtime-Log.
- SSH mesh-first (Tailscale), nie über Public-IPs.


## Environment-Isolation & Deploy-Identität (Issue #483)

Jeder Deploy trägt **genau eine** Identität: `rift_env` (`dev`|`prod`|`test`) +
`rift_deploy_ref` (dev/test = Checkout-SHA; prod = Git-Tag + SHA) →
`rift_deploy_identity = "<env> · <ref>"`. Erzeugt wird sie in den `pre_tasks`
(`deploy/tasks/deploy-identity.yml`); `rift_env` steht als **Play-Var** in
`site.yml`/`deploy-prod.yml`/`test-deploy.yml` (Play-Vars schlagen
Rollen-Defaults/host_vars — sonst erbt prod/test den dev-Wert).

### Schema: Env → Pfade / Ports / Stand

`deploy/env-schema.yml` klassifiziert **jede** Variable der Env-Override-Dateien
als `per_env` (Umgebungs-spezifisch, MUSS explizit je Env stehen) oder `shared`
(bewusst gleich, mit Begründung). `dev` hat keine Override-Datei — dev **ist**
die Basis (`inventory/host_vars/planet/vars.yml`); genau diese Asymmetrie ist
der Kern des Issues. Das Gate `tools/deploy-gate/check_env_isolation.py` bricht
bei Lücke ab (Marker `ENV-ISOLATION-GATE`), aufgerufen aus
`deploy/tasks/env-assert.yml` in den `pre_tasks` — **vor** den Rollen.

| Achse | dev (Basis) | prod (`prod-vars.yml`) | test (`test-vars.yml`) |
| --- | --- | --- | --- |
| `website_docroot` | `/srv/rbmods-site` | `/srv/rbmods-site-prod` | `/srv/rbmods-site-test-<run>` |
| `website_mods_dir` | `/srv/rbmods-site/mods` | `<docroot>/mods` | `/opt/rbbattle-deploy/test/mods-<run>` |
| `mods_zip_dest` | `<mods_dir>/rbbattle.zip` | `<mods_dir>/rbbattle.zip` | (abgeleitet, isoliert) |
| `riftbreaker_game_dir` | `/srv/rbgame` | `/srv/rbgame-prod` | `/srv/rbgame-test-<run>` |
| `riftbreaker_deploy_dir` | `/opt/rbmods/compose/riftbreaker-dedicated` | `…-prod` | `/opt/rbbattle-deploy/test/rb-<run>` |
| `riftbreaker_sessions_dir` | `/srv/rbmods-sessions` | `/srv/rbmods-sessions-prod` | `/srv/rbmods-sessions-test-<run>` |
| `rbtools_dir` | `/opt/rbmods/rbtools-drift` | `/opt/rbmods/rbtools-rift` | `/opt/rbmods/rbtools-test-<run>` |
| Game-Port (UDP) | 6321 | 6322 | ephemer (je Lauf) |
| Bridge-Port | 9001 | 9002 | je Lauf (Fallback 9003) |
| Tournament-Port | 8081 | 8082 | je Lauf |
| rift-caddy | `rift-caddy` :8787 | `rift-caddy-prod` :8788 | — (kein website-Rolle im Boot-Test) |
| Container-Env/Labels | `RBB_ENV=dev`/`RBB_REF=<sha>` | `prod`/`<tag>+<sha>` | `test`/`<sha>` |

**Additiv, kein Rename:** die dev-Pfade bleiben unverändert; prod/test bekommen
**eigene** Werte. `mods_zip_name: rbbattle.zip` + seine md5-Parität bleiben
unverändert (zusätzlich entsteht `rbbattle-<env>-<ref>.zip`).

`deploy/tasks/env-assert.yml` asserted zusätzlich, dass für `env != dev` jeder
der obigen Pfade **vom dev-Basiswert abweicht** (Distinctness gegen explizite
dev-Konstanten — Ansible kennt keine Variablen-Herkunft).

### Identitäts-Surface-Vertrag (`<env> · <ref>`)

| Surface | Feld / Ort | Wie sichtbar |
| --- | --- | --- |
| Landing (`website`) | `<meta name="rb-env">`/`rb-ref` + Badge | `deploy/roles/website/templates/index.html.j2` |
| Tournament-API | `GET /health` → `env`,`ref` | `TOURNAMENT_ENV`/`TOURNAMENT_REF` (systemd-Unit) |
| Tournament-UI | Header-Badge | `fetch(/health)` in `tournament/web/app.js` |
| Server-Control | `GET /server/status` → `env`,`ref` | `SERVER_CONTROL_ENV`/`SERVER_CONTROL_REF` |
| Session-Recorder | JSONL-Record `env`,`ref` | `RBB_ENV`/`RBB_REF` im Sidecar + CLI `--env/--ref` |
| Referee-Egress | Event-Record `env`,`ref` | dito |
| Container | Labels `RBB_ENV`/`RBB_REF` | `docker inspect` (ohne Log) |
| Mod-Log | `event=mod_load … env=… ref=…` | `mod/lua/rbbattle_autoexec.lua` — **vorbereitet, im Live-Lauf nicht wirksam** (`env=unknown`, s. u.) |

### Offene Punkte (bewusst NICHT in #483)

- **Kein `-<env>`-Rename** der dev-Pfade (`/srv/rbgame`, `/srv/rbmods-site`,
  `/opt/rbmods/rbtools-drift`) — Live-Eingriff auf prod; eigener PR mit
  Rollback-Runbook.
- **Kein host-seitiger Checkout-Umbau** (`/opt/rbbattle-deploy/repo-<env>` +
  `.deploy-<env>.sha`/`-ref`) — forced-command/Wrapper sind nicht im Repo.
- **Mod-Log-`env`/`ref` in-game** ist vorbereitet, aber im Live-Lauf **nicht
  wirksam**: der (rot gelaufene) Boot-Test-Log zeigte trotz gesetztem
  `RBB_ENV=test`/`RBB_REF=<sha>` `event=mod_load … env=unknown ref=unknown` —
  die Riftbreaker-Lua-Sandbox liefert `os.getenv` offenbar nicht, der
  defensive Fallback greift. NICHT als erledigte Surface führen. Verlässlicher
  Kanal (Config/Datei statt Lua-`getenv`) ist Follow-up (Review-F2).
- **Landing-Domain-Isolationsgrad** für Prod-Artefakte (`/mods/prod/…`) offen.
