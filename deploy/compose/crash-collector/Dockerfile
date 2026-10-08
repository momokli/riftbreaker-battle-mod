# crash-collector (Issue #1093): containerized twin of the former systemd unit
# `rbmods-crash-collector` (Issues #462/#480/#481).
#
# The image ships the runtime (python3, `llvm-symbolizer` (llvm-18), Docker CLI)
# AND the collector/symbolizer scripts (BAKED — `docker compose up --build`
# rebuilds them on code changes). The game DLL/PDB + rbbridge.dll stay OUTSIDE the
# image (Issue #480: the 252 MB PDB must not bloat the image).
#
# BUILD CONTEXT IS ./deploy/crash-collector; the Dockerfile lives here:
#   context:    ./deploy/crash-collector
#   dockerfile: ../compose/crash-collector/Dockerfile
FROM docker:cli AS dockercli

FROM ubuntu:24.04

# Docker CLI only — it talks to the host daemon via the mounted
# /var/run/docker.sock (docker logs/cp/exec/inspect).
COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
RUN docker --version

# python3          -> meta.json rendering + minidump parser + symbolizer core
# llvm-18          -> /usr/lib/llvm-18/bin/llvm-symbolizer
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

ENV RB_CRASH_LLVM_SYMBOLIZER=/usr/lib/llvm-18/bin/llvm-symbolizer

# The collector/symbolizer scripts (single source of truth: deploy/crash-collector/).
COPY crash_collector.sh crash_symbolize.sh minidump_meta.py symbolize.py /opt/crash/
COPY entrypoint.sh /usr/local/bin/rbmods-crash-collector-entrypoint.sh
RUN chmod +x /usr/local/bin/rbmods-crash-collector-entrypoint.sh \
    /opt/crash/crash_collector.sh /opt/crash/crash_symbolize.sh

# No CMD: the entrypoint defaults to the daemon (`crash_collector.sh`); pass
# `--once` to read the stream to EOF and exit (tests / one-shot runs).
ENTRYPOINT ["/usr/local/bin/rbmods-crash-collector-entrypoint.sh"]
