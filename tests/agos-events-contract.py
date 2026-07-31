#!/usr/bin/env python3
# tests/agos-events-contract.py — the CONTRACT BATTERY for modules/agos_events.py (Phase 1).
#
# Proves the acceptance criteria Rabbot + Geist pinned, against the MULTI-WRITER reality:
#   A. multi-writer exactly-once: emit N across TWO machine-files, two independent consumers each
#      process every event exactly once — NO double, NO miss.
#   B. replay-from-0 reproduces state deterministically (a fresh cursor sees the same ordered stream).
#   C. defer (Geist PIN #1): a deferred event is NOT done'd and does NOT advance the cursor; it (and
#      everything after it in the same writer-file) redelivers next wake; overall still exactly-once.
#   D. ordering is deterministic by (ts, writer-machine, id) — ts dominates, machine breaks ties.
#   E. done/await_done: completion is first-class and threaded by corr_id.
#   F. routing on (topic, to) — never by payload; empty `to` broadcasts; cc = more entries.
#
# Zero external deps: runs on a bare python3. Exits 0 on all-pass, non-zero (assert) on any failure.
# In the flake it runs in the nix sandbox next to a copy of agos_events.py; locally run with
#   PYTHONPATH=modules python3 tests/agos-events-contract.py

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "modules"))
sys.path.insert(0, os.path.dirname(__file__))  # nix sandbox: library copied alongside the test

import agos_events as E  # noqa: E402


def _root():
    return tempfile.mkdtemp(prefix="agos-events-test-")


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def test_multiwriter_exactly_once():
    root = _root()
    mini = E.EventLog(root, "mini")
    dvo = E.EventLog(root, "dvo")
    # Interleave two writers into the same topic — two machine-files, ids 1..5 each.
    for i in range(5):
        ev = mini.emit("comms", "note", {"i": i})
        check(ev["v"] == 1 and ev["id"] == i + 1 and ev["to"] == [] and "corr_id" in ev,
              "schema fields wrong on emit: %r" % ev)
        dvo.emit("comms", "note", {"i": i})

    expected = {("mini", n) for n in range(1, 6)} | {("dvo", n) for n in range(1, 6)}

    def run_consumer(name):
        seen = []
        mini.consume(name, "comms", lambda e: seen.append((e["_machine"], e["id"])))
        return seen

    a = run_consumer("brainA")
    check(len(a) == 10, "brainA processed %d, want 10" % len(a))
    check(set(a) == expected, "brainA missed/extra events: %r" % (set(a) ^ expected))
    check(len(set(a)) == 10, "brainA double-processed something: %r" % a)

    # Second pass over the same consumer: nothing new (idempotent — no double-fire).
    again = mini.consume("brainA", "comms", lambda e: (_ for _ in ()).throw(AssertionError("re-fired!")))
    check(again == {"processed": 0, "deferred": 0}, "brainA re-fired on 2nd pass: %r" % again)

    # A second, independent consumer sees the full set exactly once (per-consumer cursors).
    b = run_consumer("brainB")
    check(set(b) == expected and len(set(b)) == 10, "brainB not exactly-once: %r" % b)
    print("A. multi-writer exactly-once (no double, no miss) — PASS")


def test_replay_from_zero():
    root = _root()
    mini = E.EventLog(root, "mini")
    dvo = E.EventLog(root, "dvo")
    for i in range(3):
        mini.emit("t", "note", {"i": i})
        dvo.emit("t", "note", {"i": i})
    seq1 = [(e["_machine"], e["id"]) for e in mini.read("t")]
    seq2 = [(e["_machine"], e["id"]) for e in dvo.read("t")]
    check(seq1 == seq2, "read() not deterministic across instances: %r vs %r" % (seq1, seq2))
    # A fresh consumer replaying from cursor 0 reproduces exactly that ordered stream.
    replayed = []
    dvo.consume("replay", "t", lambda e: replayed.append((e["_machine"], e["id"])))
    check(replayed == seq1, "replay-from-0 diverged: %r vs %r" % (replayed, seq1))
    print("B. replay-from-0 reproduces state — PASS")


def test_defer_redelivers_without_done():
    root = _root()
    mini = E.EventLog(root, "mini")
    for i in range(1, 4):
        mini.emit("jobs", "request", {"i": i}, corr_id="c%d" % i)

    processed_ids = []  # every successful process, across all passes → must be exactly-once overall

    # Pass 1: defer id==2. Head-of-line means id==3 (same writer) is held behind it too.
    def h1(e):
        if e["id"] == 2:
            raise E.Defer()
        processed_ids.append(e["id"])

    r1 = mini.consume("worker", "jobs", h1)
    check(r1["processed"] == 1 and processed_ids == [1], "defer pass1 wrong: %r %r" % (r1, processed_ids))

    # A defer must NOT have emitted a done event.
    dones = [e for e in mini.read("jobs") if e["kind"] == "done"]
    check(dones == [], "defer wrongly emitted done(s): %r" % dones)

    # Pass 2: no defer now → the deferred id==2 AND the held-back id==3 redeliver, in order.
    r2 = mini.consume("worker", "jobs", lambda e: processed_ids.append(e["id"]))
    check(r2["processed"] == 2 and processed_ids == [1, 2, 3],
          "redelivery order/count wrong: %r %r" % (r2, processed_ids))

    # Pass 3: fully drained.
    r3 = mini.consume("worker", "jobs", lambda e: processed_ids.append(e["id"]))
    check(r3 == {"processed": 0, "deferred": 0}, "not drained after redelivery: %r" % r3)
    check(processed_ids == [1, 2, 3], "not exactly-once overall: %r" % processed_ids)
    print("C. defer redelivers, no done, head-of-line, exactly-once — PASS")


def test_ordering_deterministic():
    root = _root()
    events_dir = os.path.join(root, "events")
    os.makedirs(events_dir)
    T = "2026-07-31T00:00:00."

    def line(mid, ts):
        import json
        return json.dumps({"v": 1, "id": mid, "ts": T + ts, "topic": "ord", "kind": "note",
                           "actor": "x", "to": [], "corr_id": None, "payload": {}},
                          separators=(",", ":"), sort_keys=True) + "\n"

    # Hand-crafted so ts collides on the first event of each writer (tie → machine name breaks it),
    # and ts otherwise dominates id/machine.
    with open(os.path.join(events_dir, "ord.aaa.jsonl"), "w") as f:
        f.write(line(1, "005000Z"))   # tie with bbb id1
        f.write(line(2, "009000Z"))   # latest ts overall
    with open(os.path.join(events_dir, "ord.bbb.jsonl"), "w") as f:
        f.write(line(1, "005000Z"))   # tie with aaa id1 → aaa wins on machine name
        f.write(line(2, "007000Z"))
    got = [(e["_machine"], e["id"]) for e in E.EventLog(root, "aaa").read("ord")]
    want = [("aaa", 1), ("bbb", 1), ("bbb", 2), ("aaa", 2)]
    check(got == want, "ordering not (ts,machine,id): got %r want %r" % (got, want))
    print("D. deterministic ordering (ts dominates, machine breaks ties) — PASS")


def test_done_await():
    root = _root()
    mini = E.EventLog(root, "mini")
    mini.emit("run", "request", {"job": "x"}, corr_id="w1")
    check(mini.await_done("w1", "run", timeout=0.15) is None, "await_done returned before done()")
    d = mini.done("w1", "run", {"result": "ok"})
    check(d["kind"] == "done" and d["corr_id"] == "w1", "done() event malformed: %r" % d)
    got = mini.await_done("w1", "run", timeout=1.0)
    check(got is not None and got["kind"] == "done" and got["corr_id"] == "w1",
          "await_done did not find the done: %r" % got)
    print("E. done/await_done first-class + corr_id threaded — PASS")


def test_routing_on_to():
    root = _root()
    mini = E.EventLog(root, "mini")
    mini.emit("comms", "note", {"n": 1}, to=["geist"])
    mini.emit("comms", "note", {"n": 2}, to=["augur"])
    mini.emit("comms", "note", {"n": 3})                 # broadcast (empty to)
    mini.emit("comms", "note", {"n": 4}, to=["x", "y"])   # cc: two addressees

    def collect(consumer, route_to):
        seen = []
        mini.consume(consumer, "comms", lambda e: seen.append(e["payload"]["n"]), route_to=route_to)
        return sorted(seen)

    check(collect("augur", "augur") == [2, 3], "augur routing wrong")
    check(collect("geist", "geist") == [1, 3], "geist routing wrong")
    # cc: BOTH x and y receive the to=[x,y] event (plus the broadcast).
    check(collect("cx", "x") == [3, 4], "cc addressee x wrong")
    check(collect("cy", "y") == [3, 4], "cc addressee y wrong")
    # A routed-away event still advanced augur's cursor (seen, not re-delivered).
    again = mini.consume("augur", "comms", lambda e: (_ for _ in ()).throw(AssertionError("re-fire")))
    check(again["processed"] == 0, "routed-away event re-delivered: %r" % again)
    print("F. routing on (topic, to), broadcast, cc — PASS")


def test_ts_override_drives_order():
    # emit() with an explicit ts records THAT ts and it drives merge order — even within one file,
    # where id is monotonic-ascending, an earlier ts sorts first. This is what lets the comms shadow
    # stamp events with real arrival (file-mtime) times and still merge in true arrival order.
    root = _root()
    mini = E.EventLog(root, "mini")
    late = mini.emit("t", "note", {"n": "late"}, ts="2026-07-31T00:00:09.000000Z")   # id 1, late ts
    early = mini.emit("t", "note", {"n": "early"}, ts="2026-07-31T00:00:01.000000Z")  # id 2, early ts
    check(late["id"] == 1 and early["id"] == 2, "ids not monotonic per file: %r %r" % (late, early))
    order = [(e["id"], e["payload"]["n"]) for e in mini.read("t")]
    check(order == [(2, "early"), (1, "late")], "ts override did not drive order: %r" % order)
    # A done() ts override records too.
    d = mini.done("w", "t", {"ok": 1}, ts="2026-07-31T00:00:05.000000Z")
    check(d["ts"] == "2026-07-31T00:00:05.000000Z", "done ts override not recorded: %r" % d)
    print("G. ts override recorded + drives merge order — PASS")


def main():
    test_multiwriter_exactly_once()
    test_replay_from_zero()
    test_defer_redelivers_without_done()
    test_ordering_deterministic()
    test_done_await()
    test_routing_on_to()
    test_ts_override_drives_order()
    print("\nagos-events contract battery: ALL PASS")


if __name__ == "__main__":
    main()
