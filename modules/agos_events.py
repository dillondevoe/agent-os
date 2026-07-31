#!/usr/bin/env python3
# modules/agos_events.py — Agent OS orchestration engine, Phase 1: the `agos-events`
# append-only event-log library. Tiny, plaintext, ZERO external deps (stdlib only).
#
# WHY THIS EXISTS (agentos-orchestration-BUILD-PLAN + design-v1, Dillon-blessed 9006/9008):
# formalize the mesh we already run — brain-comms + the Watchman's wake-flags are a hand-rolled
# blackboard. This library is the one primitive surface everything speaks: an append-only,
# per-topic event log with idempotent (cursor'd) consumers and first-class completion events.
# It kills the two scars our file-routing has hit: the mtime-refire (a comm processed twice)
# and the missed/late pickup (a comm never processed).
#
# ⚖️ GEIST SPINE RULINGS — FOLDED IN (authoritative; the load-bearing one is #1):
#  1. LOAD-BEARING — the log is MULTI-WRITER across SyncThing (Air+mini+DVo). One locked file per
#     topic = a guaranteed conflict-file scar the first multi-machine day (same class as every
#     synced-mutable-file burn). → PER-(topic, writer-machine) FILES: events/<topic>.<machine>.jsonl
#     (comms.air.jsonl, comms.mini.jsonl). Each file is SINGLE-WRITER — the lock is only ever LOCAL
#     (flock), SyncThing never merges concurrent appends, `id` is monotonic PER FILE. read(topic)
#     MERGES all machine-files (order by ts, ties by machine name — deterministic). A cursor is per
#     (consumer, topic, machine-file).
#  2. `to: [<consumer>...]` (optional top-level) — the scheduler routes on (topic, to), NEVER by
#     parsing payloads (payload-field matching = the grep slippery slope). cc = more entries.
#  3. `v: 1` schema version (append-only logs are forever; cheap now, migration-saver later).
#  4. Priority is OUT of the schema — `payload.priority` is a named convention (a scheduler MAY read
#     it for wake-ordering); the schema stays minimal.
#  5. PIN #1 — defer(): a deferred event must NOT emit `done` and must NOT advance the cursor past
#     it (redelivery on next wake IS the contract). Geist's Dillon-interactive mutex depends on it.
#  6. PIN #2 — the human-readable comm SURVIVES: the event is the ROUTING/coordination truth; the
#     markdown comm stays the CONTENT artifact. The payload carries its path (+ hash for free
#     tamper-evidence). This library is transport-agnostic about payloads; the shadow-migration
#     puts {"path":..., "hash":...} in the payload — not this file's concern.
#
# ON-DISK SCHEMA (the one contract): a single JSON object per line, keys sorted, compact:
#   {"v":1,"id":<int monotonic per file>,"ts":"<iso8601 UTC>","topic":"<name>","kind":"<KIND>",
#    "actor":"<who emitted>","to":["<consumer>",...],"corr_id":"<work-unit id or null>","payload":{...}}
# Read-time annotations use a leading underscore (e.g. `_machine`) and are NOT persisted.

import datetime
import fcntl
import glob
import json
import os
import re
import socket
import tempfile
import time

SCHEMA_VERSION = 1

# Canonical event kinds (ruling: kind set is fixed & minimal). `done` is first-class completion.
KINDS = frozenset({"request", "result", "done", "note", "error"})

# topic + machine name grammar. A topic must contain NO dot (dots separate <topic>.<machine> in the
# filename) and no path separators (no traversal). A machine name is lowercase [a-z0-9-], also
# dot-free, so `<topic>.<machine>.jsonl` parses unambiguously by a single rsplit.
_TOPIC_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_MACHINE_RE = re.compile(r"^[a-z0-9-]+$")

_SUFFIX = ".jsonl"


class Defer(Exception):
    """Raised by a consumer handler to DEFER the current event: the cursor is NOT advanced past it,
    no `done` is emitted, and the event (plus everything after it in the same machine-file) redelivers
    on the next consume() — Geist PIN #1. Head-of-line by writer-machine, so ordering is preserved."""


def _now_iso():
    # Microsecond precision so same-wall-second emits still order deterministically within a file.
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _default_machine():
    m = os.environ.get("AGOS_MACHINE") or socket.gethostname()
    m = m.strip().lower()
    # Sanitize to the machine grammar: anything not [a-z0-9-] becomes '-'. Never contains a dot.
    m = re.sub(r"[^a-z0-9-]", "-", m).strip("-") or "unknown"
    return m


def _iter_events(path):
    """Yield each valid event dict from a machine-file, in append (id) order. Skips a trailing
    partial/corrupt line (a crash mid-append) rather than raising — the log stays readable."""
    try:
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except (ValueError, TypeError):
                    # Unparseable line (partial write from a crash). Skip; the writer's flock+fsync
                    # makes this rare, and _last_id() likewise skips it so the next id stays correct.
                    continue
    except FileNotFoundError:
        return


def _last_id(path):
    """Return the id of the last VALID event in a machine-file (0 if empty/absent). Reads the tail
    backward so emit() is not O(file) — a valid line found from the end wins; a corrupt tail line is
    skipped. Falls back to a forward scan only if the whole tail chunk is unparseable."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            if size == 0:
                return 0
            buf = b""
            pos = size
            step = 4096
            while pos > 0:
                read = min(step, pos)
                pos -= read
                f.seek(pos)
                buf = f.read(read) + buf
                lines = buf.split(b"\n")
                # Keep the first (possibly partial) fragment for the next backward chunk; scan the
                # rest from the end for the last parseable id.
                tail = lines[1:] if pos > 0 else lines
                for line in reversed(tail):
                    s = line.strip()
                    if not s:
                        continue
                    try:
                        return int(json.loads(s)["id"])
                    except (ValueError, TypeError, KeyError):
                        continue
                buf = lines[0]
            return 0
    except FileNotFoundError:
        return 0


def _atomic_write(path, text):
    """Durably replace `path` with `text`: write a temp sibling, fsync it, atomic os.replace."""
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-agos-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class EventLog:
    """An append-only, multi-writer event log rooted at a directory tree:

        <root>/events/<topic>.<machine>.jsonl   — append-only, single-writer-per-file
        <root>/cursors/<consumer>/<topic>.json  — {machine: last_processed_id} per consumer

    One EventLog instance writes ONLY its own machine's files (Geist ruling #1). Construct one per
    machine; point two instances (different `machine`) at the same `root` to simulate the mesh."""

    def __init__(self, root, machine=None):
        self.root = os.path.abspath(root)
        self.events_dir = os.path.join(self.root, "events")
        self.cursors_dir = os.path.join(self.root, "cursors")
        self.machine = machine if machine is not None else _default_machine()
        if not _MACHINE_RE.match(self.machine):
            raise ValueError("invalid machine name %r (must be [a-z0-9-], dot-free)" % (self.machine,))

    # ---- paths -------------------------------------------------------------
    def _file(self, topic, machine):
        return os.path.join(self.events_dir, "%s.%s%s" % (topic, machine, _SUFFIX))

    def _topic_files(self, topic):
        return glob.glob(os.path.join(self.events_dir, "%s.*%s" % (topic, _SUFFIX)))

    @staticmethod
    def _machine_of(path):
        base = os.path.basename(path)[: -len(_SUFFIX)]  # "<topic>.<machine>"
        return base.rsplit(".", 1)[1]

    def _cursor_file(self, consumer, topic):
        if not _TOPIC_RE.match(consumer):
            raise ValueError("invalid consumer name %r" % (consumer,))
        return os.path.join(self.cursors_dir, consumer, topic + ".json")

    @staticmethod
    def _validate(topic, kind):
        if not _TOPIC_RE.match(topic):
            raise ValueError("invalid topic %r (must be [A-Za-z0-9_-], dot-free)" % (topic,))
        if kind not in KINDS:
            raise ValueError("invalid kind %r (must be one of %s)" % (kind, sorted(KINDS)))

    # ---- write -------------------------------------------------------------
    def emit(self, topic, kind, payload=None, corr_id=None, actor=None, to=None):
        """Append one event to THIS machine's file for `topic`; assign a per-file monotonic id under
        a LOCAL exclusive flock; fsync. Returns the event dict (with id/ts filled in)."""
        self._validate(topic, kind)
        path = self._file(topic, self.machine)
        os.makedirs(self.events_dir, exist_ok=True)
        with open(path, "a+") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                eid = _last_id(path) + 1
                ev = {
                    "v": SCHEMA_VERSION,
                    "id": eid,
                    "ts": _now_iso(),
                    "topic": topic,
                    "kind": kind,
                    "actor": actor if actor is not None else self.machine,
                    "to": list(to) if to else [],
                    "corr_id": corr_id,
                    "payload": payload if payload is not None else {},
                }
                f.write(json.dumps(ev, separators=(",", ":"), sort_keys=True) + "\n")
                f.flush()
                os.fsync(f.fileno())
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        return dict(ev, _machine=self.machine)

    def done(self, corr_id, topic, payload=None, actor=None):
        """Emit a first-class completion event (kind='done') threaded by corr_id."""
        return self.emit(topic, "done", payload=payload or {}, corr_id=corr_id, actor=actor)

    # ---- read --------------------------------------------------------------
    def read(self, topic, cursor=None):
        """Merge every machine-file for `topic` and return events with id > cursor[machine], ordered
        deterministically by (ts, writer-machine, id). `cursor` is {machine: last_id} (None = from 0).
        Each returned event carries a read-time `_machine` annotation (its writer-file), NOT persisted."""
        cur = cursor or {}
        out = []
        for path in self._topic_files(topic):
            machine = self._machine_of(path)
            after = int(cur.get(machine, 0))
            for ev in _iter_events(path):
                if int(ev.get("id", 0)) > after:
                    ev["_machine"] = machine
                    out.append(ev)
        out.sort(key=lambda e: (e.get("ts", ""), e["_machine"], int(e.get("id", 0))))
        return out

    def await_done(self, corr_id, topic, timeout=30.0, poll=0.05):
        """Block until a done event for `corr_id` appears on `topic`, or return None at timeout."""
        deadline = time.monotonic() + timeout
        while True:
            for ev in self.read(topic):
                if ev.get("kind") == "done" and ev.get("corr_id") == corr_id:
                    return ev
            if time.monotonic() >= deadline:
                return None
            time.sleep(poll)

    # ---- cursors -----------------------------------------------------------
    def cursor_get(self, consumer, topic):
        """Return this consumer's durable {machine: last_id} position for `topic` ({} if none)."""
        try:
            with open(self._cursor_file(consumer, topic), "r") as f:
                return json.load(f)
        except FileNotFoundError:
            return {}

    def cursor_set(self, consumer, topic, cursor):
        """Durably persist this consumer's {machine: last_id} position for `topic` (atomic + fsync)."""
        _atomic_write(self._cursor_file(consumer, topic), json.dumps(cursor, sort_keys=True))

    # ---- the exactly-once consumer primitive -------------------------------
    def consume(self, consumer, topic, handler, route_to=None):
        """Deliver each not-yet-seen event on `topic` to `handler(event)` EXACTLY ONCE, in
        (ts, machine, id) order, then durably advance this consumer's cursor.

        Routing (Geist ruling #2): an event with a non-empty `to` is delivered only if `route_to`
        is in it (empty `to` = broadcast). Events routed away still advance the cursor (we've seen
        them; they are simply not ours).

        Defer (Geist PIN #1): a handler may raise Defer to leave the event unprocessed — the cursor
        is NOT advanced past it and everything after it IN THE SAME machine-file is held back too
        (head-of-line by writer), so the deferred work redelivers on the next consume(). Returns
        {'processed': n, 'deferred': n}."""
        cursor = self.cursor_get(consumer, topic)
        commit = dict(cursor)          # machine -> last committed id (starts at the saved cursor)
        blocked = set()                # machines with a deferred head-of-line — stop advancing
        processed = 0
        deferred = 0
        for ev in self.read(topic, cursor):
            m = ev["_machine"]
            if m in blocked:
                deferred += 1          # held behind an earlier defer on this writer; redelivers
                continue
            if ev.get("to") and route_to is not None and route_to not in ev["to"]:
                commit[m] = int(ev["id"])   # not ours — seen, advance past it
                continue
            try:
                handler(ev)
            except Defer:
                blocked.add(m)         # leave cursor BEFORE this event; redeliver next wake
                deferred += 1
                continue
            processed += 1
            commit[m] = int(ev["id"])
        self.cursor_set(consumer, topic, commit)
        return {"processed": processed, "deferred": deferred}


# ---- module-level convenience over a default env-configured log ------------
# The plan lists a free-function surface (emit/read/...). These delegate to a lazily-built default
# EventLog from AGOS_EVENTS_DIR (default /var/lib/agos-events) + AGOS_MACHINE (default hostname).
_DEFAULT = None


def default_log():
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = EventLog(os.environ.get("AGOS_EVENTS_DIR", "/var/lib/agos-events"),
                            os.environ.get("AGOS_MACHINE"))
    return _DEFAULT


def emit(topic, kind, payload=None, corr_id=None, actor=None, to=None):
    return default_log().emit(topic, kind, payload=payload, corr_id=corr_id, actor=actor, to=to)


def read(topic, cursor=None):
    return default_log().read(topic, cursor=cursor)


def cursor_get(consumer, topic):
    return default_log().cursor_get(consumer, topic)


def cursor_set(consumer, topic, cursor):
    return default_log().cursor_set(consumer, topic, cursor)


def done(corr_id, topic, payload=None, actor=None):
    return default_log().done(corr_id, topic, payload=payload, actor=actor)


def await_done(corr_id, topic, timeout=30.0, poll=0.05):
    return default_log().await_done(corr_id, topic, timeout=timeout, poll=poll)
