#!/usr/bin/env python3
# tests/agos-subagents-contract.py — the CONTRACT BATTERY for modules/agos_subagents.py
# (HARNESS-MAP slice 3: subagent fan-out with typed yields).
#
# Proves:
#   A. fan-out runs every unit, concurrently, under an explicit cap — and the cap is REAL
#      (observed max in-flight never exceeds it), not merely passed.
#   B. TYPED YIELDS: a malformed yield is an `error` event with a reason, NOT a silent skip and
#      NOT a half-trusted result. Missing field, wrong type, and extra field are all caught.
#   C. the validator has a CONTROL ARM — it ACCEPTS a known-good yield. A validator that rejects
#      everything would pass B while being useless (docs/cancelled-boundaries.md: control-arm the
#      instrument, not just the guard).
#   D. a worker that RAISES becomes an error event; the other units still complete (one bad unit
#      does not take the run down).
#   E. DEFER (Geist PIN #1): a deferred unit emits no result, no error, and the run emits NO `done`.
#   F. the run budget: an over-running unit is a timeout error and the run still returns.
#   G. gather() reconstructs the outcome from the LOG alone — replay parity with the in-memory
#      FanOut, because the events are the truth.
#   H. done is first-class and threaded by corr_id; its payload counts match the run.
#   I. bool is not an int; a bad SCHEMA raises rather than silently passing everything.
#
# Zero external deps: runs on a bare python3. Exits 0 on all-pass, non-zero (assert) on any failure.
# Locally: PYTHONPATH=modules python3 tests/agos-subagents-contract.py

import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "modules"))
sys.path.insert(0, os.path.dirname(__file__))  # nix sandbox: libraries copied alongside the test

import agos_events as E  # noqa: E402
import agos_subagents as S  # noqa: E402


def _log():
    return E.EventLog(tempfile.mkdtemp(prefix="agos-subagents-test-"), "dvo")


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


SCHEMA = {"unit": "str", "score": "int", "tags": "list[str]", "note": "str?"}


def _good(name, score=1):
    return {"unit": name, "score": score, "tags": ["a"]}


def test_fanout_runs_all_under_a_real_cap():
    log = _log()
    lock = threading.Lock()
    inflight = {"now": 0, "max": 0}

    def worker(x):
        with lock:
            inflight["now"] += 1
            inflight["max"] = max(inflight["max"], inflight["now"])
        time.sleep(0.05)          # hold the slot so concurrency is observable
        with lock:
            inflight["now"] -= 1
        return _good("u", x)

    run = S.fan_out(log, "fan", list(range(12)), worker, schema=SCHEMA, concurrency=3)
    check(run.ok, "expected a clean run: %r errors=%r" % (run, run.errors))
    check(len(run.results) == 12, "not every unit produced a result: %r" % (run.results,))
    # The cap is real, and the run genuinely overlapped (a serial run would prove nothing).
    check(inflight["max"] <= 3, "concurrency cap breached: max in flight %d > 3" % inflight["max"])
    check(inflight["max"] > 1, "never actually ran concurrently (max in flight %d) — the cap test "
                               "would pass trivially" % inflight["max"])
    print("A. fan-out runs all units under a REAL concurrency cap (max in flight %d/3) — PASS"
          % inflight["max"])


def test_malformed_yields_are_errors_not_skips():
    log = _log()
    cases = {
        "missing":   {"unit": "missing", "tags": []},                       # no `score`
        "wrongtype": {"unit": "wrongtype", "score": "7", "tags": []},       # score is a str
        "badelem":   {"unit": "badelem", "score": 1, "tags": [1, 2]},       # list[str] of ints
        "extra":     dict(_good("extra"), surprise=1),                      # undeclared field
        "notadict":  ["not", "a", "dict"],                                  # not a mapping at all
    }
    units = [(name, name) for name in cases]
    run = S.fan_out(log, "fan", units, lambda n: cases[n], schema=SCHEMA, concurrency=4)

    check(not run.ok, "a run full of malformed yields must not be ok: %r" % run)
    check(run.results == {}, "a malformed yield leaked through as a result: %r" % run.results)
    check(set(run.errors) == set(cases), "not every bad unit produced an error: %r" % run.errors)
    for name, err in run.errors.items():
        check(err["reason"] == "invalid-yield", "%s: wrong reason %r" % (name, err))
        check(err["detail"], "%s: an error with no detail is not addressable" % name)
    # The reasons are SPECIFIC — "it failed" is not a finding.
    check("missing required field 'score'" in run.errors["missing"]["detail"],
          "missing-field detail unhelpful: %r" % run.errors["missing"]["detail"])
    check("unexpected field 'surprise'" in run.errors["extra"]["detail"],
          "extra-field detail unhelpful: %r" % run.errors["extra"]["detail"])
    # And they are on the LOG as error events, not just in memory.
    logged = [e for e in log.read("fan") if e["kind"] == "error"]
    check(len(logged) == len(cases), "errors not all emitted as events: %d" % len(logged))
    print("B. malformed yields are loud errors with specific reasons, never silent skips — PASS")


def test_validator_control_arm():
    # THE CONTROL ARM. B proves the validator says no. This proves it can say YES — without it,
    # a validator that rejected literally everything would pass B and be worthless.
    check(S.validate(_good("x"), SCHEMA) == [], "control arm FAILED: a known-good yield was rejected")
    check(S.validate(dict(_good("x"), note="hi"), SCHEMA) == [],
          "control arm FAILED: a valid optional field was rejected")
    check(S.validate({"unit": "x", "score": 1, "tags": []}, SCHEMA) == [],
          "control arm FAILED: an omitted OPTIONAL field was treated as missing")
    check(S.validate({"anything": 1}, None) == [], "schema=None must skip validation")
    # And the positive control runs through the real fan-out, not just the validator in isolation.
    log = _log()
    run = S.fan_out(log, "fan", [("a", "a"), ("b", "b")], lambda n: _good(n), schema=SCHEMA)
    check(run.ok and len(run.results) == 2, "control arm FAILED end-to-end: %r %r" % (run, run.errors))
    print("C. validator control arm: known-good yields ACCEPTED (validator is not a no-op) — PASS")


def test_raising_worker_is_isolated():
    log = _log()

    def worker(n):
        if n == "boom":
            raise RuntimeError("worker exploded")
        return _good(n)

    units = [(n, n) for n in ("ok1", "boom", "ok2")]
    run = S.fan_out(log, "fan", units, worker, schema=SCHEMA)
    check(set(run.results) == {"ok1", "ok2"}, "a raising unit took its siblings down: %r" % run.results)
    check(run.errors["boom"]["reason"] == "raised", "wrong reason: %r" % run.errors["boom"])
    check("worker exploded" in run.errors["boom"]["detail"], "the raise is not diagnosable: %r"
          % run.errors["boom"])
    check(run.complete and not run.ok, "a run with an error is complete but NOT ok: %r" % run)
    print("D. a raising worker is an isolated error; siblings still complete — PASS")


def test_defer_emits_no_done():
    log = _log()

    def worker(n):
        if n == "later":
            raise E.Defer()
        return _good(n)

    run = S.fan_out(log, "fan", [(n, n) for n in ("now", "later")], worker, schema=SCHEMA)
    check(run.deferred == ["later"], "defer not recorded: %r" % run.deferred)
    check("later" not in run.results and "later" not in run.errors,
          "a deferred unit must be NEITHER result nor error: %r %r" % (run.results, run.errors))
    check(not run.complete and not run.ok, "a deferred run is not complete: %r" % run)
    dones = [e for e in log.read("fan") if e["kind"] == "done"]
    check(dones == [], "PIN #1 VIOLATED: a run with a deferred unit emitted done: %r" % dones)
    # Control arm on the same assertion: without the defer, done DOES appear.
    log2 = _log()
    S.fan_out(log2, "fan", [("now", "now")], lambda n: _good(n), schema=SCHEMA)
    check([e for e in log2.read("fan") if e["kind"] == "done"],
          "control arm FAILED: no done even on a clean run, so the PIN-#1 check proves nothing")
    print("E. defer: no result, no error, NO done (control-armed against a clean run) — PASS")


def test_run_budget_times_out():
    log = _log()

    def worker(n):
        if n == "slow":
            time.sleep(2)   # >> the 0.3s budget, small enough not to drag CI at exit
            return _good(n)
        return _good(n)

    started = time.monotonic()
    run = S.fan_out(log, "fan", [(n, n) for n in ("fast", "slow")], worker, schema=SCHEMA,
                    concurrency=2, timeout=0.3)
    elapsed = time.monotonic() - started
    check(elapsed < 1.5, "the run did not return on its budget (took %.1fs) — a budget you "
                         "cannot observe in wall-clock is not a budget" % elapsed)
    check(run.errors["slow"]["reason"] == "timeout", "slow unit not a timeout: %r" % run.errors)
    check("fast" in run.results, "the fast unit's result was lost to the timeout: %r" % run.results)
    print("F. run budget: over-running unit becomes a timeout error, run returns (%.2fs) — PASS"
          % elapsed)


def test_gather_replays_from_the_log():
    log = _log()

    def worker(n):
        if n == "bad":
            return {"unit": "bad"}       # invalid
        return _good(n)

    units = [(n, n) for n in ("a", "bad", "c")]
    run = S.fan_out(log, "fan", units, worker, schema=SCHEMA, corr_id="run-1")
    replay = S.gather(log, "fan", "run-1")
    check(replay.results == run.results, "replay lost results: %r vs %r" % (replay.results, run.results))
    check(set(replay.errors) == set(run.errors), "replay lost errors: %r" % replay.errors)
    check(replay.ok == run.ok and replay.complete == run.complete,
          "replay disagrees on ok/complete: %r vs %r" % (replay, run))
    check(set(replay.units) == {"a", "bad", "c"}, "replay lost the unit list: %r" % replay.units)
    # A second run on the SAME topic must not bleed into the first's replay.
    S.fan_out(log, "fan", [("z", "z")], lambda n: _good(n), schema=SCHEMA, corr_id="run-2")
    again = S.gather(log, "fan", "run-1")
    check(set(again.units) == {"a", "bad", "c"} and "z" not in again.results,
          "corr_id isolation broken — a sibling run bled in: %r" % again)
    print("G. gather() replays a run from the log alone, isolated by corr_id — PASS")


def test_done_is_threaded_and_counted():
    log = _log()

    def worker(n):
        return {"unit": n} if n == "bad" else _good(n)

    run = S.fan_out(log, "fan", [(n, n) for n in ("a", "b", "bad")], worker, schema=SCHEMA,
                    corr_id="c-9")
    ev = log.await_done("c-9", "fan", timeout=2.0)
    check(ev is not None, "no done event threaded by corr_id")
    check(ev["payload"] == {"units": 3, "results": 2, "errors": 1, "ok": False},
          "done payload does not describe the run: %r" % ev["payload"])
    check(run.ok is False, "run with an error reported ok")
    print("H. done is first-class, threaded by corr_id, and its counts match — PASS")


def test_type_edges_and_bad_schema():
    # bool is a subclass of int in Python; an int field silently accepting True is a real trap.
    check(S.validate({"unit": "x", "score": True, "tags": []}, SCHEMA) != [],
          "bool leaked through an int field")
    # Control arm for that very check: a real int IS accepted.
    check(S.validate({"unit": "x", "score": 0, "tags": []}, SCHEMA) == [],
          "control arm FAILED: a legitimate int was rejected")
    # A bad SCHEMA is a programming error in the fan-out — it must raise, not quietly pass all.
    for bad in ({"f": "strr"}, {"f": "list[nope]"}):
        try:
            S.validate({"f": "x"}, bad)
        except ValueError:
            pass
        else:
            raise AssertionError("bad schema %r did not raise — it would pass everything" % (bad,))
    try:
        S.fan_out(_log(), "fan", ["a"], lambda n: _good(n), schema={"f": "bogus"})
    except ValueError:
        pass
    else:
        raise AssertionError("fan_out accepted a bogus schema instead of failing before any work")
    # An explicit cap of zero is a bug, not a "no limit".
    try:
        S.fan_out(_log(), "fan", ["a"], lambda n: _good(n), concurrency=0)
    except ValueError:
        pass
    else:
        raise AssertionError("concurrency=0 accepted")
    print("I. bool != int, bad schemas raise before any work, cap must be sane — PASS")


def main():
    test_fanout_runs_all_under_a_real_cap()
    test_malformed_yields_are_errors_not_skips()
    test_validator_control_arm()
    test_raising_worker_is_isolated()
    test_defer_emits_no_done()
    test_run_budget_times_out()
    test_gather_replays_from_the_log()
    test_done_is_threaded_and_counted()
    test_type_edges_and_bad_schema()
    print("\nagos-subagents contract battery: ALL PASS")


if __name__ == "__main__":
    main()
