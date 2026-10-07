# mod-build-Init-Container (Issue #1093): baut das Battle-Mod-Zip aus dem
# aktuellen Checkout und rollt es ins Game-Volume aus (Port des Mod-Blocks der
# Ansible-Rolle riftbreaker-server).
#
# Das Repo wird zur Laufzeit read-only nach /src gemountet und fuer den Build in
# ein schreibbares Arbeitsverzeichnis kopiert (package.sh schreibt nach
# <root>/dist). Das Game-Volume (rb-game) haengt an /game.
FROM debian:stable-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
    bash \
    coreutils \
    findutils \
    tar \
    gzip \
    zip \
    unzip \
    python3 \
    && rm -rf /var/lib/apt/lists/*

COPY rollout.sh /usr/local/bin/rollout.sh
RUN chmod +x /usr/local/bin/rollout.sh

ENTRYPOINT ["/usr/local/bin/rollout.sh"]
