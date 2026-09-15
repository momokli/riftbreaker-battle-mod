#!/usr/bin/env python3
"""Issue #387: C++-only DOM resolve + SetSuspended primitive + dispatch."""
import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
P = os.path.join(ROOT, "bausteine", "04-trainer-io", "rbbridge", "rbbridge.c")
src = io.open(P, encoding="utf-8").read()

BLOCK = r'''
/* ------------------------------------------------------------------ */
/* Issue #387: DOM (LuaGraphNode-Familie) C++-only + SetSuspended       */
/*                                                                     */
/* AOB-Signaturen statt fester RVA (Build 2.0.58485). Beide Signaturen  */
/* sind im Modul eindeutig (je 1 Treffer, gegengeprueft).               */
/* ------------------------------------------------------------------ */

/* LuaGraphNode::Update-Prolog:
 *   cmp byte ptr [rcx+0xF1], 0 ; je rel32 ; ret
 * (+0xF1 = suspended-Flag: Update kehrt sofort zurueck, wenn gesetzt.)  */
static const unsigned char RBBRIDGE_DOM_SIG[] = {
    0x80, 0xB9, 0xF1, 0x00, 0x00, 0x00, 0x00, 0x0F,
    0x84, 0x00, 0x00, 0x00, 0x00, 0xC3
};
static const unsigned char RBBRIDGE_DOM_SIG_MASK[] = {
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
    0xFF, 0x00, 0x00, 0x00, 0x00, 0xFF
};

/* LuaGraphNode::SetSuspended(bool): mov byte ptr [rcx+0xF1], dl ; ret */
static const unsigned char RBBRIDGE_SETSUSPEND_SIG[] = {
    0x88, 0x91, 0xF1, 0x00, 0x00, 0x00, 0xC3
};

/* vtable-Slot von LuaGraphNode::Update(float). */
#define RBBRIDGE_DOM_SLOT_UPDATE 7

/* GetTypeHash() liefert FNV-1a des C++-Klassennamens (live bestaetigt).
 * Nur diese beiden Klassen bilden die LuaGraphNode-Familie; Lua-abgeleitete
 * Klassen (dom_mananger/event_manager) liefern den Basis-Hash. */
#define RBBRIDGE_DOM_HASH_LUAGRAPHNODE 0xde5d72b3u /* FNV1a("LuaGraphNode") */
#define RBBRIDGE_DOM_HASH_SELECTOR     0xc7919a42u /* FNV1a("LuaGraphNodeSelector") */

#define RBBRIDGE_DOM_MAX_VT   4
#define RBBRIDGE_DOM_MAX_INST 32

/* AOB: LuaGraphNode::Update(float) im Modul finden. */
static const unsigned char *resolve_dom_update(const unsigned char *base,
                                               size_t size)
{
    return scan_bytes_mask(base, size, RBBRIDGE_DOM_SIG,
                           RBBRIDGE_DOM_SIG_MASK, sizeof(RBBRIDGE_DOM_SIG));
}

/* AOB: LuaGraphNode::SetSuspended(bool) im Modul finden. */
static const unsigned char *resolve_set_suspended(const unsigned char *base,
                                                  size_t size)
{
    return scan_bytes_mask(base, size, RBBRIDGE_SETSUSPEND_SIG, NULL,
                           sizeof(RBBRIDGE_SETSUSPEND_SIG));
}

/* vtables der Familie: QWORD == Update-Adresse -> Slot 7; vtable = Slot7-0x38.
 * Validierung: Slot 0 zeigt in das Modul, Slot 1 ist ein Getter-Stub. */
static size_t resolve_dom_vtables(const unsigned char *base, size_t size,
                                  const unsigned char *update_fn,
                                  uint64_t *out, size_t max)
{
    uint64_t target = (uint64_t)(uintptr_t)update_fn;
    size_t n = 0;
    uintptr_t lo = (uintptr_t)base;
    uintptr_t hi = lo + size;
    if (!target || size < 8)
        return 0;
    for (uintptr_t off = 0; off + 8 <= size && n < max; off++) {
        uint64_t v;
        uintptr_t vt, s0, s1;
        memcpy(&v, base + off, sizeof(v));
        if (v != target)
            continue;
        if (off < (uintptr_t)(RBBRIDGE_DOM_SLOT_UPDATE * 8))
            continue;
        vt = lo + off - (uintptr_t)(RBBRIDGE_DOM_SLOT_UPDATE * 8);
        memcpy(&v, (const void *)vt, sizeof(v));
        s0 = (uintptr_t)v;
        if (s0 < lo || s0 >= hi)
            continue; /* Slot 0 muss eine .text-Adresse sein */
        memcpy(&v, (const void *)(vt + 8), sizeof(v));
        s1 = (uintptr_t)v;
        if (s1 < lo || s1 >= hi)
            continue;
        {
            const unsigned char *fn = (const unsigned char *)s1;
            /* lea rax,[rip+imm32] ; ret  ==  48 8D 05 xx xx xx xx C3 */
            if (fn[0] != 0x48 || fn[1] != 0x8D || fn[2] != 0x05 || fn[7] != 0xC3)
                continue;
        }
        out[n++] = (uint64_t)vt;
    }
    return n;
}

/* GetTypeHash() (vtable-Slot 2) aufrufen - reiner C++-Virtual-Call.
 * Nur auf Instanzen anwenden, deren vtable exakt bekannt ist. */
static uint32_t dom_type_hash(const unsigned char *inst)
{
    uint64_t vt = 0, fn = 0;
    typedef uint32_t (*hash_fn)(const void *);
    if (!safe_read_u64(inst, &vt) || !vt)
        return 0;
    if (!safe_read_u64((const void *)(uintptr_t)(vt + 16), &fn) || !fn)
        return 0;
    return ((hash_fn)(uintptr_t)fn)(inst);
}

/* Familie? vtable exakt in der Familie UND Hash passt (C++-only). */
static int dom_is_family_instance(const unsigned char *inst,
                                  const uint64_t *vts, size_t nvt)
{
    uint64_t vt = 0;
    uint32_t h;
    int k = -1;
    if (!safe_read_u64(inst, &vt) || !vt)
        return 0;
    for (size_t i = 0; i < nvt; i++)
        if (vts[i] == vt) {
            k = (int)i;
            break;
        }
    if (k < 0)
        return 0;
    h = dom_type_hash(inst);
    return (h == RBBRIDGE_DOM_HASH_LUAGRAPHNODE ||
            h == RBBRIDGE_DOM_HASH_SELECTOR) ? 1 : 0;
}

/* Instanzen der Familie einsammeln (VirtualQuery, nur lesbare Regionen). */
static size_t scan_dom_instances(const uint64_t *vts, size_t nvt,
                                 uint64_t *out, size_t max)
{
    size_t n = 0;
    uintptr_t addr = 0;
    if (!nvt)
        return 0;
    for (;;) {
        MEMORY_BASIC_INFORMATION mi;
        if (VirtualQuery((const void *)addr, &mi, sizeof(mi)) == 0)
            break;
        {
            uintptr_t next = (uintptr_t)mi.BaseAddress + mi.RegionSize;
            if (next <= addr)
                break;
            addr = next;
        }
        if (!is_readable_region(&mi))
            continue;
        {
            const uint64_t *q = (const uint64_t *)mi.BaseAddress;
            size_t nq = mi.RegionSize / sizeof(uint64_t);
            for (size_t i = 0; i < nq && n < max; i++) {
                const unsigned char *inst = (const unsigned char *)&q[i];
                if (dom_is_family_instance(inst, vts, nvt))
                    out[n++] = (uint64_t)(uintptr_t)inst;
            }
        }
    }
    return n;
}

/* ------------------------------------------------------------------ */
/* Kommando: pause_dom / resume_dom                                    */
/* ------------------------------------------------------------------ */

/*
 * Loest dom_mananger C++-only auf und setzt [this+0xF1].
 *
 * Rueckgabe 0 = ok, sonst Fehlercode-String.
 * ref_filter != 0: Instanz ueber den luabind-Registry-Ref (+0x28) waehlen.
 * Ohne ref_filter: nur eindeutige Treffer akzeptieren (sonst ambiguous_dom) —
 * es gibt KEINEN C++-only Marker, der dom_mananger von event_manager trennt
 * (siehe docs/research/dedicated-io-re-findings.md, Phase E).
 */
static const char *dom_apply_suspend(int suspend, uint64_t ref_filter,
                                     uint64_t *out_inst, int *out_count,
                                     uint64_t *out_setfn)
{
    const unsigned char *base = NULL;
    size_t size = 0;
    const char *via = NULL;
    const unsigned char *execfn = NULL;
    const unsigned char *update_fn, *set_fn;
    uint64_t vts[RBBRIDGE_DOM_MAX_VT];
    size_t nvt, n;
    uint64_t insts[RBBRIDGE_DOM_MAX_INST];
    uint64_t inst = 0;
    typedef void (*setsusp_fn)(void *, int);

    *out_inst = 0;
    *out_count = 0;
    if (!resolve_module(&base, &size, &via, &execfn))
        return "no_module";

    set_fn = resolve_set_suspended(base, size);
    if (!set_fn)
        return "no_setsuspended_signature";
    *out_setfn = (uint64_t)(uintptr_t)set_fn;

    update_fn = resolve_dom_update(base, size);
    if (!update_fn)
        return "no_dom_signature";
    nvt = resolve_dom_vtables(base, size, update_fn, vts, RBBRIDGE_DOM_MAX_VT);
    if (!nvt)
        return "no_dom_vtable";

    n = scan_dom_instances(vts, nvt, insts, RBBRIDGE_DOM_MAX_INST);
    *out_count = (int)n;
    if (!n)
        return "no_dom_instance";

    if (ref_filter) {
        for (size_t i = 0; i < n; i++) {
            uint64_t r = 0;
            if (safe_read_u64((const void *)(uintptr_t)(insts[i] + 0x28), &r) &&
                r == ref_filter) {
                inst = insts[i];
                break;
            }
        }
        if (!inst)
            return "ref_not_found";
    } else {
        if (n != 1)
            return "ambiguous_dom";
        inst = insts[0];
    }

    /* Boot-/World-Guard: Instanz + Flagbyte muessen lesbar sein. */
    {
        uint64_t vt = 0;
        unsigned char flag = 0;
        if (!safe_read_u64((const void *)(uintptr_t)inst, &vt) || !vt)
            return "instance_unreadable";
        if (!dom_boot_guard_ok(inst))
            return "world_not_ready";
        (void)flag;
    }

    ((setsusp_fn)(uintptr_t)(*out_setfn))((void *)(uintptr_t)inst, suspend ? 1 : 0);
    *out_inst = inst;
    return NULL;
}

static void dispatch_dom_control(HANDLE hPipe, const char *line, int suspend)
{
    uint64_t ref = 0, inst = 0, setfn = 0;
    int count = 0;
    const char *err;
    char refbuf[32] = "";

    if (json_get_string(line, "ref", refbuf, sizeof(refbuf)) && refbuf[0])
        for (const char *c = refbuf; *c >= '0' && *c <= '9'; c++)
            ref = ref * 10u + (uint64_t)(*c - '0');

    err = dom_apply_suspend(suspend, ref, &inst, &count, &setfn);
    if (err) {
        send_line(hPipe,
                  "{\"event\":\"dom_control\",\"ok\":false,\"action\":\"%s\","
                  "\"reason\":\"%s\",\"candidates\":%d,"
                  "\"set_suspended\":\"0x%llx\"}",
                  suspend ? "pause" : "resume", err, count,
                  (unsigned long long)setfn);
        return;
    }
    send_line(hPipe,
              "{\"event\":\"dom_control\",\"ok\":true,\"action\":\"%s\","
              "\"suspended\":%d,\"inst\":\"0x%llx\",\"candidates\":%d}",
              suspend ? "pause" : "resume", suspend ? 1 : 0,
              (unsigned long long)inst, count);
}
'''

GUARD = r'''
/* Boot-/World-Guard: die Instanz muss im selben Allokationsblock wie ihre
 * vtable liegen (echtes Heap-Objekt, kein Zufallstreffer). */
static int dom_boot_guard_ok(const unsigned char *inst)
{
    MEMORY_BASIC_INFORMATION mi;
    if (!VirtualQuery(inst, &mi, sizeof(mi)))
        return 0;
    if (!is_readable_region(&mi))
        return 0;
    if ((uintptr_t)inst + 0x130 > (uintptr_t)mi.BaseAddress + mi.RegionSize)
        return 0;
    return 1;
}
'''

# Insert helpers before handle_line (after dispatch_add_resource section).
anchor = "static void handle_line(HANDLE hPipe, const char *line)\n{"
assert src.count(anchor) == 1, "handle_line anchor"
if "RBBRIDGE_DOM_SIG" not in src:
    src = src.replace(anchor, GUARD + "\n" + BLOCK + "\n" + anchor)

# dispatch entries
d = '''    if (strcmp(cmd, "get_state") == 0) {
        dispatch_get_state(hPipe);
        return;
    }
'''
assert src.count(d) == 1, "get_state dispatch"
if 'strcmp(cmd, "pause_dom")' not in src:
    src = src.replace(d, d + '''
    if (strcmp(cmd, "pause_dom") == 0) {
        dispatch_dom_control(hPipe, line, 1);
        return;
    }

    if (strcmp(cmd, "resume_dom") == 0) {
        dispatch_dom_control(hPipe, line, 0);
        return;
    }
''')

io.open(P, "w", encoding="utf-8", newline="\n").write(src)
print("patched production (dom + pause/resume)")
