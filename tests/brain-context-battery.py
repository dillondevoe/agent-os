#!/usr/bin/env python3
"""brain-context-battery.py — the R1 context bound, armed.

Tier-0 item 3 (Rabbot's runtime-config lane) asked for `num_ctx 8192` and a
BOUNDED history. Both halves are easy to ship in a way that looks right and does
nothing, so both are checked here against the shipped module rather than against a
description of it:

  A  the ollama payload actually carries options.num_ctx, and it is NUM_CTX
  B  OLLAMA_NUM_CTX overrides it (the knob is a knob, not a comment)
  C  trim_history NEVER drops msgs[0] (the system message / KV-cache prefix)
  D  trim_history only ever cuts at a `user` boundary, so no tool result is
     orphaned from the assistant tool_call it answers
  E  CONTROL ARM — a history that already fits comes back UNCHANGED. Without this
     arm a trimmer that deleted everything would pass A-D.
  F  PRE-FIX ARM — the NAIVE trimmer ("drop the oldest k messages") run on the
     same input leaves the transcript cut MID-GROUP. Without it, D could be
     passing vacuously on an input no trimmer could get wrong.
  G  the budget is enforced: a history far over budget comes back under it.

Run standalone (tests/run-local.sh) and in the flake (brain-context-contract).
"""
import importlib.util, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
MOD  = os.path.join(os.path.dirname(HERE), "modules", "agent-brain.py")

def load(env=None):
    # Reload under a specific environment: NUM_CTX is read at import time, so the
    # override arm has to re-import rather than poke the constant.
    old = dict(os.environ)
    if env: os.environ.update(env)
    else: os.environ.pop("OLLAMA_NUM_CTX", None)
    try:
        spec = importlib.util.spec_from_file_location("brain_ctx", MOD)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m
    finally:
        os.environ.clear(); os.environ.update(old)

fails = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ((" — " + detail) if detail and not cond else ""))
    if not cond: fails.append(name)

brain = load()

# ── A / B — the window is stated on the wire ────────────────────────────────────
src = open(MOD).read()
payload_sites = src.count('"options":{"num_ctx":NUM_CTX}')
check("A options.num_ctx on every ollama payload (3 sites)", payload_sites == 3,
      "found %d" % payload_sites)
check("A NUM_CTX defaults to 8192", brain.NUM_CTX == 8192, repr(brain.NUM_CTX))
check("B OLLAMA_NUM_CTX overrides", load({"OLLAMA_NUM_CTX": "4096"}).NUM_CTX == 4096)
check("B history budget leaves room for the reply",
      0 < brain.HIST_BUDGET_CHARS < brain.NUM_CTX * 4)

# ── the fixture: five user groups, each with a real tool round-trip ─────────────
def history(groups, pad):
    msgs = [{"role": "system", "content": "SYS"}]
    for g in range(groups):
        msgs.append({"role": "user", "content": "u%d " % g + "x" * pad})
        msgs.append({"role": "assistant", "content": "",
                     "tool_calls": [{"function": {"name": "t%d" % g, "arguments": {}}}]})
        msgs.append({"role": "tool", "content": "r%d" % g})
        msgs.append({"role": "assistant", "content": "a%d" % g})
    return msgs

def orphaned(msgs):
    """True if any `tool` message is not preceded by an assistant carrying tool_calls."""
    for i, m in enumerate(msgs):
        if m.get("role") == "tool":
            prev = msgs[i - 1] if i else None
            if not (prev and prev.get("role") == "assistant" and prev.get("tool_calls")):
                return True
    return False

msgs = history(5, 8000)
n = brain.trim_history(msgs)
check("C system message survives the trim",
      msgs[0].get("role") == "system" and msgs[0]["content"] == "SYS")
check("C something was actually dropped", n > 0, "dropped=%d" % n)
check("D first message after system is a user turn",
      msgs[1].get("role") == "user", repr(msgs[1].get("role")))
check("D no orphaned tool result", not orphaned(msgs))
check("G trimmed history is under budget",
      sum(brain._msg_chars(m) for m in msgs) <= brain.HIST_BUDGET_CHARS,
      "%d > %d" % (sum(brain._msg_chars(m) for m in msgs), brain.HIST_BUDGET_CHARS))

# ── E — CONTROL ARM: a short history must come back untouched ──────────────────
short = history(2, 10)
before = json.dumps(short)
check("E control: an under-budget history is NOT trimmed",
      brain.trim_history(short) == 0 and json.dumps(short) == before)

# ── F — PRE-FIX ARM: the naive trimmer orphans on this very input ──────────────
def naive_trim(msgs, budget):
    while len(msgs) > 2 and sum(brain._msg_chars(m) for m in msgs) > budget:
        del msgs[1]
    return msgs
# The fixture is shaped so the naive trimmer stops mid-group: the TOOL RESULT is the
# large message, so deletion runs out of budget pressure the moment the assistant that
# called the tool is gone — leaving the result behind with nothing to answer. That is
# not a contrived shape; a tool that returns a page of text is the normal case here.
fat = [{"role": "system", "content": "SYS"}]
for g in range(4):
    fat.append({"role": "user", "content": "u%d" % g})
    fat.append({"role": "assistant", "content": "",
                "tool_calls": [{"function": {"name": "t%d" % g, "arguments": {}}}]})
    fat.append({"role": "tool", "content": "r" * 9000})
    fat.append({"role": "assistant", "content": "a%d" % g})
naive = naive_trim(list(map(dict, fat)), brain.HIST_BUDGET_CHARS)
# WHAT THIS ARM ASSERTS, precisely: the naive trimmer leaves the history CUT MID-GROUP.
# It is deliberately not asserting "an orphaned tool result", even though that is the
# worst outcome — WHERE the naive version stops depends on which message happened to
# push it back under budget, so on this fixture it halts on a dangling assistant reply
# and on a differently-shaped one it halts on an orphaned tool (measured both). That
# data-dependence IS the finding: the damage is real either way and which flavour you
# get is luck, which is why the shipped rule is a structural boundary and not a
# heuristic that usually lands well.
check("F pre-fix arm: the naive trimmer cuts MID-GROUP "
      "(so arm D is discriminating)", naive[1].get("role") != "user",
      "naive left %r at index 1" % naive[1].get("role"))
# and the shipped trimmer, on that same input, does not.
safe = list(map(dict, fat))
brain.trim_history(safe)
check("F same input, shipped trimmer: no orphan, cut at a user boundary",
      not orphaned(safe) and safe[1].get("role") == "user")

# ── the single oversized group is kept rather than mangled ────────────────────
huge = history(1, brain.HIST_BUDGET_CHARS * 2)
kept = list(huge)
brain.trim_history(huge)
check("D an oversized single group is kept whole, not cut mid-group",
      huge == kept)

# ── H — A BLIND INSTRUMENT MUST NOT READ AS A DESCRIPTION (Augur, #277 review §4) ───
# `_sh` raises a NAMED RuntimeError when no shell resolves, precisely so callers report a
# cause instead of an errno. live_context's `probe()` swallows it to "", so with no shell
# every machine fact below it silently vanishes and the model is handed a context in which
# the box simply HAS no hostname, no uptime, no installed apps. That absence reads exactly
# like a fresh machine rather than like a blind one, and it is the context that shapes what
# the model believes about the machine it is standing on.
#
# probe()'s per-probe fail-soft is still correct and is deliberately NOT changed here — no
# battery, no hyprctl and an empty result are all ordinary. What is armed is the one line
# that explains all of them at once.
_real_shell = brain.SHELL
try:
    brain.SHELL = None
    blind = brain.live_context()
    check("H no shell -> the context SAYS the instrument is blind",
          "no shell could be resolved" in blind)
    check("H and tells the model not to infer from the absence",
          "do not infer" in blind.lower())
    check("H the machine facts are genuinely gone, so the note is load-bearing",
          "Machine: " not in blind)
    check("H the date line survives — it needs no shell, so the context is degraded, not empty",
          "Current date & time:" in blind)

    # H2 — PERMITTING TWIN. Without it a live_context that emitted the blind note
    # unconditionally would pass every arm above while lying on every healthy box.
    brain.SHELL = _real_shell
    sighted = brain.live_context()
    check("H2 with a shell, no blind note is emitted",
          "no shell could be resolved" not in sighted)
    check("H2 and the machine line really is present, so H2 is not passing on an empty probe",
          "Machine: " in sighted)
finally:
    brain.SHELL = _real_shell

print()
if fails:
    print("brain-context-battery: %d FAILED — %s" % (len(fails), ", ".join(fails)))
    sys.exit(1)
print("brain-context-battery: all arms green")
