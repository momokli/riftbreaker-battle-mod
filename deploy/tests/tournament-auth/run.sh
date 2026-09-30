#!/usr/bin/env bash
# Auth-Test der /tournament/*-Route (Issue #298) — OHNE Spieler, OHNE planet.
#
# Was hier bewiesen wird (die DoD aus #298):
#   1. MUTIERENDE /tournament/*-Pfade (z. B. POST /tournament/wave) verlangen IM
#      CADDY die Operator-Basic-Auth: ohne Credentials kommt der 401 MIT
#      `WWW-Authenticate: Basic` (Browser-Challenge), nicht der nackte 401 des
#      Referees.
#   2. Mit den Operator-Credentials kommt der Request durch — und Caddy
#      injiziert `Authorization: Bearer <tournament_token>`, den der Browser nie
#      sieht. Der Mock-Referee protokolliert die gesehenen Header; der Test
#      prueft, dass dort exakt der Bearer ankam (und nicht die Basic-Auth).
#   3. LESEPFADE (GET /tournament/state, /health, …) + Web-UI bleiben OHNE
#      Basic-Auth frei (Poll/Landing unveraendert).
#   4. Fail-closed: bei LEEREM Token fehlt der `header_up` (kein literales
#      `Bearer `) — mit Operator-Creds liefert der Referee dann 401.
#
# Gerendert wird das PRODUKTIONS-Template (deploy/roles/website/templates/
# rift-caddy.Caddyfile.j2) mit Testports/-token; Caddy laeuft wie auf planet als
# host-net-Container auf 127.0.0.1.
#
# Laeuft hermetisch auf localhost (python3 + ansible-playbook; Caddy entweder
# per Docker wie auf planet ODER — wenn kein Docker-Daemon erreichbar ist —
# ueber ein lokales `caddy`-Binary, gleicher Caddyfile-Adapter).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# ansible.cfg (roles_path/inventory) wird relativ zum CWD gefunden — hier
# fixieren, damit der Test unabhaengig vom Aufrufort dasselbe rendert.
cd "$HERE/../../.."

PLAYBOOK="${PLAYBOOK:-$HERE/render.yml}"
ANSIBLE_PLAYBOOK="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
CADDY_IMAGE="${CADDY_IMAGE:-caddy:2}"
CADDY_BIN="${CADDY_BIN:-}"
OPERATOR_USER="operator"
# bcrypt-Hash im Rollen-Default ist der von "zukka" (kein Secret).
OPERATOR_PASS="zukka"
TOKEN="test-bearer-$(python3 -c 'import secrets; print(secrets.token_hex(12))')"

# Caddy-Backend waehlen: wie auf planet bevorzugt im Container (caddy:2,
# network host). Ist kein Docker-Daemon erreichbar, auf ein lokales
# `caddy`-Binary zurueckfallen (lokale Verifikation, gleicher Adapter); mit
# CADDY_BIN laesst sich das Binary explizit vorgeben.
if [[ -z "$CADDY_BIN" ]] && ! docker info >/dev/null 2>&1 && command -v caddy >/dev/null 2>&1; then
  CADDY_BIN="$(command -v caddy)"
fi
if [[ -n "$CADDY_BIN" ]]; then
  CADDY_MODE="caddy-Binary: $CADDY_BIN ($("$CADDY_BIN" version 2>/dev/null || echo '?'))"
elif docker info >/dev/null 2>&1; then
  CADDY_MODE="docker-Container: $CADDY_IMAGE"
else
  echo "FAIL: kein nutzbarer Docker-Daemon und kein lokales caddy-Binary gefunden." >&2
  echo "      Entweder Docker starten oder CADDY_BIN=/pfad/zu/caddy setzen." >&2
  exit 1
fi

TMP="$(mktemp -d)"
CONTAINER="rbmod-298-auth-test-$$"
CONTAINER_EMPTY="rbmod-298-auth-empty-$$"
# Caddy (Binary-Modus) soll nichts ausserhalb von $TMP anfassen.
export XDG_DATA_HOME="$TMP/caddy-data"
export XDG_CONFIG_HOME="$TMP/caddy-config"
mkdir -p "$XDG_DATA_HOME" "$XDG_CONFIG_HOME"

cleanup() {
  docker rm -f "$CONTAINER" "$CONTAINER_EMPTY" >/dev/null 2>&1 || true
  [[ -n "${CADDY_PID:-}" ]] && kill "$CADDY_PID" >/dev/null 2>&1 || true
  [[ -n "${CADDY_EMPTY_PID:-}" ]] && kill "$CADDY_EMPTY_PID" >/dev/null 2>&1 || true
  [[ -n "${BACKEND_PID:-}" ]] && kill "$BACKEND_PID" >/dev/null 2>&1 || true
  rm -rf "$TMP"
}
trap cleanup EXIT

fail() {
  echo "FAIL: $*" >&2
  [[ -f "$TMP/caddy.log" ]] && { echo "--- caddy log ---" >&2; tail -40 "$TMP/caddy.log" >&2; }
  exit 1
}

free_port() {
  python3 - <<'PY'
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
PY
}

CADDY_PORT="$(free_port)"
CADDY_EMPTY_PORT="$(free_port)"
BACKEND_PORT="$(free_port)"

echo "== Caddy-Backend: $CADDY_MODE"
echo "== Ports: caddy=$CADDY_PORT caddy-empty=$CADDY_EMPTY_PORT backend=$BACKEND_PORT"

render_caddyfile() {
  # $1 = Zieldatei, $2 = port, $3 = token
  "$ANSIBLE_PLAYBOOK" "$PLAYBOOK" \
    -e "rift_caddy_port=$2" \
    -e "rift_caddy_site_root=/tmp" \
    -e "landing_domain=localhost" \
    -e "cockpit_domain=127.0.0.1" \
    -e "riftbreaker_bridge_port=$(free_port)" \
    -e "tournament_port=$BACKEND_PORT" \
    -e "tournament_token=$3" \
    -e "test_render_dir=$TMP" \
    -e "test_render_name=$1" >"$TMP/ansible-$1.log" 2>&1 || {
    cat "$TMP/ansible-$1.log" >&2
    fail "Caddyfile ($1) liess sich nicht rendern"
  }
  [[ -s "$TMP/$1" ]] || fail "gerendertes Caddyfile $1 fehlt"
}

# ---------------------------------------------------------------------------
# 1. Caddyfiles aus dem Produktions-Template rendern (mit + ohne Token).
# ---------------------------------------------------------------------------
render_caddyfile "Caddyfile" "$CADDY_PORT" "$TOKEN"
render_caddyfile "Caddyfile-empty-token" "$CADDY_EMPTY_PORT" ""

# Fail-closed-Render: kein header_up, aber basic_auth bleibt.
write_block="$(python3 - "$TMP/Caddyfile-empty-token" <<'PY'
import sys
text = open(sys.argv[1], encoding="utf-8").read()
print(text.split("@write path", 1)[1].split("handle {", 1)[0])
PY
)"
grep -q "header_up" <<<"$write_block" && fail "leerer Token rendert dennoch header_up (fail-closed verletzt)"
grep -q "basic_auth" <<<"$write_block" || fail "leerer Token verliert die basic_auth (fail-closed verletzt)"
grep -q "Bearer " <<<"$write_block" && fail "literales 'Bearer ' trotz leerem Token gerendert"
echo "  OK   leerer Token: kein header_up, basic_auth bleibt (fail-closed im Render)"

# ---------------------------------------------------------------------------
# 2. Caddy-Konfiguration validieren (Syntax) — gleiche Caddy-Version wie live.
# ---------------------------------------------------------------------------
validate() {
  if [[ -n "$CADDY_BIN" ]]; then
    "$CADDY_BIN" validate --config "$TMP/$1" --adapter caddyfile >"$TMP/validate-$1.log" 2>&1 ||
      { cat "$TMP/validate-$1.log" >&2; fail "caddy validate ($1)"; }
  else
    docker run --rm --network host -v "$TMP/$1:/etc/caddy/Caddyfile:ro" \
      "$CADDY_IMAGE" caddy validate --config /etc/caddy/Caddyfile >"$TMP/validate-$1.log" 2>&1 ||
      { cat "$TMP/validate-$1.log" >&2; fail "caddy validate (Docker, $1)"; }
  fi
  grep -qi "valid configuration" "$TMP/validate-$1.log" || fail "caddy validate ($1) ohne Bestaetigung"
}
validate "Caddyfile"
validate "Caddyfile-empty-token"

# ---------------------------------------------------------------------------
# 3. Mock-Referee (Bearer-Pflicht auf Schreibpfaden) + Caddy (127.0.0.1).
# ---------------------------------------------------------------------------
SEEN="$TMP/seen.jsonl"
python3 "$HERE/mock_backend.py" "$BACKEND_PORT" "$TOKEN" "$SEEN" >"$TMP/backend.log" 2>&1 &
BACKEND_PID=$!

if [[ -n "$CADDY_BIN" ]]; then
  "$CADDY_BIN" run --config "$TMP/Caddyfile" --adapter caddyfile >"$TMP/caddy.log" 2>&1 &
  CADDY_PID=$!
else
  docker run -d --name "$CONTAINER" --network host \
    -v "$TMP/Caddyfile:/etc/caddy/Caddyfile:ro" "$CADDY_IMAGE" >/dev/null
  docker logs -f "$CONTAINER" >"$TMP/caddy.log" 2>&1 &
fi

# Warten, bis Caddy auf 127.0.0.1:$CADDY_PORT antwortet (irgendein Status).
for _ in $(seq 1 50); do
  code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$CADDY_PORT/tournament/health" || true)"
  [[ -n "$code" && "$code" != "000" ]] && break
  sleep 0.2
done
[[ "${code:-000}" != "000" ]] || fail "Caddy antwortete nicht auf 127.0.0.1:$CADDY_PORT"

PASSED=0
ok() { echo "  OK   $*"; PASSED=$((PASSED + 1)); }

# ---------------------------------------------------------------------------
# 4. Checks.
# ---------------------------------------------------------------------------
echo "== 1) POST /tournament/wave ohne Credentials → 401 vom CADDY (Basic-Challenge)"
hdr="$(curl -s -D - -o "$TMP/body1" -w '%{http_code}' -X POST \
  "http://127.0.0.1:$CADDY_PORT/tournament/wave" || true)"
code="${hdr: -3}"
[[ "$code" == "401" ]] || fail "erwartet 401 ohne Auth, bekam $code"
grep -qi '^www-authenticate: *basic' <<<"$hdr" ||
  fail "401 ohne WWW-Authenticate: Basic — kommt nicht vom Caddy (Issue #298)"
if grep -qE '"error":[[:space:]]*"unauthorized"' "$TMP/body1"; then
  fail "401 kommt vom BACKEND, nicht vom Caddy — /tournament/wave hat keine basic_auth (genau #298)"
fi
ok "POST /tournament/wave ohne Auth → 401 + WWW-Authenticate: Basic vom Caddy"

echo "== 2) POST /tournament/wave mit Operator-Credentials → 200, Backend sieht den Bearer"
body="$(curl -s -u "$OPERATOR_USER:$OPERATOR_PASS" -w '\n%{http_code}' -X POST \
  "http://127.0.0.1:$CADDY_PORT/tournament/wave" || true)"
code="$(tail -n1 <<<"$body")"
[[ "$code" == "200" ]] || fail "erwartet 200 mit Operator-Auth, bekam $code"
grep -q '"ok": *true' <<<"$body" || fail "Antwort des Backends fehlt/anders: $body"
seen_auth="$(python3 - "$SEEN" <<'PY'
import json, sys
lines = [json.loads(l) for l in open(sys.argv[1], encoding="utf-8") if l.strip()]
print(lines[-1]["authorization"] if lines else "")
PY
)"
[[ "$seen_auth" == "Bearer $TOKEN" ]] ||
  fail "Backend sah '$seen_auth', erwartet 'Bearer <token>' (Caddy injiziert den Bearer NICHT)"
ok "Backend sah exakt 'Bearer <token>' — der Token wird von Caddy injiziert, nie vom Browser"

echo "== 3) POST /tournament/wave mit FALSCHEM Passwort → 401"
code="$(curl -s -o /dev/null -w '%{http_code}' -u "$OPERATOR_USER:definitiv-falsch" -X POST \
  "http://127.0.0.1:$CADDY_PORT/tournament/wave" || true)"
[[ "$code" == "401" ]] || fail "erwartet 401 mit falschem Passwort, bekam $code"
ok "falsche Credentials → 401"

echo "== 4) GET /tournament/state ohne Auth → 200 (Lesepfad frei)"
body="$(curl -s -w '\n%{http_code}' "http://127.0.0.1:$CADDY_PORT/tournament/state" || true)"
code="$(tail -n1 <<<"$body")"
[[ "$code" == "200" ]] || fail "Lesepfad /tournament/state ohne Auth gab $code statt 200"
grep -q '"ok": *true' <<<"$body" || fail "Backend-Antwort fehlt/anders: $body"
ok "GET /tournament/state ohne Auth → 200 (Lesepfade bleiben frei)"

echo "== 5) Web-UI/Health frei, kein Basic-Auth"
for path in /tournament/health "/tournament/"; do
  code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$CADDY_PORT$path" || true)"
  [[ "$code" != "401" ]] || fail "$path verlangt Basic-Auth — Lesepfade/UI muessen frei bleiben"
done
ok "/tournament/health + /tournament/ ohne Auth → kein 401 (UI frei)"

echo "== 6) Fail-closed: leerer Token → Schreibpfad liefert Backend-401 (kein Bearer)"
# Zweite Caddy-Instanz mit dem leeren Caddyfile.
if [[ -n "$CADDY_BIN" ]]; then
  "$CADDY_BIN" run --config "$TMP/Caddyfile-empty-token" --adapter caddyfile >"$TMP/caddy-empty.log" 2>&1 &
  CADDY_EMPTY_PID=$!
else
  docker run -d --name "$CONTAINER_EMPTY" --network host \
    -v "$TMP/Caddyfile-empty-token:/etc/caddy/Caddyfile:ro" "$CADDY_IMAGE" >/dev/null
fi
for _ in $(seq 1 50); do
  code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$CADDY_EMPTY_PORT/tournament/state" || true)"
  [[ -n "$code" && "$code" != "000" ]] && break
  sleep 0.2
done
[[ "${code:-000}" != "000" ]] || fail "Caddy (leerer Token) antwortete nicht"
code="$(curl -s -o /dev/null -w '%{http_code}' -u "$OPERATOR_USER:$OPERATOR_PASS" -X POST \
  "http://127.0.0.1:$CADDY_EMPTY_PORT/tournament/wave" || true)"
[[ "$code" == "401" ]] ||
  fail "leerer Token: Schreibpfad mit Creds gab $code statt 401 (fail-closed verletzt)"
ok "leerer Token: Schreibpfad kommt nicht durch (Backend-401, kein injizierter Bearer)"

# Kein Token im gerenderten Caddyfile ausserhalb des Proxy-Blocks: der Browser
# bekommt nur HTML/JS; der Token liegt nur in der Caddy-Konfig auf dem Host.
if grep -q "Bearer $TOKEN" "$TMP/Caddyfile"; then
  ok "Bearer steht nur im Caddyfile (Host), der Browser sendet ihn nie"
else
  fail "Bearer fehlt im gerenderten Caddyfile"
fi

echo
echo "PASS: $PASSED Checks — schreibende /tournament/*-Pfade hinter Caddy-Basic-Auth,"
echo "      Bearer via header_up injiziert; Lesepfade + Web-UI frei; leerer Token fail-closed."
