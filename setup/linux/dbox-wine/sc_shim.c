/*
 * sc.exe stand-in for the HaptiSync installer under Wine.
 *
 * Why: this Wine build's sc.exe does not implement "sc config" ("fixme:sc:wmain command not supported").
 * The installer runs "sc config DboxMotionEngine depend= ..." and, when that fails, rolls the whole install back.
 *
 * What it does:
 *   - "sc config|failure|description|sdset|sidtype|triggerinfo|privs ..." : prints a success line, exits 0.
 *     (Wine can't do these anyway, so the only alternative is a failure.)
 *   - every other command (create, start, stop, delete, query, ...) : passed straight to the real
 *     C:\windows\system32\sc.exe with the same arguments, same standard handles, same exit code.
 *     One exception: "sc start DboxHaptiSyncApi" is run for real but always reports success.
 *   - every call, and the real sc.exe's exit code, is appended to C:\scshim.log.
 *
 * It only takes effect when it sits in the same folder as the installer exe. It has no CRT and imports
 * only kernel32.dll.
 */
typedef unsigned short WCHAR;
typedef unsigned short WORD;
typedef unsigned int DWORD;
typedef unsigned char BYTE;
typedef int BOOL;
typedef void *HANDLE;
typedef WCHAR *LPWSTR;
typedef const WCHAR *LPCWSTR;

typedef struct {
    DWORD cb;
    LPWSTR lpReserved;
    LPWSTR lpDesktop;
    LPWSTR lpTitle;
    DWORD dwX, dwY, dwXSize, dwYSize, dwXCountChars, dwYCountChars, dwFillAttribute, dwFlags;
    WORD wShowWindow, cbReserved2;
    BYTE *lpReserved2;
    HANDLE hStdInput, hStdOutput, hStdError;
} STARTUPINFOW;

typedef struct {
    HANDLE hProcess, hThread;
    DWORD dwProcessId, dwThreadId;
} PROCESS_INFORMATION;

#define IMP __declspec(dllimport)
IMP WCHAR *GetCommandLineW(void);
IMP HANDLE GetStdHandle(DWORD);
IMP BOOL CreateProcessW(LPCWSTR, LPWSTR, void *, void *, BOOL, DWORD, void *, LPCWSTR,
                        STARTUPINFOW *, PROCESS_INFORMATION *);
IMP DWORD WaitForSingleObject(HANDLE, DWORD);
IMP BOOL GetExitCodeProcess(HANDLE, DWORD *);
IMP void ExitProcess(DWORD) __attribute__((noreturn));
IMP HANDLE CreateFileW(LPCWSTR, DWORD, DWORD, void *, DWORD, DWORD, HANDLE);
IMP BOOL WriteFile(HANDLE, const void *, DWORD, DWORD *, void *);
IMP int WideCharToMultiByte(unsigned, DWORD, LPCWSTR, int, char *, int, const char *, BOOL *);
IMP unsigned GetSystemDirectoryW(WCHAR *, unsigned);
IMP BOOL CloseHandle(HANDLE);

static WCHAR cmdbuf[32768];
static char logbuf[65536];
static STARTUPINFOW si;
static PROCESS_INFORMATION pi;

static const WCHAR *skip_ws(const WCHAR *p) {
    while (*p == ' ' || *p == '\t') p++;
    return p;
}

/* skip one command-line token (program name or argument), honouring a leading quote */
static const WCHAR *skip_token(const WCHAR *p) {
    if (*p == '"') {
        p++;
        while (*p && *p != '"') p++;
        if (*p) p++;
    } else {
        while (*p && *p != ' ' && *p != '\t') p++;
    }
    return p;
}

static int lower(WCHAR c) { return (c >= 'A' && c <= 'Z') ? c + 32 : c; }

/* does the token at p equal the lowercase ASCII word (case-insensitive)? */
static int tok_eq(const WCHAR *p, const char *word) {
    while (*word) {
        if (lower(*p) != (unsigned char)*word) return 0;
        p++;
        word++;
    }
    return *p == 0 || *p == ' ' || *p == '\t';
}

static void write_log(const char *tag, const WCHAR *cl) {
    HANDLE h = CreateFileW(L"C:\\scshim.log", 0x0004 /* FILE_APPEND_DATA */, 3, 0, 4 /* OPEN_ALWAYS */, 0x80, 0);
    if (h == (HANDLE)(long long)-1 || !h) return;
    int n = 0;
    while (tag[n]) { logbuf[n] = tag[n]; n++; }
    int w = WideCharToMultiByte(65001, 0, cl, -1, logbuf + n, (int)sizeof(logbuf) - n - 4, 0, 0);
    if (w > 0) n += w - 1; /* drop the NUL */
    logbuf[n++] = '\r';
    logbuf[n++] = '\n';
    DWORD wr;
    WriteFile(h, logbuf, (DWORD)n, &wr, 0);
    CloseHandle(h);
}

/* log the real sc.exe's exit code, e.g. "EXIT 1053 <command line>" */
static void log_exit(DWORD code, const WCHAR *cl) {
    char tag[24];
    int n = 0;
    const char *pre = "EXIT ";
    while (*pre) tag[n++] = *pre++;
    char dig[12];
    int d = 0;
    DWORD v = code;
    if (v == 0) dig[d++] = '0';
    while (v) { dig[d++] = (char)('0' + v % 10); v /= 10; }
    while (d) tag[n++] = dig[--d];
    tag[n++] = ' ';
    tag[n] = 0;
    write_log(tag, cl);
}

static void say(const char *s) {
    DWORD n = 0, wr;
    while (s[n]) n++;
    HANDLE out = GetStdHandle((DWORD)-11);
    if (out && out != (HANDLE)(long long)-1) WriteFile(out, s, n, &wr, 0);
}

void mainCRTStartup(void) {
    const WCHAR *cl = GetCommandLineW();
    const WCHAR *args = skip_ws(skip_token(cl)); /* everything after the program name */

    /* subcommand: first argument, ignoring an optional \\server */
    const WCHAR *sub = args;
    if (sub[0] == '\\' && sub[1] == '\\') sub = skip_ws(skip_token(sub));

    const char *msg = 0;
    if (tok_eq(sub, "config")) msg = "[SC] ChangeServiceConfig SUCCESS\r\n";
    else if (tok_eq(sub, "sdset")) msg = "[SC] SetServiceObjectSecurity SUCCESS\r\n";
    else if (tok_eq(sub, "failure") || tok_eq(sub, "description") || tok_eq(sub, "sidtype") ||
             tok_eq(sub, "triggerinfo") || tok_eq(sub, "privs"))
        msg = "[SC] ChangeServiceConfig2 SUCCESS\r\n";

    if (msg) {
        write_log("NOOP ", cl);
        say(msg);
        ExitProcess(0);
    }

    write_log("PASS ", cl);

    /* "sc start DboxHaptiSyncApi": run it for real, but report success even if it fails to start.
       That web-UI service isn't needed for game motion, and the installer rolls everything back if it fails. */
    int tolerate = 0;
    if (tok_eq(sub, "start")) {
        const WCHAR *nm = skip_ws(skip_token(sub));
        if (tok_eq(nm, "dboxhaptisyncapi")) tolerate = 1;
    }

    /* pass through to the real sc.exe, by full path so we never call ourselves */
    WCHAR real[300];
    unsigned len = GetSystemDirectoryW(real, 260);
    if (len == 0 || len > 260) ExitProcess(1);
    const WCHAR tail[] = L"\\sc.exe";
    for (int i = 0; i < 8; i++) real[len + i] = tail[i];

    unsigned k = 0;
    cmdbuf[k++] = '"';
    for (unsigned i = 0; real[i]; i++) cmdbuf[k++] = real[i];
    cmdbuf[k++] = '"';
    cmdbuf[k++] = ' ';
    for (const WCHAR *p = args; *p && k < 32760; p++) cmdbuf[k++] = *p;
    cmdbuf[k] = 0;

    si.cb = sizeof(si);
    si.dwFlags = 0x100; /* STARTF_USESTDHANDLES */
    si.hStdInput = GetStdHandle((DWORD)-10);
    si.hStdOutput = GetStdHandle((DWORD)-11);
    si.hStdError = GetStdHandle((DWORD)-12);

    if (!CreateProcessW(real, cmdbuf, 0, 0, 1, 0, 0, 0, &si, &pi)) ExitProcess(1);
    WaitForSingleObject(pi.hProcess, 0xFFFFFFFF);
    DWORD code = 1;
    GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);
    log_exit(code, cl);
    if (tolerate) code = 0;
    ExitProcess(code);
}
