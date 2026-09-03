#!/usr/bin/env python3
"""Battery for the summon_claude consent gate (Rabbot door (i), 2026-09-02).

WHAT WAS WRONG. `_summon_claude` spawns the operator's Claude CLI as a subprocess — their
account, their permissions, no broker, no uid split. The only thing between a model-emitted
tool call and that subprocess was PROSE: the tool description and one SYS_BASE line saying
"only after the user says yes". Consent asserted by the model is the model deciding it has
consent.

EVERY ARM DRIVES `ok_to_summon`, WHICH IS THE FUNCTION THE DEPLOYED PATH CALLS. That is
#256's lesson applied before the fact: a fixture-only route proves nothing about the box. The
subprocess arm goes further and drives `_summon_claude` itself with the CLI stubbed, so the
gate is exercised where it actually sits rather than where it is convenient to test.
"""
import os, sys, types, importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "modules", "agent-brain.py")

# agent-brain.py runs a REPL at import if executed; it is written as a module with a
# __main__ guard, so a normal spec load is safe and gives us the REAL objects.
spec = importlib.util.spec_from_file_location("agent_brain_under_test", SRC)
ab = importlib.util.module_from_spec(spec)
sys.modules["agent_brain_under_test"] = ab
spec.loader.exec_module(ab)

passed = failed = 0
def check(name, got, want):
    global passed, failed
    if got == want:
        print(f"PASS {name}"); passed += 1
    else:
        print(f"FAIL {name} — got {got!r}, want {want!r}"); failed += 1

# A — CONTROL ARM. The model emits the tool call; nobody typed `:summon`. This is the
# defect, reconstructed: before the gate this reached subprocess.run.
c = ab._SummonConsent()
ok, why = ab.ok_to_summon(c)
check("A no consent -> refused", ok, False)
check("A refusal names the reason", bool(why), True)

# B — PERMITTING ARM. Without it, a gate that refused unconditionally would pass A and be
# indistinguishable from a working one while having disabled the feature.
c = ab._SummonConsent(); c.arm()
ok, why = ab.ok_to_summon(c)
check("B operator consent -> allowed", (ok, why), (True, None))

# C — SINGLE USE. One `:summon` buys exactly one summon.
c = ab._SummonConsent(); c.arm()
ab.ok_to_summon(c)
ok, _ = ab.ok_to_summon(c)
check("C consent is consumed, second summon refused", ok, False)

# D — TIME BOUND. A grant that outlives its exchange is a standing permission nobody
# remembers giving. Driven by injecting `now`, not by sleeping.
c = ab._SummonConsent(ttl=300); c.arm()
ok, why = ab.ok_to_summon(c, now=c._at + 301)
check("D expired consent -> refused", ok, False)
check("D expiry reason says so", "expired" in (why or ""), True)

# E — CONTROL FOR D: just inside the window still works, so D is not passing because the
# clock injection breaks everything.
c = ab._SummonConsent(ttl=300); c.arm()
ok, _ = ab.ok_to_summon(c, now=c._at + 299)
check("E consent just inside TTL -> allowed", ok, True)

# F — THE MODEL CANNOT ARM IT, asserted structurally rather than by absence. An "is this
# string missing" check passes vacuously if the substrate moves (this tree's own
# absence-assertion scar), so instead: locate EVERY call to arm the grant and require that
# each one sits in the operator input path. Today that is exactly one, on the `:summon`
# line. A future edit that arms it from a tool handler fails here by construction.
_src_lines = open(SRC).read().split("\n")
_arm_sites = [i for i, l in enumerate(_src_lines) if "SUMMON_CONSENT.arm()" in l]
check("F the grant is armed from exactly one place", len(_arm_sites), 1)
_ctx = "\n".join(_src_lines[max(0, _arm_sites[0] - 12):_arm_sites[0] + 1])
check("F that place is the operator's `:summon` input line", ':summon"' in _ctx, True)

# G — END TO END through the DEPLOYED function, with the CLI stubbed. No consent must mean
# no subprocess at all, not a subprocess that fails politely.
calls = []
real_run = ab.subprocess.run
def fake_run(*a, **k):
    calls.append(a)
    return types.SimpleNamespace(returncode=0, stdout="stubbed", stderr="")
ab.subprocess.run = fake_run
ab.SUMMON_CONSENT = ab._SummonConsent()
ab._TURN_LOG_PATH = os.path.join(os.environ.get("TMPDIR", "/tmp"), "summon-battery-turnlog.jsonl")
out = ab._summon_claude("do a thing", "ctx")
check("G refused summon never reaches subprocess", len(calls), 0)
check("G refusal is user-visible and names the remedy", ":summon" in out, True)

# H — PERMITTING ARM for G. The consented path must actually reach the subprocess, or the
# gate has simply broken the feature.
ab.SUMMON_CONSENT.arm()
out = ab._summon_claude("do a thing", "ctx")
check("H consented summon reaches subprocess", len(calls), 1)
check("H consented summon returns the CLI output", out, "stubbed")
ab.subprocess.run = real_run

# I — REFUSAL IS LOGGED. A legit summon blocked by this gate must surface as a defect in
# the record, not as a silence someone has to notice the absence of.
import json
lines = [json.loads(l) for l in open(ab._TURN_LOG_PATH) if '"summon_claude"' in l]
check("I refusal logged with allowed=false", any(e.get("allowed") is False for e in lines), True)
check("I allowed summon logged too", any(e.get("allowed") is True for e in lines), True)

WANT_ARMS = 15
ran = passed + failed
if ran != WANT_ARMS:
    print(f"FAIL arm count: {ran} arms ran, expected {WANT_ARMS} -- an arm was added or silently lost")
    failed += 1
print(f"--- {passed} passed, {failed} failed ({ran} arms) ---")
sys.exit(1 if failed else 0)
