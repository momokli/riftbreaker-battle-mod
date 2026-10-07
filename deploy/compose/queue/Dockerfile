# queue (Issue #1093): containerized twin of the systemd unit `rbmods-queue`
# (deploy/roles/queue, Issue #998). It runs the SAME stdlib module as the role
# (deploy/queue/queue_service.py).
#
# It talks to the provisioner (:8094) and the referee (tournament-server, :8081)
# purely over HTTP — no Docker socket, no game code. The only optional mount is
# the state dir (match-record persistence, QUEUE_STATE_DIR). See README.md for
# the exact run contract.
#
# BUILD CONTEXT IS ./deploy/queue (where the four scripts live); the Dockerfile
# lives here (same pattern as provisioner/tournament-server):
#   context:    ./deploy/queue
#   dockerfile: ../compose/queue/Dockerfile
FROM python:3.12-slim

WORKDIR /app
# Pure-stdlib service; the module is the single source of truth — the container
# runs the SAME files as the role. queue_service.py imports queue_flow.py +
# queue_core.py, and queue_flow.py imports identity.py, so all four are copied.
COPY queue_service.py /app/queue_service.py
COPY queue_flow.py /app/queue_flow.py
COPY queue_core.py /app/queue_core.py
COPY identity.py /app/identity.py

# Bind 0.0.0.0 in-container so the published loopback is reachable; the Bearer
# guard (QUEUE_TOKEN) still applies. Publish on 127.0.0.1:9221 only.
ENV QUEUE_BIND=0.0.0.0 \
    QUEUE_PORT=9221 \
    PYTHONUNBUFFERED=1

EXPOSE 9221

# ENTRYPOINT (not CMD): `docker run <img> --check` / compose `command: ["--check"]`
# appends as arguments to the service (1:1 with provisioner/capsule-flow).
ENTRYPOINT ["python3", "-u", "/app/queue_service.py"]
