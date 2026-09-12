# syntax=docker/dockerfile:1.7
#
# RIFT BATTLE Tournament-Server (Rust/axum) — Multi-Stage-Image (Issue #275).
#
# Stage "builder": rust:bookworm baut das Release-Binary reproduzierbar gegen
#   Cargo.lock (--locked). BuildKit-Cache-Mounts halten Rebuilds billig
#   (Registry + target/ bleiben zwischen Builds erhalten).
# Stage "runtime": debian:bookworm-slim, non-root. Binary + web/ sind fest im
#   Image (TOURNAMENT_WEB_DIR=/app/web). curl ist bewusst dabei: es trägt den
#   HEALTHCHECK und die `docker exec`-Verifikation des Boot-Tests (kein
#   Host-Port, Issue #275, Design D1/D6).
#
# Build-Kontext ist dieses Verzeichnis (tournament/). Der Deploy stagt es nach
# /opt/rbmods/tournament-build und baut rb-tournament:<deploy-sha>
# (Rolle tournament-server).

FROM rust:1-bookworm AS builder
WORKDIR /src
COPY Cargo.toml Cargo.lock ./
COPY src ./src
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/src/target \
    cargo build --release --locked && \
    cp /src/target/release/tournament-server /usr/local/bin/tournament-server

FROM debian:bookworm-slim AS runtime
RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates curl && \
    rm -rf /var/lib/apt/lists/* && \
    useradd --system --uid 10001 --create-home --shell /usr/sbin/nologin tournament

COPY --from=builder /usr/local/bin/tournament-server /usr/local/bin/tournament-server
COPY web /app/web

ENV TOURNAMENT_HOST=0.0.0.0 \
    TOURNAMENT_PORT=8081 \
    TOURNAMENT_WEB_DIR=/app/web \
    RUST_LOG=info

EXPOSE 8081

# Liveness über den eigenen /health-Endpoint (curl ist im Image). --start-period
# deckt den Start ab; der Boot-Test prüft zusätzlich Health.Status=healthy.
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD-SHELL curl -fsS "http://127.0.0.1:${TOURNAMENT_PORT}/health" || exit 1

USER tournament
ENTRYPOINT ["/usr/local/bin/tournament-server"]
