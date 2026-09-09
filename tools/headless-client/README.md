# Headless Riftbreaker-Client (Wine + Xvfb)

**Status: UNVERIFIZIERT** — Konzept + Basis-Skript, noch kein End-to-End-Test
gelaufen. Die Datei-Synchronisation ist bewusst NICHT Teil dieses Schritts
(Ersttransfer ~13 GB, läuft separat).

## Konzept

Auf **planet** läuft ein Riftbreaker-Client in einer headless Linux-Umgebung
(Wine + Xvfb + Mesa-llvmpipe). Molty steuert den Client über **Screenshots +
xdotool** (Maus/Tastatur) und verbindet sich so in die **Lobby** bzw. mit dem
**Testserver `rb-winetest`** — ein echter Game-Client statt nur
Server-Prozess, als Basis für Multiplayer/Mod-Tests ohne physischen Desktop.

## Voraussetzungen

- SSH-Zugriff über Tailscale-Aliasse aus `~/.ssh/config`:
  - `lan` → momo@lan (Quelle, Homelab)
  - `planet` → root@planet (Ziel, Hetzner)
  - Mesh-first: nie über Public-IPs.
- `rsync` auf beiden Seiten.
- Zielverzeichnis auf planet: `/srv/rbclient/game/` (root).
- Auf planet zusätzlich: `wine64`, `xvfb`, `xdotool`, Mesa (llvmpipe),
  Screenshot-Tool (z. B. `import`/ImageMagick oder `scrot`).
- Quelldateien auf lan:
  `/home/momo/share/games/The Riftbreaker/`

## Sync-Skript

```bash
# Voll-Sync (idempotent, rsync -a):
tools/headless-client/sync-client.sh

# Probe-Lauf ohne Transfer:
DRY_RUN=1 tools/headless-client/sync-client.sh
```

Ersttransfer ≈ 13 GB — bewusst als eigener Schritt, nicht Teil dieses PRs.

## Boot (Skizze, unverifiziert)

```bash
cd /srv/rbclient/game
xvfb-run -a wine64 riftbreaker_win_release.exe
```

Steuerung durch Molty (Screenshot + xdotool):

```bash
xdotool mousemove <x> <y> click 1      # Klick im Game-Fenster
import -window root /tmp/shot.png      # Screenshot (ImageMagick)
```

## Fallback

Falls das Wine-Rendering unter llvmpipe scheitert: **Proton-GE** mit
**DXVK-on-lavapipe** probieren (Vulkan-Overlay statt OpenGL) — gleiche
Xvfb-Basis, anderer Render-Stack.
