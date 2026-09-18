#!/usr/bin/env bash
# Hermetischer Render-Selbsttest fuer config-vars.json.j2 (Issue #704).
#
# Prueft die ECHTE Vorlage: rendert sie lokal mit Dummy-Werten und validiert
# mit `python3 -m json.tool`, dass das Ergebnis gueltiges JSON ist -- derselbe
# Pfad wie server_control.py._config_vars() (json.load()). Ohne diesen Test
# haette der #704-Bug (literaler "#"-Kommentarkopf statt Jinja-Kommentar,
# machte die Datei zu ungueltigem JSON) unentdeckt bleiben koennen.
#
# Kein Host, kein SSH, kein Docker, kein Vault, keine Prod-Aktion.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
play="$here/main.yml"

echo "== Render mit Defaults (coop_normal) -> muss gueltiges JSON sein =="
RB_SC_RENDER_DIR="$(mktemp -d)" ansible-playbook "$play"

echo "== Difficulty konfigurierbar (sandbox) -> weiterhin gueltiges JSON =="
RB_SC_RENDER_DIR="$(mktemp -d)" ansible-playbook "$play" -e test_difficulty=sandbox

echo "OK: config-vars.json.j2 rendert in jedem Fall gueltiges JSON."
