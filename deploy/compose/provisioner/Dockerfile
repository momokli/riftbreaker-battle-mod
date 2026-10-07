# provisioner-service (Issue #1083): containerized twin of the on-demand
# provisioner (deploy/provisioner/provisioner.py, Issue #908). It serves the
# Provisioner's start/stop/status over HTTP (:8094) for the cold queue path.
#
# It shells out to `docker` (DockerCli) and creates SIBLING containers via
# `docker run`, so it needs the Docker CLI + the mounted docker.sock (no daemon
# in the image). The CLI is copied from `docker:cli` exactly like
# deploy/compose/server-control/Dockerfile.
#
# BUILD CONTEXT IS ./deploy/provisioner (where the two scripts live); the
# Dockerfile lives here (same pattern as tournament-server):
#   context:    ./deploy/provisioner
#   dockerfile: ../compose/provisioner/Dockerfile
# See README.md for the exact run contract.
FROM docker:cli AS dockercli

FROM python:3.12-slim

# Docker CLI only — talks to the host daemon via the mounted docker.sock.
COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
RUN docker --version

WORKDIR /app
# Pure-stdlib service; provisioner.py is the fachliche Kern (single source of
# truth — the container runs the SAME module as the CLI). No identity.py needed.
COPY provisioner.py /app/provisioner.py
COPY provisioner_service.py /app/provisioner_service.py

# Bind 0.0.0.0 in-container so the published loopback is reachable; the Bearer
# guard (PROVISIONER_TOKEN) still applies. Publish on 127.0.0.1:8094 only.
ENV PROVISIONER_BIND=0.0.0.0 \
    PROVISIONER_PORT=8094 \
    PYTHONUNBUFFERED=1

EXPOSE 8094

# ENTRYPOINT (not CMD): `docker run <img> --check` / compose `command: ["--check"]`
# appends as arguments to the service (1:1 with server-control/capsule-flow).
ENTRYPOINT ["python3", "-u", "/app/provisioner_service.py"]
