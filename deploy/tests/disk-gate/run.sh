#!/usr/bin/env bash
# Hermetischer Selbsttest des Disk-Space-Gates (Issue #310).
#
# Prüft die ECHTE Preflight-Task-Datei mit synthetischen Mount-Facts:
#   Fall 1: genug Platz (100 GB frei, Schwelle 10 GB) -> läuft durch (exit 0)
#   Fall 2: zu wenig Platz (1 GB frei, Schwelle 10 GB) -> Abbruch + "PLATZ-GATE"
#   Fall 3: Schwelle 0 konfigurierbar -> auch bei 1 GB frei kein Abbruch
#
# Kein Host-Fact, kein SSH, keine Prod-Aktion. Läuft in deploy-check-local.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
play="$here/main.yml"

echo "== Fall 1: genug Platz (100 GB frei, Schwelle 10 GB) -> muss durchlaufen =="
ansible-playbook "$play" -e disk_gate_test=pass

echo "== Fall 2: zu wenig Platz (1 GB frei, Schwelle 10 GB) -> muss abbrechen =="
set +e
out="$(ansible-playbook "$play" -e disk_gate_test=fail 2>&1)"
rc=$?
set -e
printf '%s\n' "$out"
if [ "$rc" -eq 0 ]; then
  echo "::error::Disk-Gate hat bei zu wenig Platz NICHT abgebrochen (rc=0)."
  exit 1
fi
if ! grep -q "PLATZ-GATE" <<<"$out"; then
  echo "::error::fail_msg fehlt im Abbruch (kein 'PLATZ-GATE' im Output)."
  exit 1
fi

echo "== Fall 3: Schwelle per Variable konfigurierbar (0 GB bei 1 GB frei) -> durch =="
if ! ansible-playbook "$play" -e disk_gate_test=fail -e riftbreaker_disk_min_free_gb=0 >/dev/null 2>&1; then
  echo "::error::Disk-Gate ist nicht konfigurierbar (Schwelle 0 muss durchlaufen)."
  exit 1
fi

echo "OK: Disk-Gate bricht bei zu wenig Platz ab, nennt die Schwelle und ist konfigurierbar."
