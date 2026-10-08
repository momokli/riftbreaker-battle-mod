# server-control (Issue #1093): containerized twin of the former systemd unit
# `server-control` (Issue #424).
#
# The agent is pure-stdlib Python (server_control.py) and wraps the Docker CLI on
# the host: status/logs/restart/start/stop + config.cfg rendering. It only needs
# the Docker CLI + a socket. The module is the single source of truth and is
# BAKED in, so `docker compose up --build` rebuilds it on code changes.
#
# BUILD CONTEXT IS ./deploy/server-control; the Dockerfile lives here:
#   context:    ./deploy/server-control
#   dockerfile: ../compose/server-control/Dockerfile
FROM docker:cli AS dockercli

FROM python:3.12-slim

# Docker CLI only — talks to the host daemon via the mounted docker.sock.
COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
RUN docker --version

WORKDIR /app
COPY server_control.py /app/server_control.py

COPY entrypoint.sh /usr/local/bin/rbmods-server-control-entrypoint.sh
RUN chmod +x /usr/local/bin/rbmods-server-control-entrypoint.sh

# Bind inside the container is 0.0.0.0; publish it on the host loopback only.
EXPOSE 8092

ENTRYPOINT ["/usr/local/bin/rbmods-server-control-entrypoint.sh"]
