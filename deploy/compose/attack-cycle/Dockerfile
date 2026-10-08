# attack-cycle (Issue #1093): containerized attack-cycle.
#
# Pure-stdlib module — the single source of truth is deploy/attack-cycle/ and is
# BAKED into the image, so `docker compose up --build` rebuilds it on code
# changes (no read-only code mount anymore).
#
# BUILD CONTEXT IS ./deploy/attack-cycle; the Dockerfile lives here:
#   context:    ./deploy/attack-cycle
#   dockerfile: ../compose/attack-cycle/Dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY attack_cycle.py /app/attack_cycle.py

ENV PYTHONUNBUFFERED=1
