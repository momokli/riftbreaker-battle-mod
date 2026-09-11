FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    STEAMAPPID=4114030 \
    INSTALL_DIR=/opt/riftbreaker \
    WINEPREFIX=/data/.wine \
    WINEARCH=win64 \
    WINEDEBUG=-all,err+all \
    WINEDLLOVERRIDES="mscoree,mshtml=" \
    STEAM_USER=steamuser

RUN dpkg --add-architecture i386 \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        cabextract \
        gosu \
        iproute2 \
        lib32gcc-s1 \
        locales \
        procps \
        tini \
        xauth \
        xvfb \
    && sed -i '/en_US.UTF-8/s/^# //' /etc/locale.gen \
    && locale-gen \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -s /bin/bash "${STEAM_USER}" \
    && mkdir -p /opt/steamcmd "${INSTALL_DIR}" \
    && curl -fsSL "https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz" \
        | tar -xz -C /opt/steamcmd \
    && chown -R "${STEAM_USER}:${STEAM_USER}" /opt/steamcmd \
    && chmod +x /opt/steamcmd/steamcmd.sh

RUN mkdir -pm755 /etc/apt/keyrings \
    && curl -fsSL -o /etc/apt/keyrings/winehq-archive.key https://dl.winehq.org/wine-builds/winehq.key \
    && curl -fsSL -o /etc/apt/sources.list.d/winehq-bookworm.sources \
        https://dl.winehq.org/wine-builds/debian/dists/bookworm/winehq-bookworm.sources \
    && apt-get update \
    && apt-get install -y --install-recommends winehq-stable \
    && ln -sf /usr/bin/wine /usr/local/bin/wine \
    && ln -sf /usr/bin/wine /usr/local/bin/wine64 \
    && rm -rf /var/lib/apt/lists/*

COPY --chown=${STEAM_USER}:${STEAM_USER} scripts/ /scripts/

RUN sed -i 's/\r$//' /scripts/*.sh \
    && chmod +x /scripts/*.sh \
    && curl -fsSL -o /usr/local/bin/winetricks https://raw.githubusercontent.com/Winetricks/winetricks/master/src/winetricks \
    && chmod +x /usr/local/bin/winetricks

# Issue #241: Wine-Prefix schon beim Build initialisieren und in das Image
# baken. Docker kopiert Image-Inhalt in ein LEERES Named Volume beim ersten
# Mount — ein frisches rb-wine-Volume erbt also den fertigen Prefix (kein
# Laufzeit-Init, kein Seeding nötig). wineboot braucht kein X; winetricks schon
# (xvfb-run). Die Laufzeit-Selbstheilung in wine-init.sh bleibt als Fallback.
RUN /scripts/wine-init.sh

WORKDIR /home/${STEAM_USER}

EXPOSE 6321/tcp 6321/udp

HEALTHCHECK --interval=15s --timeout=5s --start-period=120s --retries=5 \
    CMD pgrep -f DedicatedServer.exe >/dev/null || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/scripts/docker-entrypoint.sh"]
