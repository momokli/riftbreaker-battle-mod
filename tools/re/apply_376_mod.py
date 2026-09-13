#!/usr/bin/env python3
"""Issue #376: PatchDomCapture-Hook in rbbattle_autoexec.lua (str.replace,
KEIN Formatter — gleiche Regel wie fuer .c)."""

import io
import sys

PATH = "mod/lua/rbbattle_autoexec.lua"

INSERT_AFTER = """    RBB.domTimerPatched = true
    return true
end

"""

BLOCK = """-- ---------------------------------------------------------------------------
-- #376: DOM-Wellen-Counter + time-to-next an die C++-Bridge melden (game thread).
--
-- Die DLL darf Lua NICHT vom pipe thread anfassen (crasht, LUA CRASH "attempt
-- to call a nil value", s. .agents/skills/riftbreaker-re). Stattdessen
-- registriert die DLL _G.rbbridge_capture_dom und der Mod ruft sie hier pro
-- Frame auf dem game thread auf (Function-Wrap dom_mananger:Update). Die DLL
-- cached die Werte atomar; dispatch_get_state liest nur den Cache.
-- ---------------------------------------------------------------------------
RBB.domCapturePatched = false
RBB.domCaptureOrig = nil

-- State-abhaengiger Countdown (Spiegel von dom_mananger:Update-Debuglogik).
local function DomTimeToNext(self)
    local spawner = self.spawner
    if type(spawner) ~= "userdata" and type(spawner) ~= "table" then
        return 0
    end
    local okState, state = pcall(spawner.GetCurrentState, spawner)
    if not okState or type(state) ~= "string" or state == "" then
        return 0
    end
    if state == "cooldown_after_spawn" then
        return tonumber(self.cooldownTimer) or 0
    elseif state == "prepare_spawn" then
        return tonumber(self.waitForSpawnTimer) or 0
    elseif state == "idle" then
        return tonumber(self.idleTimer) or 0
    elseif state == "sleep" then
        return tonumber(self.sleepSafeTime) or 0
    elseif state == "wait" then
        local okS, s = pcall(spawner.GetState, spawner, state)
        if okS and type(s) == "userdata" then
            local okL, lim = pcall(s.GetDurationLimit, s)
            local okD, dur = pcall(s.GetDuration, s)
            if okL and okD and type(lim) == "number" and type(dur) == "number" then
                return lim - dur
            end
        end
    end
    return 0
end

local function PatchDomCapture()
    if RBB.domCapturePatched then
        return RBB.domCaptureOrig ~= nil
    end

    local dom = nil
    if type(_G) == "table" then
        dom = rawget(_G, "dom_mananger")
    end
    local dType = type(dom)
    if dType ~= "table" and dType ~= "userdata" then
        return false -- Klasse (noch) nicht geladen; naechster Versuch spaeter
    end

    local orig = dom.Update
    if type(orig) ~= "function" then
        Log("event=dom_capture patch status=skip reason=no_api")
        RBB.domCapturePatched = true
        return false
    end

    if RBB.domCaptureOrig == nil then
        RBB.domCaptureOrig = orig
        dom.Update = function(self, dt)
            local cap = rawget(_G, "rbbridge_capture_dom")
            if type(cap) == "function" then
                local wave = tonumber(self.currentDifficultyLevel) or 0
                pcall(cap, wave, math.ceil(DomTimeToNext(self)))
            end
            return RBB.domCaptureOrig(self, dt)
        end
        Log("event=dom_capture patch status=ok")
    end

    RBB.domCapturePatched = true
    return true
end

"""

EDITS = [
    # (anchor, replacement)
    (
        "    AnnounceSetupPhase() -- #158: Start-Announce (Setup-Phase, HQ noch offen)\n    PatchDomTimer()\n",
        "    AnnounceSetupPhase() -- #158: Start-Announce (Setup-Phase, HQ noch offen)\n    PatchDomTimer()\n    PatchDomCapture() -- #376: Wave-Counter + time-to-next an die Bridge\n",
    ),
    (
        "    PatchDomTimer() -- weiterer Retry-Zeitpunkt (billig, idempotent)\n    PatchWaveStartHook() -- weiterer Retry-Zeitpunkt (#42)\n",
        "    PatchDomTimer() -- weiterer Retry-Zeitpunkt (billig, idempotent)\n    PatchDomCapture() -- #376: weiterer Retry-Zeitpunkt\n    PatchWaveStartHook() -- weiterer Retry-Zeitpunkt (#42)\n",
    ),
    (
        "PatchDomTimer()\nif not evtApiOk then\n",
        "PatchDomTimer()\nPatchDomCapture() -- #376: erster Versuch direkt beim Laden\nif not evtApiOk then\n",
    ),
]


def main():
    with io.open(PATH, "r", encoding="utf-8") as f:
        src = f.read()

    if "PatchDomCapture" in src:
        print("PatchDomCapture bereits vorhanden - nichts zu tun.")
        return 0

    if INSERT_AFTER not in src:
        print("FEHLER: Insert-Anker (PatchDomTimer-Ende) nicht gefunden.", file=sys.stderr)
        return 1

    src = src.replace(INSERT_AFTER, INSERT_AFTER + BLOCK, 1)
    for anchor, repl in EDITS:
        if anchor not in src:
            print("FEHLER: Anker nicht gefunden: %r" % anchor, file=sys.stderr)
            return 1
        src = src.replace(anchor, repl, 1)

    with io.open(PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(src)
    print("OK: PatchDomCapture in rbbattle_autoexec.lua.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
