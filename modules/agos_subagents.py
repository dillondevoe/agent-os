#!/usr/bin/env python3
# modules/agos_subagents.py — Agent OS orchestration engine: SUBAGENT FAN-OUT WITH TYPED YIELDS.
# HARNESS-MAP slice 3 (K9 workers). Layered on agos_events.py. ZERO external deps (stdlib only).
#
# WHY THIS EXISTS (HARNESS-MAP §05, build order 3; Mirror's claim 2026-08-19):
# `agos_events` gave us a multi-writer log with exactly-once consumers. What it does NOT give us is
# a first-class way to say "do these N things concurrently and hand me back results I can TRUST".
# Today that shape is hand-rolled per call site, which means every call site re-invents fan-out AND
# re-invents result parsing — and parsing an untyped yield is exactly where a worker's malformed
# output gets read as a plausible answer. This module makes the fan-out one primitive and the
# result contract explicit.
#
# THE LOAD-BEARING IDEA — a yield is not a result until it TYPE-CHECKS.
# A worker returning the wrong shape is an `error` event, never a silent skip and never a
# half-trusted dict passed downstream. Fail-LOUD, genesis-lock parity: the run tells you which
# units failed and why, and `ok` is False. A caller that ignores the failure has to do so on
# purpose. This is the instrument-error lesson from docs/cancelled-boundaries.md pointed at
# orchestration: an unvalidated yield is an instrument reporting whatever it happened to produce.
#
# EVENT SHAPE (all on one caller-chosen topic, all threaded by ONE corr_id per run):
#   kind=request  payload={"unit": <unit id>, "input": <the unit's input>}
#   kind=result   payload={"unit": <unit id>, "yield": <VALIDATED output>}
#   kind=error    payload={"unit": <unit id>, "reason": "<invalid-yield|raised|timeout>",
#                          "detail": "<message>"}
#   kind=done     payload={"units": n, "results": n, "errors": n, "ok": bool}
#
# INHERITED RULINGS (from agos_events — this module does not relax them):
#  · Geist #1 multi-writer: we only ever emit through an EventLog, so per-(topic,machine) files and
#    the local-only flock still hold. A fan-out is single-machine; the LOG is what crosses machines.
#  · Geist PIN #1 defer: a worker may raise agos_events.Defer. A deferred unit emits NEITHER result
#    NOR error, and — the part that matters — **the run emits NO `done`**. Redelivery on the next
#    wake IS the contract, and a `done` would lie about the run being finished.
#
# TWO HONEST LIMITATIONS, stated rather than hidden:
#  1. `timeout` is a WALL-CLOCK BUDGET FOR THE WHOLE RUN, measured from submission — not a per-unit
#     stopwatch. With a bounded pool a unit's own clock does not start until a slot frees, so a
#     "per-unit timeout" would mean something different for the first unit than the last. A run
#     budget is the thing that is actually well-defined here, so that is what the knob is.
#  2. A timeout ABANDONS the worker thread, it does not kill it (Python cannot kill a thread). The
#     unit is reported as a timeout error and the run moves on; a runaway worker keeps burning
#     until it returns. Workers doing unbounded work must carry their own internal deadline.
#     Named here so nobody reads "timeout" as "stopped". A consequence worth knowing: an
#     abandoned worker is a live non-daemon thread, so it can still delay INTERPRETER EXIT even
#     though fan_out() itself returned on budget. The budget bounds the call, not the process.

import concurrent.futures
import time
import uuid

import agos_events

Defer = agos_events.Defer

# ---- the type system ------------------------------------------------------
# Deliberately tiny: a schema is a dict {field: spec}. A spec is a type name, optionally suffixed
# with "?" to mark the field OPTIONAL. Element typing for lists is one level: "list[str]".
# Extra fields are REJECTED by default — a worker that returns more than it promised has drifted
# from its contract, and silently accepting drift is how an unvalidated yield gets back in.

_SCALARS = {
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "list": list,
    "dict": dict,
}


def _check_type(value, spec):
    """Return None if `value` matches `spec`, else a human-readable reason string."""
    if spec == "any":
        return None
    if spec.startswith("list[") and spec.endswith("]"):
        if not isinstance(value, list):
            return "expected list, got %s" % type(value).__name__
        inner = spec[5:-1]
        for i, item in enumerate(value):
            reason = _check_type(item, inner)
            if reason is not None:
                return "element %d: %s" % (i, reason)
        return None
    want = _SCALARS.get(spec)
    if want is None:
        raise ValueError("unknown type %r in schema (known: %s, any, list[<type>])"
                         % (spec, ", ".join(sorted(_SCALARS))))
    # bool is a subclass of int in Python — an int field must not silently accept True.
    if want is int and isinstance(value, bool):
        return "expected int, got bool"
    if not isinstance(value, want):
        return "expected %s, got %s" % (spec, type(value).__name__)
    return None


def _check_spec(spec):
    """Raise ValueError unless `spec` names a type this module knows. Checked against the SCHEMA
    itself, independent of any data — an unknown type buried in a branch the data never reaches is
    still a broken contract, and the battery caught exactly that: `list[nope]` slipped through
    whenever the value under test was not a list."""
    if not isinstance(spec, str):
        raise ValueError("type spec must be a string, got %s" % type(spec).__name__)
    if spec == "any":
        return
    if spec.startswith("list[") and spec.endswith("]"):
        _check_spec(spec[5:-1])
        return
    if spec not in _SCALARS:
        raise ValueError("unknown type %r in schema (known: %s, any, list[<type>])"
                         % (spec, ", ".join(sorted(_SCALARS))))


def validate(yielded, schema):
    """Validate a worker's yield against `schema`. Returns a list of reason strings — EMPTY means
    valid. Never raises for bad DATA (that is the caller's error event); raises only for a bad
    SCHEMA, which is a programming error in the fan-out itself and must not be swallowed."""
    if schema is None:
        return []
    if not isinstance(schema, dict):
        raise ValueError("schema must be a dict of {field: type-spec}, got %s" % type(schema).__name__)
    # Check every spec BEFORE looking at the data, so a broken schema cannot hide behind a value
    # that happens not to exercise it.
    for spec in schema.values():
        _check_spec(spec[:-1] if isinstance(spec, str) and spec.endswith("?") else spec)
    if not isinstance(yielded, dict):
        return ["yield must be a dict of fields, got %s" % type(yielded).__name__]
    reasons = []
    allowed = set()
    for field, spec in schema.items():
        optional = spec.endswith("?")
        base = spec[:-1] if optional else spec
        allowed.add(field)
        if field not in yielded:
            if not optional:
                reasons.append("missing required field %r (%s)" % (field, base))
            continue
        reason = _check_type(yielded[field], base)
        if reason is not None:
            reasons.append("field %r: %s" % (field, reason))
    for field in sorted(yielded):
        if field not in allowed:
            reasons.append("unexpected field %r (not in schema)" % (field,))
    return reasons


# ---- the run result -------------------------------------------------------
class FanOut(object):
    """The outcome of one fan-out run. Partial success is ADDRESSABLE: `results` is keyed by unit id
    and `errors` tells you which units failed and why, so a caller never has to infer either from a
    count. `ok` is True only when every unit yielded a valid result and nothing deferred."""

    __slots__ = ("corr_id", "topic", "results", "errors", "deferred", "units")

    def __init__(self, corr_id, topic, results, errors, deferred, units):
        self.corr_id = corr_id
        self.topic = topic
        self.results = results      # {unit_id: validated yield}
        self.errors = errors        # {unit_id: {"reason": str, "detail": str}}
        self.deferred = deferred    # [unit_id, ...] — redeliver next wake, run is NOT done
        self.units = units          # [unit_id, ...] in submission order

    @property
    def ok(self):
        return not self.errors and not self.deferred and len(self.results) == len(self.units)

    @property
    def complete(self):
        """True when no unit deferred — i.e. the run reached a terminal state and emitted `done`.
        A complete run can still have errors; `ok` is the stronger claim."""
        return not self.deferred

    def __repr__(self):
        return ("<FanOut %s units=%d results=%d errors=%d deferred=%d ok=%s>"
                % (self.corr_id, len(self.units), len(self.results), len(self.errors),
                   len(self.deferred), self.ok))


def _unit_id(unit, index):
    """A unit is either (id, input) or a bare input (then the id is its position)."""
    if isinstance(unit, tuple) and len(unit) == 2:
        return str(unit[0]), unit[1]
    return "u%d" % index, unit


def fan_out(log, topic, units, worker, schema=None, concurrency=8, timeout=None,
            corr_id=None, actor=None, to=None):
    """Run `worker(input)` over every unit concurrently, validate each yield against `schema`, and
    record the whole run on `log` under ONE corr_id.

      log         — an agos_events.EventLog (the events cross machines; the fan-out is local).
      topic       — event topic for this run's request/result/error/done events.
      units       — iterable of `input`, or of `(unit_id, input)` pairs for stable naming.
      worker      — callable taking one input, returning the yield (a dict, per `schema`).
                    May raise agos_events.Defer to defer the unit (no result, no error, no done).
      schema      — {field: type-spec} or None to skip validation. None is UNTYPED and is exactly
                    what this module exists to discourage; it stays available for a worker whose
                    output genuinely has no fixed shape, and that should be a deliberate choice.
      concurrency — max workers in flight (>=1). The cap is explicit, never implicit.
      timeout     — wall-clock seconds for the WHOLE run from submission, or None for no limit.
                    Units still unfinished at the deadline become timeout errors. See both notes
                    at the top of this file: it is a run budget, and it abandons rather than kills.

    Returns a FanOut. Emits `done` ONLY if nothing deferred (Geist PIN #1)."""
    if concurrency < 1:
        raise ValueError("concurrency must be >= 1, got %r" % (concurrency,))
    if schema is not None:
        # Fail fast on a malformed schema, BEFORE any work runs — a broken contract must not be
        # discovered one unit at a time, half way through a fan-out.
        validate({}, schema)

    run_id = corr_id if corr_id is not None else "fan-%s" % uuid.uuid4().hex[:12]
    pairs = [_unit_id(u, i) for i, u in enumerate(units)]
    order = [uid for uid, _ in pairs]

    for uid, payload in pairs:
        log.emit(topic, "request", {"unit": uid, "input": payload},
                 corr_id=run_id, actor=actor, to=to)

    results = {}
    errors = {}
    deferred = []

    def record_error(uid, reason, detail):
        errors[uid] = {"reason": reason, "detail": detail}
        log.emit(topic, "error", {"unit": uid, "reason": reason, "detail": detail},
                 corr_id=run_id, actor=actor, to=to)

    if pairs:
        # NOT a `with` block, on purpose. ThreadPoolExecutor.__exit__ calls shutdown(wait=True),
        # which JOINS the very thread a timeout just abandoned — the budget would be honoured in
        # bookkeeping and ignored in wall-clock, which is the worst of both. We shut down without
        # waiting instead. (Caught by the battery's F case, which timed the call and found 5s
        # against a 0.3s budget. A budget you cannot observe is not a budget.)
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=min(concurrency, len(pairs)))
        try:
            futures = {pool.submit(worker, payload): uid for uid, payload in pairs}
            deadline = None if timeout is None else time.monotonic() + timeout
            for future in list(futures):
                uid = futures[future]
                try:
                    remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
                    yielded = future.result(timeout=remaining)
                except concurrent.futures.TimeoutError:
                    future.cancel()   # only helps if it never started; a running unit is abandoned
                    record_error(uid, "timeout",
                                 "run budget of %ss elapsed with this unit unfinished "
                                 "(thread abandoned, not killed)" % (timeout,))
                    continue
                except Defer:
                    deferred.append(uid)   # no result, no error, and below: no done
                    continue
                except Exception as exc:   # noqa: BLE001 — a worker may raise anything; all of it
                    record_error(uid, "raised", "%s: %s" % (type(exc).__name__, exc))
                    continue
                reasons = validate(yielded, schema)
                if reasons:
                    record_error(uid, "invalid-yield", "; ".join(reasons))
                    continue
                results[uid] = yielded
                log.emit(topic, "result", {"unit": uid, "yield": yielded},
                         corr_id=run_id, actor=actor, to=to)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    out = FanOut(run_id, topic, results, errors, deferred, order)
    if not deferred:
        log.done(run_id, topic,
                 {"units": len(order), "results": len(results), "errors": len(errors), "ok": out.ok},
                 actor=actor)
    return out


def gather(log, topic, corr_id):
    """Reconstruct a run's outcome from the LOG alone — the replay path. A caller on another machine
    (or after a restart) can recover what happened without the in-memory FanOut, because the events
    are the truth and the object was only ever a convenience."""
    results = {}
    errors = {}
    requested = []
    finished = False
    for ev in log.read(topic):
        if ev.get("corr_id") != corr_id:
            continue
        kind, payload = ev.get("kind"), ev.get("payload") or {}
        if kind == "request":
            requested.append(payload.get("unit"))
        elif kind == "result":
            results[payload.get("unit")] = payload.get("yield")
        elif kind == "error":
            errors[payload.get("unit")] = {"reason": payload.get("reason"),
                                           "detail": payload.get("detail")}
        elif kind == "done":
            finished = True
    settled = set(results) | set(errors)
    deferred = [] if finished else [u for u in requested if u not in settled]
    return FanOut(corr_id, topic, results, errors, deferred, requested)
