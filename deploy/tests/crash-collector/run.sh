#!/usr/bin/env bash
# Hermetischer Render-Selbsttest der Rolle crash-collector (Issue #462).
#
# Prüft die ECHTE systemd-Unit-Vorlage (kein Nachbau): rendert sie lokal mit
# Dummy-Werten und asserted die Invarianten (ExecStart, RB_CRASH_*-Env,
# Restart=always, Type=simple, Requires=docker.service). Zusätzlich: die
# Retention ist über eine Variable konfigurierbar.
#
# Kein Host, kein SSH, kein Docker, kein Vault, keine Prod-Aktion.
# Läuft in deploy-check-local.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
play="$here/main.yml"

echo "== Render mit Defaults (Retention 20) =="
RB_CRASH_RENDER_DIR="$(mktemp -d)" ansible-playbook "$play"

echo "== Retention konfigurierbar (5) =="
RB_CRASH_RENDER_DIR="$(mktemp -d)" ansible-playbook "$play" -e test_retention=5

echo "OK: Unit rendert, Invarianten halten, Retention ist konfigurierbar."
