# capsule-flow (Issue #1093): containerized twin of the former systemd unit
# `rbmods-capsule-<env>` (Issue #931).
#
# Pure-stdlib Python service — it talks to Parked/Attack-Cycle/Bridge over HTTP
# only (no Docker socket, no volumes). The three scripts are mounted read-only at
# runtime (1:1 with the role). See README.md for the exact run contract.
FROM python:3.12-slim

COPY entrypoint.sh /usr/local/bin/rbmods-capsule-flow-entrypoint.sh
RUN chmod +x /usr/local/bin/rbmods-capsule-flow-entrypoint.sh

# Default port (Issue #931 plan). Bind inside the container is 0.0.0.0; publish
# it on the host loopback only (127.0.0.1:9211).
EXPOSE 9211

ENTRYPOINT ["/usr/local/bin/rbmods-capsule-flow-entrypoint.sh"]
