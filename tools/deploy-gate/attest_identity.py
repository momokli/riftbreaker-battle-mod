#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""attest_identity.py — SOC-Attestation der Deploy-Identitaet (Issue #504).

Prueft NACH einem Deploy, dass die erwartete Deploy-Identitaet ``<env> · <ref>``
auf jeder erreichbaren Surface sichtbar und konsistent ist. Das ist der
SOC-Beweis („dev ist wirklich dev, prod ist wirklich prod").

Rein stdlib (KEIN PyYAML, kein Netz-Zwang im Hermetik-Test — Muster von
check_env_isolation.py): die Kern-Logik ``attest()`` + die Extraktoren sind
pure Funktionen ohne I/O; nur ``collect_sources()`` macht (optional) I/O. Die
Quellen sind ueber ``--sources-json`` / ``RBB_ATTEST_SOURCE_<SURFACE>``
uebersteuerbar, damit der hermetische CI-Test ohne planet laeuft. Im
Live-Betrieb (``--live``) liest das Skript echte URLs / ``docker inspect``.

Surfaces (Issue #483/#492/#498/#499):

    Landing        <meta name="rb-env"/"rb-ref"> + Badge (index.html)
    Cockpit        Bridge-HTML (bausteine/08-control-ui/cockpit.html)
    Tournament     GET /health -> {"env", "ref"} (TOURNAMENT_ENV/REF)
    Server-Control GET /server/status -> {"env", "ref"} (SERVER_CONTROL_ENV/REF)
    Session        JSONL-Record {"env", "ref"} (RBB_ENV/REF im Sidecar)
    Egress         Event-Record {"env", "ref"} (RBB_ENV/REF im Sidecar)
    Container      Labels RBB_ENV/RBB_REF (docker inspect)
    Mod-Log        event=mod_load ... env=... ref=... (mod/lua) — n/a (#499)
    Mod-ZIP        Dateiname rbbattle-<env>-<ref>.zip

Status je Surface: PASS (identisch), FAIL (abweichend), n/a (exponiert keine
Identitaet bzw. keine Quelle — Cockpit heute ohne Identitaet, Mod-Log inert
bis #499).

Exit-Codes:
  0 = alle anwendbaren (nicht-n/a) Flaechen PASS
  1 = mindestens eine Flaeche FAIL (oder nichts attestierbar)
  2 = Aufruf-/Quellen-Fehler (ungueltige Env, fehlender Ref, unlesbare Quelle)

Aufruf:
  python3 tools/deploy-gate/attest_identity.py --env dev --ref <sha>
  python3 tools/deploy-gate/attest_identity.py --env dev --ref <sha> \
      --sources-json /tmp/attest-sources.json     # hermetisch/planet
  RBB_ATTEST_ENV=prod RBB_ATTEST_REF=v1.2.3 python3 ... --live
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

MARKER = "SOC-ATTEST"
VALID_ENVS = ("dev", "prod", "test")

HTTP_TIMEOUT_S = 10
DOCKER_TIMEOUT_S = 15

# (key, label, n/a-Begruendung wenn die Surface KEINE Identitaet exponiert).
SURFACES = [
    ("landing", "Landing (meta rb-env/rb-ref)", "Landing exponiert keine rb-env/rb-ref-meta."),
    ("cockpit", "Cockpit (Bridge-HTML)", "Cockpit exponiert env/ref (noch) nicht."),
    ("tournament_health", "Tournament /health", "/health liefert keine env/ref-Felder."),
    ("server_status", "/server/status", "/server/status liefert keine env/ref-Felder."),
    ("session_jsonl", "Session-JSONL", "Session-Record ohne env/ref."),
    ("egress_jsonl", "Egress-Event", "Egress-Event ohne env/ref."),
    ("container_labels", "Container-Labels", "Container ohne RBB_ENV/RBB_REF-Labels."),
    ("mod_log", "Mod-Log", "Mod-Log-Identitaet inert (n/a #499)."),
    ("mod_zip", "Mod-ZIP-Name", "Zip-Name entspricht nicht rbbattle-<env>-<ref>.zip."),
]

# Live-Quell-Konfiguration je Surface: HTTP-(URL-Env, optionales Token-Env) oder
# spezieller Fetcher. Eine fehlende Konfiguration -> n/a (keine Quelle).
LIVE_HTTP = {
    "landing": ("RBB_ATTEST_LANDING_URL", None),
    "cockpit": ("RBB_ATTEST_COCKPIT_URL", None),
    "tournament_health": ("RBB_ATTEST_HEALTH_URL", None),
    "server_status": ("RBB_ATTEST_SERVER_STATUS_URL", "RBB_ATTEST_SERVER_STATUS_TOKEN"),
}

# ---------------------------------------------------------------------------
# Pure Extraktoren: Eingabe = Rohwert (str, dict oder list), Ausgabe =
# ``(env, ref)`` oder ``None`` (Surface exponiert keine Identitaet -> n/a).
# ---------------------------------------------------------------------------

RE_META = re.compile(
    r"<meta\b(?=[^>]*\bname=[\"'](?P<name>[^\"']+)[\"'])[^>]*\bcontent=[\"'](?P<content>[^\"']*)[\"']",
    re.IGNORECASE,
)
RE_FIELD = re.compile(r"\b(?P<key>[A-Za-z_][A-Za-z0-9_]*)=(?P<val>[^\s\"']+)")
RE_MOD_LOAD = re.compile(r"\bevent=mod_load\b")
RE_ZIP_NAME = re.compile(r"^rbbattle-(?P<env>[^-]+)-(?P<ref>.+)\.zip$")


class LiveFetchError(Exception):
    """Eine konfigurierte Live-Quelle ist nicht lesbar (-> FAIL, nicht n/a)."""


def _parse_json(raw):
    """JSON-Objekt aus str/dict/list liefern (dict/list wird durchgereicht)."""
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    if not isinstance(raw, str):
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _meta_content(html, wanted):
    if not isinstance(html, str):
        return None
    for match in RE_META.finditer(html):
        if match.group("name") == wanted:
            return match.group("content")
    return None


def _json_env_ref(raw):
    """env/ref aus einem JSON-Objekt; ``None`` wenn gar keine Felder da sind."""
    obj = _parse_json(raw)
    if not isinstance(obj, dict):
        return None
    env = obj.get("env")
    ref = obj.get("ref")
    if env is None and ref is None:
        return None
    return env, ref


def extract_landing(html):
    env = _meta_content(html, "rb-env")
    ref = _meta_content(html, "rb-ref")
    if env is None and ref is None:
        return None
    return env, ref


def extract_cockpit(html):
    # Cockpit traegt heute KEINE Identitaet (nur den Titel-Badge "◆"). Falls
    # kuenftig ein Badge/Meta dazukommt, erkennt der Extraktor ihn ueber
    # dieselben rb-env/rb-ref-Marker wie die Landing.
    return extract_landing(html)


def extract_tournament_health(raw):
    return _json_env_ref(raw)


def extract_server_status(raw):
    return _json_env_ref(raw)


def extract_session_record(raw):
    return _json_env_ref(raw)


def extract_egress_event(raw):
    return _json_env_ref(raw)


def extract_container_labels(raw):
    obj = _parse_json(raw)
    if isinstance(obj, list):
        obj = obj[0] if obj else {}
    labels = None
    if isinstance(obj, dict):
        if isinstance(obj.get("Labels"), dict):
            labels = obj["Labels"]
        elif isinstance(obj.get("Config"), dict) and isinstance(obj["Config"].get("Labels"), dict):
            labels = obj["Config"]["Labels"]
        else:
            # `docker inspect --format '{{json .Config.Labels}}'` liefert das
            # Labels-Objekt direkt.
            labels = obj
    if not isinstance(labels, dict):
        return None
    env = labels.get("RBB_ENV")
    ref = labels.get("RBB_REF")
    if env is None and ref is None:
        return None
    return env, ref


def extract_mod_log(line):
    """Mod-Load-Zeile -> (env, ref); ``None`` solange die Identitaet inert ist.

    Issue #499: die Riftbreaker-Lua-Sandbox liefert ``os.getenv`` nicht, der
    defensive Fallback schreibt ``env=unknown ref=unknown``. Solange das so
    ist, gilt die Surface als n/a — die Extraktion meldet NICHT "unknown" als
    Soll-Wert zurueck, sondern explizit ``None``.
    """
    if not isinstance(line, str) or not RE_MOD_LOAD.search(line):
        return None
    fields = {m.group("key"): m.group("val") for m in RE_FIELD.finditer(line)}
    env = fields.get("env")
    ref = fields.get("ref")
    if env is None and ref is None:
        return None
    if (env or "") == "unknown" or (ref or "") == "unknown":
        return None  # n/a (#499)
    return env, ref


def extract_mod_zip(name):
    if not isinstance(name, str):
        return None
    name = name.strip()
    match = RE_ZIP_NAME.match(name)
    if not match:
        return None
    return match.group("env"), match.group("ref")


EXTRACTORS = {
    "landing": extract_landing,
    "cockpit": extract_cockpit,
    "tournament_health": extract_tournament_health,
    "server_status": extract_server_status,
    "session_jsonl": extract_session_record,
    "egress_jsonl": extract_egress_event,
    "container_labels": extract_container_labels,
    "mod_log": extract_mod_log,
    "mod_zip": extract_mod_zip,
}

# ---------------------------------------------------------------------------
# Kern-Logik (pure, kein I/O).
# ---------------------------------------------------------------------------


def _result(surface, label, status, reported_env, reported_ref, detail):
    return {
        "surface": surface,
        "label": label,
        "status": status,
        "reported_env": reported_env,
        "reported_ref": reported_ref,
        "detail": detail,
    }


def attest(expected_env, expected_ref, sources, errors=None):
    """Vergleicht die gemeldete Identitaet jeder Surface gegen das Soll.

    ``sources`` = {surface: rohwert}, ``errors`` = {surface: fehlertext} (fuer
    konfigurierte, aber nicht lesbare Live-Quellen -> FAIL statt n/a).
    """
    sources = sources or {}
    errors = errors or {}
    results = []
    for surface, label, na_reason in SURFACES:
        if surface in errors:
            results.append(_result(surface, label, "FAIL", None, None, errors[surface]))
            continue
        raw = sources.get(surface)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            results.append(_result(surface, label, "n/a", None, None, "keine Quelle bereitgestellt."))
            continue
        extracted = EXTRACTORS[surface](raw)
        if extracted is None:
            results.append(_result(surface, label, "n/a", None, None, na_reason))
            continue
        reported_env, reported_ref = extracted
        if reported_env == expected_env and reported_ref == expected_ref:
            results.append(_result(surface, label, "PASS", reported_env, reported_ref, ""))
        else:
            detail = "env=%r (erwartet %r), ref=%r (erwartet %r)" % (
                reported_env,
                expected_env,
                reported_ref,
                expected_ref,
            )
            results.append(_result(surface, label, "FAIL", reported_env, reported_ref, detail))
    return results


def exit_code(results):
    """0 nur wenn keine Flaeche FAIL und mindestens eine Flaeche PASS."""
    statuses = [r["status"] for r in results]
    if "FAIL" in statuses:
        return 1
    if "PASS" not in statuses:
        return 1  # nichts attestierbar -> kein SOC-Beweis
    return 0


def render_matrix(results):
    lines = []
    width = max(len(label) for _s, label, _n in SURFACES)
    for r in results:
        if r["detail"]:
            lines.append("  %-5s %-*s  %s" % (r["status"], width, r["label"], r["detail"]))
        else:
            lines.append("  %-5s %s" % (r["status"], r["label"]))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Separation-Layer: Quellen (nur hier passiert I/O).
# ---------------------------------------------------------------------------


def _coerce_source(value):
    if value is None:
        return None
    if isinstance(value, (str, dict, list)):
        return value
    return str(value)


def _http_get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            return resp.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise LiveFetchError("Live-Quelle nicht erreichbar (%s): %s" % (url, exc))


def _docker_inspect_labels(container):
    cmd = ["docker", "inspect", "--format", "{{json .Config.Labels}}", container]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=DOCKER_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LiveFetchError("docker inspect fehlgeschlagen: %s" % exc)
    if proc.returncode != 0:
        raise LiveFetchError("docker inspect %s: %s" % (container, proc.stderr.strip()))
    out = (proc.stdout or "").strip()
    if not out or out == "null":
        raise LiveFetchError("Container %s hat keine Labels." % container)
    return out


def _last_jsonl_line(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = [ln for ln in handle if ln.strip()]
    except OSError as exc:
        raise LiveFetchError("Session-JSONL nicht lesbar (%s): %s" % (path, exc))
    if not lines:
        raise LiveFetchError("Session-JSONL leer: %s" % path)
    return lines[-1].strip()


def _last_matching_line(path, needle):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError as exc:
        raise LiveFetchError("Mod-Log nicht lesbar (%s): %s" % (path, exc))
    for line in reversed(lines):
        if needle in line:
            return line
    raise LiveFetchError("Mod-Log ohne %s-Zeile: %s" % (needle, path))


def fetch_live(surface):
    """Rohwert einer Surface aus der Live-Umgebung; None = keine Quelle konfiguriert."""
    if surface in LIVE_HTTP:
        url_env, token_env = LIVE_HTTP[surface]
        url = os.environ.get(url_env)
        if not url:
            return None
        headers = {}
        if token_env:
            token = os.environ.get(token_env)
            if token:
                headers["Authorization"] = "Bearer " + token
        return _http_get(url, headers)
    if surface == "container_labels":
        container = os.environ.get("RBB_ATTEST_CONTAINER")
        return _docker_inspect_labels(container) if container else None
    if surface == "session_jsonl":
        path = os.environ.get("RBB_ATTEST_SESSION_JSONL")
        return _last_jsonl_line(path) if path else None
    if surface == "egress_jsonl":
        # Egress POSTet Events an den Tournament-Server; es gibt keine
        # Echo-Flaeche, die env/ref zurueckliefert. Live-Quelle daher nur via
        # --sources-json / RBB_ATTEST_SOURCE_EGRESS_JSONL (captured Event).
        return None
    if surface == "mod_log":
        path = os.environ.get("RBB_ATTEST_MOD_LOG")
        return _last_matching_line(path, "event=mod_load") if path else None
    if surface == "mod_zip":
        return os.environ.get("RBB_ATTEST_MOD_ZIP_NAME")
    return None


def collect_sources(args):
    """-> (sources, errors). Layer: --sources-json, Env-Overrides, --live."""
    sources = {}
    errors = {}

    if args.sources_json:
        with open(args.sources_json, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError("--sources-json muss ein JSON-Objekt {surface: rohwert} sein.")
        for surface, value in data.items():
            coerced = _coerce_source(value)
            if coerced is not None:
                sources[surface] = coerced

    for surface, _label, _na in SURFACES:
        env_name = "RBB_ATTEST_SOURCE_" + surface.upper()
        value = os.environ.get(env_name)
        if value:
            sources[surface] = value

    if args.live:
        for surface, _label, _na in SURFACES:
            if surface in sources:
                continue
            try:
                value = fetch_live(surface)
            except LiveFetchError as exc:
                errors[surface] = str(exc)
                continue
            if value is not None:
                sources[surface] = value

    return sources, errors


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------


def build_parser():
    parser = argparse.ArgumentParser(description="SOC-Attestation der Deploy-Identitaet (Issue #504)")
    parser.add_argument(
        "--env",
        default=os.environ.get("RBB_ATTEST_ENV"),
        help="Erwartete Env (dev|prod|test). Default: RBB_ATTEST_ENV.",
    )
    parser.add_argument(
        "--ref",
        default=os.environ.get("RBB_ATTEST_REF"),
        help="Erwarteter Ref (Commit-SHA/Tag). Default: RBB_ATTEST_REF.",
    )
    parser.add_argument(
        "--sources-json",
        default=None,
        help="JSON-Objekt {surface: rohwert} — uebersteuert alle Quellen (hermetisch).",
    )
    parser.add_argument("--live", action="store_true", help="Live-Quellen lesen (curl/docker inspect) statt Fixtures.")
    parser.add_argument("--format", choices=("text", "json"), default="text", help="Ausgabeformat (Default: text).")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)

    problems = []
    if args.env not in VALID_ENVS:
        problems.append("--env/RBB_ATTEST_ENV muss dev|prod|test sein (ist %r)." % args.env)
    if not args.ref:
        problems.append("--ref/RBB_ATTEST_REF fehlt (Commit-SHA/Tag angeben).")
    if problems:
        for problem in problems:
            print("%s FEHLER: %s" % (MARKER, problem), file=sys.stderr)
        return 2

    try:
        sources, errors = collect_sources(args)
    except (OSError, ValueError) as exc:
        print("%s FEHLER: Quellen nicht lesbar: %s" % (MARKER, exc), file=sys.stderr)
        return 2

    results = attest(args.env, args.ref, sources, errors)
    if args.format == "json":
        print(json.dumps({"env": args.env, "ref": args.ref, "results": results}, ensure_ascii=False, indent=2))
    else:
        print("%s (env=%s ref=%s):" % (MARKER, args.env, args.ref))
        print(render_matrix(results))

    code = exit_code(results)
    if code == 0:
        # Im JSON-Modus muss stdout reines JSON bleiben — Verdict auf stderr.
        verdict_stream = sys.stderr if args.format == "json" else sys.stdout
        print("%s ok — alle anwendbaren Flaechen tragen env=%s ref=%s."
              % (MARKER, args.env, args.ref), file=verdict_stream)
    else:
        print("%s FEHLER — Attestation nicht erfuellt." % MARKER, file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
