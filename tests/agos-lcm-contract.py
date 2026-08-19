#!/usr/bin/env python3
# tests/agos-lcm-contract.py — contract battery for modules/agos_lcm.py (HARNESS-MAP slice 5).
#
# The module sells exactly one hard guarantee: compaction is LOSSLESS. So the battery's job
# is not to confirm that happy-path compaction works — it is to try to catch the module
# losing something. Case A2 is the important one: it corrupts the store on purpose and
# asserts the round-trip check FIRES. A losslessness test that cannot detect a lossy store
# is decoration, and would pass just as happily against a module that stored nothing at all.
# (docs/cancelled-boundaries.md: control-arm the instrument, not just the guard.)

import hashlib
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import agos_events
import agos_lcm

FAILURES = []


def check(label, cond):
    print("%s — %s" % (label, "PASS" if cond else "FAIL"))
    if not cond:
        FAILURES.append(label)


def raises(exc, fn, *a, **kw):
    try:
        fn(*a, **kw)
    except exc:
        return True
    except Exception:
        return False
    return False


EVENTS = [
    {"v": 1, "id": 1, "ts": "2026-08-19T10:00:00Z", "topic": "t", "kind": "request",
     "actor": "mirror", "to": ["augur"], "corr_id": "c1", "payload": {"n": 1}},
    {"v": 1, "id": 2, "ts": "2026-08-19T10:00:05Z", "topic": "t", "kind": "result",
     "actor": "augur", "to": [], "corr_id": "c1", "payload": {"deep": {"a": [1, 2, {"b": None}]}}},
    {"v": 1, "id": 3, "ts": "2026-08-19T10:00:09Z", "topic": "t", "kind": "done",
     "actor": "augur", "to": [], "corr_id": "c1", "payload": {}},
]


def store():
    d = tempfile.mkdtemp()
    return agos_lcm.Store(os.path.join(d, "lcm.db"))


# ── A. THE LOAD-BEARING RULE: expand() returns exactly what went in ────────────────
s = store()
sid = agos_lcm.compact(s, "t", EVENTS, corr_id="c1")
got = s.expand(sid)
check("A. round-trip is byte-exact, nested payloads and all",
      json.dumps(got, sort_keys=True) == json.dumps(EVENTS, sort_keys=True))
check("A. verify() accepts a faithful digest (control arm: the check can say yes)",
      agos_lcm.verify(s, sid, EVENTS) is True)

# A2 — THE CONTROL ARM. Corrupt the stored bytes behind the module's back and prove the
# round-trip check NOTICES. Without this, cases A and I would pass against a store that
# quietly dropped every payload.
s2 = store()
sid2 = agos_lcm.compact(s2, "t", EVENTS)
s2._db.execute("UPDATE digests SET raw = ? WHERE sid = ?",
               (json.dumps([dict(EVENTS[0], payload={"n": 999})]), sid2))
s2._db.commit()
check("A2. CONTROL ARM — verify() FIRES on a corrupted (lossy) store",
      raises(agos_lcm.CompactionError, agos_lcm.verify, s2, sid2, EVENTS))

# ── B. the compact view is actually compact (the entire point) ─────────────────────
s3 = store()
agos_lcm.compact(s3, "t", EVENTS, corr_id="c1")
view = s3.context()
check("B. context() returns only the light fields",
      len(view) == 1 and set(view[0]) == set(agos_lcm.CONTEXT_FIELDS))
check("B. context() carries NO raw events — it must not re-import what it compacted",
      "raw" not in view[0] and "payload" not in json.dumps(view))
check("B. the view is materially smaller than the events it stands for",
      len(json.dumps(view)) < len(json.dumps(EVENTS)) / 2)
check("B. but it still says how much it is standing for",
      view[0]["n_events"] == 3)

# ── C. a duplicate sid is refused, not silently overwritten ────────────────────────
s4 = store()
a = agos_lcm.compact(s4, "t", EVENTS)
check("C. content-addressed sid is stable across identical spans",
      agos_lcm._sid_for("t", EVENTS) == a)
check("C. re-compacting the same span RAISES instead of overwriting history",
      raises(agos_lcm.CompactionError, agos_lcm.compact, s4, "t", EVENTS))
check("C. and the original is still intact after the refused write",
      agos_lcm.verify(s4, a, EVENTS) is True)

# ── D. empty spans are refused at both layers ──────────────────────────────────────
s5 = store()
check("D. compact([]) raises rather than storing an empty digest",
      raises(agos_lcm.CompactionError, agos_lcm.compact, s5, "t", []))
check("D. store.put([]) raises too (the guard is not only in the wrapper)",
      raises(agos_lcm.CompactionError, s5.put, "x", "t", [], "s"))

# ── E. a broken summariser must not take the DATA with it ──────────────────────────
def exploding(_events):
    raise RuntimeError("model unavailable")


s6 = store()
sid6 = agos_lcm.compact(s6, "t", EVENTS, summarizer=exploding)
check("E. events survive a summariser that raises",
      agos_lcm.verify(s6, sid6, EVENTS) is True)
check("E. and the failure is LOUD in the summary, not a plausible blank",
      "summarizer failed" in s6.get(sid6)["summary"] and "RuntimeError" in s6.get(sid6)["summary"])
check("E. a summariser returning a non-string is rejected outright",
      raises(agos_lcm.CompactionError, agos_lcm.compact, s6, "t2", EVENTS,
             summarizer=lambda e: {"not": "a string"}))

# ── F. missing sid is loud, never an empty list ────────────────────────────────────
s7 = store()
check("F. expand() of an unknown sid raises KeyError (≠ 'that span was empty')",
      raises(KeyError, s7.expand, "nope"))
check("F. get() of an unknown sid raises too", raises(KeyError, s7.get, "nope"))

# ── G. read-time annotations are stripped, so expand() == what was on disk ─────────
s8 = store()
annotated = [dict(e, _machine="dvo") for e in EVENTS]
sid8 = agos_lcm.compact(s8, "t", annotated)
check("G. underscore annotations are not persisted",
      all("_machine" not in e for e in s8.expand(sid8)))
check("G. and expand() equals the ON-DISK event, not the annotated one",
      agos_lcm.verify(s8, sid8, EVENTS) is True)

# ── H. compact_log groups by corr_id AND keeps corr_id-less events ─────────────────
root = tempfile.mkdtemp()
log = agos_events.EventLog(root, machine="dvo")
log.emit("work", "request", {"n": 1}, corr_id="c1", actor="mirror")
log.emit("work", "result", {"n": 2}, corr_id="c1", actor="mirror")
log.emit("work", "request", {"n": 3}, corr_id="c2", actor="mirror")
log.emit("work", "note", {"n": 4}, actor="mirror")  # no corr_id — must NOT be dropped

before = {}
for dirpath, _dirs, files in os.walk(root):
    for f in files:
        p = os.path.join(dirpath, f)
        before[p] = hashlib.sha256(open(p, "rb").read()).hexdigest()

s9 = store()
sids = agos_lcm.compact_log(s9, log, "work")
counts = {d["corr_id"]: d["n_events"] for d in s9.context(topic="work")}
check("H. one digest per corr_id, plus one for the un-threaded events", len(sids) == 3)
check("H. c1 got both of its events", counts.get("c1") == 2)
check("H. the corr_id-less event was KEPT, not silently dropped", counts.get(None) == 1)
total = sum(len(s9.expand(x)) for x in sids)
check("H. every emitted event is accounted for across the digests (4 in, 4 out)", total == 4)

# ── I. the event log is READ-ONLY to this module ───────────────────────────────────
after = {}
for dirpath, _dirs, files in os.walk(root):
    for f in files:
        p = os.path.join(dirpath, f)
        after[p] = hashlib.sha256(open(p, "rb").read()).hexdigest()
check("I. compaction did not modify ANY log file (byte hashes unchanged)", before == after)
check("I. compaction did not add or remove log files", set(before) == set(after))

# ── J. the default summariser is free, deterministic, and informative ──────────────
one = agos_lcm.default_summarizer(EVENTS)
check("J. default summariser is deterministic", one == agos_lcm.default_summarizer(EVENTS))
check("J. it reports the event count and kind histogram",
      "3 events" in one and "request=1" in one and "done=1" in one)
check("J. it distinguishes several actors from a single one",
      "2 actors" in agos_lcm.default_summarizer(
          [dict(EVENTS[0], actor="mirror"), dict(EVENTS[1], actor="augur")]))
check("J. empty input does not crash it", agos_lcm.default_summarizer([]) == "empty")

# ── K. a non-dict event is rejected before it can corrupt a digest ─────────────────
s10 = store()
check("K. a non-dict 'event' raises instead of being stored",
      raises(agos_lcm.CompactionError, agos_lcm.compact, s10, "t", ["not-an-event"]))

print()
if FAILURES:
    print("FAILED (%d): %s" % (len(FAILURES), ", ".join(FAILURES)))
    sys.exit(1)
print("agos_lcm contract battery: ALL PASS")
