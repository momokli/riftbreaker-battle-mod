# Dedicated-Server-Laufzeit auf Basis von scottyhardy/docker-wine (winehq-stable,
# winetricks, Xvfb, Gecko/Mono — gepflegtes, getestetes Wine-Setup). Wir ergänzen
# nur noch den steamuser + unsere Entrypoint-/Init-Scripts. Content wird als
# Volume gemountet (kein SteamCMD im Image).
#
# Digest-Pin (Issue #247): entspricht scottyhardy/docker-wine:latest vom
# 2026-09-12 (Ubuntu 24.04 noble, wine-11.0). Aktualisieren:
#   docker buildx imagetools inspect scottyhardy/docker-wine:latest
#   → den neuen linux/amd64-Digest unten in FROM eintragen.
FROM scottyhardy/docker-wine@sha256:e1cbc2d83a945a88702654a028fc18f596a65b3e7d585b7fc0afbed92109e81b

ENV INSTALL_DIR=/opt/riftbreaker \
    WINEPREFIX=/data/.wine \
    WINEARCH=win64 \
    WINEDEBUG=-all,err+all \
    WINEDLLOVERRIDES="mscoree,mshtml=" \
    STEAM_USER=steamuser

# procs (pgrep für den HEALTHCHECK) und useradd sind im Basis-Image bereits
# vorhanden (im Container verifiziert, Issue #247) → KEIN apt im Image, damit
# auch keine ungepinnten Pakete. Die Assertion lässt den Build laut scheitern,
# falls ein künftiges Basis-Image diese Tools verliert.
RUN command -v pgrep >/dev/null \
    && command -v useradd >/dev/null \
    && useradd -m -s /bin/bash "${STEAM_USER}" \
    && mkdir -p "${INSTALL_DIR}"

# winetricks auf Commit pinnen statt den master-Branch des Basis-Images zu nutzen.
# wget lädt das Skript des Release-Commits; sha256sum -c verifiziert es vor der
# Nutzung. Aktualisieren: Commit + SHA256 des Blobs `src/winetricks` aus
# https://github.com/Winetricks/winetricks/releases übernehmen.
ARG WINETRICKS_COMMIT=b76e1ee79ac57d7aceb384f74518fc423265810c
ARG WINETRICKS_SHA256=431f82fc74000e6c864409f1d8fb495d696c03928808e3e8acffc45179312a7b
RUN wget -nv -O /usr/bin/winetricks \
        "https://raw.githubusercontent.com/Winetricks/winetricks/${WINETRICKS_COMMIT}/src/winetricks" \
    && echo "${WINETRICKS_SHA256}  /usr/bin/winetricks" | sha256sum -c - \
    && chmod +x /usr/bin/winetricks

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
