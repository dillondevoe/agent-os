#!/usr/bin/env python3
# modules/agos_propose.py — self-improvement loop, phase 3 of 4: PROPOSE. Instantiation B
# (Agent OS) of HARNESS-SELFIMPROVE. Stdlib only. Companion to modules/agos_observe.py,
# which implements OBSERVE + COMPARE and deliberately stops short of this file.
#
# ═══ WHY THIS EXISTS AS A SEPARATE MODULE ═══
# agos_observe.py's header says, in as many words: "Do not add an apply path to this
# module — when the answer lands, that is a NEW module with its own gate and its own
# battery, so the read side can never quietly grow the ability to act." This is that new
# module, and it honours the same rule one level down: PROPOSE is separate from APPLY for
# exactly the reason OBSERVE was separate from PROPOSE.
#
# ═══ WHAT IS AND IS NOT GATED ═══
# The open question with Dillon (asked 2026-08-19, still unanswered) is Q1: *does APPLY
# ever auto-merge?* That question governs APPLY. It does NOT govern this file, and the
# distinction is not a loophole — it is the whole reason this file is shippable now:
#
#   Whatever the answer to Q1 turns out to be, a PROPOSE that emits a proposal RECORD and
#   stops is inside it. "No auto-merge" makes emit-and-stop the terminal behaviour; "yes,
#   under a gate" makes emit-and-stop the step before the gate. There is no answer to Q1
#   under which writing a candidate to a table is the wrong thing to have built.
#
# So this module is the strictly-safe SUBSET of every possible ruling, and APPLY — the part
# that is actually gated — does not exist here and must not be added here.
# Augur endorsed this posture and all three answers on 2026-08-19
# (augur-to-mirror-...-RE-capstone-B-is-yours): propose-only v1, deny-path list with a
# CONTROL-ARMED battery, REJECTED as a terminal state keyed on content hash.
#
# ═══ THE LOAD-BEARING RULE ═══
#   A PROPOSAL IS A DOCUMENT, NEVER AN ACTION.
#
# Everything this module produces is text in a table. It opens no PR, runs no command,
# touches no file in the repo, and imports nothing that could. That is asserted
# structurally by the battery (no subprocess/shutil/git/urllib import, no apply entry
# point) rather than promised in a comment, because a comment is not a guarantee and this
# is the seam where a self-improvement loop stops being self-improving and becomes
# unbounded.
#
# ═══ THE SECOND RULE: THE LOOP MAY NOT PROPOSE CHANGES TO ITS OWN GUARDRAILS ═══
# DENY_PATHS below is the blast-radius floor. A loop that can propose edits to the layer
# that decides what it may propose has no floor at all — it has a floor it can file a
# ticket against. So `agos_propose.py` is ITSELF on the deny list. That looks like
# paranoia until you notice that the single most useful-looking proposal such a loop could
# ever generate is "this deny list is too strict", and it would be right by its own lights
# in precisely the way the fabricated-recurrence bug was right by its own lights.
#
# A refusal is RECORDED, not dropped. `propose()` returns refusals alongside emissions and
# they persist with status REFUSED. A filter that silently discards what it blocks reports
# a clean run indistinguishable from one where nothing was ever filtered — the same defect
# this loop's OBSERVE phase exists to avoid, wearing a security hat. If the deny list ever
# fires, a human should be able to see that it fired and on what.

import hashlib
import os
import re
import sqlite3

# ── the blast-radius floor ────────────────────────────────────────────────────────
# Substring-matched against a proposal's target path, case-insensitively, after
# normalisation. Deliberately BROAD: a false refusal costs one proposal; a false
# permission costs the guardrail. Per the capstone proposal (2026-08-19) + Augur's ACK.
DENY_PATHS = (
    "agos_propose.py",       # the gate definition itself — see header
    "agos-propose-contract", # ...and the battery that proves the gate works
    "genesis",               # the genesis-lock fail-loud contract
    "sovereign",             # the sovereign/seal layer
    "seal",
    "secret",
    "credential",
    "password",
    "token",
    ".ssh",
    ".env",
    "antenna",               # the PS<->OS antennae (the 4 invariants)
    "post-scarcity",
    "post_scarcity",
)

EMITTED = "EMITTED"
REFUSED = "REFUSED"
REJECTED = "REJECTED"

# Proposal kinds this phase knows how to draft. Each maps a lesson TYPE to the shape of
# harness change that would have prevented the recurrence (HARNESS-SELFIMPROVE: "a rule,
# guardrail, tool, prompt patch, compaction tweak").
_DRAFTS = {
    "TOOL_FAILURE": (
        "docs/lessons.md",
        "Add a guardrail for a repeatedly failing tool path.",
    ),
    "COST_SPIKE": (
        "docs/lessons.md",
        "Tighten or document the cost-cap breaker threshold.",
    ),
    "STALLED_WORK": (
        "docs/lessons.md",
        "Add a stall detector / timeout for work that never reaches `done`.",
    ),
}

_DDL = """
CREATE TABLE IF NOT EXISTS proposals (
    prop_id   TEXT PRIMARY KEY,
    sig_id    TEXT NOT NULL,
    target    TEXT NOT NULL,
    body      TEXT NOT NULL,
    status    TEXT NOT NULL,
    reason    TEXT
);
CREATE INDEX IF NOT EXISTS prop_sig ON proposals (sig_id);
"""


def _normalise(target):
    """Fold a target path to the form the deny list is matched against.

    Case, separators and traversal are all normalised BEFORE matching, because a deny list
    that can be walked around with `MODULES/../modules/AGOS_PROPOSE.PY` is decoration. The
    match itself is substring-based on the normalised string, so a novel path that merely
    CONTAINS a denied token is refused too — over-refusal is the correct failure direction
    here.
    """
    t = (target or "").strip().replace("\\", "/").lower()
    t = re.sub(r"/+", "/", t)
    # Resolve traversal without touching the filesystem — the target need not exist.
    parts = []
    for seg in t.split("/"):
        if seg == "..":
            if parts:
                parts.pop()
        elif seg not in ("", "."):
            parts.append(seg)
    return "/".join(parts)


def denied(target):
    """Return the DENY_PATHS token that bars this target, or None.

    Returns the token rather than a bool so the refusal record can say WHICH rule fired.
    "Refused" without a reason is an unfalsifiable audit trail.
    """
    norm = _normalise(target)
    for token in DENY_PATHS:
        if token in norm:
            return token
    return None


def content_hash(sig_id, target, body):
    """Identity of a proposal's CONTENT — not its title.

    Augur's third scar, verbatim: "Suppression should key on content-hash of the proposal,
    not title, or trivial rewording resurrects it." Re-proposal by attrition is how a human
    gate degrades into a rubber stamp, so the id a REJECTED verdict attaches to must be one
    that cosmetic edits cannot move off.
    """
    raw = "%s\x00%s\x00%s" % (sig_id, _normalise(target), (body or "").strip())
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


class Proposal:
    """One drafted harness change. A document. It cannot apply itself; nothing here can."""

    __slots__ = ("sig_id", "target", "body")

    def __init__(self, sig_id, target, body):
        self.sig_id = sig_id
        self.target = target
        self.body = body

    @property
    def prop_id(self):
        return content_hash(self.sig_id, self.target, self.body)

    def __repr__(self):
        return "Proposal(%s -> %s)" % (self.sig_id, self.target)


def draft(lesson, drafts=None):
    """Turn one COMPARE-promoted lesson into a candidate Proposal, or None.

    Only LESSONS reach here — a candidate seen once is noise by HARNESS-SELFIMPROVE's own
    rule, and drafting from it would relitigate the threshold that OBSERVE already applied.

    `drafts` is injectable for one specific reason worth stating plainly: with the shipped
    _DRAFTS table every proposal targets `docs/lessons.md`, so the deny list is UNREACHABLE
    from propose()'s own path today. It is defence-in-depth for the draft shapes this table
    will grow, not a filter currently doing work — and an untested guardrail is exactly what
    Augur warned about. Injecting the table lets the battery drive a forbidden target
    through the REAL propose() path and watch it go red, rather than testing denied() in
    isolation and calling the end-to-end path proven.

    IF YOU ADD A DRAFT SHAPE HERE, READ THIS. The first entry whose target is NOT under
    `docs/` makes the deny list live for real traffic, and it must ship with its own
    E-variant in the battery — a case that drives THAT target class through propose() and
    asserts the outcome. Otherwise the injected-table case silently becomes the only armed
    arm again: the guardrail would be covered only where the TEST supplies its own input,
    and uncovered exactly where PRODUCTION supplies it. (Augur, 2026-08-20, on #129.)
    """
    kind = (drafts if drafts is not None else _DRAFTS).get(lesson.get("type"))
    if kind is None:
        return None
    target, headline = kind
    body = (
        "%s\n\n"
        "Observed %d times (signal %s).\n"
        "Detail: %s\n"
        % (headline, lesson.get("occurrences", 0), lesson.get("sig_id"), lesson.get("detail"))
    )
    return Proposal(lesson.get("sig_id"), target, body)


class ProposalStore:
    """Persistence for drafted proposals, refusals and rejections.

    Lives in the SAME SQLite file as the lesson store by default, so a reviewer reading the
    loop's state reads one artefact rather than reconciling two that can disagree.
    """

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

    def status(self, prop_id):
        row = self._db.execute(
            "SELECT status FROM proposals WHERE prop_id = ?", (prop_id,)
        ).fetchone()
        return row[0] if row else None

    def _put(self, prop, status, reason=None):
        """Insert if absent. Never OVERWRITES an existing status.

        This is the line that makes REJECTED terminal. A later run re-drafting the same
        content lands on the same prop_id and the INSERT OR IGNORE is a no-op, so the
        rejection survives; it does not get quietly reset to EMITTED by the next cadence
        tick.
        """
        cur = self._db.execute(
            "INSERT OR IGNORE INTO proposals (prop_id, sig_id, target, body, status, reason) "
            "VALUES (?,?,?,?,?,?)",
            (prop.prop_id, prop.sig_id, prop.target, prop.body, status, reason),
        )
        self._db.commit()
        return cur.rowcount > 0

    def reject(self, prop_id, reason="rejected at human gate"):
        """Mark a proposal REJECTED — a TERMINAL state. Returns True if it took effect.

        Terminal means terminal: the only transition out is a human editing the row. The
        loop has no method that moves a proposal off REJECTED, by construction.
        """
        cur = self._db.execute(
            "UPDATE proposals SET status = ?, reason = ? WHERE prop_id = ? AND status != ?",
            (REJECTED, reason, prop_id, REJECTED),
        )
        self._db.commit()
        return cur.rowcount > 0

    def by_status(self, status):
        return [
            {"prop_id": r[0], "sig_id": r[1], "target": r[2], "body": r[3], "reason": r[4]}
            for r in self._db.execute(
                "SELECT prop_id, sig_id, target, body, reason FROM proposals "
                "WHERE status = ? ORDER BY prop_id",
                (status,),
            )
        ]


def propose(store, lessons, drafts=None):
    """PROPOSE over a batch of lessons. Emits documents; performs no action.

    Returns a report whose counts are all reported SEPARATELY on purpose:

      drafted    — lessons this phase knew how to draft for
      undraftable— lessons whose type has no draft shape (NOT silently zero)
      emitted    — new proposals recorded
      refused    — blocked by DENY_PATHS, WITH the token that fired
      suppressed — already present (previously emitted, refused, or REJECTED)

    Collapsing `refused` into `emitted == 0`, or `undraftable` into "no lessons", would
    make a run that hit the guardrail look identical to a quiet one. Same discipline as
    OBSERVE reporting `examined` alongside `new`, and `malformed_lines` alongside a clean
    parse: an asserted pass and an inability to assess are different facts.
    """
    report = {
        "lessons": len(lessons),
        "drafted": 0,
        "undraftable": 0,
        "emitted": 0,
        "refused": [],
        "suppressed": 0,
    }
    for lesson in lessons:
        prop = draft(lesson, drafts)
        if prop is None:
            report["undraftable"] += 1
            continue
        report["drafted"] += 1
        token = denied(prop.target)
        if token is not None:
            # Recorded, not dropped — a guardrail that fires invisibly is not auditable.
            recorded = store._put(prop, REFUSED, reason="deny-path: %s" % token)
            report["refused"].append({"sig_id": prop.sig_id, "target": prop.target,
                                      "token": token})
            if not recorded:
                report["suppressed"] += 1
            continue
        if store._put(prop, EMITTED):
            report["emitted"] += 1
        else:
            report["suppressed"] += 1
    return report


def render(proposals):
    """Render emitted proposals as plain text for a human reviewer. Returns a string.

    Returns text; writes nothing. A caller that wants this on disk or in a brain-comm does
    that itself, which keeps the only file-writing decision in the loop outside the loop.
    """
    if not proposals:
        return "No proposals.\n"
    out = []
    for p in proposals:
        out.append("--- proposal %s\ntarget: %s\nsignal: %s\n\n%s"
                   % (p["prop_id"], p["target"], p["sig_id"], p["body"]))
    return "\n".join(out)
