#!/usr/bin/env python3
"""TEMP diagnostic (Issue #387): insert a `probe_dom` command into rbbridge.c
that scans for the LuaGraphNode-family vtables and dumps raw qwords + decoded
UtfStrings so we can find a C++-only dom_mananger marker from live memory.
Removed again before the final commit."""
import io, os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
P = os.path.join(ROOT, "bausteine", "04-trainer-io", "rbbridge", "rbbridge.c")

src = io.open(P, encoding="utf-8").read()

FN = r'''
/* TEMP-DIAG (#387): LuaGraphNode-Familie dumpen. */
static int dom_byte(const unsigned char *p, unsigned char *out)
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

static void dom_ustr_json(char *out, size_t out_sz, const unsigned char *s)
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
        data = s + 8;
    } else {
        if (!safe_read_u64(s, &ptr) || !ptr)
            return;
        data = (const unsigned char *)(uintptr_t)ptr;
    }
    if (size == 0 || size > 128)
        return;
    for (i = 0; i < size && n + 2 < out_sz; i++) {
        unsigned char c = 0;
        if (!dom_byte(data + i, &c))
            break;
        if (c == '"' || c == '\\') {
            out[n++] = '\\';
            out[n++] = (char)c;
        } else if (c >= 0x20 && c < 0x7f) {
            out[n++] = (char)c;
        } else {
            out[n++] = '.';
        }
    }
    out[n] = '\0';
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

    static const uint32_t vt_rvas[3] = { 0x2F46D70u, 0x2F46DB8u, 0x2F46E00u };
    uint64_t needles[3];
    for (int k = 0; k < 3; k++)
        needles[k] = (uint64_t)(uintptr_t)(base + vt_rvas[k]);

    send_line(hPipe, "{\"event\":\"probe\",\"kind\":\"domscan\","
                     "\"base\":\"0x%llx\"}",
              (unsigned long long)(uintptr_t)base);

    int total = 0;
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
        const uint64_t *q = (const uint64_t *)mi.BaseAddress;
        size_t nq = mi.RegionSize / sizeof(uint64_t);
        for (size_t i = 0; i < nq; i++) {
            int k = -1;
            for (int t = 0; t < 3; t++)
                if (q[i] == needles[t]) { k = t; break; }
            if (k < 0)
                continue;
            const unsigned char *inst = (const unsigned char *)&q[i];
            char s08[300], s50[300], s100[300], s120[300];
            dom_ustr_json(s08, sizeof(s08), inst + 0x08);
            dom_ustr_json(s50, sizeof(s50), inst + 0x50);
            dom_ustr_json(s100, sizeof(s100), inst + 0x100);
            dom_ustr_json(s120, sizeof(s120), inst + 0x120);
            unsigned char f0 = 0, f1 = 0;
            dom_byte(inst + 0xf0, &f0);
            dom_byte(inst + 0xf1, &f1);
            uint64_t nchild = 0, child = 0;
            safe_read_u64(inst + 0xc0, &nchild);
            safe_read_u64(inst + 0xb8, &child);
            send_line(hPipe,
                "{\"event\":\"probe\",\"kind\":\"domnode\",\"vt\":\"0x%x\","
                "\"i\":%d,\"addr\":\"0x%llx\",\"s08\":\"%s\",\"s50\":\"%s\","
                "\"s100\":\"%s\",\"s120\":\"%s\",\"f0\":%u,\"f1\":%u,"
                "\"nchild\":%llu,\"child\":\"0x%llx\"}",
                vt_rvas[k], total, (unsigned long long)(uintptr_t)inst,
                s08, s50, s100, s120, (unsigned)f0, (unsigned)f1,
                (unsigned long long)nchild, (unsigned long long)child);
            total++;
            if (total >= 80)
                break;
        }
        if (total >= 80)
            break;
    }
    send_line(hPipe, "{\"event\":\"probe\",\"kind\":\"domdone\","
                     "\"hits\":%d}", total);
}
'''

if "probe_dom_nodes" in src:
    print("already inserted")
    raise SystemExit(0)

anchor = "static void handle_line(HANDLE hPipe, const char *line)\n{"
assert src.count(anchor) == 1, "handle_line anchor"
src = src.replace(anchor, FN + "\n" + anchor)

d = '''    if (strcmp(cmd, "probe") == 0) {
        probe_resources(hPipe);
        return;
    }
'''
assert src.count(d) == 1, "probe dispatch anchor"
src = src.replace(d, '''    if (strcmp(cmd, "probe") == 0) {
        probe_resources(hPipe);
        probe_dom_nodes(hPipe);
        return;
    }
''')

io.open(P, "w", encoding="utf-8", newline="\n").write(src)
print("inserted probe_dom_nodes")
