# hygiene (Issue #1103): containerized twin of the systemd timers
# `rbmods-image-retention` (#309) and `rbmods-host-hygiene` (#308/#606)
# (deploy/roles/image-retention, deploy/roles/host-hygiene).
#
# The image ships ONLY the runtime — the Docker CLI + shell toolchain. The two
# maintenance scripts themselves are mounted read-only at runtime from
# `deploy/image-retention/` and `deploy/host-hygiene/` (1:1 with the Ansible
# roles); nothing is baked. The container idles and carries the tools + mounts;
# `ofelia` schedules the jobs as labels and execs them INTO it (`job-exec`), so
# no ENTRYPOINT/CMD is baked beyond the idle default. See README.md for the
# exact run contract.
FROM docker:cli AS dockercli

FROM ubuntu:24.04

# Docker CLI only — it talks to the host daemon via the mounted
# /var/run/docker.sock (used for `docker image ls/rmi/prune`).
# Copied first so a broken CLI fails the build fast.
COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
RUN docker --version

# bash/coreutils/findutils/grep/sed -> the shell scripts' toolchain.
# Pure shell: no python, no llvm (unlike crash-collector).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
    ca-certificates \
    bash \
    coreutils \
    findutils \
    grep \
    sed \
    && rm -rf /var/lib/apt/lists/*

# Idle default: the jobs are exec'd into the running container by Ofelia
# (`job-exec`), so the container must stay up doing nothing. No ENTRYPOINT.
CMD ["sleep", "infinity"]
