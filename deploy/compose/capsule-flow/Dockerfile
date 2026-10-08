# capsule-flow (Issue #1093): containerized twin of the former systemd unit
# `rbmods-capsule-<env>` (Issue #931).
#
# Pure-stdlib Python service — talks to Parked/Attack-Cycle/Bridge over HTTP only
# (no Docker socket). The module (capsule_service.py + capsule_flow.py +
# identity.py) is the single source of truth and is BAKED in, so
# `docker compose up --build` rebuilds it on code changes.
#
# BUILD CONTEXT IS ./deploy/capsule; the Dockerfile lives here:
#   context:    ./deploy/capsule
#   dockerfile: ../compose/capsule-flow/Dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY capsule_service.py /app/capsule_service.py
COPY capsule_flow.py /app/capsule_flow.py
COPY identity.py /app/identity.py

COPY entrypoint.sh /usr/local/bin/rbmods-capsule-flow-entrypoint.sh
RUN chmod +x /usr/local/bin/rbmods-capsule-flow-entrypoint.sh

# Bind inside the container is 0.0.0.0; publish it on the host loopback only.
EXPOSE 9211

ENTRYPOINT ["/usr/local/bin/rbmods-capsule-flow-entrypoint.sh"]
