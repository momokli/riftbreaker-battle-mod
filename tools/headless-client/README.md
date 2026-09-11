# Headless Riftbreaker-Client (Wine + Xvfb + Mesa-llvmpipe)

**Status: Teilverifiziert (Issue #9 + #10)** — Tooling (Dockerfile/Compose,
Entrypoint, Boot-bis-Hauptmenü-Fluss, Screenshot-Beweis, Lobby-Navigation +
Server-Connect) ist angelegt und so weit wie möglich lokal verifiziert
(Shellcheck, `bash -n`, Compose-Config, Trockenlauf-Tests). Der **echte
In-Game-Boot bis ins Hauptmenü UND die Lobby-Navigation bis zum
Server-Connect sind offen (Operator, Prod)** — sie brauchen die
synchronisierten Client-Dateien und laufen auf **planet**, nicht in der
Sandbox (kein Game-Asset/GPU/Netz). Details unten.

Auf **planet** läuft der Riftbreaker-Client headless in einem Container
(Wine + Xvfb + Mesa-llvmpipe Software-Rendering). Steuerung/Auswertung über
**Screenshots + xdotool**; der Client verbindet sich in die Lobby bzw. mit dem
**Dev-Server (:6321)** — ein echter Game-Client statt nur Server-Prozess, als
Basis für Multiplayer-/Client-seitige Tests ohne physischen Desktop.

> Hinweis: Für die Server-Vollkette (`rb_wave` → Spawn) ist der headless Client
> laut [`docs/ASSUMPTIONS.md`](../../docs/ASSUMPTIONS.md) **nicht zwingend
> nötig** — `debug_spawn_fake_client` erzeugt den Player-Anker serverseitig.
> Der Client bleibt relevant fürs aktive Spielen und UI-nahe Szenarien.

## Komponenten

| Datei | Zweck |
|---|---|
| `Dockerfile` | Container-Image: Wine + Xvfb + xdotool + Mesa-llvmpipe |
| `docker-compose.yml` | Compose-Service (Volumes für Game + Screenshots, shm, init) |
| `run-client.sh` | Entrypoint: Xvfb → Wine-Prefix-Init → Client booten → Menü-Check |
| `wait-menu.sh` | Wartet auf gerendertes Hauptmenü + legt Beweis-Screenshot ab |
| `xdo-nav.sh` | xdotool-Navigation-Wrapper (click/key/type/shot) |
| `nav-lobby.sh` | Screenshot-gesteuerte Navigation bis Lobby + Server-Connect (Issue #10) |
| `sync-client-data.sh` | Voll-Sync lan→planet: Größencheck → rsync → Verifikation |
| `sync-client.sh` | Minimaler Vorgänger (einfaches `rsync -a`) |
| `test-sync-client-data.sh` | Trockenlauf-Tests für `sync-client-data.sh` (Fake ssh/rsync) |
| `test-wait-menu.sh` | Trockenlauf-Tests für `wait-menu.sh` (Fake import/convert) |
| `test-nav-lobby.sh` | Trockenlauf-Tests für `nav-lobby.sh` (Fake xdo-nav/import/convert) |

## Voraussetzungen

- SSH-Zugriff über Tailscale-Aliasse aus `~/.ssh/config`:
  - `lan` → momo@lan (Quelle, Homelab)
  - `planet` → root@planet (Ziel, Hetzner)
  - Mesh-first: nie über Public-IPs.
- Auf `planet`: Docker; `rsync` ≥ 3.1 auf beiden Seiten (für `--info=progress2`
  und `--protect-args`).
- Zielverzeichnis auf planet: `/srv/rbclient/game/` (root).
- Quelldateien auf lan: `/home/momo/share/games/The Riftbreaker/`.

## 1. Client-Dateien synchronisieren

Die Client-Daten (~13 GB) werden per rsync von `lan` nach `planet` synchronisiert.
Das vollwertige Skript ist `sync-client-data.sh` (Größencheck → rsync → Verifikation).
`sync-client.sh` ist nur noch der minimale Vorgänger (einfaches `rsync -a`).

```bash
# Voll-Sync + Verifikation (Anzahl/Größe + MD5-Stichprobe):
tools/headless-client/sync-client-data.sh

# Probe-Lauf (kein Transfer, keine Verifikation):
tools/headless-client/sync-client-data.sh --dry-run

# Ziel spiegeln (Dateien löschen, die auf lan fehlen):
tools/headless-client/sync-client-data.sh --delete

# Verifikation überspringen:
tools/headless-client/sync-client-data.sh --no-verify

# MD5-Stichproben-Raster ändern (Default: jede 50. Datei):
tools/headless-client/sync-client-data.sh --sample 100

# Hilfe:
tools/headless-client/sync-client-data.sh --help
```

### Ablauf

1. **Vorab-Größencheck** (read-only): Quellgröße auf `lan` vs. freier Platz auf dem
   Ziel-Filesystem; bricht bei Platzmangel ab.
2. **rsync** idempotent (`-a --partial --info=progress2`), über die Tailscale-ssh-Aliasse.
3. **Verifikation**: Dateianzahl + Gesamtgröße müssen übereinstimmen; danach
   MD5-Stichprobe (jede N-te Datei) auf beiden Seiten identisch sein.

### Includes/Excludes

Inkludiert wird der gesamte Spiel-Ordner. Exkludiert (Dateisystem-/Laufzeit-Müll,
kein Spielinhalt):

| Pattern | Grund |
|---|---|
| `.DS_Store`, `Thumbs.db`, `desktop.ini` | OS-Dateisystem-Artefakte |
| `*.log`, `*.tmp` | Laufzeit-Logs/Temp-Dateien |

`--delete` entfernt auf planet nur Dateien, die auf lan fehlen und nicht
exkludiert sind (kein `--delete-excluded`).

### Voraussetzungen (Sync-Skript)

- SSH-Aliasse `lan`/`planet` aus `~/.ssh/config` (Mesh-first, siehe oben).
- Quelldateien auf lan: `/home/momo/share/games/The Riftbreaker`.
- Zielverzeichnis auf planet: `/srv/rbclient/game` (bzw. übergeordneter Mount für
  den Platzcheck).
- Alle Pfade per Environment überschreibbar: `SRC_HOST`, `SRC_DIR`, `DST_HOST`,
  `DST_DIR`, `SSH_BIN`, `RSYNC_BIN`, `SAMPLE_EVERY`, `DRY_RUN`.

### Tests (ohne echten Transfer)

```bash
tools/headless-client/test-sync-client-data.sh
```

Ersetzt `ssh`/`rsync` durch Fake-Binaries und prüft Argument-/Dry-Run-/
Verifikationslogik ohne 13-GB-Transfer.

Ersttransfer ≈ 13 GB — bewusst als eigener Schritt.

## 2. Image bauen

```bash
# auf planet, im Verzeichnis tools/headless-client/
docker compose build        # oder:
docker build -t rb-headless-client tools/headless-client
```

Der Container erzwingt Software-Rendering (`LIBGL_ALWAYS_SOFTWARE=1`,
`GALLIUM_DRIVER=llvmpipe`) — keine GPU nötig.

## 3. Client starten

Einfachster Weg: Compose (baut das Image, startet den Client, wartet aufs
Hauptmenü und legt den Beweis-Screenshot ab).

```bash
cd tools/headless-client
docker compose up -d               # bauen + starten (Detached)
docker compose logs -f rb-headless # Boot-/Menü-Status verfolgen
```

Beim Start (`run-client.sh`) passiert der Reihe nach:

1. **Xvfb** auf `DISPLAY=:99` (1920×1080, falls noch kein X-Server läuft).
2. **Wine-Prefix-Init** (`wineboot --init`, idempotent).
3. **Client-Boot** via `wine <exe> [args]`.
4. **Menü-Check** (`wait-menu.sh`): pollt Screenshots, bis ein Frame nicht mehr
   einfarbig schwarz ist, und legt den Beweis-Screenshot unter
   `/srv/rbclient/screenshots/menu.png` ab.

Äquivalent als `docker run`:

```bash
docker run --rm --name rb-headless \
  -v /srv/rbclient/game:/srv/rbclient/game \
  -v /srv/rbclient/screenshots:/srv/rbclient/screenshots \
  -e RB_CLIENT_EXE=riftbreaker_win_release.exe \
  rb-headless-client
```

### Modi

| Aufruf | Verhalten |
|---|---|
| `run-client.sh` (Default) | Boot + Menü-Check (Screenshot), dann weiterlaufen |
| `run-client.sh --no-verify` | Boot, kein Menü-Check, einfach weiterlaufen |
| `run-client.sh --screenshot <file>` | Boot + Menü-Check, Screenshot nach `<file>`, dann beenden |

Verbindung zum Testserver (zusätzliche Start-Argumente):

```bash
docker run --rm \
  -v /srv/rbclient/game:/srv/rbclient/game \
  -v /srv/rbclient/screenshots:/srv/rbclient/screenshots \
  -e RB_CLIENT_ARGS="--server rb-winetest" \
  rb-headless-client
```

## 4. Boot bis Hauptmenü (Beweis)

`wait-menu.sh` setzt eine **Heuristik** ein (kein pixel-perfekter Menü-Check):

1. Screenshot des virtuellen Displays (`import -window root`).
2. Ein Frame gilt als *gerendert*, sobald die Grau-Standardabweichung über
   `RENDER_MIN_STD` (Default `0.02`) liegt — ein leerer/schwarzer Frame hat
   `std = 0`, sobald Logo/Menü erscheint steigt sie.
3. Bei Erfolg wird der Frame nach `SCREENSHOT_OUT` kopiert; bei Timeout wird der
   letzte Frame trotzdem gesichert und `exit 1` zurückgegeben.

```bash
# manuell gegen den laufenden Container:
docker compose exec rb-headless wait-menu.sh /srv/rbclient/screenshots/menu.png
```

Feintuning über Umgebung: `WAIT_TIMEOUT` (Default `120`), `RENDER_MIN_STD`,
`RENDER_POLL` (Default `3`).

**Verifiziert:** Shellcheck + `bash -n` + Trockenlauf-Tests
(`test-wait-menu.sh`) der Heuristik.

**Offen (Operator, Prod):** Der *echte* Hauptmenü-Render des Clients — erfordert
die synchronisierten Game-Dateien, den Container auf planet und ggf. das
Fenstertitel-/Schwellwert-Tuning (`RENDER_MIN_STD`), falls das Menü sehr dunkel
lädt. Die Schwelle ist bewusst konservativ (`0.02`) und per Env überschreibbar.

## 5. Navigation (Screenshot + xdotool)

Zweite Shell, gegen den laufenden Container:

```bash
# Screenshot
docker exec rb-headless xdo-nav.sh shot /tmp/shot.png

# Klick (Bildschirmkoordinaten, fullscreen)
docker exec rb-headless xdo-nav.sh click 960 540

# Taste / Text
docker exec rb-headless xdo-nav.sh key Return
docker exec rb-headless xdo-nav.sh type "rb_wave 3"
```

Vollständige Sub-Kommandos: `docker exec rb-headless xdo-nav.sh` (ohne Argumente
zeigt die Hilfe).

## 5a. Lobby-Navigation + Server-Connect (`nav-lobby.sh`)

`nav-lobby.sh` führt einen **Navigationsplan** (Liste aus xdotool-Aktionen +
Screenshot-Verifikation) aus, bis die Multiplayer-Lobby erreicht und die
Verbindung zum Testserver (Default `rb-winetest`) hergestellt ist. Nach jeder
Maus/Tastatur-Eingabe wird per Screenshot geprüft, dass der Frame noch gerendert
ist (schwarzer Frame = Client weg → Abbruch).

```bash
# Default-Plan (Hauptmenü → Multiplayer → Connect) gegen den laufenden Client:
docker compose exec rb-headless nav-lobby.sh

# Eigener Plan / anderer Server / Trockenlauf:
docker compose exec rb-headless nav-lobby.sh --plan /pfad/plan.lobby --server rb-winetest
docker compose exec rb-headless nav-lobby.sh --dry-run
```

Aktionen (eine je Zeile, `#` = Kommentar, leere Zeilen ignoriert):

| Aktion | Bedeutung |
|---|---|
| `shot <file>` | Screenshot ablegen (Beweis) |
| `click <x> <y> [btn]` | Klick (xdotool, Default button=1) |
| `key <keysym>` | Taste drücken |
| `type <text>` | Text tippen (Rest der Zeile = Text) |
| `wait <sec>` | Sekunden warten |
| `wait-menu <out.png>` | Warten bis Frame gerendert + Screenshot |
| `connect <server>` | Server-Adresse auflösen + eintippen + Return |
| `verify <cmd...>` | Verifikationskommando (exit 0 = ok) |

Server-Auflösung über `NAV_SERVERS` (`"name=host:port …"`, Default
`rb-winetest=65.21.27.234:6322`). Feintuning über `NAV_TIMEOUT` (Default `180`),
`NAV_RENDER_CHECK` (`1`|`0`), `RENDER_MIN_STD`, `RENDER_POLL`.

**Verifiziert:** `bash -n` + Trockenlauf-Tests (`test-nav-lobby.sh`):
Plan-Parsing, Server-Auflösung, Connect-Ablauf (`type <addr>` + `key Return`),
Render-Check-Abbruch bei schwarzem Frame.

**Offen (Operator, Prod):** Die echten Menü-Koordinaten/-Timings im
Default-Plan sind bewusst als Startpunkt gesetzt (`click 960 540` u. ä.) und
müssen beim realen In-Game-Test auf planet festgezurrt werden. Das
Akzeptanzkriterium „Spieler-Mech existiert auf dem Server (`no_player`
verschwindet)“ wird serverseitig geprüft (Mod-Log `event=wave … status=no_player`
vs. `status=spawned`) und ist nicht statisch testbar — hierfür ein
`verify`-Schritt im Plan gegen den Server-Log (Beispiel):

```
verify "grep -q 'event=wave .* status=spawned' /var/log/rbclient/server.log"
```

## Fallback: Proton-GE

Falls das Wine-Rendering unter llvmpipe scheitert (kein Frame, Grafikfehler,
Crash beim Start), auf **Proton-GE** mit **DXVK-on-lavapipe** wechseln —
Vulkan-Software-Overlay statt OpenGL. Gleiche Xvfb-Basis, anderer Render-Stack.

Vorgehen (auf planet):

1. **Proton-GE** installieren (z. B. via
   [GloriousEggroll/proton-ge-custom](https://github.com/GloriousEggroll/proton-ge-custom)
   oder Steam-`CompatibilityTools.d`). Die Wine-Variante im bestehenden
   Dockerfile wird durch ein Proton-GE-Base-Image ersetzt bzw. Proton-GE als
   `WINE`-Runtime eingebunden.
2. **Vulkan-Software-Renderer** ergänzen: `mesa-vulkan-drivers` (lavapipe) statt
   `libgl1-mesa-dri` und Env auf Vulkan umstellen:

   ```dockerfile
   ENV VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/lvp_icd.x86_64.json \
       VK_DRIVER_FILES=/usr/share/vulkan/icd.d/lvp_icd.x86_64.json \
       DXVK_CONFIG="dxvk.enableGraphicsPipelineLibrary=false" \
       WINEDLLOVERRIDES="dxgi,d3d11,d3d10core,d3d9=n,b"
   ```

3. **Entrypoint unverändert** nutzen (`run-client.sh` + `wait-menu.sh`): die
   Start-/Menü-Logik ist renderer-agnostisch, nur der Render-Stack im Image
   ändert sich.

> Proton-GE ist ein dokumentierter Fallback, **kein** verifizierter Pfad — die
> genauen DXVK/lavapipe-Versionen und DXVK-Config-Flags müssen beim realen
> In-Game-Test auf planet festgezurrt werden.
