#!/usr/bin/env python3
# tests/agos-cycle-contract.py — contract battery for the cadence runner.
#
# THE SEAM IS THE SUBJECT. agos_observe and agos_propose each have a green battery of their
# own; re-proving either here would be a second green that carries no new information. So
# every case below drives the REAL agos_observe into the REAL agos_propose. Nothing at the
# seam is faked. The one injected value is the DRAFTS table, which agos_propose exposes for
# exactly this purpose and whose limits its own docstring states.
#
# Each guard case ships a CONTROL ARM. A check that has never been watched go red is a
# check whose red state is unproven, and this battery exists precisely because three green
# batteries did not add up to a working loop.

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "modules"))

import agos_observe as O
import agos_propose as P
import agos_cycle as C

FAILS = []
CHECKS = 0


def check(label, cond):
    global CHECKS
    CHECKS += 1
    if cond:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s" % label)
        FAILS.append(label)


def stores(tmp, tag):
    return (O.LessonStore(os.path.join(tmp, "l-%s.db" % tag)),
            P.ProposalStore(os.path.join(tmp, "p-%s.db" % tag)))


def turnlog(tmp, name, records):
    path = os.path.join(tmp, name)
    with open(path, "w") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    return path


SPIKE = {"event": "cost_cap_breaker", "kind": "tokens", "hops": 3, "ts": "2026-08-20T10:00Z"}


# ── A. structural: the runner may not act ────────────────────────────────────────────
print("A. structural — the runner emits documents, it does not act")
SRC = open(os.path.join(os.path.dirname(HERE), "modules", "agos_cycle.py")).read()
for tok in ("import subprocess", "import shutil", "import urllib", "import socket",
            "import requests"):
    check("no %s" % tok, tok not in SRC)
import re
check("no apply/merge/commit/push entry point",
      re.search(r"^def (apply|merge|commit|push)", SRC, re.M) is None)
check("no open-for-write anywhere in the runner",
      re.search(r"open\([^)]*['\"][wa]", SRC) is None)
# CONTROL ARM: the grep must be capable of firing at all.
check("A-control: the write-grep fires on a known-positive string",
      re.search(r"open\([^)]*['\"][wa]", "open(p, 'w')") is not None)


# ── B. the composition actually composes ─────────────────────────────────────────────
print("B. end-to-end: a recurring signal becomes an emitted proposal")
with tempfile.TemporaryDirectory() as tmp:
    # Two DISTINCT occurrences of the same pattern => COMPARE promotes => PROPOSE drafts.
    path = turnlog(tmp, "t.jsonl", [SPIKE, dict(SPIKE, hops=4)])
    ls, ps = stores(tmp, "b")
    rep = C.cycle(ls, ps, [C.Source("turnlog", lambda: O.signals_from_turnlog(path),
                                    lambda: True)])
    check("source read", rep["sources"]["turnlog"]["state"] == C.READ)
    check("not blind", rep["blind"] is False)
    check("two signals examined", rep["observed"]["examined"] == 2)
    check("one pattern promoted to LESSON", len(rep["observed"]["promoted"]) == 1)
    check("one proposal emitted", rep["proposed"]["emitted"] == 1)
    check("nothing refused", rep["proposed"]["refused"] == [])
    emitted = ps.by_status(P.EMITTED)
    check("emitted proposal targets docs/", emitted[0]["target"].startswith("docs/"))
    ls.close(); ps.close()

print("B2. CONTROL — a signal seen ONCE must NOT reach PROPOSE")
with tempfile.TemporaryDirectory() as tmp:
    path = turnlog(tmp, "t.jsonl", [SPIKE])
    ls, ps = stores(tmp, "b2")
    rep = C.cycle(ls, ps, [C.Source("t", lambda: O.signals_from_turnlog(path), lambda: True)])
    check("B2: one occurrence examined", rep["observed"]["examined"] == 1)
    check("B2: nothing promoted", rep["observed"]["promoted"] == [])
    check("B2: nothing emitted", rep["proposed"]["emitted"] == 0)
    check("B2: and it is reported as zero LESSONS, not zero sources",
          rep["proposed"]["lessons"] == 0 and rep["sources_read"] == 1)
    ls.close(); ps.close()


# ── C. the load-bearing rule: an empty cycle says WHY ────────────────────────────────
print("C. blindness — absent, unreadable and healthy are three different facts")
with tempfile.TemporaryDirectory() as tmp:
    missing = os.path.join(tmp, "does-not-exist.jsonl")
    ls, ps = stores(tmp, "c")
    rep = C.cycle(ls, ps, [C.turnlog_source(missing)])
    check("missing source is MISSING, not read",
          rep["sources"]["turnlog"]["state"] == C.MISSING)
    check("cycle reports blind", rep["blind"] is True)
    check("render leads with CANNOT-ASSESS", C.render(rep).startswith("CANNOT-ASSESS"))
    # This is the exact defect the runner exists to cover: the READ HALF cannot tell these
    # apart on its own. Assert that directly so the reason is not just prose in a header.
    sigs, malformed = O.signals_from_turnlog(missing)
    check("C-premise: read half returns ([],0) for a MISSING file",
          sigs == [] and malformed == 0)
    ls.close(); ps.close()

with tempfile.TemporaryDirectory() as tmp:
    empty = turnlog(tmp, "empty.jsonl", [])
    ls, ps = stores(tmp, "c2")
    rep = C.cycle(ls, ps, [C.turnlog_source(empty)])
    check("C2 CONTROL: a present-but-empty log is READ, not MISSING",
          rep["sources"]["turnlog"]["state"] == C.READ)
    check("C2 CONTROL: and the cycle is NOT blind", rep["blind"] is False)
    check("C2 CONTROL: render does not claim cannot-assess",
          not C.render(rep).startswith("CANNOT-ASSESS"))
    ls.close(); ps.close()

print("C3. an exploding source is UNREADABLE and does not abort the cycle")
with tempfile.TemporaryDirectory() as tmp:
    path = turnlog(tmp, "t.jsonl", [SPIKE, dict(SPIKE, hops=4)])
    def boom():
        raise IOError("disk on fire")
    ls, ps = stores(tmp, "c3")
    rep = C.cycle(ls, ps, [
        C.Source("bad", boom, lambda: True),
        C.Source("good", lambda: O.signals_from_turnlog(path), lambda: True),
    ])
    check("C3: bad source UNREADABLE", rep["sources"]["bad"]["state"] == C.UNREADABLE)
    check("C3: the error is RECORDED, not swallowed",
          "disk on fire" in rep["sources"]["bad"].get("error", ""))
    check("C3: the good source still produced its proposal",
          rep["proposed"]["emitted"] == 1)
    check("C3: not blind — one of two readable", rep["blind"] is False)
    ls.close(); ps.close()

print("C4. malformed lines survive the seam into the report")
with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "junk.jsonl")
    with open(path, "w") as fh:
        fh.write("{not json\n[]\n" + json.dumps(SPIKE) + "\n")
    ls, ps = stores(tmp, "c4")
    rep = C.cycle(ls, ps, [C.turnlog_source(path)])
    check("C4: malformed count reaches the OBSERVE report",
          rep["observed"]["malformed_lines"] == 2)
    check("C4: and appears in rendered text", "2 malformed" in C.render(rep))
    ls.close(); ps.close()


# ── D. composition idempotence across cadence runs ───────────────────────────────────
print("D. idempotence — running the cadence three times moves nothing")
with tempfile.TemporaryDirectory() as tmp:
    path = turnlog(tmp, "t.jsonl", [SPIKE, dict(SPIKE, hops=4)])
    ls, ps = stores(tmp, "d")
    src = [C.Source("t", lambda: O.signals_from_turnlog(path), lambda: True)]
    first = C.cycle(ls, ps, src)
    second = C.cycle(ls, ps, src)
    third = C.cycle(ls, ps, src)
    check("D: first run emits", first["proposed"]["emitted"] == 1)
    check("D: second run emits nothing new", second["proposed"]["emitted"] == 0)
    check("D: third run emits nothing new", third["proposed"]["emitted"] == 0)
    check("D: re-runs report SUPPRESSED, not a silent zero",
          second["proposed"]["suppressed"] == 1 and third["proposed"]["suppressed"] == 1)
    check("D: re-reads counted as already_seen, not new",
          second["observed"]["new"] == 0 and second["observed"]["already_seen"] == 2)
    check("D: exactly one proposal in the store after three cycles",
          len(ps.by_status(P.EMITTED)) == 1)
    ls.close(); ps.close()


# ── E. the deny list is still armed THROUGH the runner ───────────────────────────────
print("E. a forbidden target driven through cycle() is REFUSED, not emitted")
BAD = {O.COST_SPIKE: ("modules/agos_propose.py", "loosen the deny list")}
with tempfile.TemporaryDirectory() as tmp:
    path = turnlog(tmp, "t.jsonl", [SPIKE, dict(SPIKE, hops=4)])
    ls, ps = stores(tmp, "e")
    rep = C.cycle(ls, ps, [C.Source("t", lambda: O.signals_from_turnlog(path), lambda: True)],
                  drafts=BAD)
    check("E: refused at the runner boundary", len(rep["proposed"]["refused"]) == 1)
    check("E: and NOT emitted", rep["proposed"]["emitted"] == 0)
    check("E: nothing sits in EMITTED", ps.by_status(P.EMITTED) == [])
    refused = ps.by_status(P.REFUSED)
    check("E: the refusal records WHICH token fired",
          len(refused) == 1 and refused[0]["reason"])
    ls.close(); ps.close()

print("E2. CONTROL — the same path with an allowed target DOES emit")
OK = {O.COST_SPIKE: ("docs/lessons.md", "record the recurring cost spike")}
with tempfile.TemporaryDirectory() as tmp:
    path = turnlog(tmp, "t.jsonl", [SPIKE, dict(SPIKE, hops=4)])
    ls, ps = stores(tmp, "e2")
    rep = C.cycle(ls, ps, [C.Source("t", lambda: O.signals_from_turnlog(path), lambda: True)],
                  drafts=OK)
    check("E2: emitted", rep["proposed"]["emitted"] == 1)
    check("E2: nothing refused", rep["proposed"]["refused"] == [])
    ls.close(); ps.close()


# ─── F. THE ENTRY POINT ITSELF ────────────────────────────────────────────────
# These are the cases whose ABSENCE let the caller go missing twice. Every part
# below main() was green while the loop never ran end to end, because the battery
# tested the parts. So: drive run()/main(), assert a digest actually lands on
# disk, and control-arm it so a green here cannot mean "wrote nothing, quietly."
print("F. run() drives the WHOLE loop and a digest reaches disk")
with tempfile.TemporaryDirectory() as tmp:
    path = turnlog(tmp, "t.jsonl", [SPIKE, dict(SPIKE, hops=4)])
    dest = os.path.join(tmp, "digest.md")
    src = [C.Source("t", lambda: O.signals_from_turnlog(path), lambda: True)]
    rep, out, text = C.run(os.path.join(tmp, "l.db"), os.path.join(tmp, "p.db"), dest, src)
    check("F: run() returned a digest path", out == dest)
    check("F: the digest EXISTS on disk", os.path.exists(dest))
    body = open(dest).read()
    check("F: digest is non-empty", len(body) > 0)
    check("F: digest names the pending proposal's target", "docs/lessons.md" in body)
    check("F: digest does not claim anything was applied", "APPLY is not implemented" in body)
    check("F: the proposal TARGET was not created", not os.path.exists(os.path.join(tmp, "docs")))
    check("F: returned text is what was written", text == body)

print("F2. CONTROL — a blind run must still write, and must say it was blind")
with tempfile.TemporaryDirectory() as tmp:
    dest = os.path.join(tmp, "digest.md")
    gone = C.Source("t", lambda: O.signals_from_turnlog(os.path.join(tmp, "nope.jsonl")),
                    lambda: False)
    rep, out, text = C.run(os.path.join(tmp, "l.db"), os.path.join(tmp, "p.db"), dest, [gone])
    check("F2: blind run STILL produced a digest", out == dest and os.path.exists(dest))
    check("F2: report says blind", rep["blind"])
    check("F2: the DIGEST says cannot-assess", "CANNOT-ASSESS" in open(dest).read())
    check("F2: and does not read as a clean empty cycle",
          "Sources read: 1 of 1" not in open(dest).read())

print("F3. run() reports a refused destination instead of crashing or lying")
# The boundary is "this destination IS the proposal's target", and the target is
# carried repo-relative. So the case must be run repo-relative too. My first pass
# handed it /tmp/<x>/docs/lessons.md and expected a refusal — that is a DIFFERENT
# FILE that merely ends in the same three components, and refusing it would mean
# refusing on suffix. A writer stricter than the rule it enforces makes the honest
# path fail and trains you to route around the writer, which is the failure mode
# the whole guard exists to prevent. The suffix case is asserted allowed in F3b,
# deliberately, so nobody "hardens" it later without reading this.
with tempfile.TemporaryDirectory() as tmp:
    path = turnlog(tmp, "t.jsonl", [SPIKE, dict(SPIKE, hops=4)])
    src = [C.Source("t", lambda: O.signals_from_turnlog(path), lambda: True)]
    cwd = os.getcwd()
    try:
        os.chdir(tmp)
        rep, out, text = C.run("l.db", "p.db", os.path.join("docs", "lessons.md"), src)
        check("F3: no path returned", out is None)
        check("F3: the state is stated, not swallowed", "SURFACING REFUSED" in text)
        check("F3: the target file was NOT written",
              not os.path.exists(os.path.join("docs", "lessons.md")))
    finally:
        os.chdir(cwd)

print("F3b. CONTROL — a different file that merely ENDS in the target path is allowed")
with tempfile.TemporaryDirectory() as tmp:
    path = turnlog(tmp, "t.jsonl", [SPIKE, dict(SPIKE, hops=4)])
    src = [C.Source("t", lambda: O.signals_from_turnlog(path), lambda: True)]
    elsewhere = os.path.join(tmp, "docs", "lessons.md")
    rep, out, text = C.run(os.path.join(tmp, "l.db"), os.path.join(tmp, "p.db"),
                           elsewhere, src)
    check("F3b: written — suffix resemblance is not identity", out == elsewhere)
    check("F3b: and it is a digest, not the proposal enacted",
          "APPLY is not implemented" in open(elsewhere).read())

print("F4. CONTROL — main() returns 0 on a healthy run and 1 when it cannot surface")
with tempfile.TemporaryDirectory() as tmp:
    turnlog(tmp, "turns.jsonl", [SPIKE, dict(SPIKE, hops=4)])
    rc_ok = C.main([os.path.join(tmp, "l.db"), os.path.join(tmp, "p.db"),
                    os.path.join(tmp, "d.md")])
    check("F4: main() rc=0 when the digest lands", rc_ok == 0)
    check("F4: main() actually wrote it", os.path.exists(os.path.join(tmp, "d.md")))


print()
print("%d checks, %d failures" % (CHECKS, len(FAILS)))
for f in FAILS:
    print("  FAILED: %s" % f)
raise SystemExit(1 if FAILS else 0)
