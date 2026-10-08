# tournament-server (Issue #1093): containerized twin of the former systemd unit
# `tournament-server` (Issues #29/#30/#298).
#
# BUILD CONTEXT IS ./tournament (the Rust crate), the Dockerfile lives here:
#   context:    ./tournament
#   dockerfile: ../deploy/compose/tournament-server/Dockerfile
# The release binary is built from the current checkout (`cargo build --release
# --locked`, like the role on planet) and the static Web-UI is served by it.
# See README.md for the exact run contract.
FROM rust:1-bookworm AS build

WORKDIR /src
# Manifest + lockfile first for better layer caching of the dependency build.
COPY Cargo.toml Cargo.lock ./
# rusqlite `bundled` needs a C compiler (present in the rust image) — it compiles
# SQLite statically into the binary (no runtime libsqlite3).
COPY src ./src
RUN cargo build --release --locked

FROM debian:bookworm-slim

# ca-certificates: outbound HTTP(S) to the rbbridge exec endpoints.
# curl: container HEALTHCHECK against /health.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /src/target/release/tournament-server /usr/local/bin/tournament-server
# Static Web-UI (Lobby + Spectator-Dashboard), served by the binary.
COPY web /usr/share/tournament/web
ENV TOURNAMENT_WEB_DIR=/usr/share/tournament/web

EXPOSE 8080

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=5 \
    CMD curl -fsS "http://127.0.0.1:${TOURNAMENT_PORT:-8080}/health" >/dev/null || exit 1

CMD ["/usr/local/bin/tournament-server"]
