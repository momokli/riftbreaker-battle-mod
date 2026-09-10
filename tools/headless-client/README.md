# Headless Riftbreaker-Client (Wine + Xvfb + Mesa-llvmpipe)

**Status: UNVERIFIZIERT** — Konzept, Container-Setup und Tooling sind angelegt,
aber noch kein End-to-End-Test gelaufen (Live-Deploy ist Operator-Folgeschritt).

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
| `sync-client.sh` | Client-Dateien von `lan` nach `planet` (rsync) |

## Voraussetzungen

- SSH-Zugriff über Tailscale-Aliasse aus `~/.ssh/config`:
  - `lan` → momo@lan (Quelle, Homelab)
  - `planet` → root@planet (Ziel, Hetzner)
  - Mesh-first: nie über Public-IPs.
- Auf `planet`: Docker + `rsync`.
- Zielverzeichnis auf planet: `/srv/rbclient/game/` (root).
- Quelldateien auf lan: `/home/momo/share/games/The Riftbreaker/`.

## 1. Client-Dateien synchronisieren

```bash
# Voll-Sync (idempotent, rsync -a):
tools/headless-client/sync-client.sh

# Probe-Lauf ohne Transfer:
DRY_RUN=1 tools/headless-client/sync-client.sh
```

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
