# Dedicated-Server-Laufzeit auf Basis von scottyhardy/docker-wine (winehq-stable,
# winetricks, Xvfb, Gecko/Mono — gepflegtes, getestetes Wine-Setup). Wir ergänzen
# nur noch den steamuser + unsere Entrypoint-/Init-Scripts. Content wird als
# Volume gemountet (kein SteamCMD im Image).
FROM scottyhardy/docker-wine:latest

ENV INSTALL_DIR=/opt/riftbreaker \
    WINEPREFIX=/data/.wine \
    WINEARCH=win64 \
    WINEDEBUG=-all,err+all \
    WINEDLLOVERRIDES="mscoree,mshtml=" \
    STEAM_USER=steamuser

# procps für pgrep (HEALTHCHECK). steamuser für den Container-User.
RUN apt-get update \
    && apt-get install -y --no-install-recommends procps \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -s /bin/bash "${STEAM_USER}" \
    && mkdir -p "${INSTALL_DIR}"

COPY --chown=${STEAM_USER}:${STEAM_USER} scripts/ /scripts/
RUN sed -i 's/\r$//' /scripts/*.sh && chmod +x /scripts/*.sh

# Wine-Prefix beim Build initialisieren (from-zero, Issue #241). wineboot schreibt
# system.reg asynchron -> wineserver -w abwarten; WINEDEBUG=-all (err-Kanal bricht
# wineboot); wineserver -k räumt den wineserver auf (kein stale State im Image).
# Ein frisches Named Volume erbt den fertigen Prefix (Docker kopiert Image-Inhalt).
RUN export WINEPREFIX=/data/.wine WINEARCH=win64 WINEDEBUG=-all \
    && mkdir -p /data/.wine \
    && wineboot --init 2>/dev/null || true \
    && wineserver -w 2>/dev/null || true \
    && xvfb-run -a winetricks -q vcrun2022 d3dcompiler_47 || true \
    && wineserver -k 2>/dev/null || true \
    && touch /data/.wine/.riftbreaker-runtime-done

WORKDIR /home/${STEAM_USER}

EXPOSE 6321/tcp 6321/udp

HEALTHCHECK --interval=15s --timeout=5s --start-period=120s --retries=5 \
    CMD pgrep -f DedicatedServer.exe >/dev/null || exit 1

ENTRYPOINT ["/scripts/docker-entrypoint.sh"]
