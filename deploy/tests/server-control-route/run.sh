#!/usr/bin/env bash
# Hermetischer Render-Selbsttest der /server/*-Route im Cockpit-Caddyfile
# (Issue #463).
#
# Prüft die ECHTE Vorlage deploy/roles/website/templates/rift-caddy.Caddyfile.j2
# in den zwei Zuständen (prod/fail-safe, Issue #463/#1034):
#   Fall A prod (server_control_enabled=true, wie deploy-prod.yml + prod-vars)
#                -> /server/* MUSS da sein und auf 127.0.0.1:8093 zeigen,
#                   NIE auf 8092 (dev ist entfallen).
#   Fall B fail-safe (server_control_enabled=false)           -> /server/* darf
#                NICHT da sein, Cockpit-Root + /tournament/* bleiben.
#
# Kein Host, kein SSH, kein Docker, kein Vault, keine Prod-Aktion.
# Läuft in deploy-check-local.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
play="$here/main.yml"
prod_vars="$here/../../prod-vars.yml"
# Issue #535: prod-A setzt contract_basic_auth_hash als Play-Var aus dem Vault
# (deploy-prod.yml). Der Test emuliert das mit einem vom dev/legacy-Hash
# abweichenden Wert: der gerenderte Operator-Block MUSS diesen tragen und der
# geteilte Legacy-Hash darf NIRGENDS mehr vorkommen (Distinctness).
PROD_OPERATOR_HASH='$2a$14$prodoperatorhash000000000000000000000000000000000000'

echo "== Fall A: prod (server_control_enabled=true) -> /server/* auf 8093, nie 8092 =="
RB_ROUTE_RENDER_DIR="$(mktemp -d)" ansible-playbook "$play" \
  -e expect_route=true -e server_control_enabled=true -e @"$prod_vars" \
  -e contract_basic_auth_hash="$PROD_OPERATOR_HASH"

echo "== Fall B: fail-safe (server_control_enabled=false) -> /server/* fehlt =="
RB_ROUTE_RENDER_DIR="$(mktemp -d)" ansible-playbook "$play" \
  -e expect_route=false -e server_control_enabled=false

echo "OK: prod 8093, ohne Agent keine Route — kein Cross-Env."
