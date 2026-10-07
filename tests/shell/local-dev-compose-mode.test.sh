#!/usr/bin/env bash
# ============================================================
# tests/shell/local-dev-compose-mode.test.sh
# ------------------------------------------------------------
# Regressions-Guard fuer Issue #1090: deploy/roles/riftbreaker-server/
# tasks/main.yml rendert docker-compose.yml mit `become: true` (root) --
# faellt der Task-Mode auf "0600" zurueck, koennen scripts/local-dev-start.sh
# / scripts/local-dev-stop.sh (laufen OHNE sudo, siehe deploy/LOCAL_DEV.md)
# die Datei nicht mehr lesen ("permission denied", Live-Befund Matheo).
#
# Statische Assertion statt vollem Rollen-Lauf (der Task sitzt mitten in
# main.yml neben Docker-Image-Build/Netz-Tasks -- eine hermetische
# Ausfuehrung waere unverhaeltnismaessig fuer einen Konfigurationswert).
# Prueft den EXAKTEN Task-Block, nicht nur irgendein "0644" irgendwo in der
# Datei.
# ============================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TASKS_FILE="${REPO_ROOT}/deploy/roles/riftbreaker-server/tasks/main.yml"

FAIL=0
pass() { echo "PASS  $1"; }
fail() {
	echo "FAIL  $1" >&2
	FAIL=1
}

# Den Task-Block "docker-compose.yml rendern" isolieren (bis zur naechsten
# Task, erkannt an einer Zeile, die wieder bei Spalte 0 mit "- name:" beginnt).
block="$(awk '
	/^- name: docker-compose\.yml rendern$/ { capture = 1 }
	capture && /^- name:/ && !/^- name: docker-compose\.yml rendern$/ { exit }
	capture { print }
' "$TASKS_FILE")"

if [ -z "$block" ]; then
	fail "Task 'docker-compose.yml rendern' nicht in main.yml gefunden (umbenannt/entfernt?)"
elif ! echo "$block" | grep -q 'dest: "{{ riftbreaker_deploy_dir }}/docker-compose.yml"'; then
	fail "Task-Block zeigt nicht auf riftbreaker_deploy_dir/docker-compose.yml -- falscher Block isoliert"
else
	pass "Task 'docker-compose.yml rendern' gefunden, dest stimmt"
fi

mode="$(echo "$block" | grep -oP 'mode:\s*"?\K[0-7]+' | head -1)"

if [ "$mode" = "0644" ]; then
	pass "mode ist 0644 -- lesbar fuer local-dev-start.sh/-stop.sh ohne sudo"
elif [ "$mode" = "0600" ]; then
	fail "mode ist wieder 0600 (root-only) -- Regression von Issue #1090"
else
	fail "unerwarteter mode-Wert: '${mode:-<leer>}' (erwartet 0644)"
fi

if [ "$FAIL" -eq 0 ]; then
	echo
	echo "local-dev-compose-mode.test.sh: OK"
	exit 0
fi
exit 1
