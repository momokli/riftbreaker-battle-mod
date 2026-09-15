#!/usr/bin/env bash
# Hermetischer Render-Selbsttest der /server/*-Route im Cockpit-Caddyfile
# (Issue #463).
#
# Prüft die ECHTE Vorlage deploy/roles/website/templates/rift-caddy.Caddyfile.j2
# in den drei Zuständen (dev/prod/fail-safe, Issue #498):
#   Fall A dev  (server_control_enabled=true, wie site.yml)   -> /server/* MUSS
#                da sein und auf 127.0.0.1:8092 zeigen.
#   Fall B prod (server_control_enabled=true, wie deploy-prod.yml + prod-vars)
#                -> /server/* MUSS da sein und auf 127.0.0.1:8093 zeigen,
#                   NIE auf 8092.
#   Fall C fail-safe (server_control_enabled=false)           -> /server/* darf
#                NICHT da sein, Cockpit-Root + /tournament/* bleiben.
#
# Kein Host, kein SSH, kein Docker, kein Vault, keine Prod-Aktion.
# Läuft in deploy-check-local.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
play="$here/main.yml"
prod_vars="$here/../../prod-vars.yml"

echo "== Fall A: dev (server_control_enabled=true) -> /server/* MUSS gerendert sein =="
RB_ROUTE_RENDER_DIR="$(mktemp -d)" ansible-playbook "$play" \
  -e expect_route=true -e server_control_enabled=true

echo "== Fall B: prod (server_control_enabled=true) -> /server/* auf 8093, nie 8092 =="
RB_ROUTE_RENDER_DIR="$(mktemp -d)" ansible-playbook "$play" \
  -e expect_route=true -e server_control_enabled=true -e @"$prod_vars"

echo "== Fall C: fail-safe (server_control_enabled=false) -> /server/* fehlt =="
RB_ROUTE_RENDER_DIR="$(mktemp -d)" ansible-playbook "$play" \
  -e expect_route=false -e server_control_enabled=false

echo "OK: dev 8092, prod 8093, ohne Agent keine Route — kein Cross-Env."
