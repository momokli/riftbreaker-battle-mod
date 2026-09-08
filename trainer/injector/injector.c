/*
 * injector.c - minimaler DLL-Injector fuer den Rift-Breaker-Trainer (Harness).
 *
 * Zweck:
 *   Laedt eine DLL (rbbridge.dll) zur Laufzeit in einen laufenden Prozess -
 *   klassisches Remote-LoadLibrary-Verfahren. Es werden KEINERLEI Dateien im
 *   Spielverzeichnis veraendert (Steam-Kompatibilitaet: runtime-only).
 *
 * Ablauf:
 *   1. Zielprozess ermitteln (PID oder Prozessname, z.B. "riftbreaker" oder
 *      "riftbreaker.exe"; Gross-/Kleinschreibung egal)
 *   2. OpenProcess mit den noetigen VM-Rechten
 *   3. VirtualAllocEx      -> Platz fuer den DLL-Pfad im Zielprozess
 *   4. WriteProcessMemory  -> absoluten DLL-Pfad hineinschreiben (UTF-16)
 *   5. CreateRemoteThread  -> dort LoadLibraryW(pfad) ausfuehren
 *   6. GetExitCodeThread   -> HMODULE der geladenen DLL = Erfolgsbeweis
 *   7. Aufraeumen (VirtualFreeEx, Handles schliessen)
 *
 * Wichtig:
 *   - Architektur: Injector MUSS die gleiche Bitness haben wie das Ziel
 *     (Rift Breaker ist 64-bit -> injector.exe als x64 bauen). Ein x86-Ziel
 *     wird erkannt und abgelehnt (IsWow64Process2).
 *   - Kein Anti-Cheat im Spiel, kein Administrator noetig, solange Injector
 *     und Spiel vom selben Windows-Benutzer laufen.
 *   - Der Pfad wird lokal zu einem ABSOLUTEN Pfad aufgeloest, damit
 *     LoadLibraryW im Zielprozess nicht vom Arbeitsverzeichnis des Spiels
 *     abhaengt.
 *   - LoadLibraryW (statt LoadLibraryA) => auch Nicht-ASCII-Pfade ok.
 *
 * Build (x64):
 *   MinGW-w64 : x86_64-w64-mingw32-gcc -O2 -Wall -Wextra -o injector.exe injector.c
 *   MSVC      : cl /nologo /O2 /W3 injector.c shell32.lib /Fe:injector.exe
 *   (shell32.lib nur wegen CommandLineToArgvW; MinGW linkt das automatisch)
 *
 * Aufruf:
 *   injector.exe <pid|prozessname> <pfad\zu\rbbridge.dll>
 */

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <tlhelp32.h>
#include <shellapi.h>

#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>

/* Laenger als 15 s sollte LoadLibraryW im Ziel nie brauchen. */
#define INJECT_TIMEOUT_MS 15000

/* ------------------------------------------------------------------ */
/* Hilfsfunktionen                                                     */
/* ------------------------------------------------------------------ */

static int ends_with_icase(const wchar_t *s, const wchar_t *suffix)
{
    size_t ls = wcslen(s);
    size_t lx = wcslen(suffix);
    if (lx > ls)
        return 0;
    return _wcsicmp(s + ls - lx, suffix) == 0;
}

/*
 * Findet die PID eines Prozesses ueber den Namen (Toolhelp32-Snapshot).
 * Akzeptiert "riftbreaker" wie "riftbreaker.exe". Rueckgabe 0 = nicht
 * gefunden oder Fehler.
 */
static DWORD find_pid_by_name(const wchar_t *name)
{
    wchar_t with_exe[MAX_PATH];
    const wchar_t *needle_a = name;
    const wchar_t *needle_b = NULL;

    if (!ends_with_icase(name, L".exe")) {
        /* zweite Variante mit angehaengtem ".exe" mitpruefen */
        if (wcslen(name) + 4 >= MAX_PATH)
            return 0;
        swprintf(with_exe, MAX_PATH, L"%ls.exe", name);
        needle_b = with_exe;
    }

    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (snap == INVALID_HANDLE_VALUE)
        return 0;

    PROCESSENTRY32W pe;
    pe.dwSize = sizeof(pe);

    DWORD pid = 0;
    for (BOOL ok = Process32FirstW(snap, &pe); ok; ok = Process32NextW(snap, &pe)) {
        if (_wcsicmp(pe.szExeFile, needle_a) == 0 ||
            (needle_b && _wcsicmp(pe.szExeFile, needle_b) == 0)) {
            pid = pe.th32ProcessID;
            break;
        }
    }
    CloseHandle(snap);
    return pid;
}

/*
 * Bitness-Check: Der (x64-)Injector kann nur in x64-Prozesse laden.
 * IsWow64Process2 gibt es erst ab Win10; auf aelteren Systemen faellt
 * der Check auf IsWow64Process zurueck. Wenn nichts verfuegbar ist,
 * melden wir "kein WOW64" (best effort, LoadLibraryW wuerde dann eh
 * fehlschlagen und wird sauber gemeldet).
 */
static int target_is_wow64(HANDLE hProc)
{
    typedef BOOL(WINAPI *fn_iswow64process2)(HANDLE, USHORT *, USHORT *);
    typedef BOOL(WINAPI *fn_iswow64process)(HANDLE, PBOOL);

    fn_iswow64process2 p2 = (fn_iswow64process2)GetProcAddress(
        GetModuleHandleA("kernel32.dll"), "IsWow64Process2");
    if (p2) {
        USHORT process_machine = 0, native_machine = 0;
        if (p2(hProc, &process_machine, &native_machine))
            return process_machine == IMAGE_FILE_MACHINE_I386;
        return 0;
    }

    fn_iswow64process p1 = (fn_iswow64process)GetProcAddress(
        GetModuleHandleA("kernel32.dll"), "IsWow64Process");
    if (p1) {
        BOOL is_wow = FALSE;
        if (p1(hProc, &is_wow))
            return is_wow != FALSE;
    }
    return 0;
}

/* ------------------------------------------------------------------ */
/* Kern: injizieren                                                     */
/* ------------------------------------------------------------------ */

static int inject_into(HANDLE hProc, const wchar_t *dll_path)
{
    /* 1) Adresse von LoadLibraryW im ZIELprozess: kernel32 ist bei
     *    normalen Prozessen an derselben Basis geladen wie bei uns,
     *    daher ist unsere lokale Adresse dort gueltig. */
    HMODULE k32 = GetModuleHandleA("kernel32.dll");
    FARPROC load_lib = GetProcAddress(k32, "LoadLibraryW");
    if (!load_lib) {
        printf("[-] GetProcAddress(LoadLibraryW) fehlgeschlagen\n");
        return 1;
    }

    /* 2) Platz fuer den Pfad (UTF-16 inkl. Terminator) reservieren */
    size_t path_bytes = (wcslen(dll_path) + 1) * sizeof(wchar_t);
    LPVOID remote = VirtualAllocEx(hProc, NULL, path_bytes,
                                   MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!remote) {
        printf("[-] VirtualAllocEx fehlgeschlagen (GLE=%lu)\n", GetLastError());
        return 1;
    }

    /* 3) Pfad hineinschreiben */
    SIZE_T written = 0;
    if (!WriteProcessMemory(hProc, remote, dll_path, path_bytes, &written) ||
        written != path_bytes) {
        printf("[-] WriteProcessMemory fehlgeschlagen (GLE=%lu)\n", GetLastError());
        VirtualFreeEx(hProc, remote, 0, MEM_RELEASE);
        return 1;
    }

    /* 4) LoadLibraryW im Zielprozess ausfuehren */
    HANDLE hThread = CreateRemoteThread(hProc, NULL, 0,
                                        (LPTHREAD_START_ROUTINE)load_lib,
                                        remote, 0, NULL);
    if (!hThread) {
        printf("[-] CreateRemoteThread fehlgeschlagen (GLE=%lu)\n", GetLastError());
        VirtualFreeEx(hProc, remote, 0, MEM_RELEASE);
        return 1;
    }

    /* 5) Auf das Ende von LoadLibraryW warten (Timeoutschutz) */
    DWORD wait = WaitForSingleObject(hThread, INJECT_TIMEOUT_MS);
    if (wait == WAIT_TIMEOUT) {
        /* Thread laeuft evtl. noch und nutzt remote-Speicher -> NICHT
         * freigeben, nur warnen. (Praktisch unmoeglich bei LoadLibraryW.) */
        printf("[!] Inject-Thread nach %u ms noch aktiv; DLL laedt evtl. noch.\n",
               INJECT_TIMEOUT_MS);
        CloseHandle(hThread);
        return 1;
    }

    /* 6) HMODULE der DLL im Zielprozess als Erfolgsnachweis */
    DWORD exit_code = 0;
    if (!GetExitCodeThread(hThread, &exit_code) || exit_code == 0) {
        printf("[-] LoadLibraryW im Ziel fehlgeschlagen (ExitCode=0, GLE=%lu)\n",
               GetLastError());
        VirtualFreeEx(hProc, remote, 0, MEM_RELEASE);
        CloseHandle(hThread);
        return 1;
    }

    printf("[+] rbbridge.dll geladen: HMODULE=0x%p (pid laeuft weiter)\n",
           (void *)(ULONG_PTR)exit_code);

    /* 7) Aufraeumen */
    VirtualFreeEx(hProc, remote, 0, MEM_RELEASE);
    CloseHandle(hThread);
    return 0;
}

/* ------------------------------------------------------------------ */
/* main                                                                */
/* ------------------------------------------------------------------ */

static void usage(const wchar_t *prog)
{
    wprintf(L"Usage: %ls <pid|prozessname> <pfad\\zu\\rbbridge.dll>\n"
            L"  Bsp. : %ls 4821  trainer\\rbbridge\\rbbridge.dll\n"
            L"         %ls riftbreaker.exe trainer\\rbbridge\\rbbridge.dll\n"
            L"  (Prozessname ohne/mit .exe; Gross-/Kleinschreibung egal)\n",
            prog, prog, prog);
}

int main(void)
{
    int argc = 0;
    LPWSTR *argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    if (!argv) {
        printf("[-] CommandLineToArgvW fehlgeschlagen\n");
        return 2;
    }

    int rc = 2;
    if (argc != 3) {
        usage(argc > 0 ? argv[0] : L"injector.exe");
        goto out;
    }

    const wchar_t *target = argv[1];
    const wchar_t *dll_path_arg = argv[2];

    /* PID oder Name? */
    DWORD pid = 0;
    int is_numeric = target[0] != L'\0';
    for (const wchar_t *p = target; *p; p++) {
        if (*p < L'0' || *p > L'9') {
            is_numeric = 0;
            break;
        }
    }
    if (is_numeric) {
        pid = (DWORD)wcstol(target, NULL, 10);
        if (pid == 0) {
            wprintf(L"[-] Ungueltige PID: %ls\n", target);
            goto out;
        }
    } else {
        pid = find_pid_by_name(target);
        if (pid == 0) {
            wprintf(L"[-] Prozess '%ls' nicht gefunden (Name oder PID pruefen)\n",
                    target);
            goto out;
        }
        wprintf(L"[+] Prozess '%ls' gefunden: pid=%lu\n", target, (unsigned long)pid);
    }

    /* DLL lokal zu absolut aufloesen und Existenz pruefen */
    wchar_t dll_path[MAX_PATH];
    DWORD r = GetFullPathNameW(dll_path_arg, MAX_PATH, dll_path, NULL);
    if (r == 0 || r >= MAX_PATH) {
        wprintf(L"[-] DLL-Pfad ungueltig: %ls\n", dll_path_arg);
        goto out;
    }
    if (GetFileAttributesW(dll_path) == INVALID_FILE_ATTRIBUTES) {
        wprintf(L"[-] DLL nicht gefunden: %ls\n", dll_path);
        goto out;
    }

    /* Prozess oeffnen (Rechte fuer Injection + Query) */
    HANDLE hProc = OpenProcess(PROCESS_CREATE_THREAD | PROCESS_QUERY_INFORMATION |
                                   PROCESS_VM_OPERATION | PROCESS_VM_WRITE |
                                   PROCESS_VM_READ,
                               FALSE, pid);
    if (!hProc) {
        printf("[-] OpenProcess(pid=%lu) fehlgeschlagen (GLE=%lu) - gleicher "
               "Windows-Benutzer? Spiel laeuft?\n",
               (unsigned long)pid, GetLastError());
        goto out;
    }

    if (target_is_wow64(hProc)) {
        printf("[-] Ziel ist ein 32-bit-Prozess; dieser x64-Injector kann nur "
               "in x64-Prozesse laden.\n");
        CloseHandle(hProc);
        goto out;
    }

    wprintf(L"[+] Injiziere: %ls\n", dll_path);
    rc = inject_into(hProc, dll_path);

    CloseHandle(hProc);

out:
    LocalFree(argv);
    return rc;
}
