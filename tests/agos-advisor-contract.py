#!/usr/bin/env python3
# tests/agos-advisor-contract.py — CONTRACT BATTERY for modules/agos_advisor.py
# (HARNESS-MAP slice 4: the advisor/watcher).
#
# The governing risk is stated in the module and drives every case here:
# **a watcher that never fires is indistinguishable from a healthy system.** So no rule is tested
# in one direction only. Each has a QUIET arm and a FIRING arm, and neither counts alone.
#
#   A. stalled work (THE SCAR — the merge-queue stall was a watcher gap) FIRES as a blocker.
#   B. its quiet arm: a healthy stream, a completed run, and a young request are all SILENT —
#      without this, a rule that fired on everything would pass A and be worse than nothing.
#   C. progress is not a stall: quiet is measured from the LAST event on the corr_id, so a slow
#      run still emitting results is left alone.
#   D. idempotency: the same standing problem is announced ONCE, even across a fresh Advisor
#      (dedup recovered from the log, not memory) — a spammy watcher gets muted, and a muted
#      watcher is the original gap.
#   E. a rule that RAISES becomes a loud blocker finding, never a silently-skipped rule.
#   F. `examined` distinguishes "nothing is wrong" from "I looked at nothing" — the two states
#      that produce an identical empty finding list.
#   G. rules cannot mutate the stream they watch (no emit/cursor surface on Stream).
#   H. advice lands on a SEPARATE topic and the watcher does not advise about its own advice.
#   I. error-rate fires over threshold, quiet under it; levels are validated.
#
# Zero external deps. Locally: PYTHONPATH=modules python3 tests/agos-advisor-contract.py

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "modules"))
sys.path.insert(0, os.path.dirname(__file__))

import agos_advisor as A  # noqa: E402
import agos_events as E  # noqa: E402

T0 = 1_800_000_000.0          # a fixed "now" so every age in this battery is deterministic


def _log():
    return E.EventLog(tempfile.mkdtemp(prefix="agos-advisor-test-"), "dvo")


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def _stamp(offset):
    """An ISO stamp `offset` seconds before T0, in the log's own format."""
    import datetime
    return (datetime.datetime.fromtimestamp(T0 - offset, datetime.timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%S.%fZ"))


def test_stall_fires():
    log = _log()
    log.emit("work", "request", {"unit": "a"}, corr_id="old", ts=_stamp(3600))
    adv = A.Advisor(log, "work", rules=[A.stalled_work(stall_seconds=900)])
    out = adv.observe(now=T0)
    check(len(out["emitted"]) == 1, "the scar rule did not fire on an hour-old open request: %r" % out)
    f = out["emitted"][0]
    check(f.level == "blocker" and f.rule == "stalled-work", "wrong finding: %r" % f)
    check(f.corr_id == "old", "finding not threaded to the stalled corr_id: %r" % f)
    check("3600" in f.detail and "900" in f.detail,
          "detail must state the observed quiet AND the threshold: %r" % f.detail)
    published = A.advice(log, "work", level="blocker")
    check(len(published) == 1 and published[0]["about"]["corr_id"] == "old",
          "advice not published as an event: %r" % published)
    print("A. stalled work (the scar) FIRES as a blocker, threaded to the corr_id — PASS")


def test_stall_quiet_arm():
    # THE CONTROL ARM. Without it, a rule that fired on everything would pass A.
    log = _log()
    # 1. a completed run, old but DONE
    log.emit("work", "request", {}, corr_id="done-old", ts=_stamp(3600))
    log.done("done-old", "work", {}, ts=_stamp(3500))
    # 2. an open request that is simply YOUNG
    log.emit("work", "request", {}, corr_id="young", ts=_stamp(10))
    # 3. traffic with no corr_id at all
    log.emit("work", "note", {"chatter": 1}, ts=_stamp(3600))
    adv = A.Advisor(log, "work", rules=[A.stalled_work(stall_seconds=900)])
    out = adv.observe(now=T0)
    check(out["emitted"] == [], "FALSE ALARM — the watcher fired on a healthy stream: %r"
          % [repr(f) for f in out["emitted"]])
    check(out["examined"] == 4, "examined miscounted: %r" % out["examined"])
    print("A/B control arm: completed, young, and corr_id-less work are all SILENT — PASS")


def test_progress_is_not_a_stall():
    log = _log()
    log.emit("work", "request", {}, corr_id="slow", ts=_stamp(3600))   # started long ago...
    log.emit("work", "result", {"unit": "1"}, corr_id="slow", ts=_stamp(60))  # ...but still moving
    adv = A.Advisor(log, "work", rules=[A.stalled_work(stall_seconds=900)])
    check(adv.observe(now=T0)["emitted"] == [],
          "a run still emitting results was called stalled — that trains people to ignore the watcher")
    print("C. progress is not a stall: quiet measured from the LAST event — PASS")


def test_idempotent_across_restart():
    log = _log()
    log.emit("work", "request", {}, corr_id="old", ts=_stamp(3600))
    first = A.Advisor(log, "work", rules=[A.stalled_work(stall_seconds=900)]).observe(now=T0)
    check(len(first["emitted"]) == 1, "setup: first pass should emit once")
    same = A.Advisor(log, "work", rules=[A.stalled_work(stall_seconds=900)]).observe(now=T0)
    check(same["emitted"] == [], "re-announced a standing problem — a spammy watcher gets muted")
    check(len(same["findings"]) == 1, "the finding must still be REPORTED, just not re-emitted: %r" % same)
    check(len(A.advice(log, "work")) == 1, "duplicate advice events on the log: %r" % A.advice(log, "work"))
    print("D. idempotent across a fresh Advisor — dedup from the LOG, not memory — PASS")


def test_broken_rule_is_loud():
    log = _log()
    log.emit("work", "note", {}, ts=_stamp(1))

    def exploding_rule(stream):
        raise RuntimeError("rule is broken")

    out = A.Advisor(log, "work", rules=[exploding_rule, A.stalled_work()]).observe(now=T0)
    check(out["rules_failed"] == ["exploding_rule"], "broken rule not reported: %r" % out)
    blockers = [f for f in out["emitted"] if f.rule == "rule-failure"]
    check(len(blockers) == 1 and blockers[0].level == "blocker",
          "a broken rule must be a LOUD blocker, not a silent skip: %r" % out["emitted"])
    check("proves nothing" in blockers[0].detail,
          "the finding must say the rule's silence is worthless: %r" % blockers[0].detail)
    print("E. a raising rule becomes a loud blocker, never a silently-skipped rule — PASS")


def test_examined_distinguishes_empty_from_healthy():
    empty = A.Advisor(_log(), "work").observe(now=T0)
    check(empty["findings"] == [] and empty["examined"] == 0,
          "an empty topic must report examined=0: %r" % empty)
    log = _log()
    for i in range(3):
        log.emit("work", "note", {"i": i}, ts=_stamp(1))
    healthy = A.Advisor(log, "work").observe(now=T0)
    check(healthy["findings"] == [] and healthy["examined"] == 3,
          "a healthy topic must report what it examined: %r" % healthy)
    # The two states produce an IDENTICAL finding list — `examined` is the only thing separating
    # "all clear" from "pointed at nothing", which is the whole reason it is in the return value.
    check(empty["findings"] == healthy["findings"], "premise of this test no longer holds")
    print("F. examined=%d vs %d separates 'nothing wrong' from 'looked at nothing' — PASS"
          % (empty["examined"], healthy["examined"]))


def test_rules_cannot_mutate_the_stream():
    seen = {}

    def peeking_rule(stream):
        seen["has_emit"] = hasattr(stream, "emit")
        seen["has_log"] = hasattr(stream, "log")
        seen["has_cursor"] = hasattr(stream, "cursor_set")
        try:
            stream.sneaky = 1          # __slots__ must refuse this
            seen["mutable"] = True
        except AttributeError:
            seen["mutable"] = False
        return []

    A.Advisor(_log(), "work", rules=[peeking_rule]).observe(now=T0)
    check(not seen["has_emit"] and not seen["has_log"] and not seen["has_cursor"],
          "a rule can reach the log — a watcher's rules must not change what they watch: %r" % seen)
    check(seen["mutable"] is False, "the stream view is mutable: %r" % seen)
    print("G. rules see a read-only view — no emit, no log, no cursor, no new attrs — PASS")


def test_advice_is_separate_and_does_not_self_observe():
    log = _log()
    log.emit("work", "request", {}, corr_id="old", ts=_stamp(3600))
    adv = A.Advisor(log, "work", rules=[A.stalled_work(stall_seconds=900)])
    adv.observe(now=T0)
    check([e for e in log.read("work") if e["kind"] == "note"] == [],
          "advice leaked into the watched topic — the watcher would observe itself")
    check(len(log.read("work-advice")) == 1, "advice not on the -advice topic")
    # A second pass must not advise about the advice it just wrote.
    again = adv.observe(now=T0)
    check(again["examined"] == 1, "the watcher started reading its own advice: examined=%d"
          % again["examined"])
    print("H. advice lands on a separate topic; the watcher never observes itself — PASS")


def test_error_rate_both_ways():
    log = _log()
    for i in range(3):
        log.emit("work", "error", {"unit": i}, corr_id="bad", ts=_stamp(1))
    out = A.Advisor(log, "work", rules=[A.error_rate(threshold=3)]).observe(now=T0)
    check(len(out["emitted"]) == 1 and out["emitted"][0].level == "concern",
          "error-rate did not fire at threshold: %r" % out)
    # Quiet arm: two errors under a threshold of three.
    log2 = _log()
    for i in range(2):
        log2.emit("work", "error", {"unit": i}, corr_id="bad", ts=_stamp(1))
    check(A.Advisor(log2, "work", rules=[A.error_rate(threshold=3)]).observe(now=T0)["emitted"] == [],
          "error-rate fired under its own threshold")
    # A bogus level is a programming error and must raise, not become mystery advice.
    try:
        A.Finding("catastrophe", "r", "d")
    except ValueError:
        pass
    else:
        raise AssertionError("an invalid level was accepted")
    print("I. error-rate fires over threshold, quiet under it; bad levels raise — PASS")


def main():
    test_stall_fires()
    test_stall_quiet_arm()
    test_progress_is_not_a_stall()
    test_idempotent_across_restart()
    test_broken_rule_is_loud()
    test_examined_distinguishes_empty_from_healthy()
    test_rules_cannot_mutate_the_stream()
    test_advice_is_separate_and_does_not_self_observe()
    test_error_rate_both_ways()
    print("\nagos-advisor contract battery: ALL PASS")


if __name__ == "__main__":
    main()
