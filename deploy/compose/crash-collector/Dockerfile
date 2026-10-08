# crash-collector (Issue #1093): containerized twin of the former systemd unit
# `rbmods-crash-collector`.
#
# The image ships ONLY the runtime — python3, `llvm-symbolizer` (llvm-18) and the
# Docker CLI. The collector/symbolizer scripts themselves are mounted read-only
# at runtime from `deploy/crash-collector/` (1:1 with the unit), and the game
# DLL/PDB + rbbridge.dll stay OUTSIDE the image (Issue #480: the 252 MB PDB must
# not bloat the image). See README.md for the exact run contract.
FROM docker:cli AS dockercli

FROM ubuntu:24.04

# Docker CLI only — it talks to the host daemon via the mounted
# /var/run/docker.sock (used for `docker logs -f`, `docker cp`, `docker exec`).
# Copied first so a broken CLI fails the build fast.
COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
RUN docker --version

# python3          -> meta.json rendering + minidump parser + symbolizer core
# llvm-18          -> /usr/lib/llvm-18/bin/llvm-symbolizer (role default path)
# bash/coreutils/findutils/gawk/grep/sed -> the shell scripts' toolchain
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
    ca-certificates \
    python3 \
    llvm-18 \
    bash \
    coreutils \
    findutils \
    gawk \
    grep \
    sed \
    && rm -rf /var/lib/apt/lists/*

# The role default (llvm-18 Debian/Ubuntu layout) — override via
# RB_CRASH_LLVM_SYMBOLIZER if the image ever moves to another base.
ENV RB_CRASH_LLVM_SYMBOLIZER=/usr/lib/llvm-18/bin/llvm-symbolizer

COPY entrypoint.sh /usr/local/bin/rbmods-crash-collector-entrypoint.sh
RUN chmod +x /usr/local/bin/rbmods-crash-collector-entrypoint.sh

# No CMD: the entrypoint defaults to the daemon (`crash_collector.sh`); pass
# `--once` to read the stream to EOF and exit (tests / one-shot runs).
ENTRYPOINT ["/usr/local/bin/rbmods-crash-collector-entrypoint.sh"]
