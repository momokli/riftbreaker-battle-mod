/*
 * pipe_bridge.c - HTTP(9001)->Named-Pipe-Bridge fuer den Rift-Breaker-
 *                 Dedicated-Server (Baustein 04, Issue #265).
 *
 * Rolle (Architektur, docs/INGRESS_IO.md):
 *   Der Wine-Named-Pipe \\.\pipe\rbbattle der injizierten rbbridge.dll ist
 *   ein Wine-internes Objekt: er ist NUR aus einem Windows-Prozess derselben
 *   Wine-Session erreichbar. Diese kleine Wine-x64-Konsole schliesst die
 *   Luecke: sie ist der HTTP-Endpunkt auf 127.0.0.1:9001 und uebersetzt
 *   POST-Anfragen in Pipe-Kommandos (get_state / add_resource / probe).
 *   Kein Fremd-Dep, nur Win32 (Winsock) - laeuft unter Wine.
 *
 * Endpunkte (HTTP/1.1, Antwort immer application/json, Connection: close):
 *   GET  /health       -> 200 {"ok":true,"pipe":<bool>}
 *                         (<pipe> = Pipe-Verbindung moeglich, Probe-Connect)
 *   GET  /             -> Web-UI (cockpit.html, nur C++-Direktfunktionen)
 *   POST /get_state    -> carbonium/max/resources/HQ (C++)
 *   POST /add_resource -> carbonium direkt aendern (C++)
 *   POST /activate_mission_flow -> Mission-Flow starten (C++, optionaler
 *                                 Database*-Payload via spawn_point, #386)
 *   POST /deactivate_mission_flow -> Mission-Flow/Welle beenden (C++, #389)
 *   POST /probe        -> Memory-Dump (PlayerService-Kette)
 *   sonst              -> 404 {"ok":false,"reason":"not_found"}
 *
 * Protokoll auf der Pipe (v0, siehe bausteine/rbbridge/README.md):
 *   Kommandos: ping, probe, get_state, add_resource, activate_mission_flow,
 *   deactivate_mission_flow.
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

/* json_get_number: findet "key":<zahl> ODER "key":"<zahl>" und liefert
 * 1 bei erfolgreichem Parse. Nutzt strtod; akzeptiert Integer und Float.
 * Dieselbe Minimal-Logik wie json_get_string, nur ohne Quotes-Pflicht. */
static int json_get_number(const char *json, const char *key, double *out)
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
            if (*q == ':') {
                q++;
                while (*q == ' ' || *q == '\t')
                    q++;
                if (*q == '"') /* auch als String geschickt: akzeptieren */
                    q++;
                {
                    char *end = NULL;
                    double v = strtod(q, &end);
                    if (end == q)
                        return 0; /* Zahlbeginn erwartet, hier keiner */
                    *out = v;
                    return 1;
                }
            }
            /* Key-Vorkommen ohne ':' ist kein Treffer - WEITER suchen
             * (statt sofort abzubrechen; Review-Hinweis PR #433). */
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
 * FILE_NOT_FOUND liefert (live belegt: /health ok, direkt danach ein
 * Folgeaufruf -> 503 pipe_unavailable, danach wieder ok). Jeder andere Fehler gibt sofort
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

/* Text-basierte Kontrollpanel-Seite (cockpit.html). Wird unter GET / ausgeliefert;
 * die Seite redet per fetch() mit /get_state und /add_resource (same-origin). */
#include "cockpit_html.inc"

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
    http_respond_html(c, 200, "OK", COCKPIT_HTML);
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
 * get_state_result-Zeile als HTTP-Body. */
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

/* POST /add_resource: fuehrt {"cmd":"add_resource","resource":"...",
 * "amount":"..."} auf der Pipe aus und liefert die add_resource_result-Zeile.
 * amount ist ein JSON-STRING (z.B. {"amount":"-10"}).
 * resource ist optional (Default carbonium, Backward-Compat: alte Clients
 * senden nur amount); der Anzeigename wird vom rbbridge auf den internen
 * Namen gemappt (z. B. ironium -> steel). */
static void handle_add_resource(SOCKET c, const char *body)
{
    char resource[64] = "";
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
    /* resource ist optional (Default carbonium, Backward-Compat). */
    json_get_string(body, "resource", resource, sizeof(resource));

    h = pipe_connect(2500);
    if (h == INVALID_HANDLE_VALUE) {
        blog("POST /add_resource: Pipe nicht erreichbar -> pipe_unavailable");
        http_respond(c, 503, "Service Unavailable",
                     "{\"ok\":false,\"reason\":\"pipe_unavailable\"}");
        return;
    }

    {
        char esc[64 * 2];
        char resc[64 * 2];
        json_escape(amount, esc, sizeof(esc));
        json_escape(resource, resc, sizeof(resc));
        snprintf(payload, sizeof(payload),
                 "{\"cmd\":\"add_resource\",\"resource\":\"%s\","
                 "\"amount\":\"%s\"}\n",
                 resc, esc);
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

/* POST /activate_mission_flow: fuehrt
 * {"cmd":"activate_mission_flow","logic":"...","mode":"..."} auf der
 * Pipe aus (WRITE, Issue #385) und liefert die
 * activate_mission_flow_result-Zeile. `logic` ist ein Mission-Flow-Logic-File
 * (z. B. "logic/dom/attack_level_1_entry.logic"), `mode` optional
 * (Default "default"). */
static void handle_activate_mission_flow(SOCKET c, const char *body)
{
    char logic[256] = "";
    char mode[64] = "default";
    char spawn[128] = "";
    char line[READ_BUF];
    char payload[LINE_MAX];
    char esc_logic[256 * 2];
    char esc_mode[64 * 2];
    char esc_spawn[128 * 2];
    int timeout_ms = env_int("RBB_BRIDGE_TIMEOUT_MS", DEFAULT_TIMEOUT_MS);
    HANDLE h;

    if (!json_get_string(body, "logic", logic, sizeof(logic)) || !logic[0]) {
        blog("POST /activate_mission_flow ohne logic -> invalid_request");
        http_respond(c, 400, "Bad Request",
                     "{\"ok\":false,\"reason\":\"invalid_request\"}");
        return;
    }
    json_get_string(body, "mode", mode, sizeof(mode));
    if (!mode[0])
        snprintf(mode, sizeof(mode), "default");
    /* #386: optionaler spawn_point -> rbbridge baut ein Exor::Database-
     * Payload (Default-Ctor + SetString, AOB-aufgeloest) und reicht es als
     * `data` an den Mission-Flow durch. */
    json_get_string(body, "spawn_point", spawn, sizeof(spawn));

    h = pipe_connect(2500);
    if (h == INVALID_HANDLE_VALUE) {
        blog("POST /activate_mission_flow: Pipe nicht erreichbar -> "
             "pipe_unavailable");
        http_respond(c, 503, "Service Unavailable",
                     "{\"ok\":false,\"reason\":\"pipe_unavailable\"}");
        return;
    }

    json_escape(logic, esc_logic, sizeof(esc_logic));
    json_escape(mode, esc_mode, sizeof(esc_mode));
    json_escape(spawn, esc_spawn, sizeof(esc_spawn));
    snprintf(payload, sizeof(payload),
             "{\"cmd\":\"activate_mission_flow\",\"logic\":\"%s\","
             "\"mode\":\"%s\",\"spawn_point\":\"%s\"}\n",
             esc_logic, esc_mode, esc_spawn);

    if (!pipe_write_all(h, payload)) {
        CloseHandle(h);
        http_respond(c, 500, "Internal Server Error",
                     "{\"ok\":false,\"reason\":\"pipe_error\"}");
        return;
    }

    {
        int rc = pipe_wait_line(h, "activate_mission_flow_result", NULL,
                                timeout_ms, line, sizeof(line));
        CloseHandle(h);
        if (rc != 0) {
            http_respond(c, 500, "Internal Server Error",
                         "{\"ok\":false,\"reason\":\"timeout\"}");
            return;
        }
    }
    http_respond(c, 200, "OK", line);
}

/* POST /deactivate_mission_flow: fuehrt
 * {"cmd":"deactivate_mission_flow","flow":"..."} auf der Pipe aus (WRITE,
 * Issue #389) und liefert die deactivate_mission_flow_result-Zeile.
 * `flow` ist die Flow-ID aus activate_mission_flow; fehlt sie, beendet die
 * Bridge den zuletzt gestarteten Flow (rbbridge-Default). Graceful: ohne
 * Bruecke/signatur antwortet die DLL mit ok:false, kein Crash. */
static void handle_deactivate_mission_flow(SOCKET c, const char *body)
{
    char flow[192] = "";
    char line[READ_BUF];
    char payload[LINE_MAX];
    char esc_flow[192 * 2];
    int timeout_ms = env_int("RBB_BRIDGE_TIMEOUT_MS", DEFAULT_TIMEOUT_MS);
    HANDLE h;

    /* `flow` ist optional: leer -> rbbridge nimmt g_last_flow. */
    json_get_string(body, "flow", flow, sizeof(flow));

    h = pipe_connect(2500);
    if (h == INVALID_HANDLE_VALUE) {
        blog("POST /deactivate_mission_flow: Pipe nicht erreichbar -> "
             "pipe_unavailable");
        http_respond(c, 503, "Service Unavailable",
                     "{\"ok\":false,\"reason\":\"pipe_unavailable\"}");
        return;
    }

    json_escape(flow, esc_flow, sizeof(esc_flow));
    snprintf(payload, sizeof(payload),
             "{\"cmd\":\"deactivate_mission_flow\",\"flow\":\"%s\"}\n",
             esc_flow);

    if (!pipe_write_all(h, payload)) {
        CloseHandle(h);
        http_respond(c, 500, "Internal Server Error",
                     "{\"ok\":false,\"reason\":\"pipe_error\"}");
        return;
    }

    {
        int rc = pipe_wait_line(h, "deactivate_mission_flow_result", NULL,
                                timeout_ms, line, sizeof(line));
        CloseHandle(h);
        if (rc != 0) {
            http_respond(c, 500, "Internal Server Error",
                         "{\"ok\":false,\"reason\":\"timeout\"}");
            return;
        }
    }
    http_respond(c, 200, "OK", line);
}

/* POST /creatures_difficulty: CampaignService-Kreaturen-Basis-Difficulty
 * (Read/Write, Issue #388) ueber die Pipe. Body:
 *   {"op":"set|increase|decrease","value":2.5}
 * Liefert die creatures_difficulty_result-Zeile der Bridge. */
static void handle_creatures_difficulty(SOCKET c, const char *body)
{
    char op[32] = "";
    char line[READ_BUF];
    char payload[LINE_MAX];
    char esc_op[32 * 2];
    double value = 0.0;
    int timeout_ms = env_int("RBB_BRIDGE_TIMEOUT_MS", DEFAULT_TIMEOUT_MS);
    HANDLE h;

    if (!json_get_string(body, "op", op, sizeof(op)) || !op[0]) {
        blog("POST /creatures_difficulty ohne op -> invalid_request");
        http_respond(c, 400, "Bad Request",
                     "{\"ok\":false,\"reason\":\"invalid_request\"}");
        return;
    }
    if (strcmp(op, "set") != 0 && strcmp(op, "increase") != 0 &&
        strcmp(op, "decrease") != 0) {
        blog("POST /creatures_difficulty: unbekanntes op '%s'", op);
        http_respond(c, 400, "Bad Request",
                     "{\"ok\":false,\"reason\":\"invalid_op\"}");
        return;
    }
    if (!json_get_number(body, "value", &value)) {
        blog("POST /creatures_difficulty ohne value -> invalid_request");
        http_respond(c, 400, "Bad Request",
                     "{\"ok\":false,\"reason\":\"invalid_request\"}");
        return;
    }

    h = pipe_connect(2500);
    if (h == INVALID_HANDLE_VALUE) {
        blog("POST /creatures_difficulty: Pipe nicht erreichbar -> "
             "pipe_unavailable");
        http_respond(c, 503, "Service Unavailable",
                     "{\"ok\":false,\"reason\":\"pipe_unavailable\"}");
        return;
    }

    json_escape(op, esc_op, sizeof(esc_op));
    /* value als String (rbbridge json_get_string unterstuetzt nur Strings). */
    snprintf(payload, sizeof(payload),
             "{\"cmd\":\"creatures_difficulty\",\"op\":\"%s\","
             "\"value\":\"%.6f\"}\n",
             esc_op, value);

    if (!pipe_write_all(h, payload)) {
        CloseHandle(h);
        http_respond(c, 500, "Internal Server Error",
                     "{\"ok\":false,\"reason\":\"pipe_error\"}");
        return;
    }

    {
        int rc = pipe_wait_line(h, "creatures_difficulty_result", NULL,
                                timeout_ms, line, sizeof(line));
        CloseHandle(h);
        if (rc != 0) {
            http_respond(c, 500, "Internal Server Error",
                         "{\"ok\":false,\"reason\":\"timeout\"}");
            return;
        }
    }
    http_respond(c, 200, "OK", line);
}

/* POST /natural_waves: Vanilla-Naturwellen-Schalter (Read/Write, Issue #476)
 * ueber die Pipe. Body:
 *   {"op":"status|off|on"}   (op optional, Default status)
 * Liefert die natural_waves_result-Zeile der Bridge. */
static void handle_natural_waves(SOCKET c, const char *body)
{
    char op[32] = "status";
    char line[READ_BUF];
    char payload[LINE_MAX];
    char esc_op[32 * 2];
    int timeout_ms = env_int("RBB_BRIDGE_TIMEOUT_MS", DEFAULT_TIMEOUT_MS);
    HANDLE h;

    json_get_string(body, "op", op, sizeof(op));
    if (op[0] && strcmp(op, "status") != 0 && strcmp(op, "off") != 0 &&
        strcmp(op, "on") != 0) {
        blog("POST /natural_waves: unbekanntes op '%s'", op);
        http_respond(c, 400, "Bad Request",
                     "{\"ok\":false,\"reason\":\"invalid_op\"}");
        return;
    }

    h = pipe_connect(2500);
    if (h == INVALID_HANDLE_VALUE) {
        blog("POST /natural_waves: Pipe nicht erreichbar -> "
             "pipe_unavailable");
        http_respond(c, 503, "Service Unavailable",
                     "{\"ok\":false,\"reason\":\"pipe_unavailable\"}");
        return;
    }

    json_escape(op, esc_op, sizeof(esc_op));
    snprintf(payload, sizeof(payload),
             "{\"cmd\":\"natural_waves\",\"op\":\"%s\"}\n", esc_op);

    if (!pipe_write_all(h, payload)) {
        CloseHandle(h);
        http_respond(c, 500, "Internal Server Error",
                     "{\"ok\":false,\"reason\":\"pipe_error\"}");
        return;
    }

    {
        int rc = pipe_wait_line(h, "natural_waves_result", NULL,
                                timeout_ms, line, sizeof(line));
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
        } else if (strcmp(method, "POST") == 0 && strcmp(path, "/activate_mission_flow") == 0) {
            char *b = malloc((size_t)body_len + 1);
            if (!b) {
                free(req);
                return;
            }
            memcpy(b, body, (size_t)body_len);
            b[body_len] = '\0';
            handle_activate_mission_flow(c, b);
            free(b);
        } else if (strcmp(method, "POST") == 0 && strcmp(path, "/deactivate_mission_flow") == 0) {
            char *b = malloc((size_t)body_len + 1);
            if (!b) {
                free(req);
                return;
            }
            memcpy(b, body, (size_t)body_len);
            b[body_len] = '\0';
            handle_deactivate_mission_flow(c, b);
            free(b);
        } else if (strcmp(method, "POST") == 0 && strcmp(path, "/creatures_difficulty") == 0) {
            char *b = malloc((size_t)body_len + 1);
            if (!b) {
                free(req);
                return;
            }
            memcpy(b, body, (size_t)body_len);
            b[body_len] = '\0';
            handle_creatures_difficulty(c, b);
            free(b);
        } else if (strcmp(method, "POST") == 0 && strcmp(path, "/natural_waves") == 0) {
            char *b = malloc((size_t)body_len + 1);
            if (!b) {
                free(req);
                return;
            }
            memcpy(b, body, (size_t)body_len);
            b[body_len] = '\0';
            handle_natural_waves(c, b);
            free(b);
        } else if (strcmp(method, "GET") == 0 &&
                   (strcmp(path, "/") == 0 ||
                    strcmp(path, "/index.html") == 0)) {
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
/* --ping                                                                */
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


static void usage(void)
{
    printf("%s - HTTP(9001)->Named-Pipe-Bridge (Baustein 04, Issue #265)\n"
           "\n"
           "Aufruf:\n"
           "  %s                    HTTP-Server (Default 0.0.0.0:9001)\n"
           "  %s --ping             Pipe-Smoke-Test (ping -> pong), Exit 0/1\n"
           "  %s --help             diese Hilfe\n"
           "\n"
           "Umgebung: RBB_BRIDGE_BIND, RBB_BRIDGE_PORT, RBB_BRIDGE_PIPE,\n"
           "          RBB_BRIDGE_TIMEOUT_MS\n",
           BRIDGE_NAME, BRIDGE_NAME, BRIDGE_NAME, BRIDGE_NAME);
}

int main(int argc, char **argv)
{
    if (argc >= 2 && strcmp(argv[1], "--help") == 0) {
        usage();
        return 0;
    }
    if (argc >= 2 && strcmp(argv[1], "--ping") == 0)
        return mode_ping();
    return mode_server();
}
