#!/usr/bin/env bash
# Hermetischer Selbsttest des Disk-Space-Gates (Issue #310).
#
# Prüft die ECHTE Preflight-Task-Datei mit synthetischen Mount-Facts:
#   Fall 1: genug Platz (100 GB frei, Schwelle 10 GB) -> läuft durch (exit 0)
#   Fall 2: zu wenig Platz (1 GB frei, Schwelle 10 GB) -> Abbruch + "PLATZ-GATE"
#   Fall 3: Schwelle 0 konfigurierbar -> auch bei 1 GB frei kein Abbruch
#   Fall 4: ansible_mounts leer (#586-Live-Befund) -> echter `df`-Fallback
#           gegen "/" liefert eine Zahl -> läuft durch (exit 0)
#   Fall 5: ansible_mounts leer UND `df` schlägt fehl (nichtexistenter Mount,
#           Review-Blocker 2) -> Abbruch mit der eigenen Fail-Meldung, KEIN
#           roher Modul-Fehler
#   Fall 6: wie Fall 5, aber riftbreaker_disk_skip_unknown=true -> nur Warnung,
#           kein Abbruch (Review-Blocker 1, echter Notausgang)
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

echo "== Fall 4: ansible_mounts leer -> echter df-Fallback gegen / muss durchlaufen (#586) =="
out="$(ansible-playbook "$play" -e disk_gate_test=df_fallback 2>&1)"
if ! grep -q "Platz-Gate OK" <<<"$out"; then
  echo "::error::df-Fallback hat KEIN Ergebnis geliefert (kein 'Platz-Gate OK' im Output)."
  printf '%s\n' "$out"
  exit 1
fi

echo "== Fall 5: ansible_mounts leer UND df schlägt fehl -> eigene Fail-Meldung, kein roher Modul-Fehler (Review-Blocker 2) =="
set +e
out="$(ansible-playbook "$play" -e disk_gate_test=unknown_hardfail 2>&1)"
rc=$?
set -e
if [ "$rc" -eq 0 ]; then
  echo "::error::Disk-Gate ist bei unbekanntem Speicherplatz NICHT abgebrochen (rc=0)."
  printf '%s\n' "$out"
  exit 1
fi
if ! grep -q "konnte weder über ansible_mounts noch über \`df\` ermittelt werden" <<<"$out"; then
  echo "::error::Fail-Meldung fehlt (kein 'konnte ... ermittelt werden' im Output) — evtl. roher Modul-Fehler statt eigener Meldung."
  printf '%s\n' "$out"
  exit 1
fi

echo "== Fall 6: wie Fall 5, aber riftbreaker_disk_skip_unknown=true -> nur Warnung, kein Abbruch =="
out="$(ansible-playbook "$play" -e disk_gate_test=unknown_hardfail -e riftbreaker_disk_skip_unknown=true 2>&1)"
if ! grep -q "WARNUNG: Speicherplatz" <<<"$out"; then
  echo "::error::Skip-Unknown-Bypass hat KEINE Warnung ausgegeben."
  printf '%s\n' "$out"
  exit 1
fi
if grep -q "PLATZ-GATE" <<<"$out"; then
  echo "::error::Skip-Unknown-Bypass hätte den Größen-Check überspringen müssen, hat aber PLATZ-GATE ausgewertet."
  printf '%s\n' "$out"
  exit 1
fi

echo "OK: Disk-Gate bricht bei zu wenig Platz ab, nennt die Schwelle, ist konfigurierbar, faellt bei leerem ansible_mounts auf df zurueck, bricht bei wirklich unbekanntem Platz ehrlich ab (kein roher Modul-Fehler) und laesst sich mit riftbreaker_disk_skip_unknown gezielt umgehen."
