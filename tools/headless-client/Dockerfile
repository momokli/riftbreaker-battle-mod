# Headless Riftbreaker-Client: Wine + Xvfb + Mesa-llvmpipe (Software-Rendering).
#
# Kein GPU-Zugriff nötig — die Engine rendert über Mesa-llvmpipe in den
# virtuellen Framebuffer (Xvfb). Steuerung/Auswertung via xdotool + Screenshots
# (tools/headless-client/xdo-nav.sh), Dateien kommen aus sync-client.sh.
#
# Build (auf planet):
#   docker build -t rb-headless-client tools/headless-client
#
# Run:
#   docker run --rm \
#     -v /srv/rbclient/game:/srv/rbclient/game \
#     -e RB_CLIENT_EXE=riftbreaker_win_release.exe \
#     rb-headless-client
#
# Navigation (zweite Shell):
#   docker exec <container> xdo-nav.sh shot /tmp/shot.png
#   docker exec <container> xdo-nav.sh click 960 540

FROM debian:bookworm-slim

# Keine interaktiven apt-Prompts.
ENV DEBIAN_FRONTEND=noninteractive

# Wine (32+64 Bit, kanonisches Debian-Setup — 32-Bit-Teile sind für manche
# Spiele-Launcher/DRM nötig), Xvfb (virtueller Framebuffer), xdotool
# (Maus/Tastatur), ImageMagick (Screenshots) und Mesa-Software-Renderer
# (llvmpipe). --no-install-recommends hält das Image schlank.
RUN dpkg --add-architecture i386 \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        wine \
        wine32 \
        wine64 \
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

# Software-Rendering erzwingen: Mesa-llvmpipe statt echter GPU.
ENV DISPLAY=:99 \
    LIBGL_ALWAYS_SOFTWARE=1 \
    GALLIUM_DRIVER=llvmpipe \
    WINEARCH=win64 \
    WINEPREFIX=/root/.wine \
    WINEDEBUG=-all

# Mountpoint für die Client-Dateien (Quelle: sync-client.sh).
VOLUME ["/srv/rbclient/game"]
WORKDIR /srv/rbclient/game

# Tooling einbinden.
COPY run-client.sh wait-menu.sh xdo-nav.sh nav-lobby.sh run-server.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/run-client.sh /usr/local/bin/wait-menu.sh /usr/local/bin/xdo-nav.sh /usr/local/bin/nav-lobby.sh /usr/local/bin/run-server.sh

# Client-Exe überschreibbar (Steam-Version: riftbreaker_win_release.exe).
ENV RB_CLIENT_EXE=riftbreaker_win_release.exe

ENTRYPOINT ["run-client.sh"]
