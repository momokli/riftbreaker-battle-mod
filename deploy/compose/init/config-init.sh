#!/bin/sh
# config-init (Issue #1093): rendert config.cfg aus dem Template (envsubst).
# Ersetzt die frühere Jinja-Template-Rolle riftbreaker-server/config.cfg.j2.
set -eu

tpl="${RBB_CONFIG_TEMPLATE:-/templates/config.cfg.template}"
out="${RBB_CONFIG_OUT:-/config/config.cfg}"

mkdir -p "$(dirname "$out")"
envsubst < "$tpl" > "$out"
chmod 0644 "$out"
echo "config-init: $out geschrieben"
