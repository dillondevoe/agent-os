#!/usr/bin/env python3
# modules/agos_cycle.py — self-improvement loop, the CADENCE RUNNER. Stdlib only.
# Instantiation B (Agent OS) of HARNESS-SELFIMPROVE.
#
# ═══ WHY THIS FILE EXISTS ═══
# OBSERVE, COMPARE and PROPOSE all shipped, each with its own green battery, and NOTHING
# CALLED ANY OF THEM. Three libraries with no caller is not a loop; it is three libraries.
# Every guarantee those modules assert was proven in isolation, against inputs their own
# batteries supplied. The seam between them had never executed once.
#
# That is the specific failure this file is built to close, so the battery for it is built
# the corresponding way: it runs the REAL agos_observe against the REAL agos_propose. There
# are no fakes at the seam. A composition test that fakes the composition tests nothing —
# it re-proves the two halves a second time and reports it as a new fact.
#
# ═══ WHAT THIS FILE MAY NOT DO ═══
# It STOPS AT RENDER. It emits text and writes nothing outside its own SQLite stores.
# APPLY — the loop writing changes to its own harness — is gated on an open question with
# Dillon (does APPLY ever auto-merge?), and the read/propose half must never quietly grow
# the ability to act. agos_observe.py's header forbids growing an act path in the read half;
# agos_propose.py honours the same rule one level down; this file honours it one level
# further out, where it is most tempting, because a RUNNER is exactly the place someone
# would reach for "...and then commit it".
#
# Running the cadence is inside every possible answer to that question: "no" makes
# emit-and-stop terminal, "yes under a gate" makes it the step before the gate. So this
# ships now and APPLY stays unbuilt.
#
# ═══ THE LOAD-BEARING RULE ═══
#   AN EMPTY CYCLE MUST SAY WHY IT WAS EMPTY.
#
# The loop's normal output is "nothing to propose". That sentence is produced identically by
# a healthy harness, by a source file that does not exist, and by a parser that choked on
# every line. Collapsing those three is how a self-improvement loop reports perfect health
# while blind — and unlike a crash, it never stops looking fine.
#
# A concrete instance, found while writing this file: signals_from_turnlog() returns
# ([], 0) when its path does not exist — zero signals AND zero malformed lines, which is
# byte-identical to a clean, healthy, present log. That is not a bug in the read half; it
# has no way to know whether absence is expected. The COMPOSER is the layer that knows a
# source was supposed to be there, so availability is resolved and reported HERE, per
# source, as a third state (`missing`) alongside `read` and `unreadable`.
#
# Downstream consequence, stated so nobody has to rediscover it: `proposals == 0` is only
# meaningful when `sources_read > 0`. The report carries `blind` for exactly that reason —
# a caller that prints "no improvements needed" off a blind cycle has asserted a fact it
# does not have.

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import agos_observe as O          # noqa: E402
import agos_propose as P          # noqa: E402

READ = "read"
MISSING = "missing"
UNREADABLE = "unreadable"


class Source:
    """One named input to OBSERVE, plus whether it was actually there.

    `reader` returns (signals, malformed) — the agos_observe convention. `probe` answers
    "was this source available at all", which the reader cannot answer for itself.
    """

    __slots__ = ("name", "reader", "probe")

    def __init__(self, name, reader, probe=None):
        self.name = name
        self.reader = reader
        self.probe = probe

    def available(self):
        return True if self.probe is None else bool(self.probe())


def turnlog_source(path=None):
    path = path or os.environ.get(
        "AGENT_OS_TURN_LOG", os.path.expanduser("~/memory/turn-log.jsonl")
    )
    return Source(
        "turnlog",
        lambda: O.signals_from_turnlog(path),
        lambda: os.path.exists(path),
    )


def collect(sources):
    """Read every source. Returns (signals, per-source status dict).

    A source that raises is recorded UNREADABLE and does not abort the cycle — one bad
    input must not suppress the findings of the others. But it is never silently dropped:
    an unreadable source is as much a reason for an empty cycle as a healthy one, and the
    two are told apart in the report.
    """
    signals, status = [], {}
    for src in sources:
        if not src.available():
            status[src.name] = {"state": MISSING, "signals": 0, "malformed": 0}
            continue
        try:
            got, malformed = src.reader()
        except Exception as exc:                      # noqa: BLE001 — deliberate
            status[src.name] = {
                "state": UNREADABLE, "signals": 0, "malformed": 0,
                "error": "%s: %s" % (type(exc).__name__, exc),
            }
            continue
        signals.extend(got)
        status[src.name] = {
            "state": READ, "signals": len(got), "malformed": malformed,
        }
    return signals, status


def cycle(lesson_store, proposal_store, sources, drafts=None):
    """OBSERVE -> COMPARE -> PROPOSE, once. Writes no files. Returns a report.

    Idempotence across cadence runs is INHERITED, not restated here: OBSERVE dedups on
    occ_key and PROPOSE dedups on content hash, both at the database level. This function
    adds no counter of its own that a second run could double. The battery still asserts it
    end-to-end, because "each half is idempotent" and "the composition is idempotent" are
    different claims and only one of them had ever been checked.
    """
    signals, status = collect(sources)
    malformed = sum(s.get("malformed", 0) for s in status.values())

    observed = O.observe(lesson_store, signals, malformed=malformed)
    lessons = lesson_store.lessons()
    proposed = P.propose(proposal_store, lessons, drafts=drafts)

    read_ok = sum(1 for s in status.values() if s["state"] == READ)
    return {
        "sources": status,
        "sources_read": read_ok,
        "sources_total": len(status),
        "blind": read_ok == 0,
        "observed": observed,
        "proposed": proposed,
    }


def render(report, proposals=()):
    """Human-readable cycle summary. Returns text, writes nothing.

    The blind case is stated FIRST and in place of the reassuring line, not appended after
    it. A warning printed underneath "no improvements needed" is read as a footnote to a
    conclusion that was never earned.
    """
    lines = []
    if report["blind"]:
        lines.append(
            "CANNOT-ASSESS: 0 of %d sources readable — this cycle observed nothing, which "
            "is NOT the same as finding nothing." % report["sources_total"]
        )
    for name in sorted(report["sources"]):
        s = report["sources"][name]
        extra = ""
        if s["state"] == READ:
            extra = " (%d signals, %d malformed lines)" % (s["signals"], s["malformed"])
        elif s["state"] == UNREADABLE:
            extra = " (%s)" % s.get("error", "unknown error")
        lines.append("  source %-10s %s%s" % (name, s["state"], extra))

    o, p = report["observed"], report["proposed"]
    lines.append(
        "OBSERVE: examined=%d new=%d already_seen=%d promoted=%d lessons=%d"
        % (o["examined"], o["new"], o["already_seen"], len(o["promoted"]), o["lessons"])
    )
    # `refused` is a LIST of {sig_id, target, token}, not a count — agos_propose keeps the
    # token that fired on purpose, because a guardrail that fires without saying which rule
    # fired is an unfalsifiable audit trail. So render the COUNT and then the TOKENS; do not
    # flatten it to a number here and lose the only part that is auditable.
    lines.append(
        "PROPOSE: lessons=%d drafted=%d undraftable=%d emitted=%d refused=%d suppressed=%d"
        % (p["lessons"], p["drafted"], p["undraftable"], p["emitted"],
           len(p["refused"]), p["suppressed"])
    )
    for r in p["refused"]:
        lines.append("  REFUSED %s -> %s (deny-path: %s)"
                     % (r["sig_id"], r["target"], r["token"]))
    if proposals:
        lines.append("")
        lines.append(P.render(proposals))
    return "\n".join(lines)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    lesson_db = argv[0] if argv else os.path.expanduser("~/.agent-os/lessons.db")
    prop_db = argv[1] if len(argv) > 1 else os.path.expanduser("~/.agent-os/proposals.db")
    os.makedirs(os.path.dirname(lesson_db), exist_ok=True)
    ls = O.LessonStore(lesson_db)
    ps = P.ProposalStore(prop_db)
    try:
        report = cycle(ls, ps, [turnlog_source()])
        print(render(report, ps.by_status(P.EMITTED)))
    finally:
        ls.close()
        ps.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
