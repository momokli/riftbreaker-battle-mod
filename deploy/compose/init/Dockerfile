# Init-Image (Issue #1093): Game-Content + config.cfg bereitstellen.
# Ein Image, zwei Kommandos (content-init / config-init) — curl + envsubst.
FROM debian:stable-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    coreutils \
    gettext-base \
    tar \
    gzip \
    && rm -rf /var/lib/apt/lists/*

COPY content-init.sh config-init.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/content-init.sh /usr/local/bin/config-init.sh
