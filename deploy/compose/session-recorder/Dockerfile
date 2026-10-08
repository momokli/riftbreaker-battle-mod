# session-recorder (Issue #1093): containerized session recorder.
#
# Pure-stdlib module — the single source of truth is deploy/session-recorder/ and
# is BAKED into the image, so `docker compose up --build` rebuilds it on code
# changes (no read-only code mount anymore).
#
# BUILD CONTEXT IS ./deploy/session-recorder; the Dockerfile lives here:
#   context:    ./deploy/session-recorder
#   dockerfile: ../compose/session-recorder/Dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY session_recorder.py /app/session_recorder.py

ENV PYTHONUNBUFFERED=1
