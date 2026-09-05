#!/usr/bin/env python3
# SIDE_EFFECTS — Geist's law as amended 2026-09-05T13:05Z: a box-runnable battery declares
# every effect that leaves the machine or outlives the run. Read as DATA by
# tests/vm-matrix-contract.py, not as a comment.
SIDE_EFFECTS = []

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

# ── J..N — A GRANT BUYS AN ANSWER, NOT AN ATTEMPT ────────────────────────────────────
# `claude-code` is in this OS's closure but auth is per-user OAuth, so on a box where
# nobody has run `claude` once EVERY summon fails at the CLI. Before the restore, each
# failure also ate the operator's consent act: they typed `:summon`, got "isn't logged in",
# and had to re-consent to try again. Nothing was spent on their account and nothing was
# returned to them, so the grant was charged for an error string.

# J — the grant comes back after a consumed attempt that produced no answer.
c = ab._SummonConsent(); c.arm()
ab.ok_to_summon(c)
check("J restore returns True after a consumed grant", ab.restore_summon_grant(c), True)
ok, _ = ab.ok_to_summon(c)
check("J the restored grant is usable again", ok, True)

# J2 — THE LOAD-BEARING SAFETY ARM. Restore puts back the ORIGINAL stamp, never `now`.
# A restore that refreshed the clock would mint consent-time nobody granted, and a loop of
# failing attempts could hold a grant open forever.
#
# THE GRANT IS BACKDATED BY HAND, and that is the whole arm. `restore()` reads the real
# clock, so an arm that arms and restores in the same microsecond CANNOT SEE a refresh:
# `time.time()` and the original stamp are the same instant, the refresh buys ~0 seconds,
# and the arm goes green against exactly the implementation it exists to reject. Verified,
# not assumed — the first version of this arm passed against a `self._at = time.time()`
# restore. So the grant here is planted 290s into a 300s TTL: a correct restore leaves 10s
# on the original clock and the check at +301 is refused; a refreshing one hands back a
# nearly-full window and lets it through.
c = ab._SummonConsent(ttl=300); c.arm()
armed_at = c._at = c._at - 290          # armed 290s ago, 10s of TTL left
ab.ok_to_summon(c, now=armed_at + 290)
ab.restore_summon_grant(c)
ok, why = ab.ok_to_summon(c, now=armed_at + 301)
check("J2 a restored grant still expires on the ORIGINAL clock", ok, False)
check("J2 and says so", "expired" in (why or ""), True)

# J3 — CONTROL FOR J2: the restored grant is genuinely alive inside the window, so J2 is
# not passing because restore quietly returned a dead object, and not merely because the
# backdating in J2 broke the grant outright.
c = ab._SummonConsent(ttl=300); c.arm()
armed_at = c._at = c._at - 290
ab.ok_to_summon(c, now=armed_at + 290)
ab.restore_summon_grant(c)
ok, _ = ab.ok_to_summon(c, now=armed_at + 299)
check("J3 a restored grant still works inside the original TTL", ok, True)

# K — RESTORE CANNOT MANUFACTURE CONSENT. Never armed, never consumed: nothing to give back.
c = ab._SummonConsent()
check("K restore on a never-armed grant returns False", ab.restore_summon_grant(c), False)
ok, _ = ab.ok_to_summon(c)
check("K and the summon is still refused", ok, False)

# K2 — RESTORE IS SINGLE-USE, so a run of failures cannot bank grants.
c = ab._SummonConsent(); c.arm()
ab.ok_to_summon(c)
ab.restore_summon_grant(c)
check("K2 a second restore without a second consume returns False",
      ab.restore_summon_grant(c), False)

# L — END TO END through the DEPLOYED function. A CLI that fails leaves the grant standing.
calls = []
real_run = ab.subprocess.run
def failing_run(*a, **k):
    calls.append(a)
    # An auth-SHAPED failure. Representative, NOT transcribed from the real CLI — this arm
    # asserts that an unauthenticated-looking failure is recognised as one, and it caught the
    # old pair ("log in"/"auth") falling through on exactly this shape.
    return types.SimpleNamespace(returncode=1, stdout="", stderr="Invalid API key - Please run /login")
ab.subprocess.run = failing_run
ab.SUMMON_CONSENT = ab._SummonConsent(); ab.SUMMON_CONSENT.arm()
out = ab._summon_claude("do a thing", "ctx")
check("L a failing CLI still reached the subprocess", len(calls), 1)
check("L an auth-shaped failure names the login remedy", "logged in" in out, True)
check("L the operator is TOLD the grant survived", "still good" in out, True)
check("L and the grant really is armed again", ab.SUMMON_CONSENT.armed(), True)

# L2 — THE PERMITTING TWIN, AND IT IS LOAD-BEARING. A success must STILL cost the grant.
# Without this arm a `restore()` on every path would pass L while destroying single-use
# entirely — the gate would be gone and every arm above it would stay green.
def ok_run(*a, **k):
    return types.SimpleNamespace(returncode=0, stdout="an answer", stderr="")
ab.subprocess.run = ok_run
ab.SUMMON_CONSENT = ab._SummonConsent(); ab.SUMMON_CONSENT.arm()
out = ab._summon_claude("do a thing", "ctx")
check("L2 a successful summon returns the answer", out, "an answer")
check("L2 a successful summon CONSUMES the grant", ab.SUMMON_CONSENT.armed(), False)
check("L2 and does not claim the grant survived", "still good" in out, False)

# M — the CLI-absent path restores too. This is the one case that is structurally knowable
# (FileNotFoundError, no string matching), and it must not be the only one that works.
def missing_run(*a, **k):
    raise FileNotFoundError("claude")
ab.subprocess.run = missing_run
ab.SUMMON_CONSENT = ab._SummonConsent(); ab.SUMMON_CONSENT.arm()
out = ab._summon_claude("do a thing", "ctx")
check("M an absent CLI leaves the grant armed", ab.SUMMON_CONSENT.armed(), True)
check("M and says so", "still good" in out, True)
ab.subprocess.run = real_run

# N — a refused summon (no consent at all) must NOT report a surviving grant. Nothing was
# consumed, so there is nothing to restore, and saying "still good" would be a lie that
# reads as permission.
ab.SUMMON_CONSENT = ab._SummonConsent()
out = ab._summon_claude("do a thing", "ctx")
check("N a consent-less refusal does not claim a surviving grant", "still good" in out, False)

# O — THE RESTORE BUDGET. "Give the grant back on every failure" is unbounded on its own:
# the TTL bounds how LONG a grant lives, and nothing bounded how many subprocesses could be
# spawned inside it. A `claude` that fails in milliseconds lets a model loop summon_claude
# dozens of times in one 300s window. Three answerless attempts, then re-consent.
c = ab._SummonConsent(); c.arm()
for i in range(ab._SUMMON_MAX_RESTORES):
    ab.ok_to_summon(c)
    check(f"O restore {i+1} of {ab._SUMMON_MAX_RESTORES} is granted", ab.restore_summon_grant(c), True)
ab.ok_to_summon(c)
check("O the attempt AFTER the budget is not restored", ab.restore_summon_grant(c), False)
ok, _ = ab.ok_to_summon(c)
check("O and the grant is genuinely gone, not merely unreported", ok, False)

# O2 — CONTROL FOR O, and it is what stops the budget from being a way of getting green: the
# loop above must have been doing real work, so a grant restored INSIDE the budget is usable.
# Without this, a restore() that returned True while restoring nothing would satisfy O.
c = ab._SummonConsent(); c.arm()
ab.ok_to_summon(c); ab.restore_summon_grant(c)
ok, _ = ab.ok_to_summon(c)
check("O2 a grant restored inside the budget still buys a summon", ok, True)

# O3 — the budget is PER GRANT, not per process. A fresh `:summon` starts over, or the
# operator would be locked out of the feature after three failures ever.
c = ab._SummonConsent(); c.arm()
for _ in range(ab._SUMMON_MAX_RESTORES + 1):
    ab.ok_to_summon(c); ab.restore_summon_grant(c)
c.arm()
ab.ok_to_summon(c)
check("O3 a fresh arm() resets the budget", ab.restore_summon_grant(c), True)

# P — THE MALFORMED CALL. This return sat ABOVE `_kept` and was the ONE non-success path that
# still ate the grant, under a comment claiming "every return below this point" restores —
# true only because this one was not below it. A model emitting a blank task burned the
# operator's consent act for a validation error.
ab.SUMMON_CONSENT = ab._SummonConsent(); ab.SUMMON_CONSENT.arm()
out = ab._summon_claude("", "ctx")
check("P an empty task returns an error", "no task given" in out, True)
check("P and does NOT eat the grant", ab.SUMMON_CONSENT.armed(), True)

# P2 — CONTROL FOR P: the empty-task guard still FIRES. Without it, deleting the guard
# entirely would leave the grant armed on some other path and pass P.
check("P2 the empty-task guard still short-circuits before the CLI", "summon error" in out, True)

# Q — the kept-message makes NO claim about the operator's cloud SPEND. It said "nothing was
# spent", which this code cannot observe and which is close to false on the 180s timeout path:
# a timeout means the request was in flight and most likely billed. The grant and the billing
# are two different facts, and only one of them is ours to report.
check("Q the kept-message does not claim nothing was spent", "was spent" in ab._SUMMON_KEPT, False)
check("Q and still says the grant survived", "still good" in ab._SUMMON_KEPT, True)

# DERIVED, not retyped: arm O runs one check per unit of budget, so a change to
# _SUMMON_MAX_RESTORES changes the arm count. Hard-coding the total would make the guard
# fail on a legitimate edit — which is how an arm-count guard gets deleted.
# R — THE MESSAGE MUST AGREE WITH THE CLOCK IT OWNS (Augur's #278 finding). restore()
# returned True without consulting the clock, so `_kept()` told the operator their `:summon`
# was still good on a grant the very next check refuses as expired. J2's MIRROR IMAGE: J2
# proves the GRANT expires and passes today because it never reads the message; L asserts
# "still good" only on a fresh grant. Nothing joined the two.
#
# Not exotic: the subprocess timeout is 180s against a 300s TTL, so every summon consumed
# after t=120 that times out lands here. Reproduced at 250s into a 300s TTL, retried at +430.
c = ab._SummonConsent(ttl=300); c.arm()
armed_at = c._at = c._at - 250
ab.ok_to_summon(c, now=armed_at + 250)
check("R restore refuses a grant whose ORIGINAL clock has run out",
      ab.restore_summon_grant(c, now=armed_at + 430), False)
ok, why = ab.ok_to_summon(c, now=armed_at + 431)
check("R and the next check agrees, so no message could have promised otherwise", ok, False)

# R2 — CONTROL FOR R, on the same timescale, INSIDE the window. Without it a restore that
# refused everything past its first use would satisfy R while destroying the whole feature.
c = ab._SummonConsent(ttl=300); c.arm()
armed_at = c._at = c._at - 250
ab.ok_to_summon(c, now=armed_at + 250)
check("R2 a restore INSIDE the original TTL is still granted",
      ab.restore_summon_grant(c, now=armed_at + 260), True)

# R3 — AND THE BUDGET IS NOT SPENT BY THE EXPIRY REFUSAL. An expired restore must not also
# consume one of the three, or the two bounds would silently interact.
c = ab._SummonConsent(ttl=300); c.arm()
armed_at = c._at = c._at - 250
ab.ok_to_summon(c, now=armed_at + 250)
ab.restore_summon_grant(c, now=armed_at + 430)
check("R3 an expiry refusal does not spend the restore budget", c._restores, 0)

WANT_ARMS = 15 + 18 + 13 + ab._SUMMON_MAX_RESTORES
ran = passed + failed
if ran != WANT_ARMS:
    print(f"FAIL arm count: {ran} arms ran, expected {WANT_ARMS} -- an arm was added or silently lost")
    failed += 1
print(f"--- {passed} passed, {failed} failed ({ran} arms) ---")
sys.exit(1 if failed else 0)
