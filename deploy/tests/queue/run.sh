#!/usr/bin/env bash
# Hermetischer Render-Selbsttest der Rolle queue (Issue #998).
#
# Prüft die ECHTEN Rollen-Vorlagen (kein Nachbau): rendert systemd-Unit +
# EnvironmentFile lokal mit Dummy-Werten und asserted die Invarianten
# (ExecStart, EnvironmentFile, WorkingDirectory, Restart=on-failure,
# Type=simple, Token/Nachbarn/Bind/Port). Zusaetzlich: der Dienst-Einstieg
# `queue_service.py --check` muss mit gueltiger Config rc=0 liefern und mit
# einer kaputten Zahl fail-closed rc=2 (kein stiller Start).
#
# Kein Host, kein SSH, kein Docker, kein Vault, keine Prod-Aktion.
# Läuft in deploy-check-local.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
play="$here/main.yml"
repo="$(cd "$here/../../.." && pwd)"

echo "== Render mit Defaults (require_docker_service=false) =="
default_dir="$(mktemp -d)"
RB_QUEUE_RENDER_DIR="$default_dir" ansible-playbook "$play"
if grep -q 'Requires=docker.service' "${default_dir}/rbmods-queue-dev.service"; then
  echo "FEHLER: Requires=docker.service haette mit Default false NICHT gerendert werden duerfen" >&2
  exit 1
fi
grep -q 'EnvironmentFile=/etc/rbmods/queue-dev.env' "${default_dir}/rbmods-queue-dev.service"

echo "== Docker-Requires einschaltbar (Flag) =="
docker_dir="$(mktemp -d)"
RB_QUEUE_RENDER_DIR="$docker_dir" ansible-playbook "$play" -e test_require_docker_service=true
grep -q 'Requires=docker.service' "${docker_dir}/rbmods-queue-dev.service"

echo "== queue_service.py --check: gueltige Config -> rc=0 =="
QUEUE_ENV=dev QUEUE_PORT=9221 QUEUE_TOKEN=dummy \
  python3 "$repo/deploy/queue/queue_service.py" --check

echo "== queue_service.py --check: kaputte Zahl -> rc=2 (fail-closed) =="
set +e
QUEUE_ENV=dev QUEUE_PORT=0 QUEUE_TOKEN=dummy \
  python3 "$repo/deploy/queue/queue_service.py" --check >/dev/null 2>&1
rc=$?
set -e
if [ "$rc" -ne 2 ]; then
  echo "::error::queue_service.py --check haette bei QUEUE_PORT=0 mit rc=2 abbrechen muessen (rc=$rc)." >&2
  exit 1
fi

echo "OK: Unit/Env rendern, Invarianten halten, --check fail-closed."