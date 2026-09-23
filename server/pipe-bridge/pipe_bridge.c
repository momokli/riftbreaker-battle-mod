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
 *   GET  /health       -> Readiness aus der BESTEHENDEN persistenten
 *                         Verbindung (g_pipe), KEIN Zweit-Connect (#902):
 *                         200 {"ok":true,"pipe":true}; ist keine
 *                         persistente Verbindung da 503 {"ok":false,
 *                         "pipe":false,"reason":"pipe_unavailable"}.
 *   GET  /health?deep=1 -> zusaetzlich DLL-Ping ueber dieselbe bestehende
 *                         Verbindung (pipe_send_command, kein frischer
 *                         Connect); schlaegt der fehl -> 503 {"ok":false,
 *                         "pipe":true,"ping":false,"reason":"ping_failed"}
 *   GET  /             -> Web-UI (cockpit.html, nur C++-Direktfunktionen)
 *   POST /get_state    -> carbonium/max/resources/HQ (C++)
 *   POST /add_resource -> carbonium direkt aendern (C++)
 *   POST /activate_mission_flow -> Mission-Flow starten (C++, optionaler
 *                                 Database*-Payload via spawn_point, #386)
 *   POST /deactivate_mission_flow -> Mission-Flow/Welle beenden (C++, #389)
 *   POST /end_game     -> Match nativ beenden, result=win|lose (C++, #519)
 *   POST /try_spend    -> transaktionaler Carbonium-Abzug (C++, #694);
 *                         ok:false/insufficient kommt als 200 mit Rohzeile
 *   POST /probe        -> Memory-Dump (PlayerService-Kette)
 *   sonst              -> 404 {"ok":false,"reason":"not_found"}
 *
 * Protokoll auf der Pipe (v0, siehe server/README.md):
 *   Kommandos: ping, probe, get_state, add_resource, try_spend,
 *   activate_mission_flow, deactivate_mission_flow, end_game.
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

/* Windows-freie Statuscode-/Body-Wahl fuer /health (Issue #902, host-testbar). */
#include "health_logic.h"

#define BRIDGE_NAME          "pipe_bridge"

#define DEFAULT_PIPE_NAME    "\\\\.\\pipe\\rbbattle"
#define DEFAULT_BIND         "0.0.0.0"
#define DEFAULT_PORT         9001
#define DEFAULT_TIMEOUT_MS   20000

#define LINE_MAX             8192          /* max. Protokollzeile (Pipe)      */
#define READ_BUF             (LINE_MAX * 2)
#define REQ_MAX              (64 * 1024)   /* max. HTTP-Request (inkl. Body)  */
#define RESP_MAX             (64 * 1024)   /* max. HTTP-Body                  */
#define CMD_MAX              512
#define IDENT_CAP            256           /* max. Laenge env/ref-Badge */

/* Build-Identitaet (Issue #499): ref (Commit/Tag) wird beim Build per
 * -DRBBRIDGE_REF="..." gesetzt; Default "unknown". */
#ifndef RBBRIDGE_REF
#define RBBRIDGE_REF "unknown"
#endif

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

/* rbbridge-Reply-Body loggen (trunkiert + saniert, analog log_request),
 * damit ein WebUI-Klick / get_state-Poll end-to-end nachvollziehbar ist. */
static void log_response(const char *path, const char *body)
{
    char b[160];
    int n = 0;
    if (body) {
        size_t len = strlen(body);
        n = (len > 140) ? 140 : (int)len;
    }
    if (n > 0)
        memcpy(b, body, (size_t)n);
    b[n] = '\0';
    for (int i = 0; i < n; i++)
        if (b[i] == '\n' || b[i] == '\r' || b[i] == '\t')
            b[i] = ' ';
    blog("resp %s body=%s", path, n > 0 ? b : "-");
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
                    if (*q == 'n') {
                        out[n++] = '\n';
                    } else if (*q == 't') {
                        out[n++] = '\t';
                    } else if (*q == 'u' && q[1] && q[2] && q[3] && q[4]) {
                        unsigned int cp = 0;
                        int k, ok = 1;
                        for (k = 1; k <= 4; k++) {
                            char hc = q[k];
                            unsigned int d;
                            if (hc >= '0' && hc <= '9')
                                d = (unsigned int)(hc - '0');
                            else if (hc >= 'a' && hc <= 'f')
                                d = (unsigned int)(hc - 'a' + 10);
                            else if (hc >= 'A' && hc <= 'F')
                                d = (unsigned int)(hc - 'A' + 10);
                            else {
                                ok = 0;
                                break;
                            }
                            cp = (cp << 4) | d;
                        }
                        if (ok) {
                            if (cp < 0x80 && n + 1 < out_sz) {
                                out[n++] = (char)cp;
                            } else if (cp < 0x800 && n + 2 < out_sz) {
                                out[n++] = (char)(0xC0 | (cp >> 6));
                                out[n++] = (char)(0x80 | (cp & 0x3F));
                            } else if (n + 3 < out_sz) {
                                out[n++] = (char)(0xE0 | (cp >> 12));
                                out[n++] = (char)(0x80 | ((cp >> 6) & 0x3F));
                                out[n++] = (char)(0x80 | (cp & 0x3F));
                            }
                            q += 4;
                        } else {
                            out[n++] = 'u';
                        }
                    } else {
                        out[n++] = *q;
                    }
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
/* Persistente Pipe (#636): ein Reader-Thread haelt die Verbindung     */
/* offen, liest Events (player_chat) + Responses und routed sie.       */
/* HTTP-Handler schreiben Befehle auf dieselbe Verbindung und warten   */
/* auf die Antwort. Dadurch entfaellt das open/write/read/close pro    */
/* Request und der Chat wird serverseitig verarbeitet (Deduct ohne     */
/* Browser im Pfad).                                                   */
/* ------------------------------------------------------------------ */

static HANDLE g_pipe = INVALID_HANDLE_VALUE; /* persistente Verbindung */
static CRITICAL_SECTION g_pipe_cs;           /* schuetzt Writes */
static CRITICAL_SECTION g_resp_cs;           /* schuetzt pending-Response */
static HANDLE g_resp_ev = NULL;              /* auto-reset: Antwort da */
static char g_resp_expect[64];               /* erwarteter event-Name */
static char g_resp_line[READ_BUF];           /* gematchte Antwort-Zeile */
static int g_resp_ok = 0;                    /* 1 = Antwort da */

static CRITICAL_SECTION g_cmd_cs;           /* serialisiert HTTP-Commands */
static SOCKET g_sse_sock = INVALID_SOCKET;  /* der eine SSE-Client */
static CRITICAL_SECTION g_sse_cs;           /* schuetzt den SSE-Client */

/* Attack-Cycle-Status (PoC): der Sidecar POSTet seinen /status-JSON hierher;
 * die WebUI pollt ihn via GET /attack_status. Kleiner Puffer + CS. */
static char g_attack_status[8192];
static CRITICAL_SECTION g_attack_status_cs;
/* Attack-Cycle-Intervall (Sekunden), via WebUI konfigurierbar
 * (POST /attack_interval). Default 420 = 7 min. */
static int g_attack_interval_s = 420;
static CRITICAL_SECTION g_attack_interval_cs;
/* Difficulty-Escalation (Sekunden), via WebUI konfigurierbar
 * (POST /difficulty_interval): erster Schritt 1→2 Default 200 s,
 * Folge-Schritte 2→3 … 8→9 Default 600 s (Base-Game-Kurve, §3.1). */
static int g_difficulty_interval_first_s = 200;
static CRITICAL_SECTION g_difficulty_interval_first_cs;
static int g_difficulty_interval_subsequent_s = 600;
static CRITICAL_SECTION g_difficulty_interval_subsequent_cs;
/* Attack-Cycle-Reset-Epoch (via POST /attack_reset {"reset":1}). */
static int g_attack_reset_epoch = 0;
static CRITICAL_SECTION g_attack_reset_cs;

/* Round-Reset-Epoch (via POST /round_reset, Issue #854): der Sidecar wendet
 * reset()+start atomar an (neue Runde, auch aus GAME_OVER). */
static int g_round_reset_epoch = 0;
static CRITICAL_SECTION g_round_reset_cs;

/* Personas (Send-Profile) + Self-Send (Issue #788), via WebUI editierbar.
 * g_personas = roher JSON-Object-String {"name":[[level,...],...],...};
 * mit 4 Default-Personas vorbelegt (PLATZHALTER-Werte, runtime editierbar).
 * g_active_persona = aktiver Name ("" = none). Der Toggle `send_yourself`
 * lebt seit #851 ausschliesslich in `game_config` (g_game_config). */
static char g_personas[8192] =
    "{\"aggro\":[[0,0,1,0,1,0,0,0,0],[0,0,0,0,0,0,1,0,0],[0,0,0,0,0,0,0,0,2],[0,0,0,0,0,0,0,0,0],[0,0,0,0,0,0,0,0,0],[0,0,0,0,0,0,0,0,0],[0,0,0,0,0,0,0,0,0],[0,0,0,0,0,0,0,0,0],[0,0,0,0,0,0,0,0,0],[0,0,0,0,0,0,0,0,0]],\"ruhig\":[[0,0,0,0,0,0,0,0,0],[0,1,0,0,0,0,0,0,0],[0,0,0,0,0,0,0,0,0],[0,1,0,0,0,0,0,0,0],[0,0,0,0,0,0,0,0,0],[0,0,0,0,0,0,0,0,0],[0,0,0,0,0,0,0,0,0],[0,0,0,0,0,0,0,0,0],[0,0,0,0,0,0,0,0,0],[0,0,0,0,0,0,0,0,0]],\"build\":[[1,0,2,0,1,0,0,0,0],[3,0,0,0,0,0,0,0,0],[0,1,0,2,0,0,1,0,0],[0,1,0,0,0,2,0,0,0],[0,0,0,0,0,0,0,0,0],[0,0,0,0,0,0,0,0,0],[0,0,0,0,0,0,0,0,0],[0,0,0,0,0,0,0,0,0],[0,0,0,0,0,0,0,0,0],[0,0,0,0,0,0,0,0,0]],\"zerg\":[[3,0,0,0,0,0,0,0,0],[3,0,0,0,0,0,0,0,0],[0,2,0,0,0,0,0,0,0],[0,0,2,0,0,0,0,0,0],[0,0,0,0,0,0,0,0,0],[0,0,0,0,0,0,0,0,0],[0,0,0,0,0,0,0,0,0],[0,0,0,0,0,0,0,0,0],[0,0,0,0,0,0,0,0,0],[0,0,0,0,0,0,0,0,0]],\"matheo\":[[1,0,0,0,0,0,0,0,0],[0,5,0,0,0,0,0,0,0],[0,0,0,0,0,0,0,0,0],[0,0,0,0,0,0,0,0,0],[0,1,1,1,0,1,0,0,0],[0,0,0,0,0,0,0,0,0],[0,0,0,0,0,0,0,0,0],[0,0,0,0,0,0,0,0,0],[0,0,0,0,0,0,0,0,0],[0,0,0,0,0,0,0,0,0]]}";
static CRITICAL_SECTION g_personas_cs;
static char g_active_persona[64];
static CRITICAL_SECTION g_active_persona_cs;

/* Natural-Attack-Rules (#819): die vollstaendig konfigurierbaren Dimensionen
 * der Natural-Attacks-Engine (Attack-Count/Boss je Level, Creature-Attack-
 * Events, Event-Offset). roher JSON-Object-String, via WebUI editierbar.
 * Default = Base-Game "normal"-Profil (docs/DOM_REPLICA.md §2/§3.3) + die
 * Creature-Attack-Bänder (docs/research/798-event-level.md §4.2). */
static char g_natural_attack_rules[8192] =
    "{\"max_attack_count\":[1,2,2,2,2,2,3,3,3],\"boss_min_level\":5,"
    "\"creature_events\":["
    "{\"name\":\"shegret_attack\",\"logic\":\"logic/event/shegret_attack.logic\",\"min_level\":2,\"max_level\":4,\"attack_strength\":\"normal\",\"weight\":3},"
    "{\"name\":\"shegret_attack\",\"logic\":\"logic/event/shegret_attack.logic\",\"min_level\":5,\"max_level\":7,\"attack_strength\":\"hard\",\"weight\":3},"
    "{\"name\":\"shegret_attack\",\"logic\":\"logic/event/shegret_attack.logic\",\"min_level\":8,\"max_level\":9,\"attack_strength\":\"very_hard\",\"weight\":3},"
    "{\"name\":\"kermon_attack\",\"logic\":\"logic/event/kermon_attack.logic\",\"min_level\":4,\"max_level\":5,\"attack_strength\":\"normal\",\"weight\":1},"
    "{\"name\":\"kermon_attack\",\"logic\":\"logic/event/kermon_attack.logic\",\"min_level\":6,\"max_level\":7,\"attack_strength\":\"hard\",\"weight\":1},"
    "{\"name\":\"kermon_attack\",\"logic\":\"logic/event/kermon_attack.logic\",\"min_level\":8,\"max_level\":9,\"attack_strength\":\"very_hard\",\"weight\":1},"
    "{\"name\":\"phirian_attack\",\"logic\":\"logic/event/phirian_attack.logic\",\"min_level\":3,\"max_level\":9,\"attack_strength\":null,\"weight\":1}"
    "],\"event_offset_fraction\":0.35}";
static CRITICAL_SECTION g_natural_attack_rules_cs;

/* Game-Config (#828, docs/GAME_FLOW.md): modus-unabhaengige Game-Flow-Konfig
 * (mode, Warmup-Dauer und die vier Sende-Toggles) als roher JSON-Object-String,
 * via WebUI editierbar. Default = Bootzustand SOLO. */
static char g_game_config[4096] =
    "{\"mode\":\"solo\",\"warmup_s\":120,\"natural\":true,\"persona\":true,"
    "\"send_yourself\":true,\"send_enemy\":false}";
static CRITICAL_SECTION g_game_config_cs;

/* Ready-Flag (#828): der Referee wartet, bis alle Spieler ready sind, bevor
 * er /start feuert (heute Cockpit-Klick, spaeter /ready im Chat). */
static int g_ready = 0;
static CRITICAL_SECTION g_ready_cs;

/* Start-Signal (#828): POST /start inkrementiert diesen Zaehler; der attack_cycle
 * pollt ihn via GET /game_config (`start_epoch`) und startet bei einem neuen
 * Wert (Edge). So ist das Start-Signal LESBAR, nicht nur ein Ack. */
static int g_start_epoch = 0;
static CRITICAL_SECTION g_start_epoch_cs;

/* Broadcastet eine JSON-Zeile als SSE-Event an den (einen) Cockpit-Client. */
static void sse_broadcast(const char *line)
{
    if (!line)
        return;
    EnterCriticalSection(&g_sse_cs);
    if (g_sse_sock != INVALID_SOCKET) {
        char frame[LINE_MAX + 64];
        int n = snprintf(frame, sizeof(frame), "data: %s\n\n", line);
        if (n > 0)
            send(g_sse_sock, frame, n, 0);
    }
    LeaveCriticalSection(&g_sse_cs);
}


/* Route eine Pipe-Zeile: Events -> Server-Handling, Responses -> Waiter. */
static void route_pipe_line(HANDLE h, const char *line)
{
    char ev[64] = "";
    (void)h;
    if (!json_get_string(line, "event", ev, sizeof(ev)))
        return;

    if (strcmp(ev, "player_chat") == 0) {
        char text[256] = "";
        json_get_string(line, "text", text, sizeof(text));
        blog("player_chat: %.120s", text);
        sse_broadcast(line);
        return;
    }

    /* Response-Zeile: an einen wartenden HTTP-Handler liefern. */
    EnterCriticalSection(&g_resp_cs);
    if (!g_resp_ok && strcmp(ev, g_resp_expect) == 0) {
        strncpy(g_resp_line, line, sizeof(g_resp_line) - 1);
        g_resp_line[sizeof(g_resp_line) - 1] = '\0';
        g_resp_ok = 1;
        SetEvent(g_resp_ev);
    }
    LeaveCriticalSection(&g_resp_cs);
}

/* Reader-Thread: haelt die Pipe offen und liest/routed kontinuierlich. */
static DWORD WINAPI pipe_reader_main(LPVOID arg)
{
    char buf[READ_BUF];
    size_t n = 0;
    HANDLE h = INVALID_HANDLE_VALUE;

    (void)arg;

    for (;;) {
        if (h == INVALID_HANDLE_VALUE) {
            h = pipe_connect(2500);
            if (h == INVALID_HANDLE_VALUE) {
                Sleep(1000);
                continue;
            }
            EnterCriticalSection(&g_pipe_cs);
            g_pipe = h;
            LeaveCriticalSection(&g_pipe_cs);
            blog("pipe_reader: Pipe verbunden");
        }

        {
            DWORD avail = 0;
            if (!PeekNamedPipe(h, NULL, 0, NULL, &avail, NULL)) {
                EnterCriticalSection(&g_pipe_cs);
                if (g_pipe == h)
                    g_pipe = INVALID_HANDLE_VALUE;
                LeaveCriticalSection(&g_pipe_cs);
                CloseHandle(h);
                h = INVALID_HANDLE_VALUE;
                blog("pipe_reader: Pipe getrennt, reconnect");
                Sleep(500);
                continue;
            }
            if (avail > 0) {
                char chunk[4096];
                DWORD rd = 0;
                DWORD want = avail < (DWORD)sizeof(chunk) ? avail : (DWORD)sizeof(chunk);
                if (!ReadFile(h, chunk, want, &rd, NULL) || rd == 0) {
                    EnterCriticalSection(&g_pipe_cs);
                    if (g_pipe == h)
                        g_pipe = INVALID_HANDLE_VALUE;
                    LeaveCriticalSection(&g_pipe_cs);
                    CloseHandle(h);
                    h = INVALID_HANDLE_VALUE;
                    blog("pipe_reader: ReadFile-Fehler, reconnect");
                    Sleep(500);
                    continue;
                }
                if (n + rd > sizeof(buf) - 1)
                    n = 0;
                memcpy(buf + n, chunk, rd);
                n += rd;
                {
                    size_t start = 0;
                    size_t i;
                    for (i = 0; i < n; i++) {
                        if (buf[i] == '\n') {
                            char *ln = buf + start;
                            size_t len;
                            buf[i] = '\0';
                            len = strlen(ln);
                            while (len > 0 && ln[len - 1] == '\r')
                                ln[--len] = '\0';
                            if (len > 0)
                                route_pipe_line(h, ln);
                            start = i + 1;
                        }
                    }
                    if (start > 0) {
                        memmove(buf, buf + start, n - start);
                        n -= start;
                    }
                }
            } else {
                Sleep(10);
            }
        }
    }
    return 0;
}

/* Schreibt einen Befehl auf die persistente Pipe und wartet auf die
 * Antwort (event-Name). Rueckgabe 0 = Antwort in line_out, 1 = Timeout,
 * -1 = Pipe nicht erreichbar. */
static int pipe_send_command(const char *event, const char *payload,
                             int timeout_ms, char *line_out, size_t line_out_sz)
{
    int w;

    /* Serialisierung: der HTTP-Server ist jetzt multi-threaded (PR B), aber
     * Pipe + pending-Response-Slot sind single. */
    EnterCriticalSection(&g_cmd_cs);

    if (g_pipe == INVALID_HANDLE_VALUE) {
        LeaveCriticalSection(&g_cmd_cs);
        return -1;
    }

    EnterCriticalSection(&g_resp_cs);
    strncpy(g_resp_expect, event, sizeof(g_resp_expect) - 1);
    g_resp_expect[sizeof(g_resp_expect) - 1] = '\0';
    g_resp_ok = 0;
    ResetEvent(g_resp_ev);
    LeaveCriticalSection(&g_resp_cs);

    EnterCriticalSection(&g_pipe_cs);
    w = pipe_write_all(g_pipe, payload);
    LeaveCriticalSection(&g_pipe_cs);
    if (!w) {
        LeaveCriticalSection(&g_cmd_cs);
        return -1;
    }

    if (WaitForSingleObject(g_resp_ev, (DWORD)timeout_ms) != WAIT_OBJECT_0) {
        LeaveCriticalSection(&g_cmd_cs);
        return 1;
    }

    EnterCriticalSection(&g_resp_cs);
    if (!g_resp_ok) {
        LeaveCriticalSection(&g_resp_cs);
        LeaveCriticalSection(&g_cmd_cs);
        return 1;
    }
    strncpy(line_out, g_resp_line, line_out_sz - 1);
    line_out[line_out_sz - 1] = '\0';
    LeaveCriticalSection(&g_resp_cs);
    LeaveCriticalSection(&g_cmd_cs);
    return 0;
}

/* Readiness der Pipe aus der BESTEHENDEN persistenten Verbindung (#902).
 * Liest g_pipe unter g_pipe_cs und liefert 1, wenn ein gueltiges Handle da
 * ist, sonst 0. Es wird bewusst KEIN neuer Connect geoeffnet: rbbridge.dll
 * erzeugt die Pipe mit nMaxInstances=1, der pipe_reader-Thread haelt die
 * einzige Instanz -> jeder zusaetzliche Connect traefe ERROR_PIPE_BUSY und
 * meldete die Pipe faelschlich als tot. 0/1 zurueck. */
static int pipe_ready(void)
{
    int ok;
    EnterCriticalSection(&g_pipe_cs);
    ok = (g_pipe != INVALID_HANDLE_VALUE);
    LeaveCriticalSection(&g_pipe_cs);
    return ok ? 1 : 0;
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
    static const char placeholder[] = "__RBB_IDENTITY__";
    const char *env = env_str("RBB_ENV", "unknown");
    const char *ref = env_str("RBB_REF", "unknown");
    const char *pos = strstr(COCKPIT_HTML, placeholder);

    if (!pos) {
        /* Platzhalter fehlt (z. B. veraltete .inc) -> Seite unveraendert. */
        http_respond_html(c, 200, "OK", COCKPIT_HTML);
        return;
    }

    char identity[IDENT_CAP];
    snprintf(identity, sizeof(identity), "%s · %s", env, ref);

    size_t prefix = (size_t)(pos - COCKPIT_HTML);
    size_t ilen = strlen(identity);
    size_t slen = strlen(pos + sizeof(placeholder) - 1);

    /* COCKPIT_HTML ist const -> kein In-Place-Edit, sondern frischer Puffer. */
    char *buf = malloc(strlen(COCKPIT_HTML) + IDENT_CAP + 1);
    if (!buf) {
        http_respond_html(c, 200, "OK", COCKPIT_HTML);
        return;
    }

    memcpy(buf, COCKPIT_HTML, prefix);
    memcpy(buf + prefix, identity, ilen);
    memcpy(buf + prefix + ilen, pos + sizeof(placeholder) - 1, slen + 1);
    http_respond_html(c, 200, "OK", buf);
    free(buf);
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

/* GET /health[?deep=1]: Readiness aus der BESTEHENDEN persistenten
 * Verbindung (#902), KEIN Zweit-Connect mehr. Ist eine Verbindung da,
 * gilt die Pipe als bereit; der optionale Deep-Ping laeuft ueber dieselbe
 * Verbindung (pipe_send_command). Statuscode/Body kommen aus health_logic.h
 * - ehrlich statt "immer 200". Bei pipe_ok=1 bleibt der Default-Body
 * bitgleich zu vorher. */
static void handle_health(SOCKET c, int deep)
{
    char body[192];
    char line[READ_BUF];
    int pipe_ok;
    int ping_ok = 0;
    int code;

    /* Readiness aus der persistenten Verbindung: kein frischer Connect, sonst
     * ERROR_PIPE_BUSY gegen die eine DLL-Pipe-Instanz (nMaxInstances=1). */
    pipe_ok = pipe_ready();

    /* Deep-Ping ueber die BESTEHENDE Verbindung (kein neuer Connect).
     * pipe_send_command schreibt auf g_pipe und wartet auf die pong-Zeile. */
    if (deep && pipe_ok)
        ping_ok = (pipe_send_command("pong", "{\"cmd\":\"ping\"}\n",
                                     2000, line, sizeof(line)) == 0);

    code = deep ? bridge_health_status_deep(pipe_ok, ping_ok)
                : bridge_health_status(pipe_ok);
    bridge_health_body(pipe_ok, ping_ok, deep, body, sizeof(body));
    http_respond(c, code, code == 200 ? "OK" : "Service Unavailable", body);
}


/* Liest einen Request (Header + Body) und beantwortet ihn. */

/* POST /probe: fuehrt {"cmd":"probe"} ueber die persistente Pipe aus und
 * liefert die eine probe_result-Zeile (single-line, #653). */
static void handle_probe(SOCKET c)
{
    char line[READ_BUF];
    int timeout_ms = env_int("RBB_BRIDGE_TIMEOUT_MS", DEFAULT_TIMEOUT_MS);

    int rc = pipe_send_command("probe_result", "{\"cmd\":\"probe\"}\n",
                               timeout_ms, line, sizeof(line));
    if (rc == -1) {
        blog("POST /probe: Pipe nicht erreichbar -> pipe_unavailable");
        http_respond(c, 503, "Service Unavailable",
                     "{\"ok\":false,\"reason\":\"pipe_unavailable\"}");
        return;
    }
    if (rc != 0) {
        http_respond(c, 500, "Internal Server Error",
                     "{\"ok\":false,\"reason\":\"timeout\"}");
        return;
    }
    log_response("/probe", line);
    http_respond(c, 200, "OK", line);
}


/* POST /get_state: fuehrt {"cmd":"get_state"} ueber die persistente Pipe
 * aus und liefert die eine get_state_result-Zeile (reiner Snapshot). */
static void handle_get_state(SOCKET c)
{
    char line[READ_BUF];
    int timeout_ms = env_int("RBB_BRIDGE_TIMEOUT_MS", DEFAULT_TIMEOUT_MS);

    int rc = pipe_send_command("get_state_result", "{\"cmd\":\"get_state\"}\n",
                               timeout_ms, line, sizeof(line));
    if (rc == -1) {
        blog("POST /get_state: Pipe nicht erreichbar -> pipe_unavailable");
        http_respond(c, 503, "Service Unavailable",
                     "{\"ok\":false,\"reason\":\"pipe_unavailable\"}");
        return;
    }
    if (rc != 0) {
        http_respond(c, 500, "Internal Server Error",
                     "{\"ok\":false,\"reason\":\"timeout\"}");
        return;
    }
    log_response("/get_state", line);
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

    if (!json_get_string(body, "amount", amount, sizeof(amount))) {
        blog("POST /add_resource ohne amount -> invalid_request");
        http_respond(c, 400, "Bad Request",
                     "{\"ok\":false,\"reason\":\"invalid_request\"}");
        return;
    }
    /* resource ist optional (Default carbonium, Backward-Compat). */
    json_get_string(body, "resource", resource, sizeof(resource));

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

    {
        int rc = pipe_send_command("add_resource_result", payload, timeout_ms,
                                   line, sizeof(line));
        if (rc == -1) {
            blog("POST /add_resource: Pipe nicht erreichbar -> pipe_unavailable");
            http_respond(c, 503, "Service Unavailable",
                         "{\"ok\":false,\"reason\":\"pipe_unavailable\"}");
            return;
        }
        if (rc != 0) {
            http_respond(c, 500, "Internal Server Error",
                         "{\"ok\":false,\"reason\":\"timeout\"}");
            return;
        }
    }
    log_response("/add_resource", line);
    http_respond(c, 200, "OK", line);
}

/* POST /try_spend: fuehrt {"cmd":"try_spend","amount":"..."} auf der Pipe
 * aus (transaktionaler Carbonium-Abzug, Issue #694) und liefert die
 * try_spend_result-Zeile. amount ist ein JSON-STRING (Display-Einheiten).
 * ok:false + reason (z. B. "insufficient") kommt als 200 mit der Rohzeile; nur
 * Transport-/Validierungsfehler (pipe weg, fehlender amount) werden auf 4xx/5xx
 * gemappt. Der Attack-Cycle-Sidecar bezahlt darueber Sofort-Kaeufe. */
static void handle_try_spend(SOCKET c, const char *body)
{
    char amount[64] = "";
    char line[READ_BUF];
    char payload[LINE_MAX];
    int timeout_ms = env_int("RBB_BRIDGE_TIMEOUT_MS", DEFAULT_TIMEOUT_MS);

    if (!json_get_string(body, "amount", amount, sizeof(amount)) || !amount[0]) {
        blog("POST /try_spend ohne amount -> invalid_request");
        http_respond(c, 400, "Bad Request",
                     "{\"ok\":false,\"reason\":\"invalid_request\"}");
        return;
    }

    {
        char esc[64 * 2];
        json_escape(amount, esc, sizeof(esc));
        snprintf(payload, sizeof(payload),
                 "{\"cmd\":\"try_spend\",\"amount\":\"%s\"}\n", esc);
    }

    {
        int rc = pipe_send_command("try_spend_result", payload, timeout_ms,
                                   line, sizeof(line));
        if (rc == -1) {
            blog("POST /try_spend: Pipe nicht erreichbar -> pipe_unavailable");
            http_respond(c, 503, "Service Unavailable",
                         "{\"ok\":false,\"reason\":\"pipe_unavailable\"}");
            return;
        }
        if (rc != 0) {
            http_respond(c, 500, "Internal Server Error",
                         "{\"ok\":false,\"reason\":\"timeout\"}");
            return;
        }
    }
    log_response("/try_spend", line);
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
    char strength[32] = "";
    char line[READ_BUF];
    char payload[LINE_MAX];
    char esc_logic[256 * 2];
    char esc_mode[64 * 2];
    char esc_spawn[128 * 2];
    char esc_strength[32 * 2];
    int timeout_ms = env_int("RBB_BRIDGE_TIMEOUT_MS", DEFAULT_TIMEOUT_MS);

    if (!json_get_string(body, "logic", logic, sizeof(logic)) || !logic[0]) {
        blog("POST /activate_mission_flow ohne logic -> invalid_request");
        http_respond(c, 400, "Bad Request",
                     "{\"ok\":false,\"reason\":\"invalid_request\"}");
        return;
    }
    json_get_string(body, "mode", mode, sizeof(mode));
    if (!mode[0])
        snprintf(mode, sizeof(mode), "default");
    /* #386/#814: optionale Binding-Felder -> rbbridge baut ein Exor::Database-
     * Payload (Default-Ctor + SetString, AOB-aufgeloest) und reicht es als
     * `data` an den Mission-Flow durch. `attack_strength` steuert den
     * Creature-Attack-Event-Flow (normal/hard/very_hard). */
    json_get_string(body, "spawn_point", spawn, sizeof(spawn));
    json_get_string(body, "attack_strength", strength, sizeof(strength));

    json_escape(logic, esc_logic, sizeof(esc_logic));
    json_escape(mode, esc_mode, sizeof(esc_mode));
    json_escape(spawn, esc_spawn, sizeof(esc_spawn));
    json_escape(strength, esc_strength, sizeof(esc_strength));
    snprintf(payload, sizeof(payload),
             "{\"cmd\":\"activate_mission_flow\",\"logic\":\"%s\","
             "\"mode\":\"%s\",\"spawn_point\":\"%s\","
             "\"attack_strength\":\"%s\"}\n",
             esc_logic, esc_mode, esc_spawn, esc_strength);

    {
        int rc = pipe_send_command("activate_mission_flow_result", payload,
                                   timeout_ms, line, sizeof(line));
        if (rc == -1) {
            blog("POST /activate_mission_flow: Pipe nicht erreichbar -> "
                 "pipe_unavailable");
            http_respond(c, 503, "Service Unavailable",
                         "{\"ok\":false,\"reason\":\"pipe_unavailable\"}");
            return;
        }
        if (rc != 0) {
            http_respond(c, 500, "Internal Server Error",
                         "{\"ok\":false,\"reason\":\"timeout\"}");
            return;
        }
    }
    log_response("/activate_mission_flow", line);
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

    /* `flow` ist optional: leer -> rbbridge nimmt g_last_flow. */
    json_get_string(body, "flow", flow, sizeof(flow));

    json_escape(flow, esc_flow, sizeof(esc_flow));
    snprintf(payload, sizeof(payload),
             "{\"cmd\":\"deactivate_mission_flow\",\"flow\":\"%s\"}\n",
             esc_flow);

    {
        int rc = pipe_send_command("deactivate_mission_flow_result", payload,
                                   timeout_ms, line, sizeof(line));
        if (rc == -1) {
            blog("POST /deactivate_mission_flow: Pipe nicht erreichbar -> "
                 "pipe_unavailable");
            http_respond(c, 503, "Service Unavailable",
                         "{\"ok\":false,\"reason\":\"pipe_unavailable\"}");
            return;
        }
        if (rc != 0) {
            http_respond(c, 500, "Internal Server Error",
                         "{\"ok\":false,\"reason\":\"timeout\"}");
            return;
        }
    }
    log_response("/deactivate_mission_flow", line);
    http_respond(c, 200, "OK", line);
}

/* POST /end_game: fuehrt {"cmd":"end_game","result":"win|lose"} auf der
 * Pipe aus (WRITE, Issue #519) und liefert die end_game_result-Zeile.
 * `result` ist Pflicht; nur "win"/"lose" werden akzeptiert (rbbridge
 * lehnt alles andere mit ok:false, reason=bad_result ab). Die DLL ruft
 * nativ MissionService::FinishCurrentMission (kein Lua/Console); ohne
 * Bruecke/Signatur antwortet sie mit ok:false, kein Crash. */
static void handle_end_game(SOCKET c, const char *body)
{
    char result[16] = "";
    char line[READ_BUF];
    char payload[LINE_MAX];
    char esc_result[16 * 2];
    int timeout_ms = env_int("RBB_BRIDGE_TIMEOUT_MS", DEFAULT_TIMEOUT_MS);

    if (!json_get_string(body, "result", result, sizeof(result)) ||
        !result[0]) {
        blog("POST /end_game ohne result -> invalid_request");
        http_respond(c, 400, "Bad Request",
                     "{\"ok\":false,\"reason\":\"invalid_request\"}");
        return;
    }
    if (strcmp(result, "win") != 0 && strcmp(result, "lose") != 0) {
        blog("POST /end_game: unbekanntes result '%s'", result);
        http_respond(c, 400, "Bad Request",
                     "{\"ok\":false,\"reason\":\"invalid_result\"}");
        return;
    }

    json_escape(result, esc_result, sizeof(esc_result));
    snprintf(payload, sizeof(payload),
             "{\"cmd\":\"end_game\",\"result\":\"%s\"}\n",
             esc_result);

    {
        int rc = pipe_send_command("end_game_result", payload,
                                   timeout_ms, line, sizeof(line));
        if (rc == -1) {
            blog("POST /end_game: Pipe nicht erreichbar -> pipe_unavailable");
            http_respond(c, 503, "Service Unavailable",
                         "{\"ok\":false,\"reason\":\"pipe_unavailable\"}");
            return;
        }
        if (rc != 0) {
            http_respond(c, 500, "Internal Server Error",
                         "{\"ok\":false,\"reason\":\"timeout\"}");
            return;
        }
    }
    log_response("/end_game", line);
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

    json_escape(op, esc_op, sizeof(esc_op));
    /* value als String (rbbridge json_get_string unterstuetzt nur Strings). */
    snprintf(payload, sizeof(payload),
             "{\"cmd\":\"creatures_difficulty\",\"op\":\"%s\","
             "\"value\":\"%.6f\"}\n",
             esc_op, value);

    {
        int rc = pipe_send_command("creatures_difficulty_result", payload,
                                   timeout_ms, line, sizeof(line));
        if (rc == -1) {
            blog("POST /creatures_difficulty: Pipe nicht erreichbar -> "
                 "pipe_unavailable");
            http_respond(c, 503, "Service Unavailable",
                         "{\"ok\":false,\"reason\":\"pipe_unavailable\"}");
            return;
        }
        if (rc != 0) {
            http_respond(c, 500, "Internal Server Error",
                         "{\"ok\":false,\"reason\":\"timeout\"}");
            return;
        }
    }
    log_response("/creatures_difficulty", line);
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

    json_get_string(body, "op", op, sizeof(op));
    if (op[0] && strcmp(op, "status") != 0 && strcmp(op, "off") != 0 &&
        strcmp(op, "on") != 0) {
        blog("POST /natural_waves: unbekanntes op '%s'", op);
        http_respond(c, 400, "Bad Request",
                     "{\"ok\":false,\"reason\":\"invalid_op\"}");
        return;
    }

    json_escape(op, esc_op, sizeof(esc_op));
    snprintf(payload, sizeof(payload),
             "{\"cmd\":\"natural_waves\",\"op\":\"%s\"}\n", esc_op);

    {
        int rc = pipe_send_command("natural_waves_result", payload,
                                   timeout_ms, line, sizeof(line));
        if (rc == -1) {
            blog("POST /natural_waves: Pipe nicht erreichbar -> "
                 "pipe_unavailable");
            http_respond(c, 503, "Service Unavailable",
                         "{\"ok\":false,\"reason\":\"pipe_unavailable\"}");
            return;
        }
        if (rc != 0) {
            http_respond(c, 500, "Internal Server Error",
                         "{\"ok\":false,\"reason\":\"timeout\"}");
            return;
        }
    }
    log_response("/natural_waves", line);
    http_respond(c, 200, "OK", line);
}

/* POST /pause_dom + /resume_dom (Write, Issue #520): natives
 * LuaGraphNode::SetSuspended des DOM-Nodes ueber die Pipe. Kein Body.
 * Liefert die pause_dom_result-/resume_dom_result-Zeile der Bridge. */
static void handle_dom_suspend(SOCKET c, const char *cmd, const char *event)
{
    char line[READ_BUF];
    char payload[LINE_MAX];
    int timeout_ms = env_int("RBB_BRIDGE_TIMEOUT_MS", DEFAULT_TIMEOUT_MS);

    snprintf(payload, sizeof(payload), "{\"cmd\":\"%s\"}\n", cmd);

    /* Kanonischer Pfad (Issue #881): dieselbe PERSISTENTE Pipe + der
     * geteilte Response-Slot wie alle anderen Handler. Eine eigene
     * pipe_connect-Verbindung scheitert (dwShareMode 0 -> ERROR_PIPE_BUSY)
     * und meldet faelschlich pipe_unavailable. */
    {
        int rc = pipe_send_command(event, payload, timeout_ms, line, sizeof(line));
        if (rc == -1) {
            blog("POST /%s: Pipe nicht erreichbar -> pipe_unavailable", cmd);
            http_respond(c, 503, "Service Unavailable",
                         "{\"ok\":false,\"reason\":\"pipe_unavailable\"}");
            return;
        }
        if (rc != 0) {
            http_respond(c, 500, "Internal Server Error",
                         "{\"ok\":false,\"reason\":\"timeout\"}");
            return;
        }
    }
    log_response("/dom_suspend", line);
    http_respond(c, 200, "OK", line);
}

/* POST /restart_map: nativer Round-Reset (Read/Write, Issue #516) ueber die
 * Pipe. Body:
 *   {"op":"status|reset"}   (op optional, Default status)
 * `reset` setzt das Spiel-eigene Pending-Flag (GameplayState+0x52A) -> der
 * Game-Thread faehrt den vollen Map-Restart (neue Runde, Economy 0,
 * HQ-Placement). Liefert die restart_map_result-Zeile der Bridge. */
static void handle_restart_map(SOCKET c, const char *body)
{
    char op[32] = "status";
    char line[READ_BUF];
    char payload[LINE_MAX];
    char esc_op[32 * 2];
    int timeout_ms = env_int("RBB_BRIDGE_TIMEOUT_MS", DEFAULT_TIMEOUT_MS);

    json_get_string(body, "op", op, sizeof(op));
    if (op[0] && strcmp(op, "status") != 0 && strcmp(op, "reset") != 0) {
        blog("POST /restart_map: unbekanntes op '%s'", op);
        http_respond(c, 400, "Bad Request",
                     "{\"ok\":false,\"reason\":\"invalid_op\"}");
        return;
    }

    json_escape(op, esc_op, sizeof(esc_op));
    snprintf(payload, sizeof(payload),
             "{\"cmd\":\"restart_map\",\"op\":\"%s\"}\n", esc_op);

    {
        int rc = pipe_send_command("restart_map_result", payload,
                                   timeout_ms, line, sizeof(line));
        if (rc == -1) {
            blog("POST /restart_map: Pipe nicht erreichbar -> pipe_unavailable");
            http_respond(c, 503, "Service Unavailable",
                         "{\"ok\":false,\"reason\":\"pipe_unavailable\"}");
            return;
        }
        if (rc != 0) {
            http_respond(c, 500, "Internal Server Error",
                         "{\"ok\":false,\"reason\":\"timeout\"}");
            return;
        }
    }
    log_response("/restart_map", line);
    http_respond(c, 200, "OK", line);
}


/* POST /spawn_hook: fuehrt {"cmd":"spawn_hook","op":"..."} auf der
 * persistenten Pipe aus (Read #508/#513, nativer Inline-Hook auf
 * SpawnEntity statt nachtraeglichem Entity-Count) und liefert die
 * spawn_hook_result-Zeile. `op` = install|read|reset|status (Default
 * status). */
static void handle_spawn_hook(SOCKET c, const char *body)
{
    char op[16] = "status";
    char line[READ_BUF];
    char payload[LINE_MAX];
    char esc_op[16 * 2];
    int timeout_ms = env_int("RBB_BRIDGE_TIMEOUT_MS", DEFAULT_TIMEOUT_MS);

    json_get_string(body, "op", op, sizeof(op));
    if (op[0] && strcmp(op, "status") != 0 && strcmp(op, "install") != 0 &&
        strcmp(op, "read") != 0 && strcmp(op, "reset") != 0) {
        blog("POST /spawn_hook: unbekanntes op '%s'", op);
        http_respond(c, 400, "Bad Request",
                     "{\"ok\":false,\"reason\":\"invalid_op\"}");
        return;
    }

    json_escape(op, esc_op, sizeof(esc_op));
    snprintf(payload, sizeof(payload),
             "{\"cmd\":\"spawn_hook\",\"op\":\"%s\"}\n", esc_op);

    {
        int rc = pipe_send_command("spawn_hook_result", payload, timeout_ms,
                                   line, sizeof(line));
        if (rc == -1) {
            blog("POST /spawn_hook: Pipe nicht erreichbar -> pipe_unavailable");
            http_respond(c, 503, "Service Unavailable",
                         "{\"ok\":false,\"reason\":\"pipe_unavailable\"}");
            return;
        }
        if (rc != 0) {
            http_respond(c, 500, "Internal Server Error",
                         "{\"ok\":false,\"reason\":\"timeout\"}");
            return;
        }
    }
    log_response("/spawn_hook", line);
    http_respond(c, 200, "OK", line);
}

/* GET /events: SSE-Stream. Haelt die Verbindung offen; der Reader-Thread
 * broadcastet Events direkt an diesen (einen) Client. */
static void handle_events(SOCKET c)
{
    static const char hdr[] =
        "HTTP/1.1 200 OK\r\n"
        "Content-Type: text/event-stream\r\n"
        "Cache-Control: no-cache\r\n"
        "Connection: close\r\n\r\n";
    char b;

    send(c, hdr, (int)strlen(hdr), 0);

    EnterCriticalSection(&g_sse_cs);
    g_sse_sock = c; /* ein Client; ein neuer Connect ersetzt den alten */
    LeaveCriticalSection(&g_sse_cs);
    blog("SSE client verbunden");

    recv(c, &b, 1, 0); /* blockiert bis Trennung */

    EnterCriticalSection(&g_sse_cs);
    if (g_sse_sock == c)
        g_sse_sock = INVALID_SOCKET;
    LeaveCriticalSection(&g_sse_cs);
    blog("SSE client getrennt");
}

/* GET /attack_status: liefert den zuletzt vom Attack-Cycle-Sidecar gepushten
 * Status-JSON (leer -> {"active":false}). Reine Anzeige, kein Game-Call. */
static void handle_get_attack_status(SOCKET c)
{
    char body[8192];
    EnterCriticalSection(&g_attack_status_cs);
    if (g_attack_status[0]) {
        strncpy(body, g_attack_status, sizeof(body) - 1);
        body[sizeof(body) - 1] = '\0';
    } else {
        strcpy(body, "{\"active\":false}");
    }
    LeaveCriticalSection(&g_attack_status_cs);
    http_respond(c, 200, "OK", body);
}

/* POST /attack_status: speichert den Status-JSON des Attack-Cycle-Sidecars. */
static void handle_post_attack_status(SOCKET c, const char *body)
{
    if (!body || !body[0]) {
        http_respond(c, 400, "Bad Request",
                     "{\"ok\":false,\"reason\":\"invalid_request\"}");
        return;
    }
    EnterCriticalSection(&g_attack_status_cs);
    strncpy(g_attack_status, body, sizeof(g_attack_status) - 1);
    g_attack_status[sizeof(g_attack_status) - 1] = '\0';
    LeaveCriticalSection(&g_attack_status_cs);
    http_respond(c, 200, "OK", "{\"ok\":true}");
}

/* POST /attack_interval: setzt (falls interval_s > 0) und liefert das aktuelle
 * Attack-Cycle-Intervall in Sekunden. Body {"interval_s":N} oder {}. */
static void handle_post_attack_interval(SOCKET c, const char *body)
{
    double d = 0.0;
    char resp[128];
    int cur;

    if (json_get_number(body, "interval_s", &d) && d > 0.0) {
        EnterCriticalSection(&g_attack_interval_cs);
        g_attack_interval_s = (int)d;
        LeaveCriticalSection(&g_attack_interval_cs);
        blog("attack_interval -> %d s", (int)d);
    }

    EnterCriticalSection(&g_attack_interval_cs);
    cur = g_attack_interval_s;
    LeaveCriticalSection(&g_attack_interval_cs);
    snprintf(resp, sizeof(resp), "{\"ok\":true,\"interval_s\":%d}", cur);
    http_respond(c, 200, "OK", resp);
}

/* POST /difficulty_interval: setzt (falls > 0) und liefert die zwei
 * Difficulty-Schrittdauern: erster Schritt (difficulty_interval_first_s,
 * Default 200) und Folge-Schritte (difficulty_interval_subsequent_s,
 * Default 600). Der Attack-Cycle baut daraus den Schedule
 * [first, subsequent, …] (8 Schritte). */
static void handle_post_difficulty_interval(SOCKET c, const char *body)
{
    double d = 0.0;
    char resp[192];
    int first, subsequent;

    if (json_get_number(body, "difficulty_interval_first_s", &d) && d > 0.0) {
        EnterCriticalSection(&g_difficulty_interval_first_cs);
        g_difficulty_interval_first_s = (int)d;
        LeaveCriticalSection(&g_difficulty_interval_first_cs);
        blog("difficulty_interval_first -> %d s", (int)d);
    }
    if (json_get_number(body, "difficulty_interval_subsequent_s", &d) && d > 0.0) {
        EnterCriticalSection(&g_difficulty_interval_subsequent_cs);
        g_difficulty_interval_subsequent_s = (int)d;
        LeaveCriticalSection(&g_difficulty_interval_subsequent_cs);
        blog("difficulty_interval_subsequent -> %d s", (int)d);
    }

    EnterCriticalSection(&g_difficulty_interval_first_cs);
    first = g_difficulty_interval_first_s;
    LeaveCriticalSection(&g_difficulty_interval_first_cs);
    EnterCriticalSection(&g_difficulty_interval_subsequent_cs);
    subsequent = g_difficulty_interval_subsequent_s;
    LeaveCriticalSection(&g_difficulty_interval_subsequent_cs);
    snprintf(resp, sizeof(resp),
             "{\"ok\":true,\"difficulty_interval_first_s\":%d,\"difficulty_interval_subsequent_s\":%d}",
             first, subsequent);
    http_respond(c, 200, "OK", resp);
}

/* POST /attack_reset: erhoeht bei {"reset":1} die Reset-Epoch; liefert immer
 * {"reset_epoch":N}. Der Sidecar pollt die Epoch und resettet bei Aenderung. */
static void handle_post_attack_reset(SOCKET c, const char *body)
{
    double r = 0.0;
    char resp[128];
    int epoch;

    if (json_get_number(body, "reset", &r) && r > 0.0) {
        EnterCriticalSection(&g_attack_reset_cs);
        g_attack_reset_epoch++;
        LeaveCriticalSection(&g_attack_reset_cs);
        blog("attack_reset -> epoch %d", g_attack_reset_epoch);
    }

    EnterCriticalSection(&g_attack_reset_cs);
    epoch = g_attack_reset_epoch;
    LeaveCriticalSection(&g_attack_reset_cs);
    snprintf(resp, sizeof(resp), "{\"ok\":true,\"reset_epoch\":%d}", epoch);
    http_respond(c, 200, "OK", resp);
}

/* POST /round_reset: Round-Reset-Wrapper (Issue #854). Nur bei {"reset":1}
 * wird die Round-Reset-Epoch erhoeht UND der native restart_map-Reset
 * angestossen (Economy 0, HQ-Placement). Ohne den Guard ist der Endpoint NICHT
 * poll-sicher: der attack-cycle-Sidecar liest die Epoch per `POST /round_reset
 * {}` im Sekundentakt — jedes bedingungslose Epoch++ haette daraus einen
 * Endlos-Reset gemacht (Issue #868). Gleiches Muster wie /attack_reset.
 * Body: {"reset":1, "map":0} laesst den nativen Map-Reset aus. */
static void handle_post_round_reset(SOCKET c, const char *body)
{
    double reset_flag = 0.0;
    double map_flag = 1.0;
    char resp[256];
    char line[READ_BUF];
    const char *map_status = "skipped";
    int epoch;
    int timeout_ms = env_int("RBB_BRIDGE_TIMEOUT_MS", DEFAULT_TIMEOUT_MS);
    int trigger = json_get_number(body, "reset", &reset_flag) && reset_flag > 0.0;

    EnterCriticalSection(&g_round_reset_cs);
    if (trigger) {
        g_round_reset_epoch++;
    }
    epoch = g_round_reset_epoch;
    LeaveCriticalSection(&g_round_reset_cs);

    if (trigger) {
        blog("round_reset -> epoch %d", epoch);

        json_get_number(body, "map", &map_flag);
        if (map_flag != 0.0) {
            int rc = pipe_send_command("restart_map_result",
                                       "{\"cmd\":\"restart_map\",\"op\":\"reset\"}\n",
                                       timeout_ms, line, sizeof(line));
            if (rc == -1) {
                map_status = "pipe_unavailable";
            } else if (rc != 0) {
                map_status = "timeout";
            } else {
                map_status = "ok";
                log_response("/round_reset", line);
            }
        }
    }

    snprintf(resp, sizeof(resp),
             "{\"ok\":true,\"round_reset_epoch\":%d,\"restart_map\":\"%s\"}",
             epoch, map_status);
    http_respond(c, 200, "OK", resp);
}

/* GET /personas: liefert Persona-Defs (roher JSON-Object) und die aktive
 * Persona. Der Attack-Cycle-Sidecar pollt das; die WebUI liest/schreibt.
 * `send_yourself` lebt seit #851 ausschliesslich in `game_config`. */
static void handle_get_personas(SOCKET c)
{
    char resp[8192];
    char personas[8192];
    char active[64];

    EnterCriticalSection(&g_personas_cs);
    if (g_personas[0]) {
        strncpy(personas, g_personas, sizeof(personas) - 1);
        personas[sizeof(personas) - 1] = '\0';
    } else {
        strcpy(personas, "{}");
    }
    LeaveCriticalSection(&g_personas_cs);

    EnterCriticalSection(&g_active_persona_cs);
    strncpy(active, g_active_persona, sizeof(active) - 1);
    active[sizeof(active) - 1] = '\0';
    LeaveCriticalSection(&g_active_persona_cs);

    snprintf(resp, sizeof(resp),
             "{\"personas\":%s,\"active\":\"%s\"}",
             personas, active);
    http_respond(c, 200, "OK", resp);
}

/* POST /personas: ersetzt die Persona-Defs (Body = roher JSON-Object). */
static void handle_post_personas(SOCKET c, const char *body)
{
    if (!body || !body[0]) {
        http_respond(c, 400, "Bad Request",
                     "{\"ok\":false,\"reason\":\"invalid_request\"}");
        return;
    }
    EnterCriticalSection(&g_personas_cs);
    strncpy(g_personas, body, sizeof(g_personas) - 1);
    g_personas[sizeof(g_personas) - 1] = '\0';
    LeaveCriticalSection(&g_personas_cs);
    blog("personas -> gesetzt (%d bytes)", (int)strlen(g_personas));
    http_respond(c, 200, "OK", "{\"ok\":true}");
}

/* POST /persona_active: setzt die aktive Persona ("" = none). */
static void handle_post_persona_active(SOCKET c, const char *body)
{
    char name[64] = "";
    json_get_string(body, "name", name, sizeof(name));
    EnterCriticalSection(&g_active_persona_cs);
    strncpy(g_active_persona, name, sizeof(g_active_persona) - 1);
    g_active_persona[sizeof(g_active_persona) - 1] = '\0';
    LeaveCriticalSection(&g_active_persona_cs);
    blog("persona_active -> '%s'", g_active_persona);
    http_respond(c, 200, "OK", "{\"ok\":true}");
}

/* GET /natural_attack_rules: liefert die Natural-Attack-Rules als rohen
 * JSON-Object (max_attack_count, boss_min_level, creature_events,
 * event_offset_fraction). Der Attack-Cycle-Sidecar pollt das; die WebUI
 * liest/schreibt. */
static void handle_get_natural_attack_rules(SOCKET c)
{
    char rules[8192];

    EnterCriticalSection(&g_natural_attack_rules_cs);
    if (g_natural_attack_rules[0]) {
        strncpy(rules, g_natural_attack_rules, sizeof(rules) - 1);
        rules[sizeof(rules) - 1] = '\0';
    } else {
        strcpy(rules, "{}");
    }
    LeaveCriticalSection(&g_natural_attack_rules_cs);
    http_respond(c, 200, "OK", rules);
}

/* POST /natural_attack_rules: ersetzt die Natural-Attack-Rules (Body = roher
 * JSON-Object). */
static void handle_post_natural_attack_rules(SOCKET c, const char *body)
{
    if (!body || !body[0]) {
        http_respond(c, 400, "Bad Request",
                     "{\"ok\":false,\"reason\":\"invalid_request\"}");
        return;
    }
    EnterCriticalSection(&g_natural_attack_rules_cs);
    strncpy(g_natural_attack_rules, body, sizeof(g_natural_attack_rules) - 1);
    g_natural_attack_rules[sizeof(g_natural_attack_rules) - 1] = '\0';
    LeaveCriticalSection(&g_natural_attack_rules_cs);
    blog("natural_attack_rules -> gesetzt (%d bytes)",
         (int)strlen(g_natural_attack_rules));
    http_respond(c, 200, "OK", "{\"ok\":true}");
}

/* GET /game_config: liefert die Game-Flow-Konfig als rohen JSON-Object
 * (mode, warmup_s, natural/persona/send_yourself/send_enemy). Der Attack-Cycle
 * pollt das; die WebUI liest/schreibt. */
static void handle_get_game_config(SOCKET c)
{
    char cfg[4096];
    char resp[4352];
    int epoch;
    int ready;

    EnterCriticalSection(&g_game_config_cs);
    if (g_game_config[0]) {
        strncpy(cfg, g_game_config, sizeof(cfg) - 1);
        cfg[sizeof(cfg) - 1] = '\0';
    } else {
        strcpy(cfg, "{}");
    }
    LeaveCriticalSection(&g_game_config_cs);

    EnterCriticalSection(&g_start_epoch_cs);
    epoch = g_start_epoch;
    LeaveCriticalSection(&g_start_epoch_cs);
    EnterCriticalSection(&g_ready_cs);
    ready = g_ready;
    LeaveCriticalSection(&g_ready_cs);

    /* `start_epoch` + `ready` in das flache Config-Objekt haengen (vor die
     * schliessende Klammer) -> der attack_cycle hat beides aus EINEM Poll. */
    {
        size_t len = strlen(cfg);
        if (len > 0 && cfg[len - 1] == '}') {
            cfg[len - 1] = '\0';
            snprintf(resp, sizeof(resp),
                     "%s,\"start_epoch\":%d,\"ready\":%s}",
                     cfg, epoch, ready ? "true" : "false");
        } else {
            snprintf(resp, sizeof(resp),
                     "{\"start_epoch\":%d,\"ready\":%s}",
                     epoch, ready ? "true" : "false");
        }
    }
    http_respond(c, 200, "OK", resp);
}

/* POST /game_config: ersetzt die Game-Flow-Konfig (Body = roher JSON-Object). */
static void handle_post_game_config(SOCKET c, const char *body)
{
    if (!body || !body[0]) {
        http_respond(c, 400, "Bad Request",
                     "{\"ok\":false,\"reason\":\"invalid_request\"}");
        return;
    }
    EnterCriticalSection(&g_game_config_cs);
    strncpy(g_game_config, body, sizeof(g_game_config) - 1);
    g_game_config[sizeof(g_game_config) - 1] = '\0';
    LeaveCriticalSection(&g_game_config_cs);
    blog("game_config -> gesetzt (%d bytes)", (int)strlen(g_game_config));
    http_respond(c, 200, "OK", "{\"ok\":true}");
}

/* POST /start: das eigentliche Start-Signal (Referee -> beide Server im VS,
 * nur A im SOLO). Inkrementiert `start_epoch`; der attack_cycle erkennt den
 * neuen Wert via GET /game_config und vollzieht PAUSED -> WARMUP. */
static void handle_post_start(SOCKET c)
{
    int epoch;
    char resp[96];

    EnterCriticalSection(&g_start_epoch_cs);
    g_start_epoch += 1;
    epoch = g_start_epoch;
    LeaveCriticalSection(&g_start_epoch_cs);
    blog("start -> Signal empfangen (epoch %d)", epoch);
    snprintf(resp, sizeof(resp),
             "{\"ok\":true,\"start\":true,\"start_epoch\":%d}", epoch);
    http_respond(c, 200, "OK", resp);
}

/* POST /ready: setzt das Ready-Flag (Body optional {"on":0|1}, Default 1). */
static void handle_post_ready(SOCKET c, const char *body)
{
    double d = 0.0;
    char resp[128];
    int cur;

    EnterCriticalSection(&g_ready_cs);
    g_ready = json_get_number(body, "on", &d) ? (d != 0.0) : 1;
    cur = g_ready;
    LeaveCriticalSection(&g_ready_cs);
    blog("ready -> %s", cur ? "on" : "off");
    snprintf(resp, sizeof(resp), "{\"ok\":true,\"ready\":%s}",
             cur ? "true" : "false");
    http_respond(c, 200, "OK", resp);
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

        if (strcmp(method, "GET") == 0 &&
            (strcmp(path, "/health") == 0 ||
             strncmp(path, "/health?", 8) == 0)) {
            handle_health(c, strstr(path, "deep=1") != NULL);
        } else if (strcmp(method, "GET") == 0 && strcmp(path, "/events") == 0) {
            handle_events(c);
        } else if (strcmp(method, "GET") == 0 && strcmp(path, "/attack_status") == 0) {
            handle_get_attack_status(c);
        } else if (strcmp(method, "POST") == 0 && strcmp(path, "/attack_status") == 0) {
            char *b = malloc((size_t)body_len + 1);
            if (!b) {
                free(req);
                return;
            }
            memcpy(b, body, (size_t)body_len);
            b[body_len] = '\0';
            handle_post_attack_status(c, b);
            free(b);
        } else if (strcmp(method, "POST") == 0 && strcmp(path, "/attack_interval") == 0) {
            char *b = malloc((size_t)body_len + 1);
            if (!b) {
                free(req);
                return;
            }
            memcpy(b, body, (size_t)body_len);
            b[body_len] = '\0';
            handle_post_attack_interval(c, b);
            free(b);
        } else if (strcmp(method, "POST") == 0 && strcmp(path, "/difficulty_interval") == 0) {
            char *b = malloc((size_t)body_len + 1);
            if (!b) {
                free(req);
                return;
            }
            memcpy(b, body, (size_t)body_len);
            b[body_len] = '\0';
            handle_post_difficulty_interval(c, b);
            free(b);
        } else if (strcmp(method, "POST") == 0 && strcmp(path, "/attack_reset") == 0) {
            char *b = malloc((size_t)body_len + 1);
            if (!b) {
                free(req);
                return;
            }
            memcpy(b, body, (size_t)body_len);
            b[body_len] = '\0';
            handle_post_attack_reset(c, b);
            free(b);
        } else if (strcmp(method, "POST") == 0 && strcmp(path, "/round_reset") == 0) {
            char *b = malloc((size_t)body_len + 1);
            if (!b) {
                free(req);
                return;
            }
            memcpy(b, body, (size_t)body_len);
            b[body_len] = '\0';
            handle_post_round_reset(c, b);
            free(b);
        } else if (strcmp(method, "GET") == 0 && strcmp(path, "/personas") == 0) {
            handle_get_personas(c);
        } else if (strcmp(method, "POST") == 0 && strcmp(path, "/personas") == 0) {
            char *b = malloc((size_t)body_len + 1);
            if (!b) {
                free(req);
                return;
            }
            memcpy(b, body, (size_t)body_len);
            b[body_len] = '\0';
            handle_post_personas(c, b);
            free(b);
        } else if (strcmp(method, "POST") == 0 && strcmp(path, "/persona_active") == 0) {
            char *b = malloc((size_t)body_len + 1);
            if (!b) {
                free(req);
                return;
            }
            memcpy(b, body, (size_t)body_len);
            b[body_len] = '\0';
            handle_post_persona_active(c, b);
            free(b);
        } else if (strcmp(method, "GET") == 0 && strcmp(path, "/natural_attack_rules") == 0) {
            handle_get_natural_attack_rules(c);
        } else if (strcmp(method, "POST") == 0 && strcmp(path, "/natural_attack_rules") == 0) {
            char *b = malloc((size_t)body_len + 1);
            if (!b) {
                free(req);
                return;
            }
            memcpy(b, body, (size_t)body_len);
            b[body_len] = '\0';
            handle_post_natural_attack_rules(c, b);
            free(b);
        } else if (strcmp(method, "GET") == 0 && strcmp(path, "/game_config") == 0) {
            handle_get_game_config(c);
        } else if (strcmp(method, "POST") == 0 && strcmp(path, "/game_config") == 0) {
            char *b = malloc((size_t)body_len + 1);
            if (!b) {
                free(req);
                return;
            }
            memcpy(b, body, (size_t)body_len);
            b[body_len] = '\0';
            handle_post_game_config(c, b);
            free(b);
        } else if (strcmp(method, "POST") == 0 && strcmp(path, "/start") == 0) {
            handle_post_start(c);
        } else if (strcmp(method, "POST") == 0 && strcmp(path, "/ready") == 0) {
            char *b = malloc((size_t)body_len + 1);
            if (!b) {
                free(req);
                return;
            }
            memcpy(b, body, (size_t)body_len);
            b[body_len] = '\0';
            handle_post_ready(c, b);
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
        } else if (strcmp(method, "POST") == 0 && strcmp(path, "/try_spend") == 0) {
            char *b = malloc((size_t)body_len + 1);
            if (!b) {
                free(req);
                return;
            }
            memcpy(b, body, (size_t)body_len);
            b[body_len] = '\0';
            handle_try_spend(c, b);
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
        } else if (strcmp(method, "POST") == 0 && strcmp(path, "/end_game") == 0) {
            char *b = malloc((size_t)body_len + 1);
            if (!b) {
                free(req);
                return;
            }
            memcpy(b, body, (size_t)body_len);
            b[body_len] = '\0';
            handle_end_game(c, b);
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
        } else if (strcmp(method, "POST") == 0 && strcmp(path, "/pause_dom") == 0) {
            handle_dom_suspend(c, "pause_dom", "pause_dom_result");
        } else if (strcmp(method, "POST") == 0 && strcmp(path, "/resume_dom") == 0) {
            handle_dom_suspend(c, "resume_dom", "resume_dom_result");
        } else if (strcmp(method, "POST") == 0 && strcmp(path, "/restart_map") == 0) {
            char *b = malloc((size_t)body_len + 1);
            if (!b) {
                free(req);
                return;
            }
            memcpy(b, body, (size_t)body_len);
            b[body_len] = '\0';
            handle_restart_map(c, b);
            free(b);
        } else if (strcmp(method, "POST") == 0 && strcmp(path, "/spawn_hook") == 0) {
            char *b = malloc((size_t)body_len + 1);
            if (!b) {
                free(req);
                return;
            }
            memcpy(b, body, (size_t)body_len);
            b[body_len] = '\0';
            handle_spawn_hook(c, b);
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

/* Thread-pro-Connection: handle_client in eigenem Thread, damit SSE
 * (GET /events) den Accept-Loop nicht blockiert. */
static DWORD WINAPI client_thread(LPVOID arg)
{
    SOCKET c = (SOCKET)(uintptr_t)arg;
    handle_client(c);
    closesocket(c);
    return 0;
}

static int mode_server(void)
{
    init_session_id();
    blog("session start %s", g_session_id);
    blog("pipe_bridge ref=%s", RBBRIDGE_REF);

    /* #636: persistente Pipe — Reader-Thread starten (server-seitiges
     * Command-Handling + Event-Routing). */
    InitializeCriticalSection(&g_pipe_cs);
    InitializeCriticalSection(&g_resp_cs);
    InitializeCriticalSection(&g_cmd_cs);
    InitializeCriticalSection(&g_sse_cs);
    InitializeCriticalSection(&g_attack_status_cs);
    InitializeCriticalSection(&g_attack_interval_cs);
    InitializeCriticalSection(&g_attack_reset_cs);
    InitializeCriticalSection(&g_round_reset_cs);
    InitializeCriticalSection(&g_difficulty_interval_first_cs);
    InitializeCriticalSection(&g_difficulty_interval_subsequent_cs);
    InitializeCriticalSection(&g_personas_cs);
    InitializeCriticalSection(&g_active_persona_cs);
    InitializeCriticalSection(&g_natural_attack_rules_cs);
    InitializeCriticalSection(&g_game_config_cs);
    InitializeCriticalSection(&g_ready_cs);
    InitializeCriticalSection(&g_start_epoch_cs);
    g_resp_ev = CreateEvent(NULL, FALSE, FALSE, NULL);
    CreateThread(NULL, 0, pipe_reader_main, NULL, 0, NULL);

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
        {
            HANDLE th = CreateThread(NULL, 0, client_thread,
                                     (LPVOID)(uintptr_t)c, 0, NULL);
            if (th)
                CloseHandle(th);
            else
                closesocket(c);
        }
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
