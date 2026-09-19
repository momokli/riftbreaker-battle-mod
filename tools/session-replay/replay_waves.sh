#!/usr/bin/env bash
# ============================================================
# tools/session-replay/replay_waves.sh
# ------------------------------------------------------------
# Spielt eine Wellen-Sequenz gegen ein laufendes Cockpit nach (Issue #784).
#
# Hintergrund: Matheo hat auf Prod eine Runde gespielt (grob ab ~12 Uhr,
# eskaliert bis ~Wave 5). Ziel war, diese Sends automatisiert erneut
# auszuloesen. WICHTIGER CAVEAT: dies ist KEINE aus echten Server-Logs
# rekonstruierte Session -- der Zugriff dafuer fehlte (kein SSH auf `planet`,
# das Cockpit liegt komplett hinter Operator-Basic-Auth, und die einzige
# Quelle fuer "was wurde wann gesendet" -- die Session-Recorder-JSONL unter
# /srv/rift-<env>/sessions/ -- ist nur per Host-Zugriff erreichbar, nicht ueber
# HTTP). Level-Anzahl (Default 5) und --interval sind FREI GEWAEHLTE
# Platzhalter aus Matheos grober Erinnerung, KEINE gemessenen Werte (gleiche
# Konvention wie tools/wave-scheduler/wave_scheduler.py).
#
# Architektur-Grund, warum genau dieser Endpunkt: POST /queue_send (Attack-
# Cycle, respektiert Carbonium-Kosten) hat KEINEN oeffentlichen Endpunkt -- nur
# der send-tailer-Sidecar im selben Compose-Netz erreicht ihn (siehe
# deploy/attack-cycle/README.md). Von aussen (dieses Skript) ist nur
# POST /activate_mission_flow ueber die Bridge erreichbar -- derselbe Kanal wie
# die Cockpit-SEND-MENU-Buttons. Das feuert SOFORT und KOSTENLOS, ohne die
# Attack-Cycle-Economy (kein try_spend) -- kein 1:1-Nachbau eines echten Kaufs,
# sondern die direkteste verfuegbare Annaeherung.
#
# Aufruf:
#   COCKPIT_URL=https://cockpit.rift.projectmellon.de \
#   COCKPIT_USER=operator COCKPIT_PASS=*** \
#     tools/session-replay/replay_waves.sh --max-level 5 --interval 60
#
#   --dry-run  gibt nur aus, was gepostet wuerde (kein echter curl-Call).
#
# Rein Bash + curl, keine weiteren Abhaengigkeiten.
# ============================================================
set -euo pipefail

# Logic-Pfad je Level -- Spiegel von deploy/attack-cycle/attack_cycle.py
# WAVE_LOGIC (dieselben Presets wie das Cockpit-SEND-MENU): durchgehend
# _id_1 = raw spawn (sofort), Level 9 teilt sich den Pool mit Level 8 (#658).
wave_logic() {
	case "$1" in
		1) echo "logic/missions/survival/attack_level_1_id_1.logic" ;;
		2) echo "logic/missions/survival/attack_level_2_id_1.logic" ;;
		3) echo "logic/missions/survival/attack_level_3_id_1.logic" ;;
		4) echo "logic/missions/survival/attack_level_4_id_1.logic" ;;
		5) echo "logic/missions/survival/attack_level_5_id_1.logic" ;;
		6) echo "logic/missions/survival/attack_level_6_id_1.logic" ;;
		7) echo "logic/missions/survival/attack_level_7_id_1.logic" ;;
		8) echo "logic/missions/survival/attack_level_8_id_1.logic" ;;
		9) echo "logic/missions/survival/attack_level_8_id_1.logic" ;;
		*)
			echo "unbekanntes Level: $1" >&2
			return 1
			;;
	esac
}

usage() {
	cat <<'EOF'
Usage: replay_waves.sh [--start-level N] [--max-level N] [--interval SECONDS] [--dry-run]

Env (Pflicht ausser bei --dry-run):
  COCKPIT_URL   Basis-URL des Cockpits, ohne Pfad (z.B. https://cockpit.rift.projectmellon.de)
  COCKPIT_USER  Operator-Basic-Auth-User (Default: operator)
  COCKPIT_PASS  Operator-Basic-Auth-Passwort

Optionen:
  --start-level N    Erstes Level (Default 1)
  --max-level N      Letztes Level, inklusive (Default 5 -- Matheos Runde ging bis ~Wave 5)
  --interval SECONDS Sekunden zwischen den Sends (Default 60 -- FREI GEWAEHLTER Platzhalter,
                      keine gemessene Zeit, siehe Docstring)
  --dry-run          Nur ausgeben, was gepostet wuerde -- kein echter curl-Call, keine Env noetig
  -h, --help         Diese Hilfe
EOF
}

start_level=1
max_level=5
interval=60
dry_run=0

while [ $# -gt 0 ]; do
	case "$1" in
		--start-level)
			start_level="$2"
			shift 2
			;;
		--max-level)
			max_level="$2"
			shift 2
			;;
		--interval)
			interval="$2"
			shift 2
			;;
		--dry-run)
			dry_run=1
			shift
			;;
		-h | --help)
			usage
			exit 0
			;;
		*)
			echo "Unbekannte Option: $1" >&2
			usage >&2
			exit 1
			;;
	esac
done

if ! [[ "$start_level" =~ ^[0-9]+$ ]] || ! [[ "$max_level" =~ ^[0-9]+$ ]]; then
	echo "Fehler: --start-level/--max-level muessen Zahlen sein" >&2
	exit 1
fi
if [ "$start_level" -lt 1 ] || [ "$max_level" -gt 9 ] || [ "$start_level" -gt "$max_level" ]; then
	echo "Fehler: 1 <= --start-level <= --max-level <= 9 muss gelten" >&2
	exit 1
fi
if ! [[ "$interval" =~ ^[0-9]+$ ]]; then
	echo "Fehler: --interval muss eine Zahl (Sekunden) sein" >&2
	exit 1
fi

if [ "$dry_run" -eq 0 ]; then
	: "${COCKPIT_URL:?COCKPIT_URL ist nicht gesetzt (z.B. https://cockpit.rift.projectmellon.de)}"
	: "${COCKPIT_PASS:?COCKPIT_PASS ist nicht gesetzt (Operator-Basic-Auth-Passwort)}"
fi
cockpit_user="${COCKPIT_USER:-operator}"

fire_wave() {
	level="$1"
	logic="$(wave_logic "$level")"
	payload=$(printf '{"logic":"%s","mode":"default"}' "$logic")

	if [ "$dry_run" -eq 1 ]; then
		echo "[dry-run] wave${level} -> POST ${COCKPIT_URL:-<COCKPIT_URL>}/activate_mission_flow ${payload}"
		return 0
	fi

	echo "wave${level} -> POST ${COCKPIT_URL}/activate_mission_flow"
	status=$(curl -sS -o /tmp/replay_waves_response.$$ -w '%{http_code}' \
		-u "${cockpit_user}:${COCKPIT_PASS}" \
		-X POST "${COCKPIT_URL%/}/activate_mission_flow" \
		-H 'Content-Type: application/json' \
		-d "$payload")
	body="$(cat /tmp/replay_waves_response.$$ 2>/dev/null || true)"
	rm -f /tmp/replay_waves_response.$$
	echo "  HTTP ${status} ${body}"
	if [ "$status" -lt 200 ] || [ "$status" -ge 300 ]; then
		echo "  WARNUNG: wave${level} nicht erfolgreich (HTTP ${status})" >&2
	fi
}

level=$start_level
first=1
while [ "$level" -le "$max_level" ]; do
	if [ "$first" -eq 0 ]; then
		echo "warte ${interval}s ..."
		if [ "$dry_run" -eq 0 ]; then
			sleep "$interval"
		fi
	fi
	first=0
	fire_wave "$level"
	level=$((level + 1))
done
