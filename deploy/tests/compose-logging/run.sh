#!/usr/bin/env bash
# Hermetischer Regressionstest des Compose-LOG-LIMITS (Issue #301).
#
# Hintergrund: die Docker-`json-file`-Container-Logs
# (/var/lib/docker/containers/*/*-json.log) sind die reale, unverwaltete
# Disk-Luecke. Der Compose-Stack muss daher fuer JEDEN Service einen
# json-file-Log-Treiber mit Rotation setzen (max-size + max-file).
#
# Nachweis: aus dem ECHTEN Ansible-Render (dev) wird `docker compose config
# --format json` gefahren; JEDER Service in allen drei Compose-Dateien
# (riftbreaker-server, gns-relay, rift-caddy) MUSS
#   logging.driver == json-file, logging.options["max-size"] + ["max-file"]
# tragen. Ausserdem: die Werte sind per `-e` konfigurierbar (Override), und
# die Rollen-Defaults sind 10m/3.
#
# Negativ-Probe (red-before-green): eine Fixture-Kopie OHNE den logging-Block
# im riftbreaker-server-Template zeigt den Test rot (erkennt fehlendes Limit).
#
# Lokal, kein Host, kein Vault, kein Docker-Start (nur `docker compose config`).
# Laeuft in deploy-check-local.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../../.." && pwd)"
play="$here/main.yml"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

fail() { echo "::error::$1"; exit 1; }

# render <outdir> [extra ansible args...]
render() {
  local out="$1"; shift
  mkdir -p "$out"
  RBBATTLE_RENDER_DIR="$out" ansible-playbook "$play" -e "rift_env=dev" "$@" >/dev/null
}

# check_file <compose-file> <expected-max-size> <expected-max-file>
# -> prueft via `docker compose config --format json`, dass JEDER Service ein
#    json-file-Log-Limit mit den erwarteten Werten traegt. Exit != 0 sonst.
check_file() {
  local file="$1" exp_size="$2" exp_file="$3"
  docker compose -f "$file" config --format json \
    | python3 -c '
import json, sys
exp_size, exp_file = sys.argv[1], sys.argv[2]
d = json.load(sys.stdin)
services = d.get("services") or {}
if not services:
    print("::error::keine Services im Compose", file=sys.stderr); sys.exit(1)
bad = []
for name, svc in services.items():
    log = svc.get("logging") or {}
    opts = log.get("options") or {}
    if log.get("driver") != "json-file" \
       or str(opts.get("max-size")) != exp_size \
       or str(opts.get("max-file")) != exp_file:
        bad.append("%s: driver=%r max-size=%r max-file=%r" % (
            name, log.get("driver"), opts.get("max-size"), opts.get("max-file")))
if bad:
    print("::error::Service(s) ohne korrektes Log-Limit: " + "; ".join(bad), file=sys.stderr)
    sys.exit(1)
print("   %d Service(s) mit json-file max-size=%s max-file=%s" % (len(services), exp_size, exp_file))
' "$exp_size" "$exp_file"
}

echo "== Positiv dev: jeder Service traegt json-file max-size=10m max-file=3 =="
render "$work/dev"
for f in riftbreaker-server gns-relay rift-caddy; do
  [ -s "$work/dev/$f.yml" ] || fail "dev: $f.yml wurde nicht gerendert."
  echo "-- $f.yml"
  check_file "$work/dev/$f.yml" "10m" "3"
done

echo "== Konfigurierbar: Override per -e aendert die Werte in ALLEN Rollen =="
render "$work/ovr" \
  -e riftbreaker_log_max_size=1m -e riftbreaker_log_max_file=7 \
  -e gns_relay_log_max_size=2m -e gns_relay_log_max_file=4 \
  -e rift_caddy_log_max_size=3m -e rift_caddy_log_max_file=5
check_file "$work/ovr/riftbreaker-server.yml" "1m" "7"
check_file "$work/ovr/gns-relay.yml" "2m" "4"
check_file "$work/ovr/rift-caddy.yml" "3m" "5"
echo "   Override greift (riftbreaker 1m/7, gns 2m/4, caddy 3m/5)."

echo "== Negativ-Probe (red-before-green): ohne logging-Block wird der Test rot =="
fixture="$(mktemp -d)"
cp -r "$repo/deploy" "$fixture/deploy"
# Den logging-Block aus dem riftbreaker-server-Template entfernen (nur dort,
# reicht fuer den Nachweis). 6-zeiliger Block inkl. Kommentarzeilen.
python3 - "$fixture/deploy/roles/riftbreaker-server/templates/docker-compose.yml.j2" <<'PY'
import re, sys
p = sys.argv[1]
s = open(p).read()
new = re.sub(r"\n *# Container-Log begrenzen \(Issue #301\).*?max-file: \"\{\{ riftbreaker_log_max_file \}\}\"", "", s, flags=re.S)
assert new != s, "Fixture: logging-Block nicht gefunden."
open(p, "w").write(new)
PY
neg_out="$work/neg"
mkdir -p "$neg_out"
RBBATTLE_RENDER_DIR="$neg_out" ansible-playbook "$fixture/deploy/tests/compose-logging/main.yml" \
  -e rift_env=dev >/dev/null
rm -rf "$fixture"
if check_file "$neg_out/riftbreaker-server.yml" "10m" "3" >/dev/null 2>&1; then
  fail "Negativ-Probe: ohne logging-Block blieb der Test gruen (Test wirkungslos)."
fi
echo "   Negativ-Probe: fehlender logging-Block -> Test rot (erkannt)."

echo "OK: Compose-Stack begrenzt Container-Logs (json-file max-size/max-file, Issue #301)."
