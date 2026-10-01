#!/usr/bin/env bash
# Hermetischer Regressionstest der bewusst-oeffentlichen Portmenge (Issue #536, US6).
#
# Rendert die ECHTEN Compose-Templates (prod-A/prod-B/gns-relay) und asserted:
#   * Game-Publish OHNE Host-IP (0.0.0.0) fuer 6322 (A) und 6325 (B),
#   * Bridge + Attack-Cycle-Control explizit 127.0.0.1,
#   * gns-relay nur `network_mode: host` + `gns_relay_port` (kein `ports:`),
#   * Menge der oeffentlichen Host-Publishes == {6321, 6322, 6325},
#   * Negativ-Probe: Loopback-Prefix am Game-Port -> Erkennung failt.
#
# Lokal, kein Host, kein Vault, keine Prod-Aktion. Laeuft in deploy-check-local.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
play="$here/main.yml"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

fail() { echo "::error::$1"; exit 1; }

# render <outdir> [extra ansible args...]
render() {
  local out="$1"; shift
  mkdir -p "$out"
  RBBATTLE_RENDER_DIR="$out" ansible-playbook "$play" "$@" >/dev/null
  [ -s "$out/riftbreaker-server-a.yml" ] || fail "prod-A Compose wurde nicht gerendert."
  [ -s "$out/riftbreaker-server-b.yml" ] || fail "prod-B Compose wurde nicht gerendert."
  [ -s "$out/gns-relay.yml" ] || fail "gns-relay Compose wurde nicht gerendert."
}

# port_entries <file> -> alle Port-Mapping-Strings (`- "..."`, ohne /proto).
port_entries() {
  grep -oE '^[[:space:]]*- "[0-9a-zA-Z.]+:[0-9]+(/[a-z]+)?"' "$1" \
    | cut -d'"' -f2 | sed -E 's#/(udp|tcp)$##'
}

# public_host_ports <file> -> Host-Ports, die NICHT loopback-gebunden sind
# (nackte `PORT:CONTAINER`-Publishes = 0.0.0.0).
public_host_ports() {
  port_entries "$1" | while read -r m; do
    ip="${m%%:*}"
    case "$ip" in
      127.*|0.0.0.0) ;;                       # loopback / explizit wildcard -> nicht "public"
      *[!0-9]*) ;;                            # kein nackter Port -> skip
      *) echo "$ip" ;;
    esac
  done
}

out="$work/out"
render "$out"

echo "== prod-A: Game 6322 oeffentlich (0.0.0.0), Bridge/Attack-Cycle loopback =="
a="$out/riftbreaker-server-a.yml"
grep -qE '^[[:space:]]*- "6322:6321/udp"' "$a" \
  || fail "prod-A Game-Publish 6322 ist nicht oeffentlich (erwartet '6322:6321/udp')."
! grep -qE '127\.0\.0\.1:6322' "$a" \
  || fail "prod-A Game-Port 6322 ist auf 127.0.0.1 gebunden (soll bewusst oeffentlich sein)."
grep -qE '^[[:space:]]*- "127\.0\.0\.1:9002:9001"' "$a" \
  || fail "prod-A Bridge ist nicht loopback (erwartet '127.0.0.1:9002:9001')."
grep -qE '^[[:space:]]*- "127\.0\.0\.1:9103:9103"' "$a" \
  || fail "prod-A Attack-Cycle-Control ist nicht loopback (erwartet '127.0.0.1:9103:9103')."
[ "$(public_host_ports "$a" | sort -u | tr '\n' ' ')" = "6322 " ] \
  || fail "prod-A oeffentliche Host-Publishes != {6322} (ist: $(public_host_ports "$a" | sort -u | tr '\n' ' '))."
echo "   A: public={6322}, bridge=127.0.0.1:9002, attack-cycle=127.0.0.1:9103"

echo "== prod-B: Game 6325 oeffentlich (0.0.0.0), Bridge/Attack-Cycle loopback =="
b="$out/riftbreaker-server-b.yml"
grep -qE '^[[:space:]]*- "6325:6321/udp"' "$b" \
  || fail "prod-B Game-Publish 6325 ist nicht oeffentlich (erwartet '6325:6321/udp')."
! grep -qE '127\.0\.0\.1:6325' "$b" \
  || fail "prod-B Game-Port 6325 ist auf 127.0.0.1 gebunden (soll bewusst oeffentlich sein)."
grep -qE '^[[:space:]]*- "127\.0\.0\.1:9004:9001"' "$b" \
  || fail "prod-B Bridge ist nicht loopback (erwartet '127.0.0.1:9004:9001')."
grep -qE '^[[:space:]]*- "127\.0\.0\.1:9105:9105"' "$b" \
  || fail "prod-B Attack-Cycle-Control ist nicht loopback (erwartet '127.0.0.1:9105:9105')."
[ "$(public_host_ports "$b" | sort -u | tr '\n' ' ')" = "6325 " ] \
  || fail "prod-B oeffentliche Host-Publishes != {6325} (ist: $(public_host_ports "$b" | sort -u | tr '\n' ' '))."
echo "   B: public={6325}, bridge=127.0.0.1:9004, attack-cycle=127.0.0.1:9105"

echo "== gns-relay: nur network_mode host + Port 6321 (kein ports:) =="
r="$out/gns-relay.yml"
grep -qE '^[[:space:]]*network_mode: host$' "$r" \
  || fail "gns-relay nutzt nicht `network_mode: host`."
grep -qE '\-\-port 6321' "$r" \
  || fail "gns-relay startet den Probe nicht auf Port 6321 (gns_relay_port)."
! grep -qE '^[[:space:]]*ports:' "$r" \
  || fail "gns-relay deklariert ein `ports:` — bei network_mode: host muss der Port direkt gebunden sein."
echo "   relay: network_mode=host, --port 6321, kein ports:"

echo "== Oeffentliche Host-Publish-Menge == {6321, 6322, 6325} =="
union="$( { public_host_ports "$a"; public_host_ports "$b"; echo 6321; } | sort -u | tr '\n' ' ')"
[ "$union" = "6321 6322 6325 " ] \
  || fail "oeffentliche Portmenge != {6321,6322,6325} (ist: $union)."
echo "   union={6321,6322,6325}"

echo "== Negativ-Probe: Loopback-Prefix am Game-Port wird NICHT als oeffentlich erkannt =="
probe="$work/probe.yml"
printf '    ports:\n      - "127.0.0.1:6322:6321/udp"\n' > "$probe"
if public_host_ports "$probe" | grep -qx 6322; then
  fail "Negativ-Probe: loopback-gebundener Game-Port 6322 wurde faelschlich als oeffentlich erkannt."
fi
echo "   loopback:6322 korrekt NICHT in der oeffentlichen Menge"

echo "== Negativ-Probe: leerer Game-Port faellt auf Loopback (ephemer) zurueck =="
neg="$work/neg"
render "$neg" -e riftbreaker_server_port_udp=
if public_host_ports "$neg/riftbreaker-server-a.yml" | grep -qx 6322; then
  fail "Negativ-Probe: 6322 trotz leerem riftbreaker_server_port_udp oeffentlich."
fi
grep -qE '127\.0\.0\.1::6321/udp' "$neg/riftbreaker-server-a.yml" \
  || fail "Negativ-Probe: Loopback-Fallback (127.0.0.1::6321/udp) fehlt bei leerem Game-Port."
echo "   leerer Game-Port -> loopback-ephemer (kein 0.0.0.0)"

echo "OK: bewusst-oeffentliche Portmenge {6321,6322,6325} korrekt (Issue #536)."
