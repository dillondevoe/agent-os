#!/usr/bin/env python3
# tests/agos-surface-contract.py — contract battery for the surfacing half of STORE.
#
# This is the ONLY module in the self-improvement loop that writes a file, so the battery's
# job is to prove the write cannot become an APPLY. Two guarded properties, both control-armed:
#   1. it refuses to write a deny-listed path;
#   2. it refuses to write the TARGET of a proposal it is carrying — which is the specific
#      way a reporting module turns into an applying one, by a caller passing a filename.
# And the load-bearing reporting rule: a digest that could not see must not read like a
# digest that saw nothing, since this is the layer a human actually reads.

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "modules"))

import agos_observe as O
import agos_propose as P
import agos_surface as S

FAILS, CHECKS = [], 0


def check(label, cond):
    global CHECKS
    CHECKS += 1
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        FAILS.append(label)


LESSON = {"sig_id": "abc123", "type": O.COST_SPIKE, "detail": "cost cap tripped",
          "occurrences": 2}
PROP = {"prop_id": "p1", "sig_id": "abc123", "target": "docs/lessons.md",
        "body": "record the recurring cost spike", "reason": None}

# ── A. the APPLY boundary ────────────────────────────────────────────────────────────
print("A. the module may not be turned into an APPLY path")
with tempfile.TemporaryDirectory() as tmp:
    dest_ok = os.path.join(tmp, "digest.md")
    p = S.emit(dest_ok, "x\n", [PROP])
    check("A-control: an ordinary destination IS written", os.path.exists(p))

    # THE case: the caller asks for the proposal's own target.
    try:
        S.emit("docs/lessons.md", "x\n", [PROP])
        check("A: refuses to write a carried proposal's TARGET", False)
    except S.ApplyBoundary as exc:
        check("A: refuses to write a carried proposal's TARGET", "APPLY" in str(exc))

    # ...and by a path that NORMALISES to it, since that is how the check gets bypassed.
    for sneaky in ("./docs/lessons.md", "docs//lessons.md", "docs/../docs/lessons.md",
                   "DOCS/LESSONS.MD"):
        try:
            S.emit(sneaky, "x\n", [PROP])
            check("A: refuses normalised variant %r" % sneaky, False)
        except S.ApplyBoundary:
            check("A: refuses normalised variant %r" % sneaky, True)

    # A2 CONTROL: the SAME filename is fine when no proposal targets it — proving the check
    # keys on the carried set and not on a hardcoded name.
    try:
        S.emit(os.path.join(tmp, "docs", "lessons.md"), "x\n", [])
        check("A2 CONTROL: same basename allowed when no proposal targets it", True)
    except S.ApplyBoundary:
        check("A2 CONTROL: same basename allowed when no proposal targets it", False)

print("B. deny-listed destinations are refused regardless of proposals")
with tempfile.TemporaryDirectory() as tmp:
    for bad in ("modules/agos_propose.py", "secrets/token", "genesis/seal.nix",
                "~/.ssh/id_ed25519", "modules/post_scarcity.nix"):
        try:
            S.emit(bad, "x\n", [])
            check("B: refuses %r" % bad, False)
        except S.ApplyBoundary as exc:
            check("B: refuses %r (names the token)" % bad, "deny-path" in str(exc))
    # B2 CONTROL: the guard is not simply refusing everything.
    ok = os.path.join(tmp, "reports", "digest.md")
    check("B2 CONTROL: a benign path is still written", S.emit(ok, "x\n", []) == ok)

# ── C. blindness must survive to the layer a human reads ─────────────────────────────
print("C. a blind cycle must not render as a quiet one")
blind = {"blind": True, "sources_total": 1, "sources_read": 0,
         "sources": {"turnlog": {"state": "missing"}}}
healthy = {"blind": False, "sources_total": 1, "sources_read": 1,
           "sources": {"turnlog": {"state": "read"}},
           "observed": {"malformed_lines": 0}}
t_blind = S.render([], [], [], blind)
t_healthy = S.render([], [], [], healthy)
check("C: blind digest says CANNOT-ASSESS", "CANNOT-ASSESS" in t_blind)
check("C: blind digest names the unread source", "missing" in t_blind)
check("C2 CONTROL: a healthy empty digest does NOT say CANNOT-ASSESS",
      "CANNOT-ASSESS" not in t_healthy)
check("C2 CONTROL: and both otherwise report no proposals",
      "_None._" in t_blind and "_None._" in t_healthy)
check("C3: an ABSENT cycle report is its own third state, not assumed healthy",
      "Provenance unknown" in S.render([], [], [], None)
      and "CANNOT-ASSESS" not in S.render([], [], [], None))
check("C4: malformed lines are surfaced as partial",
      "partial" in S.render([], [], [], dict(healthy, observed={"malformed_lines": 3})))
check("C5: an unread-but-not-blind source is still named",
      "Not read: b" in S.render([], [], [], {
          "blind": False, "sources_total": 2, "sources_read": 1,
          "sources": {"a": {"state": "read"}, "b": {"state": "unreadable"}},
          "observed": {"malformed_lines": 0}}))

# ── D. content: refusals are surfaced, applications are not claimed ──────────────────
print("D. digest content")
t = S.render([LESSON], [PROP],
             [{"sig_id": "z", "target": "modules/agos_propose.py", "token": "agos_propose.py"}],
             healthy)
check("D: the lesson appears with its occurrence count", "seen 2×" in t)
check("D: the pending proposal names its target", "docs/lessons.md" in t)
check("D: the REFUSAL is surfaced with its token", "agos_propose.py" in t.split("Refused")[1])
check("D: the digest states nothing has been applied",
      "nothing here has changed any file" in t)
check("D: pending proposals are labelled as not applied",
      "none of these have been applied" in t)

# ── E. end to end against the REAL stores ────────────────────────────────────────────
print("E. surface() against real stores — no fakes")
with tempfile.TemporaryDirectory() as tmp:
    ls = O.LessonStore(os.path.join(tmp, "l.db"))
    ps = P.ProposalStore(os.path.join(tmp, "p.db"))
    for i in (1, 2):
        ls.record(O.Signal(O.COST_SPIKE, "cost cap tripped", "occ-%d" % i, source="t"))
    ls.compare()
    rep = P.propose(ps, ls.lessons())
    dest = os.path.join(tmp, "digest.md")
    path, text = S.surface(dest, ls, ps, healthy)
    check("E: emitted exactly one proposal upstream", rep["emitted"] == 1)
    check("E: digest written", os.path.exists(path))
    check("E: digest carries the real proposal target", "docs/lessons.md" in text)
    check("E: the proposal's TARGET was NOT created on disk",
          not os.path.exists(os.path.join(tmp, "docs", "lessons.md")))
    # E2: re-surfacing is safe and stable — the digest is a snapshot, not an accumulator.
    _, text2 = S.surface(dest, ls, ps, healthy)
    check("E2: re-surfacing produces identical text", text == text2)
    ls.close(); ps.close()

print()
print("%d checks, %d failures" % (CHECKS, len(FAILS)))
for f in FAILS:
    print("  FAILED: %s" % f)
raise SystemExit(1 if FAILS else 0)
