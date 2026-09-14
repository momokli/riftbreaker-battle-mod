/*
 * pipe_bridge.c - HTTP(9001)->Named-Pipe-Bridge fuer den Rift-Breaker-
 *                 Dedicated-Server (Baustein 04, Issue #265).
 *
 * Rolle (Architektur, docs/INGRESS_IO.md):
 *   Der Wine-Named-Pipe \\.\pipe\rbbattle der injizierten rbbridge.dll ist
 *   ein Wine-internes Objekt: er ist NUR aus einem Windows-Prozess derselben
 *   Wine-Session erreichbar. Der Tournament-Server (nativ, Linux) pusht aber
 *   auf RBBRIDGE_A_URL=http://127.0.0.1:9001/exec. Diese kleine Wine-x64-
 *   Konsole schliesst die Luecke: sie ist der HTTP-Endpunkt auf
 *   127.0.0.1:9001 und uebersetzt jeden POST /exec in exec-Zeilen auf die
 *   Pipe. Kein Fremd-Dep, nur Win32 (Winsock) - laeuft unter Wine.
 *
 * Endpunkte (HTTP/1.1, Antwort immer application/json, Connection: close):
 *   GET  /health -> 200 {"ok":true,"pipe":<bool>}
 *                   (<pipe> = Pipe-Verbindung moeglich, kurzer Probe-Connect)
 *   POST /exec   -> 200 {"ok":<bool>,"results":[{"command":C,"ok":bool
 *                        [,"reason":"..."]}, ...]}
 *                   Body-Varianten (ODER):
 *                     {"command":"rb_wave 3"}
 *                     {"match_id":"...","round":3,"commands":["rb_wave 3"]}
 *                   Pipe nicht erreichbar -> 503
 *                   {"ok":false,"reason":"pipe_unavailable"}
 *   sonst        -> 404 {"ok":false,"reason":"not_found"}
 *
 * Protokoll auf der Pipe (v0, siehe bausteine/04-trainer-io/README.md):
 *   -> {"cmd":"exec","command":C,"cmd_id":N}\n
 *   <- {"event":"exec_result","command":C,"ok":bool[,"reason":"..."]}
 *   Ein Antwort-Timeout ist KEIN Schreibfehler: das Kommando wurde gesendet,
 *   das Ergebnis traegt dann ok:false + reason "timeout".
 *   Line-delimited JSON, max. 8 KiB pro Zeile (LINE_MAX).
 *
 * Umgebung:
 *   RBB_BRIDGE_BIND        Bind-Adresse (Default 0.0.0.0)
 *   RBB_BRIDGE_PORT        TCP-Port (Default 9001)
 *   RBB_BRIDGE_PIPE        Pipe-Pfad (Default \\.\pipe\rbbattle)
 *   RBB_BRIDGE_TIMEOUT_MS  Antwort-Timeout je Kommando (Default 5000)
 *
 * Modi:
 *   pipe_bridge.exe                 HTTP-Server (Dauerbetrieb, docker log)
 *   pipe_bridge.exe --ping          Pipe-Smoke: ping -> pong, Exit 0/1
 *   pipe_bridge.exe --once "<cmd>"  ein Kommando; druckt die exec_result-Zeile
 *   pipe_bridge.exe --help          Usage
 *
 * Build (x64):
 *   MinGW-w64 : x86_64-w64-mingw32-gcc -O2 -Wall -Wextra -o pipe_bridge.exe pipe_bridge.c -lws2_32
 *   zig       : zig cc -target x86_64-windows-gnu -O2 -Wall -Wextra -o pipe_bridge.exe pipe_bridge.c -lws2_32
 *
 * Logs: jede Zeile mit Praefix "[pipe_bridge] " auf stdout (docker log).
 */

#define WIN32_LEAN_AND_MEAN
#include <winsock2.h>
#include <windows.h>

#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#include <string.h>

#define BRIDGE_NAME          "pipe_bridge"

#define DEFAULT_PIPE_NAME    "\\\\.\\pipe\\rbbattle"
#define DEFAULT_BIND         "0.0.0.0"
#define DEFAULT_PORT         9001
#define DEFAULT_TIMEOUT_MS   5000

#define LINE_MAX             8192          /* max. Protokollzeile (Pipe)      */
#define READ_BUF             (LINE_MAX * 2)
#define REQ_MAX              (64 * 1024)   /* max. HTTP-Request (inkl. Body)  */
#define RESP_MAX             (64 * 1024)   /* max. HTTP-Body                  */
#define CMD_MAX              512
#define MAX_CMDS             16
#define REASON_MAX           256

/* ------------------------------------------------------------------ */
/* Logging                                                             */
/* ------------------------------------------------------------------ */

/* Session-ID (Server-Boot) + Zeitstempel fuer Traceability (#392). */
static char g_session_id[32] = "boot";

static void init_session_id(void)
{
    SYSTEMTIME st;
    GetLocalTime(&st);
    snprintf(g_session_id, sizeof(g_session_id), "%04d%02d%02d-%02d%02d%02d",
             st.wYear, st.wMonth, st.wDay, st.wHour, st.wMinute, st.wSecond);
}

static void blog(const char *fmt, ...)
{
    char buf[1024];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    SYSTEMTIME st;
    GetLocalTime(&st);
    printf("[%02d:%02d:%02d.%03d] [%s] %s\n",
           st.wHour, st.wMinute, st.wSecond, st.wMilliseconds,
           BRIDGE_NAME, buf);
    fflush(stdout);
}

/* HTTP-Request mit vollem (trunkiertem, saniertem) Body loggen — damit ein
 * WebUI-Klick (z. B. POST /add_resource {"amount":"10"}) nachvollziehbar ist. */
static void log_request(const char *method, const char *path,
                        const char *body, int body_len)
{
    char b[160];
    int n = body_len;
    if (n < 0)
        n = 0;
    if (n > 140)
        n = 140;
    if (n > 0)
        memcpy(b, body, n);
    b[n] = '\0';
    for (int i = 0; i < n; i++)
        if (b[i] == '\n' || b[i] == '\r' || b[i] == '\t')
            b[i] = ' ';
    blog("%s %s body=%s", method, path, n > 0 ? b : "-");
}

/* ------------------------------------------------------------------ */
/* Umgebung                                                            */
/* ------------------------------------------------------------------ */

static const char *env_str(const char *name, const char *def)
{
    const char *v = getenv(name);
    return (v && *v) ? v : def;
}

static int env_int(const char *name, int def)
{
    const char *v = getenv(name);
    if (!v || !*v)
        return def;
    {
        int n = atoi(v);
        return n > 0 ? n : def;
    }
}

/* ------------------------------------------------------------------ */
/* Minimales JSON (nur die Felder, die dieser Vertrag braucht)         */
/* ------------------------------------------------------------------ */

/* Kopiert <in> nach <out> und escaped '\\' und '"' (nur das braucht der
 * Vertrag - Kommandos sind einfache ASCII-Strings). */
static void json_escape(const char *in, char *out, size_t out_sz)
{
    size_t n = 0;
    for (const char *p = in; *p && n + 1 < out_sz; p++) {
        if (*p == '\\' || *p == '"') {
            if (n + 2 < out_sz) {
                out[n++] = '\\';
                out[n++] = *p;
            }
        } else {
            out[n++] = *p;
        }
    }
    out[n] = '\0';
}

/* json_get_string: findet "key":"value" (nur String-Werte). Liefert 1 und
 * schreibt den entpackten Wert nach out; 0 wenn nicht gefunden. Bewusst
 * dieselbe Minimal-Logik wie rbbridge.c (json_get_string), damit dasselbe
 * Wire-Format gilt. */
static int json_get_string(const char *json, const char *key,
                           char *out, size_t out_sz)
{
    if (!json || !key || !out || out_sz == 0)
        return 0;

    size_t key_len = strlen(key);
    const char *p = json;

    while ((p = strstr(p, key)) != NULL) {
        if (p != json && p[-1] == '"' && p[key_len] == '"') {
            const char *q = p + key_len + 1;
            while (*q == ' ' || *q == '\t')
                q++;
            if (*q != ':')
                return 0;
            q++;
            while (*q == ' ' || *q == '\t')
                q++;
            if (*q != '"')
                return 0; /* nur String-Werte */
            q++;
            size_t n = 0;
            while (*q && *q != '"' && n + 1 < out_sz) {
                if (*q == '\\' && q[1]) {
                    q++;
                    if (*q == 'n')
                        out[n++] = '\n';
                    else if (*q == 't')
                        out[n++] = '\t';
                    else
                        out[n++] = *q; /* \\ \" \/ -> Zeichen selbst */
                } else {
                    out[n++] = *q;
                }
                q++;
            }
            out[n] = '\0';
            return *q == '"';
        }
        p += key_len;
    }
    return 0;
}

/* json_get_bool: findet "key":true|false. Liefert 1 und setzt out. */
static int json_get_bool(const char *json, const char *key, int *out)
{
    if (!json || !key || !out)
        return 0;

    size_t key_len = strlen(key);
    const char *p = json;

    while ((p = strstr(p, key)) != NULL) {
        if (p != json && p[-1] == '"' && p[key_len] == '"') {
            const char *q = p + key_len + 1;
            while (*q == ' ' || *q == '\t')
                q++;
            if (*q != ':')
                return 0;
            q++;
            while (*q == ' ' || *q == '\t')
                q++;
            if (strncmp(q, "true", 4) == 0) {
                *out = 1;
                return 1;
            }
            if (strncmp(q, "false", 5) == 0) {
                *out = 0;
                return 1;
            }
            return 0;
        }
        p += key_len;
    }
    return 0;
}

/* json_get_string_array: liest "key":["a","b",...] in items[] (je CMD_MAX).
 * Liefert die Anzahl der Elemente (max. max_items), 0 wenn das Array fehlt
 * oder leer ist. Minimal-String-Entpackung wie json_get_string. */
static int json_get_string_array(const char *json, const char *key,
                                 char items[][CMD_MAX], int max_items)
{
    if (!json || !key || !items || max_items <= 0)
        return 0;

    size_t key_len = strlen(key);
    const char *p = json;

    while ((p = strstr(p, key)) != NULL) {
        if (p != json && p[-1] == '"' && p[key_len] == '"') {
            const char *q = p + key_len + 1;
            while (*q == ' ' || *q == '\t')
                q++;
            if (*q != ':')
                return 0;
            q++;
            while (*q == ' ' || *q == '\t')
                q++;
            if (*q != '[')
                return 0;
            q++;

            int n = 0;
            for (;;) {
                while (*q == ' ' || *q == '\t' || *q == '\r' || *q == '\n')
                    q++;
                if (*q == ']' || *q == '\0')
                    break;
                if (*q == ',') {
                    q++;
                    continue;
                }
                if (*q != '"')
                    break; /* nur Strings */
                q++;
                {
                    size_t w = 0;
                    char *dst = items[n];
                    while (*q && *q != '"' && w + 1 < CMD_MAX) {
                        if (*q == '\\' && q[1]) {
                            q++;
                            if (*q == 'n')
                                dst[w++] = '\n';
                            else if (*q == 't')
                                dst[w++] = '\t';
                            else
                                dst[w++] = *q;
                        } else {
                            dst[w++] = *q;
                        }
                        q++;
                    }
                    dst[w] = '\0';
                }
                if (*q == '"')
                    q++;
                n++;
                if (n >= max_items) {
                    /* Rest (falls vorhanden) ignorieren, aber kein Overflow */
                    while (*q && *q != ']')
                        q++;
                    break;
                }
            }
            return n;
        }
        p += key_len;
    }
    return 0;
}

/* ------------------------------------------------------------------ */
/* Named-Pipe-Client (\\.\pipe\rbbattle)                                */
/* ------------------------------------------------------------------ */

/* Wandelt GetTickCount-Werte wrap-sicher in "Deadline erreicht". */
static int deadline_passed(DWORD deadline)
{
    return (LONG)(GetTickCount() - deadline) >= 0;
}

/* Verbindet mit der Pipe. Innerhalb von timeout_ms wird sowohl bei
 * ERROR_PIPE_BUSY (Server da, aber Instanz belegt) als auch bei
 * ERROR_FILE_NOT_FOUND weiterprobiert: rbbridge baut zwischen zwei Clients
 * eine NEUE Pipe-Instanz auf, so dass ein direkter Folge-Connect transient
 * FILE_NOT_FOUND liefert (live belegt: /health ok, direkt danach /exec ->
 * 503 pipe_unavailable, danach wieder ok). Jeder andere Fehler gibt sofort
 * INVALID_HANDLE_VALUE zurueck. INVALID_HANDLE_VALUE = nicht erreichbar. */
static HANDLE pipe_connect(int timeout_ms)
{
    const char *path = env_str("RBB_BRIDGE_PIPE", DEFAULT_PIPE_NAME);
    DWORD deadline = GetTickCount() + (DWORD)timeout_ms;

    for (;;) {
        HANDLE h = CreateFileA(path, GENERIC_READ | GENERIC_WRITE, 0, NULL,
                               OPEN_EXISTING, 0, NULL);
        if (h != INVALID_HANDLE_VALUE)
            return h;

        {
            DWORD err = GetLastError();
            if (err != ERROR_PIPE_BUSY && err != ERROR_FILE_NOT_FOUND)
                return INVALID_HANDLE_VALUE; /* sonstiger Fehler -> sofort */

            if (deadline_passed(deadline))
                return INVALID_HANDLE_VALUE;

            /* Busy: auf eine freie Instanz warten. Not found (Instanz wird
             * neu aufgebaut): kurz schlafen, dann erneut versuchen. */
            if (err == ERROR_PIPE_BUSY)
                WaitNamedPipeA(path, 50);
            else
                Sleep(50);
        }
    }
}

static int pipe_write_all(HANDLE h, const char *data)
{
    size_t len = strlen(data);
    size_t off = 0;
    while (off < len) {
        DWORD wr = 0;
        if (!WriteFile(h, data + off, (DWORD)(len - off), &wr, NULL) || wr == 0)
            return 0;
        off += wr;
    }
    return 1;
}

/* Liest Pipe-Zeilen (Polling via PeekNamedPipe, kein blockierendes ReadFile,
 * damit ein Timeout greift). Liefert 0 = passende Zeile gefunden (in line_out),
 * 1 = Timeout, -1 = Pipe-Fehler/geschlossen. <command> darf NULL sein (dann
 * zaehlt nur <event>). */
static int pipe_wait_line(HANDLE h, const char *event, const char *command,
                          int timeout_ms, char *line_out, size_t line_out_sz)
{
    char buf[READ_BUF];
    size_t n = 0;
    DWORD deadline = GetTickCount() + (DWORD)timeout_ms;

    for (;;) {
        DWORD avail = 0;
        if (!PeekNamedPipe(h, NULL, 0, NULL, &avail, NULL))
            return -1;

        if (avail > 0) {
            char chunk[4096];
            DWORD rd = 0;
            DWORD want = avail < (DWORD)sizeof(chunk) ? avail : (DWORD)sizeof(chunk);
            if (!ReadFile(h, chunk, want, &rd, NULL) || rd == 0)
                return -1;

            if (n + rd > sizeof(buf) - 1)
                n = 0; /* Overflow-Schutz: Rest verwerfen statt sprengen */
            memcpy(buf + n, chunk, rd);
            n += rd;

            {
                size_t start = 0;
                size_t i;
                for (i = 0; i < n; i++) {
                    if (buf[i] == '\n') {
                        char *line = buf + start;
                        size_t len;
                        buf[i] = '\0';
                        len = strlen(line);
                        while (len > 0 && line[len - 1] == '\r')
                            line[--len] = '\0';

                        {
                            char ev[64] = "";
                            int match = 0;
                            if (json_get_string(line, "event", ev, sizeof(ev))
                                && strcmp(ev, event) == 0) {
                                if (!command) {
                                    match = 1;
                                } else {
                                    char cmd[CMD_MAX] = "";
                                    if (json_get_string(line, "command", cmd,
                                                        sizeof(cmd))
                                        && strcmp(cmd, command) == 0)
                                        match = 1;
                                }
                            }
                            if (match) {
                                if (line_out && line_out_sz) {
                                    strncpy(line_out, line, line_out_sz - 1);
                                    line_out[line_out_sz - 1] = '\0';
                                }
                                return 0;
                            }
                        }
                        start = i + 1;
                    }
                }
                if (start > 0) {
                    memmove(buf, buf + start, n - start);
                    n -= start;
                }
            }
        }

        if (deadline_passed(deadline))
            return 1;
        Sleep(10);
    }
}

/* Ein Kommando ausfuehren: exec-Zeile schreiben, auf exec_result warten.
 * Rueckgabe 0 = Ergebnis da (*ok/reason gesetzt), 1 = Timeout, -1 = Pipe-Fehler. */
static int pipe_exec_one(HANDLE h, const char *command, int cmd_id,
                         int timeout_ms, int *ok, char *reason, size_t reason_sz)
{
    char esc[CMD_MAX * 2];
    char payload[LINE_MAX];
    char line[READ_BUF];
    int rc;

    json_escape(command, esc, sizeof(esc));
    snprintf(payload, sizeof(payload),
             "{\"cmd\":\"exec\",\"command\":\"%s\",\"cmd_id\":%d}\n", esc, cmd_id);

    if (!pipe_write_all(h, payload))
        return -1;

    rc = pipe_wait_line(h, "exec_result", command, timeout_ms, line, sizeof(line));
    if (rc != 0)
        return rc;

    *ok = 0;
    json_get_bool(line, "ok", ok);
    if (reason && reason_sz) {
        reason[0] = '\0';
        json_get_string(line, "reason", reason, reason_sz);
    }
    return 0;
}

/* ------------------------------------------------------------------ */
/* HTTP                                                                */
/* ------------------------------------------------------------------ */

static void http_respond(SOCKET c, int code, const char *status, const char *body)
{
    char hdr[512];
    int blen = (int)strlen(body);
    int n = snprintf(hdr, sizeof(hdr),
                     "HTTP/1.1 %d %s\r\n"
                     "Content-Type: application/json\r\n"
                     "Content-Length: %d\r\n"
                     "Connection: close\r\n\r\n",
                     code, status, blen);
    send(c, hdr, n, 0);
    if (blen > 0)
        send(c, body, blen, 0);
}

/* Text-basierte Kontrollpanel-Seite (contract.html). Wird unter GET / ausgeliefert;
 * die Seite redet per fetch() mit /get_state, /add_resource und /exec (same-origin). */
static const char CONTRACT_HTML[] =
    "<!doctype html>\n"
    "<html lang=\"en\">\n"
    "<head>\n"
    "<meta charset=\"utf-8\">\n"
    "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
    "<title>rbbridge contract</title>\n"
    "<style>\n"
    "  :root { color-scheme: dark; }\n"
    "  body { background:#0a0f0a; color:#9fef9f; font:14px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; margin:0; padding:1.25rem; }\n"
    "  h1 { font-size:1rem; margin:0 0 .75rem; }\n"
    "  a { color:#7fdfff; }\n"
    "  section { border:1px solid #2a3a2a; border-radius:4px; padding:.75rem 1rem; margin-bottom:.75rem; max-width:52rem; }\n"
    "  h2 { font-size:.85rem; margin:.25rem 0 .5rem; border-bottom:1px solid #2a3a2a; padding-bottom:.25rem; color:#cfeecf; }\n"
    "  .row { display:flex; flex-wrap:wrap; gap:.4rem; align-items:center; }\n"
    "  .val { color:#fff; }\n"
    "  .dim { color:#5f8f5f; }\n"
    "  input, button { background:#0f1a0f; color:#9fef9f; border:1px solid #3a5a3a; border-radius:3px; font:inherit; padding:.3rem .55rem; }\n"
    "  button:hover { background:#1a2a1a; cursor:pointer; }\n"
    "  button.danger { border-color:#a03a3a; color:#ef9f9f; }\n"
    "  pre { background:#060a06; border:1px solid #1a2a1a; padding:.6rem; max-height:40vh; overflow:auto; white-space:pre-wrap; }\n"
    "  .err { color:#ef9f9f; }\n"
    "</style>\n"
    "</head>\n"
    "<body>\n"
    "  <h1>rbbridge <span class=\"dim\">contract</span></h1>\n"
    "\n"
    "  <section>\n"
    "    <h2>bridge</h2>\n"
    "    <div class=\"row\">\n"
    "      <span class=\"dim\">url</span>\n"
    "      <input id=\"url\" size=\"34\" spellcheck=\"false\">\n"
    "      <button id=\"refresh\">refresh</button>\n"
    "      <span id=\"conn\" class=\"dim\"></span>\n"
    "    </div>\n"
    "  </section>\n"
    "\n"
    "  <section>\n"
    "    <h2>carbonium</h2>\n"
    "    <div class=\"row\">\n"
    "      <span class=\"dim\">now</span>\n"
    "      <span id=\"carbonium\" class=\"val\">—</span>\n"
    "      <span class=\"dim\">/ max</span>\n"
    "      <span id=\"carbonium_max\" class=\"val\">—</span>\n"
    "    </div>\n"
    "    <div class=\"row\" style=\"margin-top:.5rem\">\n"
    "      <input id=\"amount\" size=\"10\" value=\"10\">\n"
    "      <button id=\"add\">+ add</button>\n"
    "      <button id=\"sub\">− subtract</button>\n"
    "    </div>\n"
    "  </section>\n"
    "\n"
    "  <section>\n"
    "    <h2>players</h2>\n"
    "    <div class=\"row\">\n"
    "      <span class=\"dim\">connected</span>\n"
    "      <span id=\"players\" class=\"val\">—</span>\n"
    "    </div>\n"
    "  </section>\n"
    "\n"
    "  <section>\n"
    "    <h2>HQ</h2>\n"
    "    <div class=\"row\">\n"
    "      <span class=\"dim\">hp</span>\n"
    "      <span id=\"hq_hp\" class=\"val\">—</span>\n"
    "      <span class=\"dim\">/ max</span>\n"
    "      <span id=\"hq_hp_max\" class=\"val\">—</span>\n"
    "      <span class=\"dim\">dead</span>\n"
    "      <span id=\"hq_dead\" class=\"val\">—</span>\n"
    "    </div>\n"
    "  </section>\n"
    "\n"
    "  <section>\n"
    "    <h2>game</h2>\n"
    "    <div class=\"row\">\n"
    "      <button id=\"end_game\" class=\"danger\">end game</button>\n"
    "      <input id=\"cmd\" size=\"24\" placeholder=\"custom command\" spellcheck=\"false\">\n"
    "      <button id=\"exec\">exec</button>\n"
    "    </div>\n"
    "    <div id=\"exec_out\" class=\"dim\" style=\"margin-top:.5rem\"></div>\n"
    "  </section>\n"
    "\n"
    "  <pre id=\"raw\" class=\"dim\">loading…</pre>\n"
    "\n"
    "<script>\n"
    "(function () {\n"
    "  var $ = function (id) { return document.getElementById(id); };\n"
    "\n"
    "  function baseUrl() {\n"
    "    var v = $('url').value.trim();\n"
    "    if (v) return v.replace(/\\/+$/, '') + '/';\n"
    "    // Page's own directory, so it also works behind a path prefix (/contract/).\n"
    "    return new URL('.', location.href).href;\n"
    "  }\n"
    "\n"
    "  function setConn(s) { $('conn').textContent = s; }\n"
    "\n"
    "  async function post(path, body) {\n"
    "    var res = await fetch(baseUrl() + path, {\n"
    "      method: 'POST',\n"
    "      headers: { 'content-type': 'application/json' },\n"
    "      body: body === undefined ? '{}' : JSON.stringify(body)\n"
    "    });\n"
    "    return res.json();\n"
    "  }\n"
    "\n"
    "  async function refresh() {\n"
    "    try {\n"
    "      var s = await post('get_state');\n"
    "      $('raw').textContent = JSON.stringify(s, null, 2);\n"
    "      $('carbonium').textContent = (typeof s.carbonium === 'number') ? s.carbonium.toLocaleString() : String(s.carbonium);\n"
    "      $('carbonium_max').textContent = (typeof s.carbonium_max === 'number') ? s.carbonium_max.toLocaleString() : String(s.carbonium_max);\n"
    "      $('players').textContent = (s.players === null || s.players === undefined) ? '—' : s.players;\n"
    "      $('hq_hp').textContent = (s.hq_hp === null || s.hq_hp === undefined) ? '—' : s.hq_hp;\n"
    "      $('hq_hp_max').textContent = (s.hq_hp_max === null || s.hq_hp_max === undefined) ? '—' : s.hq_hp_max;\n"
    "      $('hq_dead').textContent = (s.hq_dead === null || s.hq_dead === undefined) ? '—' : String(s.hq_dead);\n"
    "      setConn('ok');\n"
    "    } catch (e) {\n"
    "      setConn('ERR ' + e.message);\n"
    "      $('raw').textContent = String(e);\n"
    "    }\n"
    "  }\n"
    "\n"
    "  async function addResource(amount) {\n"
    "    try {\n"
    "      var r = await post('add_resource', { amount: String(amount) });\n"
    "      $('exec_out').textContent = JSON.stringify(r);\n"
    "      refresh();\n"
    "    } catch (e) {\n"
    "      $('exec_out').textContent = 'ERR ' + e.message;\n"
    "    }\n"
    "  }\n"
    "\n"
    "  $('refresh').onclick = refresh;\n"
    "  $('add').onclick = function () { addResource($('amount').value); };\n"
    "  $('sub').onclick = function () {\n"
    "    var a = parseFloat($('amount').value) || 0;\n"
    "    addResource(-a);\n"
    "  };\n"
    "  $('end_game').onclick = async function () {\n"
    "    try {\n"
    "      var r = await post('exec', { command: 'end_game' });\n"
    "      $('exec_out').textContent = JSON.stringify(r);\n"
    "      refresh();\n"
    "    } catch (e) { $('exec_out').textContent = 'ERR ' + e.message; }\n"
    "  };\n"
    "  $('exec').onclick = async function () {\n"
    "    var c = $('cmd').value.trim();\n"
    "    if (!c) return;\n"
    "    try {\n"
    "      var r = await post('exec', { command: c });\n"
    "      $('exec_out').textContent = JSON.stringify(r);\n"
    "      refresh();\n"
    "    } catch (e) { $('exec_out').textContent = 'ERR ' + e.message; }\n"
    "  };\n"
    "\n"
    "  // Empty = relative to this page (works at the bridge root AND under /contract/).\n"
    "  $('url').value = '';\n"
    "\n"
    "  refresh();\n"
    "})();\n"
    "</script>\n"
    "</body>\n"
    "</html>\n";

static void http_respond_html(SOCKET c, int code, const char *status,
                              const char *body)
{
    char hdr[512];
    int blen = (int)strlen(body);
    int n = snprintf(hdr, sizeof(hdr),
                     "HTTP/1.1 %d %s\r\n"
                     "Content-Type: text/html; charset=utf-8\r\n"
                     "Content-Length: %d\r\n"
                     "Connection: close\r\n\r\n",
                     code, status, blen);
    send(c, hdr, n, 0);
    if (blen > 0)
        send(c, body, blen, 0);
}

static void handle_index(SOCKET c)
{
    http_respond_html(c, 200, "OK", CONTRACT_HTML);
}


/* Case-insensitive Suche nach <needle> in <hay> (fuer HTTP-Header). */
static const char *find_icase(const char *hay, const char *needle)
{
    size_t nl = strlen(needle);
    for (const char *p = hay; *p; p++) {
        size_t i;
        for (i = 0; i < nl; i++) {
            char a = p[i], b = needle[i];
            if (a >= 'A' && a <= 'Z')
                a = (char)(a - 'A' + 'a');
            if (b >= 'A' && b <= 'Z')
                b = (char)(b - 'A' + 'a');
            if (a != b)
                break;
        }
        if (i == nl)
            return p;
    }
    return NULL;
}

static void handle_health(SOCKET c)
{
    char body[128];
    int pipe_ok = 0;
    /* Kurzes Fenster: /health ist ein Probe-Connect, der nie lange warten darf. */
    HANDLE h = pipe_connect(500);
    if (h != INVALID_HANDLE_VALUE) {
        pipe_ok = 1;
        CloseHandle(h);
    }
    snprintf(body, sizeof(body), "{\"ok\":true,\"pipe\":%s}",
             pipe_ok ? "true" : "false");
    http_respond(c, 200, "OK", body);
}

static void handle_exec(SOCKET c, const char *body)
{
    char cmds[MAX_CMDS][CMD_MAX];
    int ncmds = 0;
    char one[CMD_MAX];
    char results[RESP_MAX];
    char resp[RESP_MAX];
    size_t off = 0;
    int all_ok = 1;
    int timeout_ms = env_int("RBB_BRIDGE_TIMEOUT_MS", DEFAULT_TIMEOUT_MS);
    HANDLE h;
    int i;

    ncmds = json_get_string_array(body, "commands", cmds, MAX_CMDS);
    if (ncmds == 0) {
        if (json_get_string(body, "command", one, sizeof(one))) {
            strncpy(cmds[0], one, CMD_MAX - 1);
            cmds[0][CMD_MAX - 1] = '\0';
            ncmds = 1;
        }
    }
    if (ncmds == 0) {
        blog("POST /exec ohne commands/command -> invalid_request");
        http_respond(c, 400, "Bad Request",
                     "{\"ok\":false,\"reason\":\"invalid_request\"}");
        return;
    }

    /* Breiteres Fenster als /health: deckt den transienten Instanz-Neuaufbau
     * von rbbridge zwischen zwei Clients ab (~2.5 s). */
    h = pipe_connect(2500);
    if (h == INVALID_HANDLE_VALUE) {
        blog("POST /exec: Pipe %s nicht erreichbar -> pipe_unavailable",
             env_str("RBB_BRIDGE_PIPE", DEFAULT_PIPE_NAME));
        http_respond(c, 503, "Service Unavailable",
                     "{\"ok\":false,\"reason\":\"pipe_unavailable\"}");
        return;
    }

    off += (size_t)snprintf(results + off, sizeof(results) - off, "[");
    for (i = 0; i < ncmds; i++) {
        int ok = 0;
        char reason[REASON_MAX] = "";
        char esc[CMD_MAX * 2];
        int rc = pipe_exec_one(h, cmds[i], i, timeout_ms, &ok, reason,
                               sizeof(reason));
        if (rc == 1) {
            ok = 0;
            strncpy(reason, "timeout", sizeof(reason) - 1);
        } else if (rc < 0) {
            ok = 0;
            strncpy(reason, "pipe_error", sizeof(reason) - 1);
        }
        if (!ok)
            all_ok = 0;

        json_escape(cmds[i], esc, sizeof(esc));
        off += (size_t)snprintf(results + off, sizeof(results) - off,
                                "%s{\"command\":\"%s\",\"ok\":%s",
                                i ? "," : "", esc, ok ? "true" : "false");
        if (!ok && reason[0]) {
            char ersc[REASON_MAX * 2];
            json_escape(reason, ersc, sizeof(ersc));
            off += (size_t)snprintf(results + off, sizeof(results) - off,
                                    ",\"reason\":\"%s\"", ersc);
        }
        off += (size_t)snprintf(results + off, sizeof(results) - off, "}");

        blog("exec cmd_id=%d command='%s' -> ok=%s%s%s", i, cmds[i],
             ok ? "true" : "false", reason[0] ? " reason=" : "",
             reason[0] ? reason : "");
    }
    off += (size_t)snprintf(results + off, sizeof(results) - off, "]");
    CloseHandle(h);

    snprintf(resp, sizeof(resp), "{\"ok\":%s,\"results\":%s}",
             all_ok ? "true" : "false", results);
    http_respond(c, 200, "OK", resp);
}

/* Liest einen Request (Header + Body) und beantwortet ihn. */

/* POST /probe: fuehrt {"cmd":"probe"} auf der Pipe aus und sammelt alle
 * Antwortzeilen (event: probe + probe_dump*) als events-Array ein. */
static void handle_probe(SOCKET c)
{
    char results[RESP_MAX];
    size_t off = 0;
    int nlines = 0;
    int timeout_ms = env_int("RBB_BRIDGE_TIMEOUT_MS", DEFAULT_TIMEOUT_MS);
    HANDLE h = pipe_connect(2500);

    if (h == INVALID_HANDLE_VALUE) {
        blog("POST /probe: Pipe nicht erreichbar -> pipe_unavailable");
        http_respond(c, 503, "Service Unavailable",
                     "{\"ok\":false,\"reason\":\"pipe_unavailable\"}");
        return;
    }

    if (!pipe_write_all(h, "{\"cmd\":\"probe\"}\n")) {
        CloseHandle(h);
        http_respond(c, 500, "Internal Server Error",
                     "{\"ok\":false,\"reason\":\"pipe_error\"}");
        return;
    }

    {
        char buf[READ_BUF];
        size_t n = 0;
        DWORD deadline = GetTickCount() + (DWORD)timeout_ms;
        int got_scan_done = 0;

        off += (size_t)snprintf(results + off, sizeof(results) - off, "[");
        for (;;) {
            DWORD avail = 0;
            if (!PeekNamedPipe(h, NULL, 0, NULL, &avail, NULL))
                break;
            if (avail > 0) {
                char chunk[4096];
                DWORD rd = 0;
                DWORD want = avail < (DWORD)sizeof(chunk) ? avail : (DWORD)sizeof(chunk);
                if (!ReadFile(h, chunk, want, &rd, NULL) || rd == 0)
                    break;
                if (n + rd > sizeof(buf) - 1)
                    n = 0;
                memcpy(buf + n, chunk, rd);
                n += rd;
                {
                    size_t start = 0;
                    size_t i;
                    for (i = 0; i < n; i++) {
                        if (buf[i] == '\n') {
                            char *line = buf + start;
                            size_t len;
                            char ev[64] = "";
                            buf[i] = '\0';
                            len = strlen(line);
                            while (len > 0 && line[len - 1] == '\r')
                                line[--len] = '\0';
                            if (json_get_string(line, "event", ev, sizeof(ev)) &&
                                (strcmp(ev, "probe") == 0 ||
                                 strcmp(ev, "probe_dump") == 0 ||
                                 strcmp(ev, "scan_hit") == 0 ||
                                 strcmp(ev, "scan_done") == 0 ||
                                 strcmp(ev, "account") == 0 ||
                                 strcmp(ev, "basket_entry") == 0 ||
                                 strcmp(ev, "error") == 0)) {
                                if (strcmp(ev, "scan_done") == 0)
                                    got_scan_done = 1;
                                if (off + len + 4 < sizeof(results)) {
                                    off += (size_t)snprintf(
                                        results + off, sizeof(results) - off,
                                        "%s%s", nlines ? "," : "", line);
                                    nlines++;
                                }
                            }
                            start = i + 1;
                        }
                    }
                    if (start > 0) {
                        memmove(buf, buf + start, n - start);
                        n -= start;
                    }
                }
            } else if (got_scan_done) {
                break;
            }
            if (deadline_passed(deadline))
                break;
            Sleep(10);
        }
        off += (size_t)snprintf(results + off, sizeof(results) - off, "]");
    }
    CloseHandle(h);

    {
        char resp[RESP_MAX];
        snprintf(resp, sizeof(resp), "{\"ok\":true,\"events\":%s}", results);
        http_respond(c, 200, "OK", resp);
    }
}

/* POST /get_state: fuehrt {"cmd":"get_state"} aus und liefert die eine
 * get_state_result-Zeile als HTTP-Body (symmetrisch zu /exec). */
static void handle_get_state(SOCKET c)
{
    char line[READ_BUF];
    int timeout_ms = env_int("RBB_BRIDGE_TIMEOUT_MS", DEFAULT_TIMEOUT_MS);
    HANDLE h = pipe_connect(2500);

    if (h == INVALID_HANDLE_VALUE) {
        blog("POST /get_state: Pipe nicht erreichbar -> pipe_unavailable");
        http_respond(c, 503, "Service Unavailable",
                     "{\"ok\":false,\"reason\":\"pipe_unavailable\"}");
        return;
    }

    if (!pipe_write_all(h, "{\"cmd\":\"get_state\"}\n")) {
        CloseHandle(h);
        http_respond(c, 500, "Internal Server Error",
                     "{\"ok\":false,\"reason\":\"pipe_error\"}");
        return;
    }

    int rc = pipe_wait_line(h, "get_state_result", NULL, timeout_ms,
                            line, sizeof(line));
    CloseHandle(h);

    if (rc != 0) {
        http_respond(c, 500, "Internal Server Error",
                     "{\"ok\":false,\"reason\":\"timeout\"}");
        return;
    }
    http_respond(c, 200, "OK", line);
}

/* POST /add_resource: fuehrt {"cmd":"add_resource","amount":"..."} auf der
 * Pipe aus und liefert die add_resource_result-Zeile (symmetrisch zu /exec).
 * amount ist ein JSON-STRING (z.B. {"amount":"-10"}), analog zu /exec. */
static void handle_add_resource(SOCKET c, const char *body)
{
    char amount[64] = "";
    char line[READ_BUF];
    char payload[LINE_MAX];
    int timeout_ms = env_int("RBB_BRIDGE_TIMEOUT_MS", DEFAULT_TIMEOUT_MS);
    HANDLE h;

    if (!json_get_string(body, "amount", amount, sizeof(amount))) {
        blog("POST /add_resource ohne amount -> invalid_request");
        http_respond(c, 400, "Bad Request",
                     "{\"ok\":false,\"reason\":\"invalid_request\"}");
        return;
    }

    h = pipe_connect(2500);
    if (h == INVALID_HANDLE_VALUE) {
        blog("POST /add_resource: Pipe nicht erreichbar -> pipe_unavailable");
        http_respond(c, 503, "Service Unavailable",
                     "{\"ok\":false,\"reason\":\"pipe_unavailable\"}");
        return;
    }

    {
        char esc[64 * 2];
        json_escape(amount, esc, sizeof(esc));
        snprintf(payload, sizeof(payload),
                 "{\"cmd\":\"add_resource\",\"amount\":\"%s\"}\n", esc);
    }

    if (!pipe_write_all(h, payload)) {
        CloseHandle(h);
        http_respond(c, 500, "Internal Server Error",
                     "{\"ok\":false,\"reason\":\"pipe_error\"}");
        return;
    }

    {
        int rc = pipe_wait_line(h, "add_resource_result", NULL, timeout_ms,
                                line, sizeof(line));
        CloseHandle(h);
        if (rc != 0) {
            http_respond(c, 500, "Internal Server Error",
                         "{\"ok\":false,\"reason\":\"timeout\"}");
            return;
        }
    }
    http_respond(c, 200, "OK", line);
}

static void handle_client(SOCKET c)
{
    char *req = malloc(REQ_MAX + 1);
    if (!req)
        return;

    int n = 0;
    int header_end = -1;
    int content_length = 0;
    int total_needed;

    while (n < REQ_MAX) {
        int rd = recv(c, req + n, REQ_MAX - n, 0);
        if (rd <= 0) {
            free(req);
            return;
        }
        n += rd;
        req[n] = '\0';
        if (header_end < 0) {
            char *p = strstr(req, "\r\n\r\n");
            if (p) {
                header_end = (int)(p - req) + 4;
                {
                    const char *cl = find_icase(req, "Content-Length:");
                    if (cl)
                        content_length = atoi(cl + strlen("Content-Length:"));
                    if (content_length < 0)
                        content_length = 0;
                }
            }
        }
        if (header_end >= 0) {
            total_needed = header_end + content_length;
            if (total_needed > REQ_MAX)
                total_needed = REQ_MAX;
            if (n >= total_needed)
                break;
        }
    }

    if (header_end < 0) {
        blog("HTTP: kein Header-Ende -> verworfen");
        free(req);
        return;
    }

    {
        char method[16] = "";
        char path[512] = "";
        const char *body = req + header_end;
        int body_len = n - header_end;

        if (body_len > content_length)
            body_len = content_length;
        if (body_len < 0)
            body_len = 0;

        /* Request-Zeile: METHOD PATH VERSION */
        if (sscanf(req, "%15s %511s", method, path) != 2) {
            free(req);
            return;
        }

        log_request(method, path, body, body_len);

        if (strcmp(method, "GET") == 0 && strcmp(path, "/health") == 0) {
            handle_health(c);
        } else if (strcmp(method, "POST") == 0 && strcmp(path, "/exec") == 0) {
            char *b = malloc((size_t)body_len + 1);
            if (!b) {
                free(req);
                return;
            }
            memcpy(b, body, (size_t)body_len);
            b[body_len] = '\0';
            handle_exec(c, b);
            free(b);
        } else if (strcmp(method, "POST") == 0 && strcmp(path, "/probe") == 0) {
            handle_probe(c);
        } else if (strcmp(method, "POST") == 0 && strcmp(path, "/get_state") == 0) {
            handle_get_state(c);
        } else if (strcmp(method, "POST") == 0 && strcmp(path, "/add_resource") == 0) {
            char *b = malloc((size_t)body_len + 1);
            if (!b) {
                free(req);
                return;
            }
            memcpy(b, body, (size_t)body_len);
            b[body_len] = '\0';
            handle_add_resource(c, b);
            free(b);
        } else if (strcmp(method, "GET") == 0 &&
                   (strcmp(path, "/") == 0 ||
                    strcmp(path, "/index.html") == 0 ||
                    strcmp(path, "/contract.html") == 0)) {
            handle_index(c);
        } else {
            http_respond(c, 404, "Not Found",
                         "{\"ok\":false,\"reason\":\"not_found\"}");
        }
    }

    free(req);
}

/* ------------------------------------------------------------------ */
/* Server-Modus                                                        */
/* ------------------------------------------------------------------ */

static int mode_server(void)
{
    init_session_id();
    blog("session start %s", g_session_id);

    const char *bind_addr = env_str("RBB_BRIDGE_BIND", DEFAULT_BIND);
    int port = env_int("RBB_BRIDGE_PORT", DEFAULT_PORT);
    WSADATA wsa;
    SOCKET srv;
    struct sockaddr_in addr;
    int on = 1;

    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) {
        blog("WSAStartup fehlgeschlagen");
        return 1;
    }

    srv = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (srv == INVALID_SOCKET) {
        blog("socket() fehlgeschlagen (%d)", WSAGetLastError());
        WSACleanup();
        return 1;
    }

    setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, (const char *)&on, sizeof(on));

    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port = htons((u_short)port);
    addr.sin_addr.s_addr = inet_addr(bind_addr);
    if (addr.sin_addr.s_addr == INADDR_NONE &&
        strcmp(bind_addr, "255.255.255.255") != 0)
        addr.sin_addr.s_addr = INADDR_ANY;

    if (bind(srv, (struct sockaddr *)&addr, sizeof(addr)) == SOCKET_ERROR) {
        blog("bind(%s:%d) fehlgeschlagen (%d)", bind_addr, port,
             WSAGetLastError());
        closesocket(srv);
        WSACleanup();
        return 1;
    }

    if (listen(srv, 16) == SOCKET_ERROR) {
        blog("listen() fehlgeschlagen (%d)", WSAGetLastError());
        closesocket(srv);
        WSACleanup();
        return 1;
    }

    blog("HTTP-Bridge lauscht auf %s:%d (Pipe %s, Timeout %d ms)",
         bind_addr, port, env_str("RBB_BRIDGE_PIPE", DEFAULT_PIPE_NAME),
         env_int("RBB_BRIDGE_TIMEOUT_MS", DEFAULT_TIMEOUT_MS));

    for (;;) {
        SOCKET c = accept(srv, NULL, NULL);
        if (c == INVALID_SOCKET) {
            blog("accept() fehlgeschlagen (%d)", WSAGetLastError());
            Sleep(100);
            continue;
        }
        handle_client(c);
        closesocket(c);
    }

    /* nicht erreichbar */
    closesocket(srv);
    WSACleanup();
    return 0;
}

/* ------------------------------------------------------------------ */
/* --ping / --once                                                      */
/* ------------------------------------------------------------------ */

static int mode_ping(void)
{
    char line[READ_BUF];
    int timeout_ms = env_int("RBB_BRIDGE_TIMEOUT_MS", DEFAULT_TIMEOUT_MS);
    HANDLE h = pipe_connect(2000);

    if (h == INVALID_HANDLE_VALUE) {
        blog("--ping: Pipe %s nicht erreichbar",
             env_str("RBB_BRIDGE_PIPE", DEFAULT_PIPE_NAME));
        return 1;
    }
    if (!pipe_write_all(h, "{\"cmd\":\"ping\"}\n")) {
        blog("--ping: Schreiben fehlgeschlagen");
        CloseHandle(h);
        return 1;
    }
    {
        int rc = pipe_wait_line(h, "pong", NULL, timeout_ms, line, sizeof(line));
        CloseHandle(h);
        if (rc == 0) {
            blog("--ping: OK (%s)", line);
            return 0;
        }
        if (rc == 1)
            blog("--ping: kein pong innerhalb %d ms", timeout_ms);
        else
            blog("--ping: Pipe-Fehler");
        return 1;
    }
}

static int mode_once(const char *command)
{
    int timeout_ms = env_int("RBB_BRIDGE_TIMEOUT_MS", DEFAULT_TIMEOUT_MS);
    int ok = 0;
    char reason[REASON_MAX] = "";
    HANDLE h = pipe_connect(timeout_ms);
    int rc;

    if (h == INVALID_HANDLE_VALUE) {
        blog("--once: pipe_unavailable");
        return 1;
    }
    rc = pipe_exec_one(h, command, 1, timeout_ms, &ok, reason, sizeof(reason));
    CloseHandle(h);

    if (rc == 0) {
        /* Eine exec_result-Zeile auf stdout (maschinenlesbar). */
        char esc[CMD_MAX * 2];
        char ersc[REASON_MAX * 2];
        json_escape(command, esc, sizeof(esc));
        json_escape(reason, ersc, sizeof(ersc));
        printf("{\"event\":\"exec_result\",\"command\":\"%s\",\"ok\":%s%s%s}\n",
               esc, ok ? "true" : "false", reason[0] ? ",\"reason\":\"" : "",
               reason[0] ? ersc : "");
        fflush(stdout);
        return ok ? 0 : 1;
    }
    if (rc == 1)
        blog("--once: timeout nach %d ms", timeout_ms);
    else
        blog("--once: Pipe-Fehler");
    return 1;
}

static void usage(void)
{
    printf("%s - HTTP(9001)->Named-Pipe-Bridge (Baustein 04, Issue #265)\n"
           "\n"
           "Aufruf:\n"
           "  %s                    HTTP-Server (Default 0.0.0.0:9001)\n"
           "  %s --ping             Pipe-Smoke-Test (ping -> pong), Exit 0/1\n"
           "  %s --once \"<cmd>\"     ein Kommando, druckt die exec_result-Zeile\n"
           "  %s --help             diese Hilfe\n"
           "\n"
           "Umgebung: RBB_BRIDGE_BIND, RBB_BRIDGE_PORT, RBB_BRIDGE_PIPE,\n"
           "          RBB_BRIDGE_TIMEOUT_MS\n",
           BRIDGE_NAME, BRIDGE_NAME, BRIDGE_NAME, BRIDGE_NAME, BRIDGE_NAME);
}

int main(int argc, char **argv)
{
    if (argc >= 2 && strcmp(argv[1], "--help") == 0) {
        usage();
        return 0;
    }
    if (argc >= 2 && strcmp(argv[1], "--ping") == 0)
        return mode_ping();
    if (argc >= 2 && strcmp(argv[1], "--once") == 0) {
        if (argc < 3) {
            blog("--once braucht ein Kommando");
            return 2;
        }
        return mode_once(argv[2]);
    }
    return mode_server();
}
