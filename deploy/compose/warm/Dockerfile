# warm (Issue #1093): "warmed capsule" claim source for the STATIC compose
# dedicated server.
#
# Pure-stdlib Python service. It implements the SUBSET of the Parked-service HTTP
# contract the capsule needs (/health, /status, /claim, /recycle) for exactly ONE
# static instance — no provisioner, no pool, no Docker socket. It talks to the
# dedicated's HTTP bridge only. See README.md for the exact run contract.
FROM python:3.12-slim

COPY warm_service.py /app/warm_service.py

WORKDIR /app

# Bind inside the container is 0.0.0.0 (WARM_BIND); publish host-loopback only.
EXPOSE 9201

CMD ["python3", "-u", "/app/warm_service.py"]
