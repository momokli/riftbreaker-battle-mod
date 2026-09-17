#!/usr/bin/env bash
# Hermetischer Render-Selbsttest der Rolle server-control (Issue #424/#593).
#
# Prüft die ECHTE systemd-Unit-Vorlage (kein Nachbau): rendert sie lokal mit
# Dummy-Werten und asserted die Invarianten (ExecStart, EnvironmentFile,
# WorkingDirectory, Restart=on-failure, Type=simple). Requires=docker.service
# ist KEINE feste Invariante (Issue #593) — beide Zweige von
# server_control_require_docker_service (true/false) werden separat gerendert
# und geprüft, inkl. Regression-Guard gegen eine verwaiste Leerzeile im
# [Unit]-Block, wenn die Zeile weggelassen wird.
#
# Kein Host, kein SSH, kein Docker, kein Vault, keine Prod-Aktion.
# Läuft in deploy-check-local.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
play="$here/main.yml"

echo "== Render mit Defaults (require_docker_service=true, planet-Verhalten) =="
default_dir="$(mktemp -d)"
RB_SC_RENDER_DIR="$default_dir" ansible-playbook "$play"
grep -q 'Requires=docker.service' "${default_dir}/server-control.service"

echo "== Docker-Requires abschaltbar (Issue #593, WSL2 + Docker Desktop) =="
no_docker_dir="$(mktemp -d)"
RB_SC_RENDER_DIR="$no_docker_dir" ansible-playbook "$play" -e test_require_docker_service=false
no_docker_unit="${no_docker_dir}/server-control.service"
if grep -q 'Requires=docker.service' "$no_docker_unit"; then
  echo "FEHLER: Requires=docker.service haette bei require_docker_service=false NICHT gerendert werden duerfen" >&2
  exit 1
fi
after_wants="$(grep -A1 '^Wants=network-online.target$' "$no_docker_unit" | tail -1)"
if [ -z "$after_wants" ]; then
  echo "FEHLER: verwaiste Leerzeile direkt nach Wants= im [Unit]-Block bei require_docker_service=false (Issue #593)" >&2
  exit 1
fi
echo "OK: require_docker_service=false laesst Requires=docker.service weg, keine Leerzeile."

echo "OK: Unit rendert, Invarianten halten, Docker-Requires ist konfigurierbar."
