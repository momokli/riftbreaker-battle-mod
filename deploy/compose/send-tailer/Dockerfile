# send-tailer (Issue #1093): containerized send-tailer.
#
# Pure-stdlib module — the single source of truth is deploy/send-tailer/ and is
# BAKED into the image, so `docker compose up --build` rebuilds it on code
# changes (no read-only code mount anymore).
#
# BUILD CONTEXT IS ./deploy/send-tailer; the Dockerfile lives here:
#   context:    ./deploy/send-tailer
#   dockerfile: ../compose/send-tailer/Dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY send_tailer.py /app/send_tailer.py

ENV PYTHONUNBUFFERED=1
