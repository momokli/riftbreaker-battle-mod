#!/usr/bin/env bash
# Auth-Test der /server/*-Route (Issue #454) — OHNE Spieler, OHNE planet.
#
# Was hier bewiesen wird (genau der Bug aus #454):
#   1. `/server/*` verlangt IM CADDY die Operator-Basic-Auth: ohne Credentials
#      kommt der 401 MIT `WWW-Authenticate: Basic` (Browser-Challenge), nicht
#      der nackte 401 des Agenten.
#   2. Mit den Operator-Credentials kommt der Request durch — und Caddy
#      injiziert `Authorization: Bearer <token>`, den der Browser nie sieht.
#      Der Mock-Agent protokolliert die gesehenen Header; der Test prueft, dass
#      dort exakt der Bearer ankam (und nicht die Basic-Auth des Browsers).
#   3. Falsche Credentials → 401 (kein Durchrutschen).
#   4. Der Agent selbst bleibt fail-closed: Direktzugriff ohne Bearer → 401.
#   5. `/tournament/*` bleibt unveraendert (keine Basic-Auth, Issue #298).
#   6. Der Cockpit-Root `/` bleibt wie bisher hinter der Basic-Auth.
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
CONTAINER="rbmod-454-auth-test-$$"
# Caddy (Binary-Modus) soll nichts ausserhalb von $TMP anfassen.
export XDG_DATA_HOME="$TMP/caddy-data"
export XDG_CONFIG_HOME="$TMP/caddy-config"
mkdir -p "$XDG_DATA_HOME" "$XDG_CONFIG_HOME"

cleanup() {
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  [[ -n "${CADDY_PID:-}" ]] && kill "$CADDY_PID" >/dev/null 2>&1 || true
  [[ -n "${AGENT_PID:-}" ]] && kill "$AGENT_PID" >/dev/null 2>&1 || true
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
AGENT_PORT="$(free_port)"
BRIDGE_PORT="$(free_port)"
TOURNAMENT_PORT="$(free_port)"

echo "== Caddy-Backend: $CADDY_MODE"
echo "== Ports: caddy=$CADDY_PORT agent=$AGENT_PORT bridge=$BRIDGE_PORT tournament=$TOURNAMENT_PORT"

# ---------------------------------------------------------------------------
# 1. Caddyfile aus dem Produktions-Template rendern.
# ---------------------------------------------------------------------------
# Testwerte als extra-vars unter den PRODUKTIONS-Namen: extra-vars gewinnen
# gegen die Rollen-Defaults (vars_files) — das Template wird also genau so
# verdrahtet wie auf planet, nur mit Testports/-token. server_control_enabled=true
# explizit, weil die Rolle die /server/*-Route seit Issue #463 nur bei
# deploytem Agenten rendert — dieser Test prueft genau diese Route.
"$ANSIBLE_PLAYBOOK" "$PLAYBOOK" \
  -e "rift_caddy_port=$CADDY_PORT" \
  -e "rift_caddy_site_root=/tmp" \
  -e "landing_domain=localhost" \
  -e "cockpit_domain=127.0.0.1" \
  -e "server_control_port=$AGENT_PORT" \
  -e "riftbreaker_bridge_port=$BRIDGE_PORT" \
  -e "tournament_port=$TOURNAMENT_PORT" \
  -e "vault_server_control_token=$TOKEN" \
  -e "server_control_enabled=true" \
  -e "test_render_dir=$TMP" >"$TMP/ansible.log" 2>&1 || {
  cat "$TMP/ansible.log" >&2
  fail "Caddyfile liess sich nicht rendern"
}
[[ -s "$TMP/Caddyfile" ]] || fail "gerendertes Caddyfile fehlt"

# ---------------------------------------------------------------------------
# 2. Caddy-Konfiguration validieren (Syntax) — gleiche Caddy-Version wie live.
# ---------------------------------------------------------------------------
if [[ -n "$CADDY_BIN" ]]; then
  "$CADDY_BIN" validate --config "$TMP/Caddyfile" --adapter caddyfile >"$TMP/validate.log" 2>&1 ||
    { cat "$TMP/validate.log" >&2; fail "caddy validate (Binary)"; }
else
  # --network host auch fuer validate: manche Hosts haben keinen nutzbaren
  # docker0-Bridge-Pfad; host-net ist ohnehin die Betriebsart des rift-caddy.
  docker run --rm --network host -v "$TMP/Caddyfile:/etc/caddy/Caddyfile:ro" \
    "$CADDY_IMAGE" caddy validate --config /etc/caddy/Caddyfile >"$TMP/validate.log" 2>&1 ||
    { cat "$TMP/validate.log" >&2; fail "caddy validate (Docker)"; }
fi
grep -qi "valid configuration" "$TMP/validate.log" || fail "caddy validate ohne Bestaetigung"

# ---------------------------------------------------------------------------
# 3. Mock-Agent (Bearer Pflicht) + Caddy (127.0.0.1) starten.
# ---------------------------------------------------------------------------
SEEN="$TMP/seen.jsonl"
python3 "$HERE/mock_agent.py" "$AGENT_PORT" "$TOKEN" "$SEEN" >"$TMP/agent.log" 2>&1 &
AGENT_PID=$!

if [[ -n "$CADDY_BIN" ]]; then
  "$CADDY_BIN" run --config "$TMP/Caddyfile" --adapter caddyfile >"$TMP/caddy.log" 2>&1 &
  CADDY_PID=$!
else
  docker run -d --name "$CONTAINER" --network host \
    -v "$TMP/Caddyfile:/etc/caddy/Caddyfile:ro" \
    "$CADDY_IMAGE" >/dev/null
  docker logs -f "$CONTAINER" >"$TMP/caddy.log" 2>&1 &
  LOGS_PID=$!
fi

# Warten, bis Caddy auf 127.0.0.1:$CADDY_PORT antwortet (irgendein Status).
for _ in $(seq 1 50); do
  code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$CADDY_PORT/server/status" || true)"
  [[ -n "$code" && "$code" != "000" ]] && break
  sleep 0.2
done
[[ "${code:-000}" != "000" ]] || fail "Caddy antwortete nicht auf 127.0.0.1:$CADDY_PORT"

PASSED=0
ok() { echo "  OK   $*"; PASSED=$((PASSED + 1)); }

# ---------------------------------------------------------------------------
# 4. Checks.
# ---------------------------------------------------------------------------
echo "== 1) /server/* ohne Credentials → 401 vom CADDY (Basic-Challenge)"
hdr="$(curl -s -D - -o "$TMP/body1" -w '%{http_code}' "http://127.0.0.1:$CADDY_PORT/server/status" || true)"
code="${hdr: -3}"
[[ "$code" == "401" ]] || fail "erwartet 401 ohne Auth, bekam $code"
echo "$hdr" | grep -qi '^www-authenticate: *basic' ||
  fail "401 ohne WWW-Authenticate: Basic — kommt nicht vom Caddy (Issue #454)"
if grep -qE '"error":[[:space:]]*"unauthorized"' "$TMP/body1"; then
  fail "401 kommt vom AGENTEN (Body {\"error\":\"unauthorized\"}), nicht vom Caddy — /server/* hat keine basic_auth (genau #454)"
fi
ok "/server/status ohne Auth → 401 + WWW-Authenticate: Basic vom Caddy (nicht der Agent-401)"

echo "== 2) /server/* mit Operator-Credentials → 200, Agent sieht den Bearer"
body="$(curl -s -u "$OPERATOR_USER:$OPERATOR_PASS" -w '\n%{http_code}' \
  "http://127.0.0.1:$CADDY_PORT/server/status" || true)"
code="$(tail -n1 <<<"$body")"
[[ "$code" == "200" ]] || fail "erwartet 200 mit Operator-Auth, bekam $code"
grep -q '"state": *"running"' <<<"$body" || fail "Antwort des Agenten fehlt/anders: $body"
ok "/server/status mit Operator-Auth → 200 (Agent-Antwort durchgereicht)"

seen_auth="$(python3 - "$SEEN" <<'PY'
import json, sys
lines = [json.loads(l) for l in open(sys.argv[1], encoding="utf-8") if l.strip()]
print(lines[-1]["authorization"] if lines else "")
PY
)"
[[ "$seen_auth" == "Bearer $TOKEN" ]] ||
  fail "Agent sah '$seen_auth', erwartet wurde 'Bearer <token>' (Caddy injiziert den Bearer NICHT)"
ok "Agent sah exakt 'Bearer <token>' — der Token wird von Caddy injiziert, nie vom Browser"

echo "== 3) /server/* mit FALSCHEM Passwort → 401"
code="$(curl -s -o /dev/null -w '%{http_code}' -u "$OPERATOR_USER:definitiv-falsch" \
  "http://127.0.0.1:$CADDY_PORT/server/status" || true)"
[[ "$code" == "401" ]] || fail "erwartet 401 mit falschem Passwort, bekam $code"
ok "falsche Credentials → 401"

echo "== 4) Agent direkt (ohne Bearer) → 401 (fail-closed)"
code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$AGENT_PORT/server/status" || true)"
[[ "$code" == "401" ]] || fail "Agent ohne Bearer gab $code statt 401"
ok "Direktzugriff ohne Bearer → 401 (der Agent oeffnet sich auch ohne Caddy nicht)"

echo "== 5) /tournament/* bleibt OHNE Basic-Auth (Issue #298)"
# Kein tournament-server im Test: entscheidend ist, dass Caddy hier KEINE
# Basic-Auth verlangt (der Upstream fehlt → 502, aber NICHT 401).
code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$CADDY_PORT/tournament/health" || true)"
[[ "$code" != "401" ]] || fail "/tournament/* verlangt Basic-Auth — #454 darf #298 nicht mitverdrahlen"
ok "/tournament/health → $code (kein 401: Basic-Auth bleibt dort aussen vor)"

echo "== 6) Cockpit-Root / unveraendert hinter der Basic-Auth"
code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$CADDY_PORT/" || true)"
[[ "$code" == "401" ]] || fail "Cockpit-Root ohne Auth gab $code statt 401"
ok "Cockpit-Root / ohne Auth → 401 (unveraendert)"

# Kein Token im gerenderten Caddyfile ausserhalb des Proxy-Blocks: der Browser
# bekommt nur HTML/JS; der Token liegt nur in der Caddy-Konfig auf dem Host.
if grep -q "Bearer $TOKEN" "$TMP/Caddyfile"; then
  ok "Bearer steht nur im Caddyfile (Host), der Browser sendet ihn nie"
else
  fail "Bearer fehlt im gerenderten Caddyfile"
fi

[[ -n "${LOGS_PID:-}" ]] && kill "$LOGS_PID" >/dev/null 2>&1 || true
echo
echo "PASS: $PASSED Checks — /server/* hinter Caddy-Basic-Auth, Bearer via header_up injiziert."
