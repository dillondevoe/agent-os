#!/usr/bin/env python3
# modules/agos_observe.py — self-improvement loop, phase 1 of 4: OBSERVE (+ the COMPARE
# recurrence test). Instantiation B (Agent OS) of HARNESS-SELFIMPROVE. Stdlib only.
#
# SCOPE — DELIBERATELY PARTIAL, AND THE LIMIT IS THE POINT.
# The full loop is OBSERVE → COMPARE → PROPOSE → APPLY. This file implements the first two
# and STOPS. There is no PROPOSE here and no APPLY, because APPLY means the loop writes
# changes to its own harness, and whether it may ever auto-merge is an open question with
# Dillon (asked 2026-08-19, unanswered at time of writing). Building the read-only half
# while that is open is safe; building the half that acts is not. Do not add an apply path
# to this module — when the answer lands, that is a NEW module with its own gate and its
# own battery, so the read side can never quietly grow the ability to act.
#
# ═══ THE LOAD-BEARING RULE ═══
#   RE-OBSERVATION MUST NOT MANUFACTURE RECURRENCE.
#
# This is the failure that would matter most, and it is not obvious. OBSERVE runs on a
# CADENCE over a log that only grows, so consecutive runs re-read overlapping history. If
# occurrences were counted naively, a SINGLE failure observed three times would cross the
# "seen ≥2 = recurring" threshold and be promoted to a LESSON — and the loop would then
# propose a harness change to fix a problem that happened once. That is worse than a loop
# that finds nothing: it is a loop that fabricates its own evidence, and every downstream
# phase would treat that evidence as real.
#
# So occurrences are keyed by a CONTENT-DERIVED occurrence id (source, machine, event id)
# with a PRIMARY KEY on it. Re-observing is a no-op at the database level rather than a
# thing the calling code has to remember not to do. Idempotence is structural; the battery
# proves it by running OBSERVE three times over the same log and asserting the counts do
# not move.
#
# Two distinct hashes, and conflating them is the bug this design exists to avoid:
#   occ_key — identifies THIS OCCURRENCE (includes the event id). Dedup key.
#   sig_id  — identifies the PATTERN that recurs (excludes the event id, normalises
#             volatile detail). Grouping key.
# Recurrence = COUNT(DISTINCT occ_key) per sig_id. Same failure twice = 2. Same event read
# twice = 1.
#
# ═══ SOURCES ARE READ-ONLY, AND HETEROGENEOUS ═══
# Reads `agos_events` topics and the provenance turn-log. Never writes either.
# Per Augur (2026-08-19), the turn-log at AGENT_OS_TURN_LOG (default ~/memory/turn-log.jsonl)
# is newline-delimited JSON that MULTIPLE writers append to: #114's cost-cap breaker emits
# {"event":"cost_cap_breaker","kind":"token"|"hop","hops":int,"output_tokens":int}, and other
# writers may append OTHER event types. Consumers MUST filter on `event` and must not assume
# the file is homogeneous — so every read here is defensive about shape, and a line that does
# not parse is COUNTED and reported rather than skipped in silence. A parser that quietly
# drops what it does not understand reports a clean world it never actually looked at.

import hashlib
import json
import os
import sqlite3

# A signal seen this many distinct times stops being noise and becomes a LESSON.
# HARNESS-SELFIMPROVE: "same mistake 2+ times = signal; once = noise."
RECURRENCE_THRESHOLD = 2

CANDIDATE = "CANDIDATE"
LESSON = "LESSON"

# Signal types extracted here. PROPOSE (not in this module) is what would act on them.
TOOL_FAILURE = "TOOL_FAILURE"
COST_SPIKE = "COST_SPIKE"
STALLED_WORK = "STALLED_WORK"

_DDL = """
CREATE TABLE IF NOT EXISTS occurrences (
    occ_key   TEXT PRIMARY KEY,
    sig_id    TEXT NOT NULL,
    ts        TEXT,
    source    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS occ_sig ON occurrences (sig_id);
CREATE TABLE IF NOT EXISTS signals (
    sig_id    TEXT PRIMARY KEY,
    type      TEXT NOT NULL,
    detail    TEXT NOT NULL,
    status    TEXT NOT NULL
);
"""


class Signal:
    """One extracted observation. Not a lesson yet — that is COMPARE's verdict."""

    __slots__ = ("type", "detail", "occ_key", "ts", "source")

    def __init__(self, type, detail, occ_key, ts=None, source="unknown"):
        self.type = type
        self.detail = detail
        self.occ_key = occ_key
        self.ts = ts
        self.source = source

    @property
    def sig_id(self):
        """The PATTERN id — what recurs. Excludes the occurrence, by construction.

        Note what is NOT hashed: occ_key, ts. Two separate failures of the same kind must
        land on the same sig_id or recurrence can never be detected; the same event read
        twice must land on the same occ_key or recurrence is fabricated. The two ids answer
        different questions and deriving one from the other would break one of them.
        """
        return hashlib.sha256(
            ("%s\x00%s" % (self.type, self.detail)).encode("utf-8")
        ).hexdigest()[:16]

    def __repr__(self):
        return "Signal(%s, %r, occ=%s)" % (self.type, self.detail, self.occ_key)


def _occ_key(source, machine, event_id, extra=""):
    """Stable id for a single observed occurrence.

    Derived from WHERE it was seen, never from when it was READ. A key that included the
    read time would make every cadence run a new occurrence — which is precisely the
    fabricated-recurrence bug, arrived at from the other direction.
    """
    raw = "%s\x00%s\x00%s\x00%s" % (source, machine, event_id, extra)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


# ── extractors ────────────────────────────────────────────────────────────────────
def signals_from_events(log, topic, stall_seconds=900, now=None):
    """Extract signals from an agos_events topic. READ ONLY.

    Two signal types: an `error` event is a TOOL_FAILURE; a corr_id with a `request` and no
    terminal `done` is STALLED_WORK. The stall rule is deliberately the same shape as the
    advisor's — a watcher that cannot see silence is not a watcher — but here it feeds
    recurrence rather than an immediate finding.
    """
    events = list(log.read(topic))
    out = []
    seen_ts = []
    by_corr = {}
    for ev in events:
        machine = ev.get("_machine", "?")
        ts = ev.get("ts")
        if ts:
            seen_ts.append(ts)
        if ev.get("kind") == "error":
            payload = ev.get("payload") or {}
            detail = payload.get("reason") or payload.get("error") or "unspecified error"
            out.append(Signal(
                TOOL_FAILURE,
                "%s: %s" % (topic, detail),
                _occ_key("events:%s" % topic, machine, ev.get("id")),
                ts=ts,
                source="events:%s" % topic,
            ))
        corr = ev.get("corr_id")
        if corr:
            by_corr.setdefault(corr, []).append(ev)

    for corr, evs in by_corr.items():
        if any(e.get("kind") == "done" for e in evs):
            continue
        if not any(e.get("kind") == "request" for e in evs):
            continue
        first = evs[0]
        out.append(Signal(
            STALLED_WORK,
            "%s: work never completed" % topic,
            _occ_key("events:%s" % topic, first.get("_machine", "?"), corr, extra="stall"),
            ts=first.get("ts"),
            source="events:%s" % topic,
        ))
    return out


def signals_from_turnlog(path=None):
    """Extract COST_SPIKE signals from the provenance turn-log. READ ONLY.

    Returns (signals, malformed_count). The count is RETURNED, not swallowed: a turn-log
    full of lines this parser cannot read would otherwise present as a perfectly healthy
    system with no cost spikes. Callers should surface it.
    """
    path = path or os.environ.get(
        "AGENT_OS_TURN_LOG", os.path.expanduser("~/memory/turn-log.jsonl")
    )
    out, malformed = [], 0
    if not os.path.exists(path):
        return out, malformed
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                malformed += 1
                continue
            if not isinstance(rec, dict):
                malformed += 1
                continue
            # Filter on `event` — the file is multi-writer and heterogeneous by contract.
            if rec.get("event") != "cost_cap_breaker":
                continue
            kind = rec.get("kind", "?")
            out.append(Signal(
                COST_SPIKE,
                "cost cap tripped (%s)" % kind,
                _occ_key("turnlog", "local", lineno, extra=str(rec.get("hops", ""))),
                ts=rec.get("ts"),
                source="turnlog",
            ))
    return out, malformed


# ── the store + COMPARE ───────────────────────────────────────────────────────────
class LessonStore:
    """Recurrence bookkeeping. The PRIMARY KEY on occ_key is what makes OBSERVE idempotent."""

    __slots__ = ("path", "_db")

    def __init__(self, path):
        self.path = path
        parent = os.path.dirname(os.path.abspath(path))
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.executescript(_DDL)
        self._db.commit()

    def close(self):
        self._db.close()

    def record(self, signal):
        """Record one occurrence. Returns True if NEW, False if already seen.

        The INSERT OR IGNORE is the load-bearing line in this file. Re-observing the same
        event is a database no-op, so idempotence does not depend on any caller remembering
        to track a cursor.
        """
        cur = self._db.execute(
            "INSERT OR IGNORE INTO occurrences (occ_key, sig_id, ts, source) VALUES (?,?,?,?)",
            (signal.occ_key, signal.sig_id, signal.ts, signal.source),
        )
        is_new = cur.rowcount > 0
        self._db.execute(
            "INSERT OR IGNORE INTO signals (sig_id, type, detail, status) VALUES (?,?,?,?)",
            (signal.sig_id, signal.type, signal.detail, CANDIDATE),
        )
        self._db.commit()
        return is_new

    def occurrences(self, sig_id):
        cur = self._db.execute(
            "SELECT COUNT(*) FROM occurrences WHERE sig_id = ?", (sig_id,)
        )
        return cur.fetchone()[0]

    def compare(self, threshold=RECURRENCE_THRESHOLD):
        """COMPARE: promote signals that have RECURRED to LESSON. Returns promoted sig_ids.

        Promotion is computed from DISTINCT occurrence rows, so it inherits the idempotence
        guarantee rather than restating it.
        """
        promoted = []
        rows = self._db.execute(
            "SELECT s.sig_id, COUNT(o.occ_key) FROM signals s "
            "JOIN occurrences o ON o.sig_id = s.sig_id "
            "WHERE s.status = ? GROUP BY s.sig_id",
            (CANDIDATE,),
        ).fetchall()
        for sig_id, n in rows:
            if n >= threshold:
                self._db.execute(
                    "UPDATE signals SET status = ? WHERE sig_id = ?", (LESSON, sig_id)
                )
                promoted.append(sig_id)
        self._db.commit()
        return promoted

    def lessons(self):
        return [
            {"sig_id": r[0], "type": r[1], "detail": r[2], "occurrences": r[3]}
            for r in self._db.execute(
                "SELECT s.sig_id, s.type, s.detail, COUNT(o.occ_key) FROM signals s "
                "JOIN occurrences o ON o.sig_id = s.sig_id "
                "WHERE s.status = ? GROUP BY s.sig_id ORDER BY s.sig_id",
                (LESSON,),
            )
        ]

    def candidates(self):
        return [
            {"sig_id": r[0], "type": r[1], "detail": r[2], "occurrences": r[3]}
            for r in self._db.execute(
                "SELECT s.sig_id, s.type, s.detail, COUNT(o.occ_key) FROM signals s "
                "JOIN occurrences o ON o.sig_id = s.sig_id "
                "WHERE s.status = ? GROUP BY s.sig_id ORDER BY s.sig_id",
                (CANDIDATE,),
            )
        ]


def observe(store, signals, malformed=0):
    """Record a batch, then run COMPARE. Returns a report.

    `examined` is reported separately from `new` for the reason slice 4 hammered: zero
    findings from zero inputs and zero findings from a healthy system are the same empty
    result, and only one of them means anything. `malformed` rides along so a caller can
    tell "no cost spikes" from "I could not read the file".
    """
    new = sum(1 for s in signals if store.record(s))
    promoted = store.compare()
    return {
        "examined": len(signals),
        "new": new,
        "already_seen": len(signals) - new,
        "promoted": promoted,
        "lessons": len(store.lessons()),
        "malformed_lines": malformed,
    }
