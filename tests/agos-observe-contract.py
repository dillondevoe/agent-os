#!/usr/bin/env python3
# tests/agos-observe-contract.py — contract battery for modules/agos_observe.py
# (self-improvement loop, phase OBSERVE + COMPARE).
#
# The module sells one hard guarantee: RE-OBSERVATION DOES NOT MANUFACTURE RECURRENCE.
# A cadence-run observer re-reads overlapping history, and if it counted naively, one
# failure seen three times would be promoted to a LESSON — the loop inventing evidence for
# its own proposals. So the battery's job is to try to catch it double-counting.
#
# Case A is the load-bearing one, and A2 is its CONTROL ARM. "I ran OBSERVE three times and
# the count stayed at 1" is EXACTLY what a store that records nothing at all would report.
# A2 therefore feeds a genuinely NEW occurrence of the SAME pattern and asserts the count
# DOES move to 2 and DOES promote. Without A2 the idempotence proof is decoration.
# (docs/cancelled-boundaries.md: control-arm the instrument, not just the guard.)

import hashlib
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import agos_events
import agos_observe

FAILURES = []


def check(label, cond):
    print("%s — %s" % (label, "PASS" if cond else "FAIL"))
    if not cond:
        FAILURES.append(label)


def store():
    return agos_observe.LessonStore(os.path.join(tempfile.mkdtemp(), "lessons.db"))


def sig(detail="boom", occ="occ-1", typ=agos_observe.TOOL_FAILURE):
    return agos_observe.Signal(typ, detail, occ, ts="2026-08-19T10:00:00Z", source="test")


# ── A. THE LOAD-BEARING RULE: observing the same thing twice is not recurrence ──────
s = store()
first = agos_observe.observe(s, [sig()])
second = agos_observe.observe(s, [sig()])
third = agos_observe.observe(s, [sig()])
check("A. the first observation is new", first["new"] == 1)
check("A. re-observing the SAME occurrence records nothing new",
      second["new"] == 0 and third["new"] == 0)
check("A. and it is reported as already_seen, not silently dropped",
      second["already_seen"] == 1)
check("A. the occurrence count does NOT grow by re-reading",
      s.occurrences(sig().sig_id) == 1)
check("A. so a once-seen failure is NEVER promoted to a LESSON, however often observed",
      third["promoted"] == [] and s.lessons() == [])
check("A. it stays a CANDIDATE with an honest count of 1",
      len(s.candidates()) == 1 and s.candidates()[0]["occurrences"] == 1)

# A2 — THE CONTROL ARM. A store that recorded nothing would pass every assertion above.
# Feed a genuinely SECOND occurrence of the SAME pattern and prove the counter can move.
promo = agos_observe.observe(s, [sig(occ="occ-2")])
check("A2. CONTROL ARM — a genuinely distinct occurrence DOES count", promo["new"] == 1)
check("A2. CONTROL ARM — two real occurrences reach the threshold and PROMOTE",
      len(promo["promoted"]) == 1 and s.occurrences(sig().sig_id) == 2)
check("A2. CONTROL ARM — and it is now a LESSON, not a candidate",
      len(s.lessons()) == 1 and s.candidates() == [])

# ── B. the two hashes answer different questions ───────────────────────────────────
check("B. same pattern, different occurrence → SAME sig_id (so recurrence can be seen)",
      sig(occ="a").sig_id == sig(occ="b").sig_id)
check("B. different pattern → DIFFERENT sig_id",
      sig(detail="boom").sig_id != sig(detail="other").sig_id)
check("B. type participates in the pattern id",
      sig(typ=agos_observe.TOOL_FAILURE).sig_id != sig(typ=agos_observe.COST_SPIKE).sig_id)
check("B. sig_id ignores the timestamp — the same failure an hour later still recurs",
      agos_observe.Signal("T", "d", "o1", ts="A").sig_id ==
      agos_observe.Signal("T", "d", "o2", ts="B").sig_id)

# ── C. occurrence keys are derived from WHERE, never from WHEN READ ────────────────
k1 = agos_observe._occ_key("events:t", "dvo", 7)
k2 = agos_observe._occ_key("events:t", "dvo", 7)
check("C. the same event yields the same key on every pass", k1 == k2)
check("C. a different event yields a different key",
      k1 != agos_observe._occ_key("events:t", "dvo", 8))
check("C. the same id on a different machine is a DIFFERENT occurrence",
      k1 != agos_observe._occ_key("events:t", "mini", 7))

# ── D. extraction from a real event log, and the log is READ-ONLY ──────────────────
root = tempfile.mkdtemp()
log = agos_events.EventLog(root, machine="dvo")
log.emit("build", "request", {"n": 1}, corr_id="c1", actor="mirror")
log.emit("build", "error", {"reason": "flake check failed"}, corr_id="c1", actor="mirror")
log.emit("build", "done", {}, corr_id="c1", actor="mirror")
log.emit("build", "request", {"n": 2}, corr_id="c2", actor="mirror")  # never done → stall

before = {}
for dp, _d, fs in os.walk(root):
    for f in fs:
        p = os.path.join(dp, f)
        before[p] = hashlib.sha256(open(p, "rb").read()).hexdigest()

ev_sigs = agos_observe.signals_from_events(log, "build")
kinds = sorted(x.type for x in ev_sigs)
check("D. an error event becomes a TOOL_FAILURE", agos_observe.TOOL_FAILURE in kinds)
check("D. a request with no done becomes STALLED_WORK", agos_observe.STALLED_WORK in kinds)
check("D. CONTROL ARM — the COMPLETED thread (c1) is NOT reported as stalled",
      sum(1 for x in ev_sigs if x.type == agos_observe.STALLED_WORK) == 1)
check("D. the failure reason survives into the detail",
      any("flake check failed" in x.detail for x in ev_sigs))

after = {}
for dp, _d, fs in os.walk(root):
    for f in fs:
        p = os.path.join(dp, f)
        after[p] = hashlib.sha256(open(p, "rb").read()).hexdigest()
check("D. OBSERVE did not modify ANY log file (byte hashes unchanged)", before == after)
check("D. OBSERVE did not add or remove log files", set(before) == set(after))

# re-extracting the same log must not inflate anything
s2 = store()
agos_observe.observe(s2, agos_observe.signals_from_events(log, "build"))
r2 = agos_observe.observe(s2, agos_observe.signals_from_events(log, "build"))
check("D. re-extracting the whole log adds no new occurrences", r2["new"] == 0)
check("D. and still promotes nothing (each thing happened once)", r2["promoted"] == [])

# ── E. the turn-log is multi-writer: filter on `event`, never assume homogeneity ────
tl = os.path.join(tempfile.mkdtemp(), "turn-log.jsonl")
with open(tl, "w") as fh:
    fh.write(json.dumps({"event": "cost_cap_breaker", "kind": "token",
                         "hops": 3, "output_tokens": 90000}) + "\n")
    fh.write(json.dumps({"event": "some_other_writer", "kind": "token"}) + "\n")
    fh.write(json.dumps({"no_event_field": True}) + "\n")
    fh.write("{ not json at all\n")
    fh.write("[1,2,3]\n")          # valid JSON, wrong shape
    fh.write("\n")                 # blank lines are not malformed
    fh.write(json.dumps({"event": "cost_cap_breaker", "kind": "hop", "hops": 9}) + "\n")

sigs, malformed = agos_observe.signals_from_turnlog(tl)
check("E. only cost_cap_breaker lines are picked up (foreign events ignored)",
      len(sigs) == 2 and all(x.type == agos_observe.COST_SPIKE for x in sigs))
check("E. CONTROL ARM — it really does read the file (both breaker kinds seen)",
      sorted(x.detail for x in sigs) == ["cost cap tripped (hop)", "cost cap tripped (token)"])
check("E. unreadable lines are COUNTED, not silently swallowed", malformed == 2)
check("E. blank lines are not counted as malformed", malformed == 2)
check("E. the count is surfaced in the report, so 'no spikes' ≠ 'could not read'",
      agos_observe.observe(store(), sigs, malformed)["malformed_lines"] == 2)

# a growing turn-log: re-reading old lines must not manufacture a spike recurrence
s3 = store()
agos_observe.observe(s3, agos_observe.signals_from_turnlog(tl)[0])
r3 = agos_observe.observe(s3, agos_observe.signals_from_turnlog(tl)[0])
check("E. re-reading the same turn-log adds nothing", r3["new"] == 0)
check("E. one token spike read twice is still ONE occurrence",
      s3.occurrences(sigs[0].sig_id) == 1 and s3.lessons() == [])

# ── F. absence is reported honestly ────────────────────────────────────────────────
gone, m = agos_observe.signals_from_turnlog(os.path.join(tempfile.mkdtemp(), "nope.jsonl"))
check("F. a missing turn-log yields no signals and does not crash", gone == [] and m == 0)
rep = agos_observe.observe(store(), [])
check("F. an empty batch reports examined=0 — distinguishable from a healthy system",
      rep["examined"] == 0 and rep["new"] == 0 and rep["lessons"] == 0)

# ── G. this module has NO ability to act (the scope limit is a property, not a promise) ──
# read the module through its OWN __file__ — the nix sandbox lays these out flat,
# and a hardcoded ../modules/ path would make this case a no-op there.
src = open(agos_observe.__file__).read()
check("G. OBSERVE holds no apply/propose entry point",
      not any(("def %s" % n) in src for n in ("apply", "propose", "apply_lesson", "write_patch")))
check("G. and imports nothing that could edit the harness",
      "subprocess" not in src and "shutil" not in src)

print()
if FAILURES:
    print("FAILED (%d): %s" % (len(FAILURES), ", ".join(FAILURES)))
    sys.exit(1)
print("agos_observe contract battery: ALL PASS")
