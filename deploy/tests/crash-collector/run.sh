#!/usr/bin/env bash
# Hermetischer Render-Selbsttest der Rolle crash-collector (Issue #462/#481/#593).
#
# Prüft die ECHTE systemd-Unit-Vorlage (kein Nachbau): rendert sie lokal mit
# Dummy-Werten und asserted die Invarianten (ExecStart, RB_CRASH_*-Env inkl.
# RB_CRASH_MINIDUMP_PY, Restart=always, Type=simple). Requires=docker.service
# ist KEINE feste Invariante mehr (Issue #593) — beide Zweige von
# crash_collector_require_docker_service (true/false) werden separat gerendert
# und geprüft, inkl. Regression-Guard gegen eine verwaiste Leerzeile im
# [Unit]-Block, wenn die Zeile weggelassen wird.
# Zusätzlich: die Retention ist über eine Variable konfigurierbar, und die
# prod-Variante (eigene Unit + eigenes Bundle-Dir + eigener Container) rendert
# getrennt von dev — Nachweis der Prod-Instanz ohne Host/Docker (Issue #481).
#
# Kein Host, kein SSH, kein Docker, kein Vault, keine Prod-Aktion.
# Läuft in deploy-check-local.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
play="$here/main.yml"

dev_dir="$(mktemp -d)"
prod_dir="$(mktemp -d)"

echo "== Render mit Defaults (dev, Retention 20) =="
RB_CRASH_RENDER_DIR="$dev_dir" ansible-playbook "$play"

echo "== Retention konfigurierbar (5) =="
RB_CRASH_RENDER_DIR="$(mktemp -d)" ansible-playbook "$play" -e test_retention=5

echo "== Docker-Requires abschaltbar (Issue #593, WSL2 + Docker Desktop) =="
no_docker_dir="$(mktemp -d)"
RB_CRASH_RENDER_DIR="$no_docker_dir" ansible-playbook "$play" -e test_require_docker_service=false
no_docker_unit="${no_docker_dir}/rbmods-crash-collector.service"
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

echo "== Prod-Variante (eigene Unit/Bundle-Dir/Container/Env/Ref) =="
RB_CRASH_RENDER_DIR="$prod_dir" ansible-playbook "$play" \
  -e crash_collector_unit=rbmods-crash-collector-prod \
  -e crash_collector_dir=/opt/rbmods/crashes-prod \
  -e crash_collector_env=prod \
  -e crash_collector_ref=v0.38.0 \
  -e crash_collector_container=riftbreaker-dedicated-prod

# dev und prod müssen sich in Unit-Name-relevanten Env-Werten unterscheiden
# (sonst würden beide Instanzen dasselbe Bundle-Dir beschreiben).
dev_unit="${dev_dir}/rbmods-crash-collector.service"
prod_unit="${prod_dir}/rbmods-crash-collector.service"
grep -q 'RB_CRASH_DIR=/opt/rbmods/crashes"' "$dev_unit"
grep -q 'RB_CRASH_DIR=/opt/rbmods/crashes-prod' "$prod_unit"
grep -q 'RB_CRASH_ENV=dev' "$dev_unit"
grep -q 'RB_CRASH_ENV=prod' "$prod_unit"
grep -q 'RB_CRASH_REF=8131ee0bd0c8' "$dev_unit"
grep -q 'RB_CRASH_REF=v0.38.0' "$prod_unit"
grep -q 'RB_CRASH_CONTAINER=riftbreaker-dedicated-prod' "$prod_unit"
if grep -q 'crashes-prod' "$dev_unit"; then
  echo "FEHLER: dev-Unit rendert das prod-Bundle-Dir (Regression)" >&2
  exit 1
fi
echo "OK: dev- und prod-Instanz rendern getrennt (Unit/Bundle-Dir/Container)."

echo "OK: Unit rendert, Invarianten halten, Retention ist konfigurierbar."
