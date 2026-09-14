#!/usr/bin/env python3
"""TEMP diagnostic (Issue #387, step 3) — dump the LuaGraphNode member strings.

Live finding from step 2: every LuaGraphNode-family instance shares the vftable
0x2F46D70 and `GetTypeName()` (slot 1) returns "LuaGraphNode" for ALL of them
-> no per-class C++ vtable marker. Each instance carries its own luabind
registry ref (+0x28). This probe dumps the member strings (std::string-like,
same decoder as probe v1: data@+0, size@+0x18, cap@+0x20, SSO at +0x08) at
+0x08 / +0x50 / +0x100 / +0x120 so we can find the node/class name that
separates dom_mananger from event_manager.

Removed again before the final commit.
"""
import io, os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
P = os.path.join(ROOT, "bausteine", "04-trainer-io", "rbbridge", "rbbridge.c")

src = io.open(P, encoding="utf-8").read()

start = src.find("/* TEMP-DIAG (#387")
if start != -1:
    end = src.find("static void handle_line(HANDLE hPipe, const char *line)")
    assert end > start
    src = src[:start] + src[end:]
    src = src.replace('''    if (strcmp(cmd, "probe") == 0) {
        probe_dom_nodes(hPipe);
        probe_resources(hPipe);
        return;
    }
''', '''    if (strcmp(cmd, "probe") == 0) {
        probe_resources(hPipe);
        return;
    }
''')

FN = r'''
/* TEMP-DIAG (#387): GetTypeHash-Return + Member-Strings dumpen. */
static uint64_t p3q(const unsigned char *p)
{
    uint64_t v = 0;
    if (!safe_read_u64(p, &v))
        return 0;
    return v;
}

static int p3byte(const unsigned char *p, unsigned char *out)
{
    MEMORY_BASIC_INFORMATION mi;
    if (!VirtualQuery(p, &mi, sizeof(mi)))
        return 0;
    if (!is_readable_region(&mi))
        return 0;
    if ((uintptr_t)p + 1 > (uintptr_t)mi.BaseAddress + mi.RegionSize)
        return 0;
    *out = *p;
    return 1;
}

/* std::string-artiger Member: data@+0, size@+0x18, cap@+0x20, SSO @+0x08. */
static void p3str(const unsigned char *s, char *out, size_t out_sz)
{
    uint64_t cap = 0, size = 0, ptr = 0;
    const unsigned char *data;
    size_t i, n = 0;
    out[0] = '\0';
    if (!safe_read_u64(s + 0x20, &cap))
        return;
    if (!safe_read_u64(s + 0x18, &size))
        return;
    if (cap <= 0xf) {
        if (cap == 0)
            return;
        data = s + 8;
    } else {
        if (!safe_read_u64(s, &ptr) || !ptr)
            return;
        data = (const unsigned char *)(uintptr_t)ptr;
    }
    if (size == 0 || size > 96)
        return;
    for (i = 0; i < size && n + 1 < out_sz; i++) {
        unsigned char c = 0;
        if (!p3byte(data + i, &c))
            break;
        out[n++] = (c >= 0x20 && c < 0x7f) ? (char)c : '.';
    }
    out[n] = '\0';
}

static uint32_t p3hash(const unsigned char *inst)
{
    const unsigned char *vt;
    uint64_t fn;
    typedef uint32_t (*gethash_fn)(const void *);
    vt = (const unsigned char *)(uintptr_t)p3q(inst);
    if (!vt)
        return 0;
    fn = p3q(vt + 16);
    if (!fn)
        return 0;
    return ((gethash_fn)(uintptr_t)fn)(inst);
}

static void probe_dom_nodes(HANDLE hPipe)
{
    const unsigned char *base = NULL;
    size_t size = 0;
    const char *via = NULL;
    const unsigned char *execfn = NULL;
    if (!resolve_module(&base, &size, &via, &execfn)) {
        send_line(hPipe, "{\"event\":\"probe\",\"error\":\"no_module\"}");
        return;
    }
    const uint64_t upd = (uint64_t)(uintptr_t)(base + 0x1BAA140u);
    send_line(hPipe, "{\"event\":\"probe\",\"kind\":\"domscan\","
                     "\"base\":\"0x%llx\"}",
              (unsigned long long)(uintptr_t)base);

    int total = 0, named = 0;
    uintptr_t addr = 0;
    for (;;) {
        MEMORY_BASIC_INFORMATION mi;
        if (VirtualQuery((const void *)addr, &mi, sizeof(mi)) == 0)
            break;
        uintptr_t next = (uintptr_t)mi.BaseAddress + mi.RegionSize;
        if (next <= addr)
            break;
        addr = next;
        if (!is_readable_region(&mi))
            continue;
        {
            const uint64_t *q = (const uint64_t *)mi.BaseAddress;
            size_t nq = mi.RegionSize / sizeof(uint64_t);
            size_t i;
            for (i = 0; i < nq; i++) {
                uint64_t vtv = q[i];
                const unsigned char *inst;
                char s08[40], s50[40], s100[40], s120[40];
                if (vtv < (uint64_t)(uintptr_t)base ||
                    vtv >= (uint64_t)(uintptr_t)(base + size))
                    continue;
                if (p3q((const unsigned char *)(uintptr_t)vtv + 7 * 8) != upd)
                    continue;
                inst = (const unsigned char *)&q[i];
                p3str(inst + 0x08, s08, sizeof(s08));
                p3str(inst + 0x50, s50, sizeof(s50));
                p3str(inst + 0x100, s100, sizeof(s100));
                p3str(inst + 0x120, s120, sizeof(s120));
                total++;
                if (named >= 80)
                    continue;
                named++;
                send_line(hPipe,
                    "{\"event\":\"probe\",\"kind\":\"domname\",\"addr\":\"0x%llx\","
                    "\"ref\":%lld,\"hash\":\"0x%x\",\"s08\":\"%s\",\"s50\":\"%s\","
                    "\"s100\":\"%s\",\"s120\":\"%s\"}",
                    (unsigned long long)(uintptr_t)inst,
                    (long long)p3q(inst + 0x28), p3hash(inst),
                    s08, s50, s100, s120);
            }
        }
    }
    send_line(hPipe, "{\"event\":\"probe\",\"kind\":\"domdone\",\"hits\":%d,"
                     "\"named\":%d}", total, named);
}
'''

anchor = "static void handle_line(HANDLE hPipe, const char *line)\n{"
assert src.count(anchor) == 1, "handle_line anchor"
src = src.replace(anchor, FN + "\n" + anchor)

d = '''    if (strcmp(cmd, "probe") == 0) {
        probe_resources(hPipe);
        return;
    }
'''
assert src.count(d) == 1
src = src.replace(d, '''    if (strcmp(cmd, "probe") == 0) {
        probe_dom_nodes(hPipe);
        probe_resources(hPipe);
        return;
    }
''')

io.open(P, "w", encoding="utf-8", newline="\n").write(src)
print("inserted probe v4")
