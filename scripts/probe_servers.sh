#!/usr/bin/env bash
# ============================================================
# rbmods-probe.sh — Erreichbarkeits-Probe der RIFT-BATTLE-Server
# ------------------------------------------------------------
# - Liveness-Check je Endpoint (Issue #239/#246): prüft, ob der Server
#   IM Container wirklich einen UDP-Socket auf dem Port gebunden hat
#   (docker exec → ss -lun, sonst procfs /proc/net/udp*). Der frühere
#   /dev/udp-Trick bewies nur "Paket raus", nicht "Server antwortet" — er
#   meldete "up", während DedicatedServer.exe im Console-Init hing (kein
#   bind). Genau das hat den Ausfall #239 maskiert (#246).
#   Feld `udpOk` heißt daher jetzt "gebundener Listener", nicht mehr
#   "UDP-connect ok".
# - Zusatzfelder localListen (ss, Host) + containerUp (docker) bleiben als
#   Kontext erhalten.
# - Zusätzlich HTTP(S)-Selbstcheck der Website (curl -sI).
# - Schreibt JSON nach $RB_OUT (Default /srv/rbmods-site/status.json).
#
# Endpoint-Liste: Zeilen  "id|host|port|container|manifest_dir"
# (Container/Manifest-Verzeichnis optional, leer = kein Mapping bzw. keine
# Versionsermittlung). Optional via Datei übersteuern:
#   RB_ENDPOINTS_FILE=/pfad/zur/liste ./rbmods-probe.sh
#   (Zeilen mit führendem '#' = Kommentar)
# ============================================================
set -u

OUT="${RB_OUT:-/srv/rbmods-site/status.json}"
SITE_URL="${RB_SITE_URL:-https://rift.projectmellon.de}"
UDP_TIMEOUT="${RB_UDP_TIMEOUT:-2}"
HTTP_TIMEOUT="${RB_HTTP_TIMEOUT:-8}"
ENDPOINTS_FILE="${RB_ENDPOINTS_FILE:-/usr/local/etc/rbmods-probe.endpoints}"

# Default-Endpoint-Liste (id|host|port|container|manifest_dir)
DEFAULT_ENDPOINTS=(
  "RIFT-MOD|65.21.27.234|6321|riftbreaker-dedicated|/srv/rbgame/mods/rbbattle"
)

now_iso(){ date -u +%Y-%m-%dT%H:%M:%SZ; }

# Ehrlicher Liveness-Check (Issue #239/#246): true nur, wenn der Dienst IM
# Container wirklich einen UDP-Socket auf dem Port gebunden hat.
#
# Begründung: das Repo definiert kein Query-/Join-Protokoll (kein A2S o. ä.),
# das man ohne Client sprechen könnte — der /dev/udp-Trick bewies nur, dass
# ein Paket rausgeht. Verlässliches, host-lokales Signal ist der gebundene
# Socket im Server-Container. Kein Listener → false.
# Rückgabe: true | false | null (null = docker/Container nicht verfügbar).
udp_bound(){
  local ctr="$1" port="$2" out hex
  if ! command -v docker >/dev/null 2>&1; then echo null; return; fi
  if [ -z "$ctr" ]; then echo false; return; fi

  # 1) ss im Container (falls vorhanden) — präzise Port-Spalte.
  if out="$(timeout "$UDP_TIMEOUT" docker exec "$ctr" ss -lun 2>/dev/null)"; then
    if [ -n "$out" ]; then
      if printf '%s\n' "$out" | grep -qE "[:.]${port}[[:space:]]"; then echo true
      else echo false; fi
      return
    fi
  fi

  # 2) Fallback procfs (immer vorhanden): Port als Hex in local_address.
  hex="$(printf '%04x' "$port" 2>/dev/null || true)"
  if [ -z "$hex" ]; then echo false; return; fi
  if timeout "$UDP_TIMEOUT" docker exec "$ctr" cat /proc/net/udp /proc/net/udp6 2>/dev/null \
       | awk '{print $2}' | tr '[:upper:]' '[:lower:]' | grep -q ":${hex}$"; then
    echo true
  else
    echo false
  fi
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

# Mod-Version aus dem Manifest-Verzeichnis lesen (leer = nicht verfügbar).
# Die Dedicated-Server-Manifeste tragen die Zeile: version "X.Y.Z"
mod_version(){
  local dir="$1" out=""
  if [ -z "$dir" ] || [ ! -d "$dir" ]; then
    echo ""
    return 0
  fi
  out="$(grep -rhoE --include='*.manifest' '^[[:space:]]*version[[:space:]]*"[^"]+"' "$dir" 2>/dev/null | head -1)"
  out="${out#*\"}"; out="${out%\"*}"
  echo "$out"
}

# Website-Selbstcheck → "up httpCode"
site_check(){
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' -m "$HTTP_TIMEOUT" -I "$SITE_URL" 2>/dev/null)"
  # curl liefert bei Fehler "000" — als Zahl ``000`` ist das ungültiges JSON.
  # Auf 0 normalisieren, damit status.json immer valides JSON bleibt.
  case "$code" in ''|000|*[!0-9]*) code=0 ;; esac
  case "$code" in
    2*|3*) echo "true $code" ;;
    *)     echo "false $code" ;;
  esac
}

main(){
  local checked_at e_ts site_out site_up site_code
  checked_at="$(now_iso)"
  site_out="$(site_check)"; site_up="${site_out%% *}"; site_code="${site_out#* }"

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

  local -a parts=() line id host port ctr mdir u l c e_ts v
  for line in "${E[@]}"; do
    IFS='|' read -r id host port ctr mdir <<< "$line"
    u="$(udp_bound "${ctr:-}" "$port")"
    l="$(local_listen "$port")"
    if [ -n "${ctr:-}" ]; then c="$(container_up "$ctr")"; else c="null"; fi
    v="$(mod_version "${mdir:-}")"
    e_ts="$(now_iso)"
    parts+=("{\"id\":\"${id}\",\"host\":\"${host}\",\"port\":${port},\"udpOk\":${u},\"containerUp\":${c},\"localListen\":${l},\"version\":\"${v}\",\"checkedAt\":\"${e_ts}\"}")
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
