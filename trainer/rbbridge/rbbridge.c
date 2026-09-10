/*
 * rbbridge.c - In-Game-Bridge fuer den Rift-Breaker-Trainer (Harness),
 *              dual: Trainer-DLL ODER Standalone-EXE.
 *
 * Rolle (Architektur, siehe trainer/README.md):
 *   Die Trainer-DLL ist das EINZIGE I/O-Gateway zwischen Spielprozess und
 *   Aussenwelt (Tournament-Server). Der Lua-Mod bleibt reine Spiellogik.
 *   Diese DLL wird per Injector zur Laufzeit geladen (keine Datei-Engine-
 *   Eingriffe, Steam-kompatibel) und stellt einen Named-Pipe-Server bereit.
 *
 * Dual-Mode: Derselbe Quelltext baut zwei Varianten - die Pipe-Server-
 *   Logik liegt in rbbridge_start() und wird von beiden gerufen:
 *     - rbbridge.dll (Default, per Injector in den Spielprozess laden)
 *     - rbbridge_standalone.exe (#define RBBRIDGE_STANDALONE): dieselbe
 *       Server-Logik als normales Programm, damit ist die Trainer-IO auf
 *       jedem Windows-Rechner OHNE Injection testbar (Baustein 04, Test 0).
 *   Protokollverhalten ist in beiden Varianten IDENTISCH; die Standalone-
 *   Variante druckt nur eine Hinweiszeile beim Start.
 *
 * Was der Harness schon kann:
 *   - Named-Pipe-Server "\\.\pipe\rbbattle" (ein Client zur Zeit, v0)
 *   - Line-delimited-JSON-Protokoll v0 (siehe trainer/protocol.md):
 *       Ingress: {"cmd":"ping"}                      -> {"event":"pong"}
 *                {"cmd":"exec","command":"rb_wave 3"}-> dispatch_exec()
 *                (dispatch_exec ruft seit RE-Stand 2.0.58485 die echte
 *                 ConsoleService::ExecuteCommand() im Spielprozess auf,
 *                 siehe Abschnitt "ConsoleService-Anbindung" unten)
 *       Egress : {"event":"score_update","score":...,"resources":{...},
 *                "wave":...} (periodischer State-Snapshot, Issue #13,
 *                alle 5 s solange ein Client verbunden ist)
 *   - Robustheit: Fehler im Pipe-Dienst duerfen das Spiel NIEMALS
 *     abstuerzen; kein Client/kein Connect = ruhiger Wartethread; Client-
 *     disconnect = automatischer Reconnect ins naechste Connect.
 *   - Logging: OutputDebugString (DebugView) + %TEMP%\rbbridge.log.
 *
 * Was RE-abhaengig noch offen ist (TODO/FIXME im Code; Phase 2 des Projekts):
 *   - read_game_state(): echte Spiel-State-Werte (Score, Ressourcen, Wave)
 *     aus dem Prozess lesen statt Defaults (alles 0). Der score_update-
 *     Egress (send_state) ist damit strukturell schon verdrahtet
 *     (dispatch_exec selbst ist seit dem RE-Stand unten implementiert).
 *
 * Wichtig:
 *   - Kein Datei-I/O ueber die Lua-API noetig - alles laeuft hier in der DLL.
 *   - DllMain macht NICHTS Schweres (Loader-Lock): nur Thread starten.
 *   - Ein FreeLibrary zur Laufzeit (waerend der Pipe-Thread blockiert) wird
 *     best effort behandelt (Wake-up-Connect + kurzes Join), ist aber kein
 *     unterstuetzter Fall: Ueblich ist Inject-once / unload beim Prozessende.
 *
 * Build (x64):
 *   1) Trainer-DLL (Injection):
 *      MinGW-w64 : x86_64-w64-mingw32-gcc -O2 -Wall -Wextra -shared -o rbbridge.dll rbbridge.c
 *      MSVC      : cl /nologo /O2 /W3 /LD rbbridge.c /Fe:rbbridge.dll
 *   2) Standalone-EXE (kein Injection noetig, Testmodus):
 *      MinGW-w64 : x86_64-w64-mingw32-gcc -O2 -Wall -Wextra -DRBBRIDGE_STANDALONE -o rbbridge_standalone.exe rbbridge.c
 *      MSVC      : cl /nologo /O2 /W3 /DRBBRIDGE_STANDALONE rbbridge.c /Fe:rbbridge_standalone.exe
 *      (-lws2_32 ist nicht noetig: die Named Pipe nutzt nur Win32-API.)
 */

#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0601 /* GetTickCount64, Win7+ */
#endif

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <stdarg.h>
#include <stdint.h>
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
 * Sendet eine fertige Zeile an den Client. Protokoll v0 ist strikt
 * zeilenbasiert: JEDE outbound JSON-Nachricht wird mit '\n' terminiert
 * (Fix fuer den Newline-Quirk - frueher fehlte der Terminator und Clients
 * mussten roh/ohne Zeilenstruktur lesen).
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
    if ((size_t)n >= sizeof(buf) - 1) /* Platz fuer '\n' + NUL lassen */
        n = (int)sizeof(buf) - 2;
    buf[n++] = '\n';
    buf[n] = '\0';

    DWORD written = 0;
    if (!WriteFile(hPipe, buf, (DWORD)n, &written, NULL) || written != (DWORD)n) {
        dbg("send_line: WriteFile fehlgeschlagen (GLE=%lu)", GetLastError());
        return -1;
    }
    return 0;
}

/*
 * Spiel-State-Egress (Issue #13): periodischer State-Snapshot.
 *
 * send_state() emittiert score_update gemaeß trainer/protocol.md (Score,
 * Ressourcen, aktuelle Wave). Die Struktur ist stabil und wird vom Server
 * (POST /report event=score_update) und der Web-UI konsumiert; die Werte
 * kommen aus read_game_state().
 */
typedef struct {
    uint64_t score;
    uint64_t resources_iron;
    uint64_t resources_carbon;
    uint32_t wave;
} game_state_t;

/*
 * Liest den aktuellen Spiel-State aus dem Prozess.
 *
 * FIXME(RE): echte Werte (Score, Ressourcen, aktuelle Wave) ueber die per
 * scan/ ermittelten Adressen/Signaturen lesen. Bis dahin liefert diese
 * Funktion einen neutralen Snapshot (alles 0): Das Protokoll ist damit
 * end-to-end verdrahtet, die Werte folgen in der RE-Phase.
 */
static void read_game_state(game_state_t *st)
{
    memset(st, 0, sizeof(*st));
}

/*
 * send_state(): periodischer State-Snapshot (Egress, Issue #13).
 *
 * Emittiert score_update gemaeß trainer/protocol.md. Die Werte stammen aus
 * read_game_state() (bis zur RE-Phase Defaults, alles 0).
 */
static void send_state(HANDLE hPipe)
{
    game_state_t st;
    read_game_state(&st);
    send_line(hPipe,
              "{\"event\":\"score_update\",\"t\":%llu,\"score\":%llu,"
              "\"resources\":{\"iron\":%llu,\"carbon\":%llu},\"wave\":%u}",
              (unsigned long long)GetTickCount64(),
              (unsigned long long)st.score,
              (unsigned long long)st.resources_iron,
              (unsigned long long)st.resources_carbon,
              (unsigned)st.wave);
}

/* ------------------------------------------------------------------ */
/* ConsoleService-Anbindung (RE)                                       */
/*                                                                    */
/* Seit Build 2.0.58485 (GOG-Version == DedicatedServer-Build, DLL    */
/* md5-identisch, verifiziert 2026-09-09) sind die folgenden Offsets   */
/* gegen die Modul-Basis von riftbreaker_dll_win_release.dll           */
/* (ImageBase 0x180000000) bekannt. RVA-Quelle: RE-Protokoll vom       */
/* 2026-09-09 (trainer/scan + docs), Musterkatalog: patterns_v1.json   */
/* auf lan (Research-Ablage, Scan-Skripte trainer/scan).               */
/*                                                                    */
/*   RVA 0x2F23C80 : ConsoleService-vftable ??_7ConsoleService@Exor@@6B@
 *   RVA 0x1C0BEF0 : ConsoleService::ExecuteCommand(char const*)
 *                   (x64: this=RCX, cmd=RDX, void-Rueckgabe)
 *                                                                    */
/* ------------------------------------------------------------------ */

#define RBBRIDGE_MODULE_NAME        "riftbreaker_dll_win_release.dll"
#define RBBRIDGE_RVA_CONSOLE_VFTABLE 0x2F23C80UL /* ??_7ConsoleService@Exor@@6B@ */
#define RBBRIDGE_RVA_EXEC_COMMAND    0x1C0BEF0UL /* ExecuteCommand(char const*)   */

/* x64-Aufrufkonvention: this=RCX, cmd=RDX - __fastcall ist auf x64 der
 * Standard (das Schluesselwort dokumentiert die Konvention nur). */
typedef void (__fastcall *console_exec_fn)(void *self, const char *command);

/*
 * Lesbare, committete Region ohne PAGE_GUARD? (Lesen dort ist sicher.)
 */
static int is_readable_region(const MEMORY_BASIC_INFORMATION *mi)
{
    if (mi->State != MEM_COMMIT || (mi->Protect & PAGE_GUARD))
        return 0;
    switch (mi->Protect & 0xFF) {
    case PAGE_READONLY:
    case PAGE_READWRITE:
    case PAGE_WRITECOPY:
    case PAGE_EXECUTE_READ:
    case PAGE_EXECUTE_READWRITE:
    case PAGE_EXECUTE_WRITECOPY:
        return 1;
    default:
        return 0;
    }
}

/*
 * Findet die ConsoleService-Instanz im laufenden Spielprozess.
 *
 * Vorgehen:
 *   1. Modul-Basis per GetModuleHandle (kein LoadLibrary noetig - das
 *      Spiel hat riftbreaker_dll_win_release.dll laengst geladen).
 *   2. Erwartete vftable-Adresse = Basis + RVA 0x2F23C80.
 *   3. Scan des eigenen Adressraums (VirtualQuery-Schleife ueber alle
 *      MEM_COMMIT- und lesbaren Seiten): gesucht werden 8-Byte-Werte
 *      (little-endian) == Basis + 0x2F23C80 - also Ablagen des vftable-
 *      Zeigers. Nur 8-Byte-alignierte Kandidaten zaehlen: eine echte
 *      vftable-Ablage (Objektanfang, x64) liegt immer aligniert, ein
 *      Zufallstreffer auf exakt diesen Pointerwert waere praktisch
 *      ausgeschlossen (Absicherung gegen Muell). Self-check: QWORD an
 *      der Fundstelle muss == erwarteter vftable-Pointer sein (wird
 *      durch den Vergleich erfuellt - das Objekt, Fundstelle als this
 *      interpretiert, beginnt also mit seiner vftable).
 *   4. Alle Treffer zaehlen, ersten plausiblen Kandidaten nehmen.
 *
 * Rueckgabe: Instanz-Pointer (this) oder NULL - der Aufrufer darf sich
 * auf NULL NICHT verlassen, sondern muss sie als "nicht verfuegbar"
 * behandeln (Fehler-Event statt Crash).
 */
static void *resolve_console_service(void)
{
    HMODULE hMod = GetModuleHandleA(RBBRIDGE_MODULE_NAME);
    if (!hMod) {
        dbg("resolve_console_service: Modul '%s' nicht geladen (GLE=%lu) - "
            "kein Spielprozess?",
            RBBRIDGE_MODULE_NAME, (unsigned long)GetLastError());
        return NULL;
    }

    const unsigned char *base    = (const unsigned char *)hMod;
    const unsigned char *vftable = base + RBBRIDGE_RVA_CONSOLE_VFTABLE;
    const unsigned char *execfn  = base + RBBRIDGE_RVA_EXEC_COMMAND;
    const uint64_t needle = (uint64_t)(uintptr_t)vftable;

    /* vftable-Adresse muss in einer lesbaren Region liegen (sonst ist
     * der Offset fuer diesen Build/diese Basis unplausibel). */
    MEMORY_BASIC_INFORMATION mi;
    if (!VirtualQuery(vftable, &mi, sizeof(mi)) ||
        !is_readable_region(&mi)) {
        dbg("resolve_console_service: vftable=%p nicht in lesbarer Region "
            "(base=%p) - Abbruch", (void *)vftable, (void *)base);
        return NULL;
    }

    int hits = 0;
    void *instance = NULL;
    uintptr_t addr = 0; /* VirtualQuery ab Adresse 0 */

    for (;;) {
        if (VirtualQuery((const void *)addr, &mi, sizeof(mi)) == 0)
            break; /* Ende des Adressraums */
        uintptr_t next = (uintptr_t)mi.BaseAddress + mi.RegionSize;
        if (next <= addr) /* Overflow-Schutz (kommt praktisch nie vor) */
            break;
        addr = next;

        if (!is_readable_region(&mi))
            continue;

        const uint64_t *q = (const uint64_t *)mi.BaseAddress;
        size_t nq = mi.RegionSize / sizeof(uint64_t); /* BaseAddress ist
                                                         seiten-, also auch
                                                         8-Byte-aligniert */
        for (size_t i = 0; i < nq; i++) {
            /* Der Vergleich ist zugleich der Self-check: das QWORD an der
             * Fundstelle MUSS dem erwarteten vftable-Pointer entsprechen
             * (Fundstelle als this interpretiert => Objekt beginnt mit
             * seiner vftable; nur echte Ablagen erreichen diesen Zweig). */
            if (q[i] != needle)
                continue;
            hits++;
            if (instance == NULL)
                instance = (void *)&q[i]; /* erster plausibler Kandidat */
        }
    }

    dbg("resolve_console_service: base=%p vftable=%p execfn=%p hits=%d "
        "instance=%p",
        (void *)base, (void *)vftable, (void *)execfn, hits, instance);
    return instance;
}

/* ------------------------------------------------------------------ */
/* Dispatch: Ingress-Kommandos                                         */
/* ------------------------------------------------------------------ */

/*
 * {"cmd":"exec","command":"rb_wave 3"}
 *
 * Fuehrt das Kommando im Spiel aus - aequivalent zu
 *   ConsoleService::ExecuteCommand("rb_wave 3")
 * aus der Lua-Perspektive (der Lua-Mod registriert rb_wave, siehe
 * mod/lua/rbbattle_autoexec.lua im Spike-Branch).
 *
 * RE-Stand (Build 2.0.58485, GOG == Dedi, verifiziert 2026-09-09):
 *   - Instanz: per resolve_console_service() (Scan nach vftable-Zeiger,
 *     siehe oben - keine feste Adresse, ASLR-fest).
 *   - Aufruf: console_exec_fn(base + RVA 0x1C0BEF0)(inst, command),
 *     direkt im Pipe-Thread. pcall-artige Absicherung gibt es unter
 *     MinGW-x64 in C nicht (kein __try/__except; nur MSVC kann das) -
 *     die Absicherung ist der Instanz-Check oben: ohne gefundene
 *     Instanz wird NICHT aufgerufen, sondern ein Fehler-Event gesendet.
 *
 * Antwort bei Erfolg: {"event":"exec_result","ok":true,"command":"..."}
 */
static void dispatch_exec(HANDLE hPipe, const char *command)
{
    char escaped[RESP_BUF_SIZE];
    json_escape(command, escaped, sizeof(escaped));

    void *instance = resolve_console_service();
    if (!instance) {
        dbg("dispatch_exec: command='%s' -> keine ConsoleService-Instanz "
            "gefunden, KEIN Aufruf", command);
        send_line(hPipe,
                  "{\"event\":\"exec_result\",\"ok\":false,"
                  "\"command\":\"%s\",\"reason\":"
                  "\"console_service_not_found\"}",
                  escaped);
        return;
    }

    HMODULE hMod = GetModuleHandleA(RBBRIDGE_MODULE_NAME);
    if (!hMod) { /* kurz nach resolve() praktisch ausgeschlossen */
        dbg("dispatch_exec: Modul '%s' verschwunden (GLE=%lu)",
            RBBRIDGE_MODULE_NAME, (unsigned long)GetLastError());
        send_line(hPipe,
                  "{\"event\":\"exec_result\",\"ok\":false,"
                  "\"command\":\"%s\",\"reason\":"
                  "\"module_unloaded\"}",
                  escaped);
        return;
    }
    console_exec_fn fn =
        (console_exec_fn)((const unsigned char *)hMod +
                          RBBRIDGE_RVA_EXEC_COMMAND);

    dbg("dispatch_exec: command='%s' -> ExecuteCommand(inst=%p, fn=%p)",
        command, instance, (void *)fn);
    fn(instance, command); /* x64: this=RCX, cmd=RDX */

    dbg("dispatch_exec: command='%s' -> zurueckgekehrt (ok)", command);
    send_line(hPipe,
              "{\"event\":\"exec_result\",\"ok\":true,"
              "\"command\":\"%s\"}",
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

        /* State-Heartbeat (score_update, Egress Issue #13), nur bei Client */
        DWORD now = GetTickCount();
        if (last_beat == 0 || now - last_beat >= HEARTBEAT_MS) {
            last_beat = now;
            send_state(hPipe);
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
/* Gemeinsame Start-/Stopp-API (DLL-Attach UND Standalone-main)        */
/* ------------------------------------------------------------------ */

/*
 * Startet den Pipe-Server-Dienst (Pipe-Server-Thread + Init).
 * Wird gerufen von DllMain (DLL_PROCESS_ATTACH) und - im Standalone-
 * Modus - von main(). Doppelte Aufrufe sind dank g_thread_started ein
 * No-op (Rueckgabe 0 = ok/laeuft bereits, -1 = Fehler).
 */
int rbbridge_start(void)
{
    if (InterlockedCompareExchange(&g_thread_started, 1, 0) != 0)
        return 0; /* laeuft bereits (z.B. zweiter Attach) */

    /* Umgebungsvariable RBBRIDGE_LOG=0 schaltet das Datei-Log ab */
    char env[4] = "";
    if (GetEnvironmentVariableA("RBBRIDGE_LOG", env, sizeof(env)) > 0 &&
        strcmp(env, "0") == 0) {
        g_file_log = 0;
    }

    InitializeCriticalSection(&g_log_cs);
    g_stop = 0;

    /* WICHTIG (DLL-Fall): Hier laeuft das ggf. im DllMain-Kontext
     * (Loader-Lock) - nie blockieren/kein LoadLibrary, wir starten nur
     * einen unabhaengigen Thread. */
    g_thread = CreateThread(NULL, 0, pipe_server_main, NULL, 0, NULL);
    if (g_thread) {
        dbg("rbbridge_start: Pipe-Server-Thread laeuft");
        return 0;
    }
    dbg("rbbridge_start: CreateThread fehlgeschlagen (GLE=%lu)",
        GetLastError());
    InterlockedExchange(&g_thread_started, 0);
    DeleteCriticalSection(&g_log_cs);
    return -1;
}

/*
 * Stoppt den Pipe-Dienst (Wake-up + Join + Aufraeumen).
 * Wird gerufen von DllMain (DLL_PROCESS_DETACH) und von main() beim
 * Beenden (Ctrl+C im Standalone-Modus).
 */
void rbbridge_stop(void)
{
    if (!g_thread_started)
        return;

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
    InterlockedExchange(&g_thread_started, 0);
}

/* ------------------------------------------------------------------ */
/* DllMain (nur im DLL-Build aktiv; im Standalone-Build ungenutzt)     */
/* ------------------------------------------------------------------ */

BOOL WINAPI DllMain(HINSTANCE hinst, DWORD reason, LPVOID reserved)
{
    (void)reserved;

    switch (reason) {
    case DLL_PROCESS_ATTACH: {
        /* Nur der allererste Attach startet den Thread (Guard liegt in
         * rbbridge_start; Doppel-Attach ist dort ein No-op). */
        rbbridge_start();

        /* Handle erst im Detach schliessen (brauchen es zum Join).
         * "DisableThreadLibraryCalls" spart die Load/Unload-Benach-
         * richtigungen fuer andere Threads. */
        DisableThreadLibraryCalls(hinst);
        break;
    }

    case DLL_PROCESS_DETACH: {
        rbbridge_stop();
        break;
    }

    default:
        break;
    }
    return TRUE;
}

/* ------------------------------------------------------------------ */
/* Standalone-Modus: main() statt DllMain                              */
/*                                                                    */
/* Aktiv nur bei #define RBBRIDGE_STANDALONE - dann ist dieser         */
/* Quelltext ein normales Konsolen-Programm (rbbridge_standalone.exe)  */
/* mit IDENTISCHEM Pipe-Protokoll, aber ohne Injection/Spielprozess.   */
/* ------------------------------------------------------------------ */

#ifdef RBBRIDGE_STANDALONE

static volatile LONG g_running = 1; /* 0 = main() soll den Dienst stoppen */

static BOOL WINAPI ctrl_handler(DWORD ctrl_type)
{
    (void)ctrl_type;
    if (g_running) {
        dbg("rbbridge_standalone: Ctrl+C/Ctrl+Break - beende Dienst");
        InterlockedExchange((volatile LONG *)&g_running, 0);
        g_stop = 1;
    }
    return TRUE; /* Ereignis behandelt -> kein Standard-Exit */
}

int main(void)
{
    /* Ctrl+C soll den Dienst sauber stoppen statt den Prozess sofort zu
     * beenden (sonst bliebe der Pipe-Server-Thread haengen). */
    SetConsoleCtrlHandler(ctrl_handler, TRUE);

    if (rbbridge_start() != 0) {
        fprintf(stderr, "rbbridge_standalone: Start fehlgeschlagen (GLE=%lu)\n",
                (unsigned long)GetLastError());
        return 1;
    }

    /* Hinweiszeile (einziger sichtbarer Unterschied zur DLL-Variante): */
    printf("rbbridge_standalone: Pipe-Server aktiv auf %s - Standalone-\n"
           "Modus ohne Injection. Test: python pipe_client.py "
           "(ping/pong). Ctrl+C beendet.\n",
           PIPE_NAME_A);
    fflush(stdout);

    /* Hauptschleife: warten, bis Ctrl+C g_stop/g_running setzt */
    while (g_running)
        Sleep(200);

    rbbridge_stop();
    printf("rbbridge_standalone: beendet\n");
    return 0;
}

#endif /* RBBRIDGE_STANDALONE */
