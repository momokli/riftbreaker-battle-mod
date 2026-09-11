# Headless Riftbreaker-Laufzeit: Wine + Xvfb + Mesa-llvmpipe (Software-Rendering).
#
# Zwei Rollen teilen sich dieses Image:
#   * Client (tools/headless-client/run-client.sh, eigener Xvfb auf :99) —
#     Steuerung/Auswertung via xdotool + Screenshots (xdo-nav.sh), Client-Dateien
#     aus sync-client.sh.
#   * Dedicated Server (tools/headless-client/run-server.sh, xvfb-run -a) — die
#     Wine-Laufzeit für den Stack auf :6321 (deploy/).
#
# Issue #239 — warum winehq-stable statt Debian-Wine und warum Prefix-Init:
#   Der DedicatedServer.exe braucht drei Dinge im Wine-Prefix, die Debian-Wine
#   8.0 / ein frisches Prefix nicht liefern (je live auf planet belegt):
#     1) Windows-Version >= win10  → sonst Dialog „Windows 8.1 or greater is
#        required"; Fix: HKCU\Software\Wine Version=win10.
#     2) VC++ Runtime (vcrun2022) + d3dcompiler_47 → sonst „failed to load game
#        library".
#     3) winehq-stable (statt Debian-Wine 8.0) → liefert den Builtin-Stub für
#        api-ms-win-core-psm-appnotify-l1-1-0.dll, den die
#        riftbreaker_dll_win_release.dll importiert.
#   Der Prefix wird daher bereits beim IMAGE-BUILD über wine-init.sh
#   initialisiert (schneller Start) UND zusätzlich beim Containerstart
#   aufgerufen (marker-guarded, selbstheilend für frische/alte Named Volumes).
#
# Build (auf planet):
#   docker build -t rb-headless-client tools/headless-client
#
# Run (Client):
#   docker run --rm \
#     -v /srv/rbclient/game:/srv/rbclient/game \
#     -e RB_CLIENT_EXE=riftbreaker_win_release.exe \
#     rb-headless-client

FROM debian:bookworm-slim

# Keine interaktiven apt-Prompts.
ENV DEBIAN_FRONTEND=noninteractive

# Basis-Tooling: Xvfb (virtueller Framebuffer), xdotool (Maus/Tastatur),
# ImageMagick (Screenshots), Mesa-Software-Renderer (llvmpipe), xauth
# (xvfb-run), cabextract (winetricks braucht es für vcrun2022) sowie
# curl/ca-certificates (WineHQ-Key/Sources, winetricks)
# und iproute2/procps (In-Container-Diagnose: ss/pgrep). 32-Bit-Teile
# (i386) sind für die WineHQ-Pakete nötig. --no-install-recommends hält die
# Basis schlank; winehq-stable kommt unten mit --install-recommends.
RUN dpkg --add-architecture i386 \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        cabextract \
        iproute2 \
        procps \
        xvfb \
        x11-utils \
        xdotool \
        imagemagick \
        libgl1 \
        libgl1-mesa-dri \
        libglx-mesa0 \
        mesa-utils \
        xauth \
    && rm -rf /var/lib/apt/lists/*

# winehq-stable aus dem WineHQ-Bookworm-Repo (statt Debian-Wine 8.0). Liefert
# u. a. den Builtin-Stub für api-ms-win-core-psm-appnotify-l1-1-0.dll (Issue
# #239, Layer 3). Symlinks wine/wine64 nach /usr/local/bin für stabile Pfade.
RUN mkdir -pm755 /etc/apt/keyrings \
    && curl -fsSL -o /etc/apt/keyrings/winehq-archive.key https://dl.winehq.org/wine-builds/winehq.key \
    && curl -fsSL -o /etc/apt/sources.list.d/winehq-bookworm.sources \
        https://dl.winehq.org/wine-builds/debian/dists/bookworm/winehq-bookworm.sources \
    && apt-get update \
    && apt-get install -y --install-recommends winehq-stable \
    && ln -sf /usr/bin/wine /usr/local/bin/wine \
    && ln -sf /usr/bin/wine /usr/local/bin/wine64 \
    && rm -rf /var/lib/apt/lists/*

# winetricks (installiert vcrun2022 + d3dcompiler_47 in das Prefix).
RUN curl -fsSL -o /usr/local/bin/winetricks \
        https://raw.githubusercontent.com/Winetricks/winetricks/master/src/winetricks \
    && chmod +x /usr/local/bin/winetricks

# Software-Rendering erzwingen: Mesa-llvmpipe statt echter GPU.
# DISPLAY gilt nur für den Client-Pfad (run-client.sh mit eigenem Xvfb :99);
# der Server-Pfad nutzt `xvfb-run -a` und ignoriert DISPLAY.
# Issue #239 — Streaming-/Sync-Primitive und Locale wie im funktionierenden Rig:
#   * WINEESYNC=0/WINEFSYNC=0: die Wine-Sync-Primitive (esync/fsync) bringen den
#     DedicatedServer-Crash im Boost-Thread; das Rig fährt sie AUS.
#   * LC_ALL=C.UTF-8: stabile Locale für die Engine (Rig-Rezept).
#   * WINEDEBUG=-all,err+all: `err`-Kanal bleibt sichtbar (Diagnose), sonst still.
ENV DISPLAY=:99 \
    LIBGL_ALWAYS_SOFTWARE=1 \
    GALLIUM_DRIVER=llvmpipe \
    WINEARCH=win64 \
    WINEPREFIX=/root/.wine \
    WINEESYNC=0 \
    WINEFSYNC=0 \
    LC_ALL=C.UTF-8 \
    WINEDEBUG=-all,err+all \
    WINEDLLOVERRIDES="mscoree,mshtml="

# Mountpoint für die Client-Dateien (Quelle: sync-client.sh).
VOLUME ["/srv/rbclient/game"]
WORKDIR /srv/rbclient/game

# Tooling einbinden (inkl. wine-init.sh für den Server-Pfad).
COPY run-client.sh wait-menu.sh xdo-nav.sh nav-lobby.sh run-server.sh wine-init.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/run-client.sh /usr/local/bin/wait-menu.sh \
        /usr/local/bin/xdo-nav.sh /usr/local/bin/nav-lobby.sh \
        /usr/local/bin/run-server.sh /usr/local/bin/wine-init.sh

# Prefix-Init zur BUILD-Zeit (Issue #239): wineboot + win10 + vcrun2022 +
# d3dcompiler_47 + Marker. Bewusst non-fatal: schlägt der winetricks-Download
# im Build-Netz fehl, baut das Image trotzdem — run-server.sh ruft wine-init.sh
# beim Containerstart erneut auf (marker-guarded) und heilt das Prefix selbst.
RUN /usr/local/bin/wine-init.sh || \
        echo "[build] WARNUNG: Prefix-Init im Build fehlgeschlagen — Runtime-Selbstheilung greift."

# Client-Exe überschreibbar (Steam-Version: riftbreaker_win_release.exe).
ENV RB_CLIENT_EXE=riftbreaker_win_release.exe

ENTRYPOINT ["run-client.sh"]
