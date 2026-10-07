# rbtools-Builder (Issue #1093): cross-compiled die Server-I/O-Tools + gns_probe.
# Das Repo wird zur Laufzeit read-only gemountet (/src), damit `docker compose up`
# IMMER den aktuellen Checkout baut (kein Image-Rebuild bei Quelländerung).
FROM debian:stable-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
    ca-certificates \
    gcc-mingw-w64-x86-64 \
    g++-mingw-w64-x86-64 \
    binutils-mingw-w64-x86-64 \
    python3 \
    zip \
    && rm -rf /var/lib/apt/lists/*

COPY build.sh /usr/local/bin/rbtools-build.sh
RUN chmod +x /usr/local/bin/rbtools-build.sh

ENTRYPOINT ["/usr/local/bin/rbtools-build.sh"]
