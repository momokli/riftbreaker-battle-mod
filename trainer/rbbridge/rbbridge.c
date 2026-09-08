/*
 * rbbridge.c - In-Game-Bridge-DLL fuer den Rift-Breaker-Trainer (Harness).
 *
 * Rolle (Architektur, siehe trainer/README.md):
 *   Die Trainer-DLL ist das EINZIGE I/O-Gateway zwischen Spielprozess und
 *   Aussenwelt (Tournament-Server). Der Lua-Mod bleibt reine Spiellogik.
 *   Diese DLL wird per Injector zur Laufzeit geladen (keine Datei-Engine-
 *   Eingriffe, Steam-kompatibel) und stellt einen Named-Pipe-Server bereit.
 *
 * Was der Harness schon kann:
 *   - Named-Pipe-Server "\\.\pipe\rbbattle" (ein Client zur Zeit, v0)
 *   - Line-delimited-JSON-Protokoll v0 (siehe trainer/protocol.md):
 *       Ingress: {"cmd":"ping"}                      -> {"event":"pong"}
 *                {"cmd":"exec","command":"rb_wave 3"}-> dispatch_exec()
 *       Egress : {"event":"state","state":{...}}     (Heartbeat-Platzhalter,
 *                alle 5 s solange ein Client verbunden ist)
 *   - Robustheit: Fehler im Pipe-Dienst duerfen das Spiel NIEMALS
 *     abstuerzen; kein Client/kein Connect = ruhiger Wartethread; Client-
 *     disconnect = automatischer Reconnect ins naechste Connect.
 *   - Logging: OutputDebugString (DebugView) + %TEMP%\rbbridge.log.
 *
 * Was RE-abhaengig offen ist (TODO/FIXME im Code; Phase 2 des Projekts):
 *   - dispatch_exec(): "rb_wave N" tatsaechlich im Spiel ausfuehren
 *     (RE: Lua-State / ConsoleService-Instanz / ExecuteCommand-Binding im
 *     Spielprozess finden und aufrufen).
 *   - send_state(): echte Spiel-State-Werte (Score, Ressourcen, Wave) aus
 *     dem Prozess lesen statt leerer Platzhalter.
 *
 * Wichtig:
 *   - Kein Datei-I/O ueber die Lua-API noetig - alles laeuft hier in der DLL.
 *   - DllMain macht NICHTS Schweres (Loader-Lock): nur Thread starten.
 *   - Ein FreeLibrary zur Laufzeit (waerend der Pipe-Thread blockiert) wird
 *     best effort behandelt (Wake-up-Connect + kurzes Join), ist aber kein
 *     unterstuetzter Fall: Ueblich ist Inject-once / unload beim Prozessende.
 *
 * Build (x64):
 *   MinGW-w64 : x86_64-w64-mingw32-gcc -O2 -Wall -Wextra -shared -o rbbridge.dll rbbridge.c
 *   MSVC      : cl /nologo /O2 /W3 /LD rbbridge.c /Fe:rbbridge.dll
 */

#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0601 /* GetTickCount64, Win7+ */
#endif

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <stdarg.h>
#include <stdio.h>
#include <string.h>

/* ------------------------------------------------------------------ */
/* Konstanten                                                          */
/* ------------------------------------------------------------------ */

#define PIPE_NAME_A "\\\\.\\pipe\\rbbattle"
#define PIPE_NAME_W L"\\\\.\\pipe\\rbbattle"

#define PIPE_INBUF_SIZE   4096 /* Lesechunk vom Pipe-Client              */
#define PIPE_LINE_MAX     8192 /* max. Laenge einer Protokollzeile       */
#define RESP_BUF_SIZE     4096 /* max. Laenge einer Antwortzeile         */
#define HEARTBEAT_MS      5000 /* Intervall des State-Platzhalter-Events */
#define POLL_MS           100  /* Serviceloop-Takt (nur bei Client)      */

/* Datei-Log: 1 = %TEMP%\rbbridge.log mitschreiben (Default an; abschalten
 * mit Umgebungsvariable RBBRIDGE_LOG=0). DebugView geht immer. */
static int g_file_log = 1;

/* ------------------------------------------------------------------ */
/* Globaler Zustand                                                    */
/* ------------------------------------------------------------------ */

static volatile LONG g_stop = 0;   /* 1 = Thread soll sich beenden       */
static HANDLE g_thread = NULL;     /* Handle des Pipe-Server-Threads     */
static LONG g_thread_started = 0;  /* verhindert doppelte Attach-Threads */
static CRITICAL_SECTION g_log_cs;  /* schuetzt das Datei-Log             */

/* ------------------------------------------------------------------ */
/* Logging (OutputDebugString + optionale Datei)                       */
/* ------------------------------------------------------------------ */

static void dbg(const char *fmt, ...)
{
    char buf[1024];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);

    OutputDebugStringA(buf);

    if (!g_file_log)
        return;

    /* Anhaengen an %TEMP%\rbbridge.log - bewusst pro Zeile oeffnen/
     * schliessen: einfach, robust, kein Handle-Lebenszyklus-Problem. */
    EnterCriticalSection(&g_log_cs);
    wchar_t path[MAX_PATH];
    if (GetTempPathW(MAX_PATH, path) != 0) {
        size_t plen = wcslen(path);
        if (plen + 14 < MAX_PATH) { /* + "rbbridge.log" */
            wcscpy(path + plen, L"rbbridge.log");
            HANDLE h = CreateFileW(path, FILE_APPEND_DATA, FILE_SHARE_READ,
                                   NULL, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
            if (h != INVALID_HANDLE_VALUE) {
                char out[1152];
                int n = snprintf(out, sizeof(out), "[pid=%lu tid=%lu] %s\r\n",
                                 (unsigned long)GetCurrentProcessId(),
                                 (unsigned long)GetCurrentThreadId(), buf);
                if (n > 0) {
                    DWORD written = 0;
                    WriteFile(h, out, (DWORD)n, &written, NULL);
                }
                CloseHandle(h);
            }
        }
    }
    LeaveCriticalSection(&g_log_cs);
}

/* ------------------------------------------------------------------ */
/* Minimal-JSON-Helfer (nur fuer das flache v0-Format noetig)          */
/* ------------------------------------------------------------------ */

/*
 * Sucht in einem flachen JSON-Objekt den Wert des Keys "key".
 * Erwartet das Muster:  ... "key" : "wert" ...
 * Kopiert den String-Wert (ohne Anfuehrungszeichen, einfache Backslash-
 * Escapes wie \" \\ werden aufgeloest) nach out. Rueckgabe 1 = gefunden.
 *
 * Kein vollwertiger Parser - bewusst klein; das v0-Protokoll ist flach und
 * wird von uns selbst erzeugt. FIXME(spaeter): echte JSON-Lib, falls das
 * Protokoll verschachtelt wird.
 */
static int json_get_string(const char *json, const char *key,
                           char *out, size_t out_sz)
{
    if (!json || !key || !out || out_sz == 0)
        return 0;

    size_t key_len = strlen(key);
    const char *p = json;

    while ((p = strstr(p, key)) != NULL) {
        /* Key muss in Anfuehrungszeichen stehen: vorher schliessendes ",
         * danach oeffnendes " */
        if (p != json && p[-1] == '"' && p[key_len] == '"') {
            const char *q = p + key_len + 1; /* hinter schliessendem " */
            while (*q == ' ' || *q == '\t')
                q++;
            if (*q != ':')
                return 0;
            q++;
            while (*q == ' ' || *q == '\t')
                q++;
            if (*q != '"')
                return 0; /* wir unterstuetzen nur String-Werte */
            q++;
            size_t n = 0;
            while (*q && *q != '"' && n + 1 < out_sz) {
                if (*q == '\\' && q[1]) { /* Minimal-Escapes */
                    q++;
                    if (*q == 'n')
                        out[n++] = '\n';
                    else if (*q == 't')
                        out[n++] = '\t';
                    else
                        out[n++] = *q; /* \\ \" \/ ... -> Zeichen selbst */
                } else {
                    out[n++] = *q;
                }
                q++;
            }
            out[n] = '\0';
            return *q == '"'; /* sauber geschlossen? */
        }
        p += key_len;
    }
    return 0;
}

/*
 * Kopiert einen String JSON-sicher (escaped \ und ") nach out.
 * Minimale Variante; reicht fuer Kommando-Echos im v0-Protokoll.
 */
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

/* ------------------------------------------------------------------ */
/* Antworten auf die Pipe schreiben                                    */
/* ------------------------------------------------------------------ */

/*
 * Sendet eine fertige Zeile (JSON + "\n") an den Client.
 * Rueckgabe 0 = ok, -1 = Fehler (Client weg o.ae.).
 */
static int send_line(HANDLE hPipe, const char *fmt, ...)
{
    char buf[RESP_BUF_SIZE];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    if (n < 0)
        return -1;
    if ((size_t)n >= sizeof(buf))
        n = (int)sizeof(buf) - 1;

    DWORD written = 0;
    if (!WriteFile(hPipe, buf, (DWORD)n, &written, NULL) || written != (DWORD)n) {
        dbg("send_line: WriteFile fehlgeschlagen (GLE=%lu)", GetLastError());
        return -1;
    }
    return 0;
}

/*
 * Egress-Platzhalter: periodischer Spiel-State.
 *
 * FIXME(RE): Hier spaeter echte Werte aus dem Spielprozess eintragen
 * (Score, Ressourcen, aktuelle Wave, Rundenstand ...), sobald die
 * Adressen/Signaturen per scan/ ermittelt sind. Die Events daraus fressen
 * spaeter score_update/wave_* (siehe trainer/protocol.md).
 */
static void send_state_placeholder(HANDLE hPipe)
{
    send_line(hPipe,
              "{\"event\":\"state\",\"t\":%llu,\"state\":{}}",
              (unsigned long long)GetTickCount64());
}

/* ------------------------------------------------------------------ */
/* Dispatch: Ingress-Kommandos                                         */
/* ------------------------------------------------------------------ */

/*
 * {"cmd":"exec","command":"rb_wave 3"}
 *
 * Ziel (Phase 2, RE): das Kommando im Spiel ausfuehren - aequivalent zu
 *   ConsoleService:ExecuteCommand("rb_wave 3")
 * aus der Lua-Perspektive (der Lua-Mod registriert rb_wave, siehe
 * mod/lua/rbbattle_autoexec.lua im Spike-Branch).
 *
 * TODO(RE): Dafuer muss im Spielprozess gefunden werden:
 *   1. der Lua-State / die ConsoleService-Instanz bzw. die Engine-Funktion,
 *      die Konsolen-Kommandos ausfuehrt (Anhaltspunkt: docs/findings.md,
 *      Punkt 8 - ExecuteCommand existiert nachweislich),
 *   2. eine stabile Aufrufstelle - bevorzugt per AOB-Signatur statt fester
 *      Adresse (Spiel-Updates verschieben alles).
 * Danach hier den Aufruf verdrahten (z.B. Thread im Spielkontext oder
 * Remote-Call in die gefundene Funktion).
 *
 * Harness-Verhalten: Kommando loggen und mit exec_result antworten.
 */
static void dispatch_exec(HANDLE hPipe, const char *command)
{
    dbg("dispatch_exec: command='%s' -> TODO(RE): im Spiel ausfuehren", command);

    char escaped[RESP_BUF_SIZE];
    json_escape(command, escaped, sizeof(escaped));

    send_line(hPipe,
              "{\"event\":\"exec_result\",\"command\":\"%s\",\"ok\":false,"
              "\"reason\":\"not_implemented (RE: ConsoleService/Lua-State "
              "finden)\"}",
              escaped);
}

/*
 * Verteilt eine empfangene Protokollzeile (ohne \n).
 * Unbekanntes/Nicht-JSON wird geloggt und (nur bei JSON-artigen Zeilen)
 * mit einem error-Event beantwortet - der Client soll Feedback bekommen,
 * das Spiel darf sich nie daran stoeren.
 */
static void handle_line(HANDLE hPipe, const char *line)
{
    char cmd[64] = "";
    char command[512] = "";

    if (!json_get_string(line, "cmd", cmd, sizeof(cmd))) {
        /* kein "cmd"-Key: falls es wie JSON aussieht -> error-Event,
         * sonst still ignorieren (Moeglicherweise Fremdtext auf der Pipe). */
        dbg("handle_line: Zeile ohne 'cmd': %.160s", line);
        if (line[0] == '{') {
            send_line(hPipe, "{\"event\":\"error\",\"error\":\"missing_cmd\"}");
        }
        return;
    }

    if (strcmp(cmd, "ping") == 0) {
        /* TODO(spaeter): ggf. "t" mitgeben, damit der Client Latenz messen
         * kann - v0 bewusst minimal. */
        if (send_line(hPipe, "{\"event\":\"pong\",\"t\":%llu}",
                      (unsigned long long)GetTickCount64()) == 0) {
            dbg("handle_line: ping -> pong");
        }
        return;
    }

    if (strcmp(cmd, "exec") == 0) {
        if (!json_get_string(line, "command", command, sizeof(command))) {
            send_line(hPipe,
                      "{\"event\":\"error\",\"error\":\"exec_ohne_command\"}");
            return;
        }
        dispatch_exec(hPipe, command);
        return;
    }

    dbg("handle_line: unbekanntes cmd '%s'", cmd);
    send_line(hPipe, "{\"event\":\"error\",\"error\":\"unknown_cmd\"}");
}

/* ------------------------------------------------------------------ */
/* Pipe-Server: Verbindung bedienen                                    */
/* ------------------------------------------------------------------ */

/*
 * Bedient EINEN verbundenen Client, bis er sich trennt oder g_stop.
 * Polling mit PeekNamedPipe statt blockierendem ReadFile: so kann der
 * Thread nebenbei den State-Heartbeat senden und auf g_stop reagieren.
 * Rueckgabe 0 = normal beendet, -1 = Client weg / Fehler.
 */
static int serve_client(HANDLE hPipe)
{
    char line[PIPE_LINE_MAX];
    size_t nline = 0;
    DWORD last_beat = 0;

    dbg("serve_client: Client verbunden");

    for (;;) {
        if (g_stop)
            return 0;

        /* Client noch da? (PeekNamedPipe blockiert nicht) */
        DWORD avail = 0;
        if (!PeekNamedPipe(hPipe, NULL, 0, NULL, &avail, NULL)) {
            DWORD gle = GetLastError();
            if (gle != ERROR_BROKEN_PIPE && gle != ERROR_BAD_PIPE &&
                gle != ERROR_INVALID_HANDLE && gle != ERROR_PIPE_NOT_CONNECTED) {
                dbg("serve_client: PeekNamedPipe-Fehler GLE=%lu", gle);
            }
            dbg("serve_client: Client getrennt (GLE=%lu)", gle);
            return -1;
        }

        if (avail > 0) {
            char rbuf[PIPE_INBUF_SIZE];
            DWORD rd = 0;
            DWORD want = avail < sizeof(rbuf) ? avail : (DWORD)sizeof(rbuf);
            if (!ReadFile(hPipe, rbuf, want, &rd, NULL) || rd == 0) {
                dbg("serve_client: ReadFile fehlgeschlagen (GLE=%lu)",
                    GetLastError());
                return -1;
            }
            /* Bytes zeilenweise aufsammeln; '\r' ueberspringen */
            for (DWORD i = 0; i < rd; i++) {
                char c = rbuf[i];
                if (c == '\n') {
                    if (nline > 0) {
                        line[nline] = '\0';
                        handle_line(hPipe, line);
                    }
                    nline = 0;
                } else if (c != '\r') {
                    if (nline + 1 >= sizeof(line)) {
                        /* Zeile zu lang: verwerfen statt Puffer zu sprengen */
                        dbg("serve_client: Zeile >%u Bytes verworfen",
                            (unsigned)sizeof(line));
                        nline = 0;
                    } else {
                        line[nline++] = c;
                    }
                }
            }
        }

        /* State-Heartbeat (Egress-Platzhalter), nur bei aktivem Client */
        DWORD now = GetTickCount();
        if (last_beat == 0 || now - last_beat >= HEARTBEAT_MS) {
            last_beat = now;
            send_state_placeholder(hPipe);
        }

        Sleep(POLL_MS);
    }
}

/*
 * Hauptschleife des Pipe-Servers. Laueft bis g_stop.
 * Kein Client = blockiert in ConnectNamedPipe (ruhig, keine CPU-Last);
 * Client weg = sofort naechste Connect-Runde (Reconnect).
 */
static DWORD WINAPI pipe_server_main(LPVOID unused)
{
    (void)unused;
    dbg("pipe_server_main: Start (Pipe %s)", PIPE_NAME_A);

    while (!g_stop) {
        HANDLE hPipe = CreateNamedPipeA(
            PIPE_NAME_A,
            PIPE_ACCESS_DUPLEX,
            PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT,
            1,                          /* nur 1 Instanz in v0             */
            RESP_BUF_SIZE,              /* Outbound-Puffergroesse          */
            PIPE_INBUF_SIZE,            /* Inbound-Puffergroesse           */
            0, NULL);

        if (hPipe == INVALID_HANDLE_VALUE) {
            dbg("pipe_server_main: CreateNamedPipeA fehlgeschlagen (GLE=%lu)",
                GetLastError());
            Sleep(1000);
            continue;
        }

        if (g_stop) {
            CloseHandle(hPipe);
            break;
        }

        BOOL connected = ConnectNamedPipe(hPipe, NULL);
        if (!connected && GetLastError() != ERROR_PIPE_CONNECTED) {
            /* z.B. kurz nach CreateNamedPipe schon verbunden (ERROR_PIPE_
             * CONNECTED = ok) - alles andere: aufraeumen und weitermachen */
            dbg("pipe_server_main: ConnectNamedPipe fehlgeschlagen (GLE=%lu)",
                GetLastError());
            CloseHandle(hPipe);
            Sleep(250);
            continue;
        }

        if (g_stop) {
            CloseHandle(hPipe);
            break;
        }

        serve_client(hPipe);

        DisconnectNamedPipe(hPipe);
        CloseHandle(hPipe);
        Sleep(100); /* kurze Pause vor dem naechsten Connect */
    }

    dbg("pipe_server_main: Ende");
    return 0;
}

/* ------------------------------------------------------------------ */
/* DllMain                                                             */
/* ------------------------------------------------------------------ */

BOOL WINAPI DllMain(HINSTANCE hinst, DWORD reason, LPVOID reserved)
{
    (void)reserved;

    switch (reason) {
    case DLL_PROCESS_ATTACH: {
        /* Nur der allererste Attach startet den Thread */
        if (InterlockedCompareExchange(&g_thread_started, 1, 0) != 0)
            break;

        /* Umgebungsvariable RBBRIDGE_LOG=0 schaltet das Datei-Log ab */
        char env[4] = "";
        if (GetEnvironmentVariableA("RBBRIDGE_LOG", env, sizeof(env)) > 0 &&
            strcmp(env, "0") == 0) {
            g_file_log = 0;
        }

        InitializeCriticalSection(&g_log_cs);
        g_stop = 0;

        /* WICHTIG: In DllMain nie blockieren/kein LoadLibrary - wir
         * starten nur einen unabhaengigen Thread. */
        g_thread = CreateThread(NULL, 0, pipe_server_main, NULL, 0, NULL);
        if (g_thread) {
            dbg("DllMain: Attach ok, Pipe-Server-Thread laeuft");
        } else {
            dbg("DllMain: CreateThread fehlgeschlagen (GLE=%lu)", GetLastError());
            InterlockedExchange(&g_thread_started, 0);
        }

        /* Handle erst im Detach schliessen (brauchen es zum Join).
         * "DisableThreadLibraryCalls" spart die Load/Unload-Benach-
         * richtigungen fuer andere Threads. */
        DisableThreadLibraryCalls(hinst);
        break;
    }

    case DLL_PROCESS_DETACH: {
        if (!g_thread_started)
            break;

        g_stop = 1;

        /* Wake-up: Ein kurzer Eigen-Connect bringt den Thread aus einem
         * blockierenden ConnectNamedPipe (und danach sofort wieder raus,
         * weil g_stop gesetzt ist). Mehrmals versuchen, um die kleine
         * Race zwischen CreateNamedPipe/ConnectNamedPipe abzudecken. */
        for (int i = 0; i < 40 && g_thread; i++) {
            if (WaitForSingleObject(g_thread, 50) == WAIT_OBJECT_0)
                break;
            HANDLE w = CreateFileA(PIPE_NAME_A,
                                   GENERIC_READ | GENERIC_WRITE,
                                   0, NULL, OPEN_EXISTING, 0, NULL);
            if (w != INVALID_HANDLE_VALUE) {
                FlushFileBuffers(w);
                CloseHandle(w);
            }
        }
        if (g_thread) {
            WaitForSingleObject(g_thread, 500);
            CloseHandle(g_thread);
            g_thread = NULL;
        }
        DeleteCriticalSection(&g_log_cs);
        break;
    }

    default:
        break;
    }
    return TRUE;
}
