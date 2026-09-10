# Headless Riftbreaker-Client (Wine + Xvfb + Mesa-llvmpipe)

**Status: UNVERIFIZIERT** — Konzept, Container-Setup und Tooling sind angelegt,
aber noch kein End-to-End-Test gelaufen (Live-Deploy ist Operator-Folgeschritt).
Der Ersttransfer (~13 GB) läuft separat über `sync-client-data.sh`.

Auf **planet** läuft der Riftbreaker-Client headless in einem Container
(Wine + Xvfb + Mesa-llvmpipe Software-Rendering). Steuerung/Auswertung über
**Screenshots + xdotool**; der Client verbindet sich in die Lobby bzw. mit dem
**Testserver `rb-winetest`** — ein echter Game-Client statt nur Server-Prozess,
als Basis für Multiplayer-/Client-seitige Tests ohne physischen Desktop.

> Hinweis: Für die Server-Vollkette (`rb_wave` → Spawn) ist der headless Client
> laut [`docs/ASSUMPTIONS.md`](../../docs/ASSUMPTIONS.md) **nicht zwingend
> nötig** — `debug_spawn_fake_client` erzeugt den Player-Anker serverseitig.
> Der Client bleibt relevant fürs aktive Spielen und UI-nahe Szenarien.

## Komponenten

| Datei | Zweck |
|---|---|
| `Dockerfile` | Container-Image: Wine + Xvfb + xdotool + Mesa-llvmpipe |
| `run-client.sh` | Entrypoint: Xvfb starten, Client-Exe via Wine booten |
| `xdo-nav.sh` | xdotool-Navigation-Wrapper (click/key/type/shot) |
| `sync-client-data.sh` | Voll-Sync lan→planet: Größencheck → rsync → Verifikation |
| `sync-client.sh` | Minimaler Vorgänger (einfaches `rsync -a`) |
| `test-sync-client-data.sh` | Trockenlauf-Tests für `sync-client-data.sh` (Fake ssh/rsync) |

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
# auf planet
docker build -t rb-headless-client tools/headless-client
```

Der Container erzwingt Software-Rendering (`LIBGL_ALWAYS_SOFTWARE=1`,
`GALLIUM_DRIVER=llvmpipe`) — keine GPU nötig.

## 3. Client starten

```bash
docker run --rm --name rb-headless \
  -v /srv/rbclient/game:/srv/rbclient/game \
  -e RB_CLIENT_EXE=riftbreaker_win_release.exe \
  rb-headless-client
```

Optionaler Sofort-Screenshot nach dem Start (Hilfe für Verbindungstests):

```bash
docker run --rm \
  -v /srv/rbclient/game:/srv/rbclient/game \
  rb-headless-client --screenshot /tmp/shot.png
```

Verbindung zum Testserver (zusätzliche Start-Argumente):

```bash
docker run --rm \
  -v /srv/rbclient/game:/srv/rbclient/game \
  -e RB_CLIENT_ARGS="--server rb-winetest" \
  rb-headless-client
```

## 4. Navigation (Screenshot + xdotool)

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

## Fallback

Falls das Wine-Rendering unter llvmpipe scheitert: **Proton-GE** mit
**DXVK-on-lavapipe** probieren (Vulkan-Overlay statt OpenGL) — gleiche
Xvfb-Basis, anderer Render-Stack.
