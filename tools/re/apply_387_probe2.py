#!/usr/bin/env python3
"""TEMP diagnostic (Issue #387, step 2) — replace the first probe with a
vtable/GetTypeName probe that answers:
  (a) do Lua-derived classes (dom_mananger, event_manager) get their OWN
      C++ vftable, or do they share LuaGraphNode's 0x2F46D70?
  (b) what does the virtual `GetTypeName()` (vtable slot 1) return per
      instance -> is it usable as a C++-only class marker?

Scan criterion: object whose first qword points into the game module AND whose
vtable slot 7 == LuaGraphNode::Update (the family entry point). That finds ALL
LuaGraphNode-family instances regardless of exact vtable. Then it calls slot 1
(GetTypeName) and dumps the luabind object at +0x20.

Removed again before the final commit.
"""
import io, os, re

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
P = os.path.join(ROOT, "bausteine", "04-trainer-io", "rbbridge", "rbbridge.c")

src = io.open(P, encoding="utf-8").read()

# Drop any previously inserted probe (idempotent re-apply).
start = src.find("/* TEMP-DIAG (#387")
if start != -1:
    end = src.find("static void handle_line(HANDLE hPipe, const char *line)")
    assert end > start
    src = src[:start] + src[end:]
    src = src.replace("""    if (strcmp(cmd, "probe") == 0) {
        probe_resources(hPipe);
        probe_dom_nodes(hPipe);
        return;
    }
""", """    if (strcmp(cmd, "probe") == 0) {
        probe_resources(hPipe);
        return;
    }
""")

FN = r'''
/* TEMP-DIAG (#387): LuaGraphNode-Familie + GetTypeName-Probe. */
static uint64_t p2q(const unsigned char *p)
{
    uint64_t v = 0;
    if (!safe_read_u64(p, &v))
        return 0;
    return v;
}

static int p2byte(const unsigned char *p, unsigned char *out)
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

static void p2str(const unsigned char *p, char *out, size_t out_sz)
{
    size_t i;
    out[0] = '\0';
    if (!p)
        return;
    for (i = 0; i + 1 < out_sz; i++) {
        unsigned char c = 0;
        if (!p2byte(p + i, &c) || c == 0)
            break;
        out[i] = (c >= 0x20 && c < 0x7f) ? (char)c : '.';
        out[i + 1] = '\0';
    }
}

/* GetTypeName() ueber die vtable (Slot 1) aufrufen — reiner C++-Virtual-Call. */
static void p2typename(const unsigned char *inst, char *out, size_t out_sz)
{
    const unsigned char *vt;
    uint64_t fn;
    const char *nm;
    typedef const char *(*getname_fn)(const void *);
    out[0] = '\0';
    vt = (const unsigned char *)(uintptr_t)p2q(inst);
    if (!vt)
        return;
    fn = p2q(vt + 8);
    if (!fn)
        return;
    nm = ((getname_fn)(uintptr_t)fn)(inst);
    p2str((const unsigned char *)nm, out, out_sz);
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

    const uint64_t vt_lgn   = (uint64_t)(uintptr_t)(base + 0x2F46D70u);
    const uint64_t vt_sel   = (uint64_t)(uintptr_t)(base + 0x2F46F38u);
    const uint64_t upd      = (uint64_t)(uintptr_t)(base + 0x1BAA140u);

    send_line(hPipe, "{\"event\":\"probe\",\"kind\":\"domscan\","
                     "\"base\":\"0x%llx\",\"vt_lgn\":\"0x%llx\","
                     "\"vt_sel\":\"0x%llx\",\"update\":\"0x%llx\"}",
              (unsigned long long)(uintptr_t)base,
              (unsigned long long)vt_lgn,
              (unsigned long long)vt_sel,
              (unsigned long long)upd);

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
        {
            const uint64_t *q = (const uint64_t *)mi.BaseAddress;
            size_t nq = mi.RegionSize / sizeof(uint64_t);
            size_t i;
            for (i = 0; i < nq; i++) {
                uint64_t vtv = q[i];
                /* vtable muss im Modul-Image liegen */
                if (vtv < (uint64_t)(uintptr_t)base ||
                    vtv >= (uint64_t)(uintptr_t)(base + size))
                    continue;
                /* Familien-Kriterium: Slot 7 == LuaGraphNode::Update */
                if (p2q((const unsigned char *)(uintptr_t)vtv + 7 * 8) != upd)
                    continue;
                {
                    const unsigned char *inst = (const unsigned char *)&q[i];
                    char tn[120], obj[64];
                    unsigned char f0 = 0, f1 = 0;
                    uint64_t s0, s1, s2, s8;
                    s0 = p2q((const unsigned char *)(uintptr_t)vtv);
                    s1 = p2q((const unsigned char *)(uintptr_t)vtv + 8);
                    s2 = p2q((const unsigned char *)(uintptr_t)vtv + 16);
                    s8 = p2q((const unsigned char *)(uintptr_t)vtv + 64);
                    p2typename(inst, tn, sizeof(tn));
                    p2byte(inst + 0xf0, &f0);
                    p2byte(inst + 0xf1, &f1);
                    {
                        uint64_t la = p2q(inst + 0x20);
                        uint64_t lref = p2q(inst + 0x28);
                        _snprintf(obj, sizeof(obj), "0x%llx/%lld",
                                  (unsigned long long)la, (long long)lref);
                    }
                    send_line(hPipe,
                        "{\"event\":\"probe\",\"kind\":\"domnode\",\"i\":%d,"
                        "\"addr\":\"0x%llx\",\"vt\":\"0x%llx\","
                        "\"s0\":\"0x%llx\",\"s1\":\"0x%llx\","
                        "\"s2\":\"0x%llx\",\"s8\":\"0x%llx\","
                        "\"tn\":\"%s\",\"f0\":%u,\"f1\":%u,\"la\":\"%s\"}",
                        total, (unsigned long long)(uintptr_t)inst,
                        (unsigned long long)vtv,
                        (unsigned long long)(s0 ? s0 - (uint64_t)(uintptr_t)base : 0),
                        (unsigned long long)(s1 ? s1 - (uint64_t)(uintptr_t)base : 0),
                        (unsigned long long)(s2 ? s2 - (uint64_t)(uintptr_t)base : 0),
                        (unsigned long long)(s8 ? s8 - (uint64_t)(uintptr_t)base : 0),
                        tn, (unsigned)f0, (unsigned)f1, obj);
                    total++;
                    if (total >= 40)
                        break;
                }
            }
        }
        if (total >= 40)
            break;
    }
    send_line(hPipe, "{\"event\":\"probe\",\"kind\":\"domdone\",\"hits\":%d}",
              total);
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
assert src.count(d) == 1, "probe dispatch anchor"
src = src.replace(d, '''    if (strcmp(cmd, "probe") == 0) {
        probe_resources(hPipe);
        probe_dom_nodes(hPipe);
        return;
    }
''')

io.open(P, "w", encoding="utf-8", newline="\n").write(src)
print("inserted probe v2")
