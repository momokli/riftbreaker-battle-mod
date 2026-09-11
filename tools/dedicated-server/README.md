# Riftbreaker Dedicated Server (Image-Rezept)

Vendored, MIT-lizenziertes Dedicated-Server-Image von
[j3n5-group/riftbreaker-docker](https://gitlab.com/j3n5-group/riftbreaker-docker)
(Copyright 2026 Jens Schiller). Ursprünglicher Einsatzort: planet, `/srv/riftbreaker`.

Dieses Verzeichnis ist die **Vorlage für das Laufzeit-Image des :6321-Servers** —
es ersetzt das frühere `tools/headless-client/`-Image als Server-Runtime (Issue #241).

## Warum dieses Rezept statt `rb-headless-client`

Der Dedicated-Server (`DedicatedServer.exe`, Windows x86) braucht eine andere
Wine-Laufzeit als der headless Client:

- **winehq-stable** (nicht Debian-Wine 8.0) → liefert u. a. den Builtin-Stub für
  `api-ms-win-core-psm-appnotify-l1-1-0.dll`.
- **Wine-Prefix-Init** (`wine-init.sh`): Windows ≥ 10 + `vcrun2022` +
  `d3dcompiler_47`.
- **`WINEESYNC=0` / `WINEFSYNC=0`** (im Compose gesetzt): mit esync/fsync crasht
  der Server im Boost-Thread, bevor er den UDP-Port bindet.
- **`config=config.cfg`** statt Einzel-Args: die App löst die Config relativ zum
  Install-Root auf.
- **`steamuser` + `gosu`** statt root.

## Inhalt

| Datei | Zweck |
|---|---|
| `Dockerfile` | Image: winehq-stable + steamcmd + winetricks + gosu/tini |
| `scripts/docker-entrypoint.sh` | root → `gosu steamuser` → entrypoint |
| `scripts/entrypoint.sh` | Config kopieren, Logs streamen, Port-/Startup-Watch, Server starten |
| `scripts/wine-init.sh` | Einmalige Prefix-Init (win10, vcrun2022, d3dcompiler_47) |
| `scripts/install-update.sh` | steamcmd `app_update 4114030` (Content) |
| `config/config.cfg.example` | Vorlage Server-Config (LAN/Direct-IP-Modus) |

## Build (CI → GHCR, Issue #247)

Das Image wird **im CI gebaut und nach GHCR gepusht** —
`.github/workflows/dedicated-server-image.yml`:

- Push auf `main` (ohne paths-Filter) → jeder main-SHA bekommt ein Image.
- PR mit Änderungen unter `tools/dedicated-server/**` → Build (kein Push bei Fork-PRs).
- `workflow_dispatch` für einen manuellen Rebuild.

**Image-Name:** `ghcr.io/momokli/riftbreaker-dedicated`

**Tags:**

| Tag | Wann |
|---|---|
| `sha-<12>` | immer (Head-Commit des Events) |
| `latest` | zusätzlich auf `main` |
| `pr-<nummer>` | zusätzlich bei Pull Requests (nur Repo-eigene) |

Der **Deploy zieht** das Image aus GHCR (Rolle `dedicated-server-image`,
`dedicated_server_image` = `ghcr.io/momokli/riftbreaker-dedicated:<deploy-sha>`).
Der lokale Build in der Rolle ist **nur Fallback**, wenn der Tag in GHCR noch
fehlt (z. B. PR-Gate).

### Pins aktualisieren

Reproduzierbar sind Basis-Image und winetricks gepinnt:

- **Basis-Image (`FROM ...@sha256:...`)** — aktueller Digest entspricht
  `scottyhardy/docker-wine:latest` vom 2026-09-12 (Ubuntu 24.04, wine-11.0):

  ```bash
  docker buildx imagetools inspect scottyhardy/docker-wine:latest
  #   → neuen linux/amd64-Digest in tools/dedicated-server/Dockerfile eintragen
  ```

- **winetricks (`ARG WINETRICKS_COMMIT` / `WINETRICKS_SHA256`)** — Commit +
  SHA256 des Blobs `src/winetricks` aus den
  [Winetricks-Releases](https://github.com/Winetricks/winetricks/releases):

  ```bash
  COMMIT=<release-commit>
  curl -fsSL "https://raw.githubusercontent.com/Winetricks/winetricks/$COMMIT/src/winetricks" \
    | sha256sum
  ```

### Lokaler Build (Fallback/Debug)

```bash
docker build -t ghcr.io/momokli/riftbreaker-dedicated:dev tools/dedicated-server
```

Der Entrypoint erwartet beim Start `config.cfg` unter `/data/config/config.cfg`
(vom Compose als read-only gemountet).
