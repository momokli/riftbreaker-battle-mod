# server-control (Issue #1093): containerized twin of the systemd unit
# `server-control` (deploy/roles/server-control, Issue #424).
#
# The agent is pure-stdlib Python (`deploy/server-control/server_control.py`) and
# wraps the Docker CLI on the host: status/logs/restart/start/stop + config.cfg
# rendering. It only needs the Docker CLI + a socket, so the image is a slim
# Python base plus the CLI. The script is mounted read-only at runtime (1:1 with
# the role). See README.md for the exact run contract.
FROM docker:cli AS dockercli

FROM python:3.12-slim

# Docker CLI only — talks to the host daemon via the mounted docker.sock.
COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
RUN docker --version

COPY entrypoint.sh /usr/local/bin/rbmods-server-control-entrypoint.sh
RUN chmod +x /usr/local/bin/rbmods-server-control-entrypoint.sh

# Default port (role: 8091, prod twin: 8092). Bind inside the container is
# 0.0.0.0; publish it on the host loopback only (127.0.0.1:<port>).
EXPOSE 8092

ENTRYPOINT ["/usr/local/bin/rbmods-server-control-entrypoint.sh"]
