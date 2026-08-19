#!/usr/bin/env python3
# modules/agos_advisor.py — Agent OS orchestration engine: THE ADVISOR / WATCHER.
# HARNESS-MAP slice 4 (omp §06; K9's conceptual core). Atop agos_events.py. Stdlib only.
#
# WHY THIS EXISTS — a scar, not a wishlist item.
# HARNESS-MAP records the merge-queue stall as a **watcher GAP**: work went quiet and nothing in the
# system was watching for quiet. Every other failure mode we have announces itself — an error event,
# a red check, a raised exception. Silence announces nothing, and a queue with nothing moving looks
# identical to a queue with nothing to do. So the first thing this watcher detects is work that
# STOPPED, and the first thing its battery proves is that it can tell those two apart.
#
# THE TRAP THIS MODULE IS BUILT AGAINST (docs/cancelled-boundaries.md, "the sibling catch"):
# **a watcher that never fires is indistinguishable from a healthy system.** It is the instrument
# error with the stakes inverted — a broken instrument reporting "all clear" forever, and the
# all-clear is exactly what you hoped to see, so nobody looks. Therefore:
#   · every rule is tested BOTH ways — quiet on a healthy stream, firing on a sick one;
#   · `Advisor.observe()` reports how many events it actually examined, so "no findings" can be
#     distinguished from "looked at nothing" by the caller, not just by a human reading logs;
#   · a rule that raises is itself a `blocker` finding, never a silently-skipped rule. A watcher
#     that quietly drops half its rules still returns a confident empty list.
#
# WHAT IT IS NOT. It observes and it says so. It does NOT execute, gate, retry, or cancel anything.
# Advice is an EVENT — a `note` on the log, threaded to what it is about — so it is auditable,
# replayable, and ignorable. A watcher with authority is a scheduler, and that is a different
# component with a different blast radius.
#
# THE MODEL SEAM, FLAGGED NOT QUIETLY DECIDED. omp §06 says "a 2nd MODEL watches every turn".
# Which model, whose budget, and what per-turn cost is POLICY — and it collides directly with the
# cost-cap breaker. So this module is the MECHANISM (observe → judge → emit) with a pluggable judge,
# defaulting to cheap deterministic rules that cost nothing. Wiring a real model in is a caller's
# choice made deliberately, not a default someone inherits by importing this.
#
# ⚠️ CONSTRAINT ON THAT WIRING (Augur, 2026-08-19, re #114): a real judge must be routed
# through `chat_stream`. The `_out_tokens` accounting the cost-cap breaker enforces lives in the
# transport's return value, so a judge that goes through the transport inherits the turn limits
# for free; one that calls a provider directly gets a parallel, UNCAPPED budget. A watcher fires
# on every turn by design, so an unmetered judge here is exactly the runaway the breaker exists
# to stop — the failure would be a watcher that bankrupts the loop it was added to protect.
#
# ADVICE SHAPE — kind=`note`, threaded by the corr_id it is about:
#   payload={"level": "aside"|"concern"|"blocker", "rule": "<rule name>", "detail": "<text>",
#            "about": {"corr_id": ..., "topic": ...}, "key": "<dedup key>"}

import time

import agos_events

# Severity ladder, lowest first. `aside` = worth knowing. `concern` = probably wrong.
# `blocker` = someone is stuck and will stay stuck without intervention.
LEVELS = ("aside", "concern", "blocker")

ADVICE_TOPIC_SUFFIX = "-advice"


class Finding(object):
    """One piece of advice. `key` is the IDEMPOTENCY handle: the same underlying situation must
    produce the same key on every pass, or a watcher on a loop becomes a spam generator and gets
    muted — and a muted watcher is the gap all over again."""

    __slots__ = ("level", "rule", "detail", "corr_id", "key")

    def __init__(self, level, rule, detail, corr_id=None, key=None):
        if level not in LEVELS:
            raise ValueError("invalid level %r (must be one of %s)" % (level, ", ".join(LEVELS)))
        self.level = level
        self.rule = rule
        self.detail = detail
        self.corr_id = corr_id
        self.key = key if key is not None else "%s:%s" % (rule, corr_id)

    def __repr__(self):
        return "<Finding %s %s %s: %s>" % (self.level, self.rule, self.corr_id, self.detail)

    def as_payload(self, topic):
        return {"level": self.level, "rule": self.rule, "detail": self.detail,
                "about": {"corr_id": self.corr_id, "topic": topic}, "key": self.key}


# ---- the view a rule sees -------------------------------------------------
class Stream(object):
    """A read-only digest of one topic, handed to every rule. Rules get this rather than the raw
    log so that a rule cannot emit, mutate, or advance a cursor — a watcher's rules must not be
    able to change the thing they are watching."""

    __slots__ = ("topic", "events", "now", "by_corr")

    def __init__(self, topic, events, now):
        self.topic = topic
        self.events = events
        self.now = now
        self.by_corr = {}
        for ev in events:
            self.by_corr.setdefault(ev.get("corr_id"), []).append(ev)

    def age_of(self, ev):
        """Seconds between `ev`'s recorded ts and this pass's `now`. Negative ages are clamped to 0:
        a clock skew across machines must not read as 'this happened in the future, ignore it'."""
        return max(0.0, self.now - _epoch(ev.get("ts")))


def _epoch(ts):
    """Parse the log's ISO-8601 UTC stamp to epoch seconds. Unparseable → 0.0, which ages the event
    to 'very old'. That biases toward FIRING on a malformed stamp rather than staying quiet, which
    is the correct direction for a watcher: a false alarm gets read, a missed stall does not."""
    if not ts:
        return 0.0
    try:
        cleaned = ts.rstrip("Z")
        head, _, frac = cleaned.partition(".")
        base = time.strptime(head, "%Y-%m-%dT%H:%M:%S")
        import calendar
        return calendar.timegm(base) + (float("0." + frac) if frac else 0.0)
    except (ValueError, TypeError):
        return 0.0


# ---- the built-in deterministic rules -------------------------------------
def stalled_work(stall_seconds=900):
    """THE SCAR RULE. A `request` whose corr_id has no terminal `done` and has been quiet longer
    than `stall_seconds` is a stall. Quiet is measured from the LAST event on that corr_id, not from
    the request — work that is still emitting results is progressing, however slowly, and calling
    that a stall trains people to ignore the watcher."""

    def rule(stream):
        out = []
        for corr_id, events in stream.by_corr.items():
            if corr_id is None:
                continue
            kinds = {e.get("kind") for e in events}
            if "request" not in kinds or "done" in kinds:
                continue
            quiet = min(stream.age_of(e) for e in events)  # age of the most RECENT event
            if quiet > stall_seconds:
                out.append(Finding(
                    "blocker", "stalled-work",
                    "corr_id %s has an open request with no done and has been quiet for %ds "
                    "(threshold %ds)" % (corr_id, int(quiet), stall_seconds),
                    corr_id=corr_id))
        return out

    return rule


def error_rate(threshold=3):
    """A corr_id accumulating errors. Not fatal on its own — the fan-out reports errors by design —
    but a run that is mostly errors is a concern a human should see without reading the log."""

    def rule(stream):
        out = []
        for corr_id, events in stream.by_corr.items():
            if corr_id is None:
                continue
            errors = [e for e in events if e.get("kind") == "error"]
            if len(errors) >= threshold:
                out.append(Finding(
                    "concern", "error-rate",
                    "corr_id %s has %d error events (threshold %d)" % (corr_id, len(errors), threshold),
                    corr_id=corr_id))
        return out

    return rule


DEFAULT_RULES = (stalled_work(), error_rate())


# ---- the watcher ----------------------------------------------------------
class Advisor(object):
    """Watches one topic and emits advice onto `<topic>-advice`.

    Advice goes on a SEPARATE topic on purpose: a watcher writing into the stream it watches can
    observe its own output and advise about its own advising. (`agos_subagents`' battery caught the
    in-process version of that — a pgrep matching its own sampling shell.) Separate topic, no loop."""

    def __init__(self, log, topic, rules=None, advice_topic=None, actor="advisor"):
        self.log = log
        self.topic = topic
        self.rules = list(rules) if rules is not None else list(DEFAULT_RULES)
        self.advice_topic = advice_topic or (topic + ADVICE_TOPIC_SUFFIX)
        self.actor = actor

    def _already_said(self):
        """Dedup keys this advisor has already emitted, recovered from the LOG rather than from
        memory — so a restarted watcher does not re-announce every standing problem."""
        said = set()
        for ev in self.log.read(self.advice_topic):
            key = (ev.get("payload") or {}).get("key")
            if key is not None:
                said.add(key)
        return said

    def observe(self, now=None):
        """Run every rule over the current stream and emit advice for findings not already said.

        Returns {'examined': n, 'findings': [...], 'emitted': [...], 'rules_failed': [...]}.
        **`examined` is not decoration** — it is how a caller tells "nothing is wrong" from "I was
        pointed at an empty topic and learned nothing". Those are the same empty finding list."""
        now = time.time() if now is None else now
        events = self.log.read(self.topic)
        stream = Stream(self.topic, events, now)

        findings = []
        rules_failed = []
        for rule in self.rules:
            name = getattr(rule, "__name__", repr(rule))
            try:
                produced = rule(stream) or []
            except Exception as exc:  # noqa: BLE001 — a broken rule must be LOUD, never skipped
                rules_failed.append(name)
                findings.append(Finding(
                    "blocker", "rule-failure",
                    "watcher rule %s raised %s: %s — this rule observed NOTHING this pass, so its "
                    "silence proves nothing" % (name, type(exc).__name__, exc),
                    key="rule-failure:%s:%s" % (name, type(exc).__name__)))
                continue
            findings.extend(produced)

        said = self._already_said()
        emitted = []
        for finding in findings:
            if finding.key in said:
                continue
            self.log.emit(self.advice_topic, "note", finding.as_payload(self.topic),
                          corr_id=finding.corr_id, actor=self.actor)
            said.add(finding.key)
            emitted.append(finding)

        return {"examined": len(events), "findings": findings, "emitted": emitted,
                "rules_failed": rules_failed}


def advice(log, topic, level=None):
    """Read the advice emitted about `topic`, newest last, optionally filtered to one level."""
    out = []
    for ev in log.read(topic + ADVICE_TOPIC_SUFFIX):
        payload = ev.get("payload") or {}
        if level is not None and payload.get("level") != level:
            continue
        out.append(payload)
    return out
