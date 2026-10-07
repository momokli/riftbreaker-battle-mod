#!/usr/bin/env bash
# Hermetischer Regressionstest der Ofelia-Hygiene-Verdrahtung (Issue #1103).
#
# Hintergrund: die zwei Host-Wartungsjobs
#   * image-retention (#309)       -> docker_image_tag_retention.sh
#   * host-hygiene    (#308/#606)  -> host_hygiene.sh
# laufen containerisiert als Ofelia-"job-exec"-Jobs. `ofelia` liest die
# Job-Definitionen aus den LABELS des `hygiene`-Services und fuehrt sie via
# `docker exec` IN `hygiene` aus — daher muessen Labels, Env und Mounts des
# idle-Containers exakt stimmen (sonst laufen die Jobs leer/falsch).
#
# Nachweis: die EINGECHECKTE compose.yaml wird direkt gerendert
#   RBB_REF=check RBB_HOST_ROOT=/tmp/rbh docker compose --env-file .env.example config --format json
# und das resultierende JSON durch einen stdlib-Python-Checker geprueft.
# KEIN Ansible, KEIN main.yml, KEIN Schreiben einer .env (die .env eines Devs
# bleibt unberuehrt; --env-file zeigt auf die eingecheckte .env.example).
#
# Negativ-Probe (red-before-green): dieselbe gerenderte JSON wird in-memory
# mutiert (ofelia.enabled entfernt + image-retention-Command geleert) und der
# IDENTISCHE Checker erneut gefahren -> er MUSS rot werden. Das belegt, dass der
# Checker nicht vakuum-gruen ist.
#
# Lokal, kein Host, kein Vault, kein Docker-Start (nur `docker compose config`).
# Laeuft in deploy-check-local.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../../.." && pwd)"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

fail() { echo "::error::$1"; exit 1; }

# --- Checker (stdlib only: json, sys) -----------------------------------------
# Liest die gerenderte Compose-JSON von stdin. Exit != 0 und je Verletzung eine
# ::error::-Zeile, wenn eine Zusicherung fehlt.
checker="$work/check_hygiene.py"
cat > "$checker" <<'PY'
#!/usr/bin/env python3
import json
import sys


def as_map(x):
    """environment/labels kommen in `config --format json` als Map; die
    list-of-"K=V"-Form wird zur Sicherheit ebenfalls toleriert."""
    if x is None:
        return {}
    if isinstance(x, dict):
        return {str(k): ("" if v is None else str(v)) for k, v in x.items()}
    out = {}
    for item in x:
        if isinstance(item, dict):
            for k, v in item.items():
                out[str(k)] = "" if v is None else str(v)
        elif isinstance(item, str) and "=" in item:
            k, v = item.split("=", 1)
            out[k] = v
    return out


def mount_at(volumes, target):
    for vol in volumes or []:
        if isinstance(vol, dict) and vol.get("target") == target:
            return vol
    return None


def main():
    data = json.load(sys.stdin)
    services = data.get("services") or {}
    errors = []

    def check(cond, msg):
        if not cond:
            errors.append(msg)

    hygiene = services.get("hygiene")
    if hygiene is None:
        errors.append("service 'hygiene' fehlt im gerenderten Compose")

    # --- ofelia: Job-Labels am `hygiene`-Service (source of truth) ------------
    labels = as_map((hygiene or {}).get("labels"))
    check(labels.get("ofelia.enabled") == "true",
          "hygiene.labels['ofelia.enabled'] != 'true' (ist %r)" % labels.get("ofelia.enabled"))
    check(labels.get("ofelia.job-exec.image-retention.schedule") == "@daily",
          "image-retention.schedule != '@daily' (ist %r)"
          % labels.get("ofelia.job-exec.image-retention.schedule"))
    check((labels.get("ofelia.job-exec.image-retention.command") or "")
          .endswith("docker_image_tag_retention.sh"),
          "image-retention.command endet nicht auf docker_image_tag_retention.sh (ist %r)"
          % labels.get("ofelia.job-exec.image-retention.command"))
    check(labels.get("ofelia.job-exec.host-hygiene.schedule") == "@weekly",
          "host-hygiene.schedule != '@weekly' (ist %r)"
          % labels.get("ofelia.job-exec.host-hygiene.schedule"))
    check((labels.get("ofelia.job-exec.host-hygiene.command") or "")
          .endswith("host_hygiene.sh"),
          "host-hygiene.command endet nicht auf host_hygiene.sh (ist %r)"
          % labels.get("ofelia.job-exec.host-hygiene.command"))

    # --- hygiene-Env: die Jobs erben sie via `docker exec` --------------------
    env = as_map((hygiene or {}).get("environment"))
    protected = env.get("RB_PROTECTED_TAGS") or ""
    check(protected.startswith("rb-dedicated:"),
          "RB_PROTECTED_TAGS beginnt nicht mit 'rb-dedicated:' (ist %r)" % protected)
    check(protected.endswith(":check"),
          "RB_PROTECTED_TAGS spiegelt ${RBB_REF}=check nicht (ist %r)" % protected)
    check(env.get("RB_ROLLBACK_TAGS") == "2",
          "RB_ROLLBACK_TAGS != '2' (ist %r)" % env.get("RB_ROLLBACK_TAGS"))
    check(env.get("RB_HYGIENE_RBTOOLS_DIR") == "/opt/rbmods/rbtools",
          "RB_HYGIENE_RBTOOLS_DIR != '/opt/rbmods/rbtools' (ist %r)" % env.get("RB_HYGIENE_RBTOOLS_DIR"))
    check(env.get("RB_HYGIENE_RBTOOLS_RETENTION") == "10",
          "RB_HYGIENE_RBTOOLS_RETENTION != '10' (ist %r)" % env.get("RB_HYGIENE_RBTOOLS_RETENTION"))
    check("rb-dedicated" in (env.get("RB_IMAGE_REPOS") or ""),
          "RB_IMAGE_REPOS enthaelt 'rb-dedicated' nicht (ist %r)" % env.get("RB_IMAGE_REPOS"))

    # --- hygiene-Mounts: Docker-Socket (rw) + Host-rbtools-Dir ----------------
    hvols = (hygiene or {}).get("volumes") or []
    sock = mount_at(hvols, "/var/run/docker.sock")
    if sock is None:
        errors.append("hygiene mountet /var/run/docker.sock nicht")
    else:
        check(sock.get("source") == "/var/run/docker.sock",
              "hygiene docker.sock source != '/var/run/docker.sock' (ist %r)" % sock.get("source"))
        check(not sock.get("read_only"),
              "hygiene docker.sock muss read-write sein (read_only=%r)" % sock.get("read_only"))
    rbtools = mount_at(hvols, "/opt/rbmods/rbtools")
    if rbtools is None:
        errors.append("hygiene mountet /opt/rbmods/rbtools nicht")
    else:
        check((rbtools.get("source") or "").endswith("/rbtools"),
              "hygiene rbtools source endet nicht auf '/rbtools' (ist %r)" % rbtools.get("source"))

    # --- ofelia-Service: Scheduler + Socket read-only -------------------------
    ofelia = services.get("ofelia")
    if ofelia is None:
        errors.append("service 'ofelia' fehlt im gerenderten Compose")
    else:
        check(ofelia.get("command") == ["daemon", "--docker"],
              "ofelia.command != ['daemon','--docker'] (ist %r)" % ofelia.get("command"))
        depends = ofelia.get("depends_on") or {}
        if isinstance(depends, list):
            depends = {name: {} for name in depends}
        check("hygiene" in depends,
              "ofelia.depends_on enthaelt 'hygiene' nicht (ist %r)" % list(depends))
        osock = mount_at(ofelia.get("volumes"), "/var/run/docker.sock")
        if osock is None:
            errors.append("ofelia mountet /var/run/docker.sock nicht")
        else:
            check(bool(osock.get("read_only")),
                  "ofelia docker.sock muss read-only sein (read_only=%r)" % osock.get("read_only"))
        check(ofelia.get("image") == "mcuadros/ofelia:0.3.22",
              "ofelia.image != 'mcuadros/ofelia:0.3.22' (ist %r)" % ofelia.get("image"))

    if errors:
        for msg in errors:
            print("::error::" + msg, file=sys.stderr)
        sys.exit(1)
    print("   hygiene+ofelia: Labels, Env und Mounts vollstaendig verdrahtet.")


main()
PY

# --- Positiv: eingecheckte compose.yaml rendern -------------------------------
render_json="$work/render.json"
echo "== Positiv: eingecheckte compose.yaml rendern (RBB_REF=check, RBB_HOST_ROOT=/tmp/rbh) =="
(
  cd "$repo"
  RBB_REF=check RBB_HOST_ROOT=/tmp/rbh \
    docker compose --env-file .env.example config --format json > "$render_json"
) || fail "docker compose config --format json ist fehlgeschlagen."

python3 "$checker" < "$render_json" \
  || fail "Ofelia-Hygiene-Verdrahtung unvollstaendig (siehe ::error:: oben)."

# --- Negativ-Probe (red-before-green) -----------------------------------------
echo "== Negativ-Probe (red-before-green): mutierte Kopie MUSS rot werden =="
neg_json="$work/render.neg.json"
python3 - "$render_json" "$neg_json" <<'PY'
import json
import sys

src, dst = sys.argv[1], sys.argv[2]
data = json.load(open(src))
labels = data["services"]["hygiene"]["labels"]
# 1) Ofelia-Enable-Flag entfernen  2) einen Job-Command leeren.
labels.pop("ofelia.enabled", None)
labels["ofelia.job-exec.image-retention.command"] = ""
json.dump(data, open(dst, "w"))
PY

if python3 "$checker" < "$neg_json" > "$work/neg.out" 2>&1; then
  fail "Negativ-Probe: mutiertes Compose blieb gruen (Checker wirkungslos)."
fi
grep -q "ofelia.enabled" "$work/neg.out" \
  || fail "Negativ-Probe: Checker wurde rot, aber NICHT wegen ofelia.enabled (unerwartet)."
echo "   Negativ-Probe: entferntes ofelia.enabled + leerer Job-Command -> Checker rot (erkannt)."

echo "OK: Ofelia hygiene-sidecar verdrahtet (#1103)."
