# match-loop (Issue #1093): containerized match-loop.
#
# Pure-stdlib module — the single source of truth is deploy/match-loop/ and is
# BAKED into the image, so `docker compose up --build` rebuilds it on code
# changes (no read-only code mount anymore).
#
# BUILD CONTEXT IS ./deploy/match-loop; the Dockerfile lives here:
#   context:    ./deploy/match-loop
#   dockerfile: ../compose/match-loop/Dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY match_loop.py /app/match_loop.py

ENV PYTHONUNBUFFERED=1
