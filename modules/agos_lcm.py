#!/usr/bin/env python3
# modules/agos_lcm.py — Agent OS orchestration engine, HARNESS-MAP slice 5: the LCM
# (lossless context management) compaction layer. Stdlib only, like everything else here.
#
# WHY THIS EXISTS (HARNESS-SELFIMPROVE P2): the brain loop currently pays for history by
# dumping it. A long-running corr_id costs more every turn, and the cost is paid in the
# most expensive currency we have — context. The LCM pattern is: events land in a local
# SQLite store, each span gets a summarization id (sid), the live context carries only the
# sids + one-line summaries, and the agent expands a sid AD HOC when it actually needs the
# detail. Light context, same information, cheaper turns.
#
# ═══ THE LOAD-BEARING RULE ═══
#   A compacted record must be able to reconstruct EXACTLY what it replaced.
#   If it cannot, that is not compaction — it is deletion wearing compaction's name.
#
# Everything below is shaped by that one sentence, and it is enforced two ways:
#
#  (1) STRUCTURALLY. The digest stores the verbatim events in `raw`. The summary is
#      DERIVED and disposable. So losslessness is not a property we have to trust a
#      summariser to preserve — a bad summary makes the compact VIEW less useful, and
#      cannot make the store lossy. This is the whole reason `raw` exists instead of the
#      obvious "just keep the summary, that's the point" design. The point is cheap
#      CONTEXT, not cheap disk. Disk is free; a silently dropped event is not.
#  (2) BY TEST. `expand()` round-trips to byte-equal JSON in the contract battery. A
#      summariser that merely SOUNDS faithful passes no test worth having.
#
# ═══ THE EVENT LOG IS AUTHORITATIVE AND IS NEVER TOUCHED ═══
# This module only ever READS `.jsonl` files. It does not mutate, truncate, rewrite, or
# delete them, and it never will — an append-only log that something else prunes is not
# append-only. The SQLite store is a DERIVED index. If it is deleted, nothing is lost and
# it can be rebuilt from the log; if the log were pruned, the store would be the only copy
# and one corrupted db would be a silent data loss. Derived-only keeps the failure mode
# recoverable, and keeps this module clear of the must-ask "deleting data" line entirely.
#
# ═══ SEAM DELIBERATELY LEFT OPEN ═══
# `summarizer` is pluggable and defaults to a FREE, DETERMINISTIC function (kind histogram
# + first/last actors). Whether a MODEL writes the summary — and whose token budget pays
# for it — is policy, it collides with the cost-cap breaker, and it is not mine to default
# on. Wire it, don't switch it on. Same call as the advisor's judge in slice 4.
#
# ⚠️ CONSTRAINT ON THAT WIRING (Augur, 2026-08-19, re #114): if a MODEL ever writes these
# summaries, route the call through `chat_stream`. The `_out_tokens` accounting the
# cost-cap breaker enforces lives in the transport's return value, so a summariser that
# goes through the transport inherits the turn limits for free — and one that bypasses it
# gets a parallel, UNCAPPED budget, which is precisely the runaway the breaker exists to
# stop. A compaction pass runs over history, i.e. potentially many spans per turn, so this
# is the worst possible place to leak an unmetered provider call.
#
# INGEST SOURCES: this module is source-agnostic (compact() takes events; compact_log()
# takes an EventLog). Per Augur, #114 appends `cost_cap_breaker` events to a SEPARATE
# provenance turn-log (`AGENT_OS_TURN_LOG`, default ~/memory/turn-log.jsonl), append-only.
# That log is a legitimate future ingest source; the raw-column losslessness covers those
# events unchanged. No conflict with agos_events — different file, different writer.

import json
import os
import sqlite3

_SCHEMA_VERSION = 1

# The compact view's per-row shape. Anything not in here is expansion-only, i.e. you must
# pay a lookup to see it. Keep this list SHORT — every field added here is a field every
# turn pays for, forever, which is the exact cost this module exists to cut.
CONTEXT_FIELDS = ("sid", "topic", "corr_id", "n_events", "summary")

_DDL = """
CREATE TABLE IF NOT EXISTS digests (
    sid        TEXT PRIMARY KEY,
    topic      TEXT NOT NULL,
    corr_id    TEXT,
    ts_first   TEXT,
    ts_last    TEXT,
    n_events   INTEGER NOT NULL,
    kinds      TEXT NOT NULL,
    summary    TEXT NOT NULL,
    raw        TEXT NOT NULL,
    v          INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS digests_topic ON digests (topic);
CREATE INDEX IF NOT EXISTS digests_corr  ON digests (corr_id);
"""


class CompactionError(Exception):
    """Raised when a compaction would be lossy, ambiguous, or is simply empty."""


def _canon(events):
    """Canonical JSON for a list of events.

    Read-time annotations (leading underscore, e.g. `_machine`) are NOT part of the event
    as persisted, so they are stripped before storage. Storing them would make expand()
    return something that never existed on disk, which is a subtler way to fail the
    load-bearing rule than dropping a field outright.
    """
    cleaned = []
    for ev in events:
        if not isinstance(ev, dict):
            raise CompactionError("event is not a dict: %r" % (ev,))
        cleaned.append({k: v for k, v in ev.items() if not k.startswith("_")})
    return json.dumps(cleaned, sort_keys=True, separators=(",", ":"))


def default_summarizer(events):
    """Free, deterministic, no model, no tokens, no network.

    Deliberately boring. Its job is to be a usable INDEX line — enough for the agent to
    decide whether expanding this sid is worth a lookup — not to be a good prose summary.
    A model can do better and can be plugged in; that is a policy call (see header).
    """
    if not events:
        return "empty"
    kinds = {}
    for ev in events:
        k = ev.get("kind", "?")
        kinds[k] = kinds.get(k, 0) + 1
    histogram = " ".join("%s=%d" % (k, kinds[k]) for k in sorted(kinds))
    actors = [ev.get("actor") for ev in events if ev.get("actor")]
    who = ""
    if actors:
        who = " by %s" % actors[0] if len(set(actors)) == 1 else " by %d actors" % len(set(actors))
    return "%d events (%s)%s" % (len(events), histogram, who)


class Store:
    """The derived SQLite index. Safe to delete; rebuildable from the log."""

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

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    # -- writes ------------------------------------------------------------------
    def put(self, sid, topic, events, summary, corr_id=None):
        if not events:
            raise CompactionError("refusing to compact zero events into sid %r" % (sid,))
        raw = _canon(events)
        kinds = sorted({ev.get("kind", "?") for ev in events})
        stamps = [ev.get("ts") for ev in events if ev.get("ts")]
        row = (
            sid,
            topic,
            corr_id,
            min(stamps) if stamps else None,
            max(stamps) if stamps else None,
            len(events),
            json.dumps(kinds),
            summary,
            raw,
            _SCHEMA_VERSION,
        )
        try:
            self._db.execute(
                "INSERT INTO digests "
                "(sid,topic,corr_id,ts_first,ts_last,n_events,kinds,summary,raw,v) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                row,
            )
        except sqlite3.IntegrityError:
            # A duplicate sid is not a benign overwrite: the existing row is the only copy
            # of an event span the caller may already have dropped from context. Refuse
            # loudly rather than silently replacing history.
            raise CompactionError("sid already present, refusing to overwrite: %r" % (sid,))
        self._db.commit()
        return sid

    # -- reads -------------------------------------------------------------------
    def expand(self, sid):
        """Return the exact events that were compacted under `sid`.

        This is the other half of the load-bearing rule. A missing sid raises — returning
        [] here would make "I lost your data" indistinguishable from "that span was empty",
        and put() already refuses to store an empty span so [] can never be legitimate.
        """
        cur = self._db.execute("SELECT raw FROM digests WHERE sid = ?", (sid,))
        row = cur.fetchone()
        if row is None:
            raise KeyError("no such sid: %r" % (sid,))
        return json.loads(row[0])

    def get(self, sid):
        cur = self._db.execute(
            "SELECT sid,topic,corr_id,ts_first,ts_last,n_events,kinds,summary FROM digests "
            "WHERE sid = ?",
            (sid,),
        )
        row = cur.fetchone()
        if row is None:
            raise KeyError("no such sid: %r" % (sid,))
        return {
            "sid": row[0],
            "topic": row[1],
            "corr_id": row[2],
            "ts_first": row[3],
            "ts_last": row[4],
            "n_events": row[5],
            "kinds": json.loads(row[6]),
            "summary": row[7],
        }

    def context(self, topic=None, corr_id=None):
        """The LIGHT view — what the live loop actually carries.

        Returns only CONTEXT_FIELDS. `raw` is deliberately absent: if loading the compact
        view also loaded the full events, this module would save exactly nothing.
        """
        q = "SELECT sid,topic,corr_id,n_events,summary FROM digests"
        where, args = [], []
        if topic is not None:
            where.append("topic = ?")
            args.append(topic)
        if corr_id is not None:
            where.append("corr_id = ?")
            args.append(corr_id)
        if where:
            q += " WHERE " + " AND ".join(where)
        q += " ORDER BY ts_first, sid"
        return [dict(zip(CONTEXT_FIELDS, r)) for r in self._db.execute(q, args)]

    def sids(self):
        return [r[0] for r in self._db.execute("SELECT sid FROM digests ORDER BY ts_first, sid")]


def _sid_for(topic, events):
    """A content-addressed sid: same events → same sid, always.

    Content addressing (not a counter, not a timestamp) means compacting the same span
    twice is DETECTABLE — it collides in put() and raises — instead of quietly producing
    two digests of the same history. It also keeps sids stable across machines, which
    matters because this store sits next to a multi-writer synced log.
    """
    import hashlib

    digest = hashlib.sha256(_canon(events).encode("utf-8")).hexdigest()[:16]
    return "%s-%s" % (topic, digest)


def compact(store, topic, events, summarizer=None, corr_id=None, sid=None):
    """Compact `events` into one digest row; return its sid.

    Does NOT read or write the event log — the caller supplies the events. That keeps this
    function pure with respect to the log and makes the "we never touch the .jsonl"
    guarantee checkable by reading this file rather than by trusting it.
    """
    if not events:
        raise CompactionError("refusing to compact an empty span")
    summarizer = summarizer or default_summarizer
    if sid is None:
        sid = _sid_for(topic, events)
    try:
        summary = summarizer(events)
    except Exception as exc:
        # A broken summariser must not take the DATA down with it. The span is still
        # perfectly compactable — we simply lose the nice index line, and we say so out
        # loud rather than storing a plausible-looking blank.
        summary = "summarizer failed (%s: %s) — expand the sid, the events are intact" % (
            type(exc).__name__,
            exc,
        )
    if not isinstance(summary, str):
        raise CompactionError("summarizer returned %s, expected str" % type(summary).__name__)
    return store.put(sid, topic, events, summary, corr_id=corr_id)


def compact_log(store, log, topic, summarizer=None, group_by_corr=True, cursor=None):
    """Read a topic from the event log and compact it. The log is only ever READ.

    With group_by_corr (the default) each corr_id becomes its own digest, which is the
    unit the agent actually reasons about — "what happened to this work item" — and keeps
    an expansion from dragging in unrelated work. Events with no corr_id are grouped
    together under a single digest rather than dropped; a corr_id-less event is still an
    event, and slice 4 already taught me that the un-threaded ones are exactly the ones a
    naive grouping loses.
    """
    events = list(log.read(topic, cursor=cursor))
    if not events:
        return []
    if not group_by_corr:
        return [compact(store, topic, events, summarizer=summarizer)]

    groups, order = {}, []
    for ev in events:
        key = ev.get("corr_id")
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(ev)
    return [
        compact(store, topic, groups[k], summarizer=summarizer, corr_id=k) for k in order
    ]


def verify(store, sid, events):
    """Assert that `sid` expands to exactly `events`. Returns True or raises.

    Exposed as real API, not just test scaffolding, so a caller can CHECK the load-bearing
    rule at runtime instead of assuming it. Cheap paranoia about the one property this
    whole module is selling.
    """
    got = _canon(store.expand(sid))
    want = _canon(events)
    if got != want:
        raise CompactionError("LOSSY COMPACTION for %r — expand() did not round-trip" % (sid,))
    return True
