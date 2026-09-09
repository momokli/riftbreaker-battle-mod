#!/usr/bin/env bash
# ============================================================
# rbmods-probe.sh — Erreichbarkeits-Probe der RIFT-BATTLE-Server
# ------------------------------------------------------------
# - UDP-Check je Endpoint OHNE externe Tools: bash /dev/udp-Trick
#   (timeout 2 bash -c 'echo > /dev/udp/<host>/<port>'; exit 0 = ok)
#   Hinweis: UDP-connect gelingt auch bei geschlossenem Port, wenn der
#   Host antwortet → deshalb Zusatzfelder localListen (ss) + containerUp
#   (docker), damit die Seite LIVE nur bei wirklich laufendem Dienst zeigt.
# - Zusätzlich HTTP(S)-Selbstcheck der Website (curl -sI).
# - Schreibt JSON nach $RB_OUT (Default /srv/rbmods-site/status.json).
#
# Endpoint-Liste: Zeilen  "id|host|port|container"  (Container optional,
# leer = kein Mapping). Optional via Datei übersteuern:
#   RB_ENDPOINTS_FILE=/pfad/zur/liste ./rbmods-probe.sh
#   (Zeilen mit führendem '#' = Kommentar)
# ============================================================
set -u

OUT="${RB_OUT:-/srv/rbmods-site/status.json}"
SITE_URL="${RB_SITE_URL:-https://rift.projectmellon.de}"
UDP_TIMEOUT="${RB_UDP_TIMEOUT:-2}"
HTTP_TIMEOUT="${RB_HTTP_TIMEOUT:-8}"
ENDPOINTS_FILE="${RB_ENDPOINTS_FILE:-/usr/local/etc/rbmods-probe.endpoints}"

# Default-Endpoint-Liste (id|host|port|container)
DEFAULT_ENDPOINTS=(
  "RIFT-MOD|65.21.27.234|6321|riftbreaker-dedicated"
  "RIFT-VANILLA|65.21.27.234|6322|rb-winetest"
)

now_iso(){ date -u +%Y-%m-%dT%H:%M:%SZ; }

# UDP-Erreichbarkeit via /dev/udp (exit 0 = Paket rausgegangen)
udp_ok(){
  local host="$1" port="$2"
  timeout "$UDP_TIMEOUT" bash -c "echo > /dev/udp/${host}/${port}" >/dev/null 2>&1
  [ $? -eq 0 ] && echo true || echo false
}

# Lokaler UDP-Listener auf dem Port vorhanden? (ss; null = Tool fehlt)
local_listen(){
  local port="$1"
  if ! command -v ss >/dev/null 2>&1; then echo null; return; fi
  if ss -ulnH "sport = :${port}" 2>/dev/null | grep -q .; then echo true; else echo false; fi
}

# Docker-Container-Zustand (null = docker nicht verfügbar)
container_up(){
  local name="$1"
  if ! command -v docker >/dev/null 2>&1; then echo null; return; fi
  local st
  st="$(docker ps --filter "name=^/${name}$" --format '{{.Status}}' 2>/dev/null)"
  if [ -z "$st" ]; then echo false
  elif [ "${st#Up}" != "$st" ]; then echo true
  else echo false; fi
}

# Website-Selbstcheck → "up httpCode"
site_check(){
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' -m "$HTTP_TIMEOUT" -I "$SITE_URL" 2>/dev/null)"
  [ -z "$code" ] && code="000"
  case "$code" in
    2*|3*) echo "true $code" ;;
    *)     echo "false $code" ;;
  esac
}

main(){
  local checked_at e_ts site_out site_up site_code
  checked_at="$(now_iso)"
  site_out="$(site_check)"; set -- $site_out; site_up="$1"; site_code="$2"

  # Endpoint-Liste laden: Datei > Default
  local -a E=()
  if [ -r "$ENDPOINTS_FILE" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
      case "$line" in ''|\#*) continue ;; esac
      E+=("$line")
    done < "$ENDPOINTS_FILE"
  else
    E=("${DEFAULT_ENDPOINTS[@]}")
  fi

  local -a parts=() line id host port ctr u l c e_ts
  for line in "${E[@]}"; do
    IFS='|' read -r id host port ctr <<< "$line"
    u="$(udp_ok "$host" "$port")"
    l="$(local_listen "$port")"
    if [ -n "${ctr:-}" ]; then c="$(container_up "$ctr")"; else c="null"; fi
    e_ts="$(now_iso)"
    parts+=("{\"id\":\"${id}\",\"host\":\"${host}\",\"port\":${port},\"udpOk\":${u},\"containerUp\":${c},\"localListen\":${l},\"checkedAt\":\"${e_ts}\"}")
  done

  local json eps_json
  eps_json="$(IFS=,; echo "${parts[*]}")"
  json="{\"checkedAt\":\"${checked_at}\",\"site\":{\"url\":\"${SITE_URL}\",\"up\":${site_up},\"httpCode\":${site_code},\"checkedAt\":\"${checked_at}\"},\"endpoints\":[${eps_json}]}"

  # Atomar schreiben (mktemp + mv), damit die Seite nie halbes JSON liest
  local dir tmp
  dir="$(dirname "$OUT")"
  mkdir -p "$dir" || { echo "rbmods-probe: FEHLER — $dir nicht erstellbar" >&2; exit 1; }
  tmp="$(mktemp "${OUT}.XXXXXX")" || { echo "rbmods-probe: FEHLER — mktemp" >&2; exit 1; }
  printf '%s\n' "$json" > "$tmp"
  chmod 644 "$tmp"
  mv -f "$tmp" "$OUT"

  echo "rbmods-probe: OK — ${#parts[@]} Endpoints, site_up=${site_up} (HTTP ${site_code}), geschrieben nach ${OUT} (${checked_at})"
}

main "$@"
