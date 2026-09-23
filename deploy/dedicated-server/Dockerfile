# Dedicated-Server-Laufzeit auf Basis von scottyhardy/docker-wine (winehq-stable,
# winetricks, Xvfb, Gecko/Mono — gepflegtes, getestetes Wine-Setup). Wir ergänzen
# nur noch den steamuser + unsere Entrypoint-/Init-Scripts. Content wird als
# Volume gemountet (kein SteamCMD im Image).
FROM scottyhardy/docker-wine:latest

ENV INSTALL_DIR=/opt/riftbreaker \
    WINEPREFIX=/data/.wine \
    WINEARCH=win64 \
    WINEDEBUG=-all \
    WINEDLLOVERRIDES="mscoree,mshtml=" \
    STEAM_USER=steamuser

# procps für pgrep (HEALTHCHECK), iproute2 für `ss` im Port-Watch
# (scripts/entrypoint.sh watch_server_port). Ohne iproute2 ist der
# In-Container-Port-Check still (das Basis-Image bringt `ss` nicht mit).
# curl (Issue #902/US5): das Basis-Image bringt KEIN curl/wget -> für den
# HEALTHCHECK (Pipe-Status) und den host-seitigen Watchdog nachinstallieren.
RUN apt-get update \
    && apt-get install -y --no-install-recommends procps iproute2 curl \
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

# Healthcheck spiegelt den Pipe-Status (Issue #902/US5): nicht nur "Prozess
# laeuft", sondern auch die Ingress-Bridge (HTTP 9001 -> Named-Pipe). Seit US1
# liefert /health bei toter Pipe HTTP 503 -> curl -f schlaegt fehl -> unhealthy.
# start-period 300s (statt 120s): Injection + Spielbereitschaft + Bridge-Start
# koennen >180s brauchen -> kein Flapping in der Startphase.
HEALTHCHECK --interval=15s --timeout=5s --start-period=300s --retries=5 \
    CMD pgrep -f DedicatedServer.exe >/dev/null && curl -fsS --max-time 4 http://127.0.0.1:9001/health >/dev/null

ENTRYPOINT ["/scripts/docker-entrypoint.sh"]
