#!/usr/bin/env python3
"""RBBattle Deploy-Hook — HTTP-Endpoint für den CD-Deploy auf planet.

Ersetzt den früheren SSH-Deploy aus GitHub Actions (Issue #91): Der
self-hosted Runner auf planet stößt nach jedem Merge auf `main` diesen Hook
an; der Hook aktualisiert den lokalen Checkout und führt den Deploy aus.

Endpunkte (beide: `Authorization: Bearer <DEPLOY_TOKEN>`, constant-time):
  POST /deploy              {"sha": "<40-hex>", "ref": "refs/heads/main"}
                            -> 202 {"job_id": "<uuid4>"} — Job läuft in einem
                               Hintergrund-Thread (Jobs laufen seriell).
  GET  /deploy/<id>/status  -> 200 {"status": "running|success|failed",
                                    "exit_code": <int|null>,
                                    "log_tail": "<letzte Zeilen>"}

Ablauf je Job: `git fetch` + Hard-Checkout der SHA in CHECKOUT_DIR, danach
DEPLOY_CMD (cwd = CHECKOUT_DIR). stdout/stderr landen in
/var/log/rbbattle-deploy/<job_id>.log und auf stdout (-> journald).

Konfiguration ausschließlich über Umgebungsvariablen
(EnvironmentFile /etc/rbbattle-deploy/hook.env):
  LISTEN           default 127.0.0.1:6321
  DEPLOY_TOKEN     Pflicht — gemeinsames Geheimnis mit dem Workflow
  CHECKOUT_DIR     default /opt/rbbattle-deploy/repo
  DEPLOY_CMD       Pflicht — Deploy-Kommando, läuft im Checkout
  VAULT_PASS_FILE  optional — wird als $VAULT_PASS_FILE exportiert
  LOG_DIR          optional — default /var/log/rbbattle-deploy (Tests/Dev)

Sicherheit: Die SHA aus dem Payload wird strikt validiert
(^[0-9a-f]{40}$) und ausschließlich als git-Argument verwendet — niemals in
Shell-Strings interpoliert. DEPLOY_CMD stammt aus der root-eigenen hook.env
(0640 root:deploy); Payload-Daten (sha/ref) erreichen niemals eine Shell.
"""

import hmac
import json
import logging
import os
import re
import subprocess
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

LOG = logging.getLogger("rbbattle-deploy-hook")

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
JOB_RE = re.compile(r"^/deploy/([0-9a-f-]{36})/status$")
MAX_BODY_BYTES = 16384
LOG_TAIL_BYTES = 8192
LOG_TAIL_LINES = 60

# Laufzeit-Konfiguration (aus Umgebungsvariablen, siehe Modul-Docstring).
CFG = {}
# Job-Registry (in-memory; nach Service-Restart leer). DEPLOY_LOCK
# serialisiert Jobs: alle teilen sich dasselbe Checkout-Verzeichnis.
JOBS = {}
JOBS_LOCK = threading.Lock()
DEPLOY_LOCK = threading.Lock()


def load_config():
    """Liest die Konfiguration aus dem Environment; bricht bei Fehlern ab."""
    listen = os.environ.get("LISTEN", "127.0.0.1:6321")
    host, sep, port = listen.rpartition(":")
    if not sep or not port.isdigit() or not 1 <= int(port) <= 65535:
        raise SystemExit("LISTEN ungültig: {!r} (erwartet host:port)".format(listen))
    token = os.environ.get("DEPLOY_TOKEN", "")
    if not token:
        raise SystemExit("DEPLOY_TOKEN fehlt — siehe deploy/README.md (hook.env)")
    deploy_cmd = os.environ.get("DEPLOY_CMD", "").strip()
    if not deploy_cmd:
        raise SystemExit("DEPLOY_CMD fehlt — siehe deploy/README.md (hook.env)")
    return {
        "host": host or "127.0.0.1",
        "port": int(port),
        "token": token,
        "checkout_dir": os.environ.get("CHECKOUT_DIR", "/opt/rbbattle-deploy/repo"),
        "deploy_cmd": deploy_cmd,
        "vault_pass_file": os.environ.get("VAULT_PASS_FILE", ""),
        "log_dir": os.environ.get("LOG_DIR", "/var/log/rbbattle-deploy"),
    }


def write_log(fh, text):
    """Schreibt Text in die Job-Logdatei UND auf stdout (-> journald)."""
    fh.write(text)
    fh.flush()
    sys.stdout.write(text)
    sys.stdout.flush()


def read_log_tail(path):
    """Letzte Zeilen der Job-Logdatei (für die Status-Antwort), begrenzt."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if size > LOG_TAIL_BYTES:
                fh.seek(size - LOG_TAIL_BYTES)
            data = fh.read()
    except OSError:
        return ""
    lines = data.decode("utf-8", errors="replace").splitlines()
    return "\n".join(lines[-LOG_TAIL_LINES:])


def run_argv(argv, cwd, env, fh):
    """Führt ein Kommando als Argumentliste aus (kein Shell-Parsing)."""
    write_log(fh, "$ " + " ".join(argv) + "\n")
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    for line in proc.stdout:
        write_log(fh, line)
    return proc.wait()


def run_deploy_cmd(cmd, cwd, env, fh):
    """Führt DEPLOY_CMD aus (Shell-Kommandozeile aus der root-eigenen hook.env).

    Sicherheit: `cmd` ist die einzige Shell-Kommandoquelle und stammt aus
    /etc/rbbattle-deploy/hook.env (root:deploy 0640). Der Payload (sha/ref)
    wird nie interpoliert, sondern nur validiert als git-Argument bzw. als
    Umgebungsvariable (RBBATTLE_*) übergeben.
    """
    write_log(fh, "$ " + cmd + "\n")
    proc = subprocess.Popen(
        ["/bin/sh", "-c", cmd],
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    for line in proc.stdout:
        write_log(fh, line)
    return proc.wait()


def run_job(job_id, sha, ref):
    """Hintergrund-Thread: fetch + Checkout + DEPLOY_CMD, dann Status setzen."""
    log_path = os.path.join(CFG["log_dir"], job_id + ".log")
    exit_code = -1
    status = "failed"
    try:
        with open(log_path, "w", encoding="utf-8") as fh:
            os.chmod(log_path, 0o640)
            write_log(fh, "== rbbattle deploy job {} ==\n".format(job_id))
            write_log(fh, "sha={}  ref={}\n\n".format(sha, ref))
            env = os.environ.copy()
            env.pop("DEPLOY_TOKEN", None)  # Token gehört nicht in die Deploy-Umgebung
            env["RBBATTLE_JOB_ID"] = job_id
            env["RBBATTLE_SHA"] = sha
            env["RBBATTLE_REF"] = ref
            if CFG["vault_pass_file"]:
                env["VAULT_PASS_FILE"] = CFG["vault_pass_file"]
            with DEPLOY_LOCK:
                exit_code = run_argv(
                    ["git", "fetch", "--prune", "--quiet", "origin"],
                    CFG["checkout_dir"], env, fh,
                )
                if exit_code == 0:
                    exit_code = run_argv(
                        ["git", "checkout", "--force", sha],
                        CFG["checkout_dir"], env, fh,
                    )
                if exit_code == 0:
                    exit_code = run_deploy_cmd(
                        CFG["deploy_cmd"], CFG["checkout_dir"], env, fh,
                    )
            status = "success" if exit_code == 0 else "failed"
            write_log(fh, "\n== status: {} (exit_code={}) ==\n".format(status, exit_code))
    except Exception:  # Job darf nie stumm hängen bleiben
        LOG.exception("Job %s: unerwarteter Fehler", job_id)
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write("hook-interner Fehler — Details im journald-Log\n")
        except OSError:
            pass
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is not None:
            job["status"] = status
            job["exit_code"] = exit_code


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "rbbattle-deploy-hook/1.0"
    timeout = 120  # idle Keep-Alive-Verbindungen nicht ewig offen halten

    def log_message(self, fmt, *args):
        # Request-Log mit Client-IP + Zeit (Zeit via logging-Format -> journald).
        LOG.info("client %s: %s", self.client_address[0], fmt % args)

    def send_json(self, code, payload, close=False):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if close:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        self.wfile.write(body)

    def auth_ok(self):
        header = self.headers.get("Authorization", "")
        scheme, _, value = header.partition(" ")
        if scheme.lower() != "bearer":
            return False
        return hmac.compare_digest(
            value.encode("utf-8"), CFG["token"].encode("utf-8"),
        )

    def do_POST(self):
        if urlsplit(self.path).path != "/deploy":
            # 404 ohne Body-Drain: Verbindung schließen, damit eventuelle
            # Body-Restdaten nicht als nächster Request geparst werden.
            self.send_json(404, {"error": "not found"}, close=True)
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY_BYTES:
            self.send_json(413, {"error": "missing or oversized body"}, close=True)
            return
        # Body vollständig lesen (begrenzt), damit Keep-Alive synchron bleibt —
        # auch bei 401/400-Antworten ohne weitere Verarbeitung.
        raw = self.rfile.read(length)
        if not self.auth_ok():
            self.send_json(401, {"error": "unauthorized"})
            return
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self.send_json(400, {"error": "invalid JSON"})
            return
        if not isinstance(payload, dict):
            self.send_json(400, {"error": "invalid JSON (object expected)"})
            return
        sha = payload.get("sha")
        ref = payload.get("ref")
        if not isinstance(sha, str) or not SHA_RE.match(sha):
            self.send_json(400, {"error": "invalid sha (expect 40 lowercase hex)"})
            return
        if not isinstance(ref, str) or not ref or len(ref) > 255:
            self.send_json(400, {"error": "invalid ref"})
            return
        job_id = str(uuid.uuid4())
        with JOBS_LOCK:
            JOBS[job_id] = {
                "status": "running",
                "exit_code": None,
                "log_path": os.path.join(CFG["log_dir"], job_id + ".log"),
            }
        threading.Thread(
            target=run_job, args=(job_id, sha, ref),
            name="deploy-" + job_id, daemon=True,
        ).start()
        LOG.info("Deploy-Job %s gestartet (sha=%s, ref=%s)", job_id, sha, ref)
        self.send_json(202, {"job_id": job_id})

    def do_GET(self):
        match = JOB_RE.match(urlsplit(self.path).path)
        if not match:
            self.send_json(404, {"error": "not found"})
            return
        if not self.auth_ok():
            self.send_json(401, {"error": "unauthorized"})
            return
        with JOBS_LOCK:
            job = JOBS.get(match.group(1))
            snapshot = dict(job) if job else None
        if snapshot is None:
            self.send_json(404, {"error": "unknown job_id"})
            return
        self.send_json(200, {
            "status": snapshot["status"],
            "exit_code": snapshot["exit_code"],
            "log_tail": read_log_tail(snapshot["log_path"]),
        })


def main():
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    CFG.update(load_config())
    os.makedirs(CFG["log_dir"], exist_ok=True)
    if CFG["vault_pass_file"] and not os.path.exists(CFG["vault_pass_file"]):
        LOG.warning("VAULT_PASS_FILE existiert (noch) nicht: %s", CFG["vault_pass_file"])
    if not os.path.isdir(os.path.join(CFG["checkout_dir"], ".git")):
        LOG.warning("CHECKOUT_DIR ist (noch) kein Git-Checkout: %s", CFG["checkout_dir"])
    server = ThreadingHTTPServer((CFG["host"], CFG["port"]), Handler)
    LOG.info(
        "rbbattle-deploy-hook läuft auf http://%s:%d (checkout=%s, log=%s)",
        CFG["host"], CFG["port"], CFG["checkout_dir"], CFG["log_dir"],
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOG.info("beende (SIGINT)")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
