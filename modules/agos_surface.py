#!/usr/bin/env python3
# modules/agos_surface.py — self-improvement loop, the SURFACING half of STORE. Stdlib only.
# Instantiation B (Agent OS) of HARNESS-SELFIMPROVE. Reads the stores, writes ONE digest.
#
# ═══ WHY THIS IS NOT APPLY ═══
# HARNESS-SELFIMPROVE's instantiation B specifies the store as "markdown + SQLite mirror,
# SURFACED AS A BRAIN-COMM". The SQLite half shipped; this is the surfacing half. It is the
# only module in the loop that writes a file at all, so the boundary needs to be stated
# precisely rather than assumed:
#
#   Writing `docs/lessons.md` — a proposal's TARGET — is APPLY. It enacts the proposal.
#   Writing a digest that SAYS "there is a pending proposal against docs/lessons.md" is a
#   report about the loop's state. It changes no harness behaviour and nothing downstream
#   consumes it as configuration.
#
# The distinction is crisp, but a crisp distinction that lives only in a comment is one
# refactor from gone. So it is ENFORCED: `emit()` refuses any destination that matches a
# proposal target in the store it is reporting on, and refuses the deny-listed paths
# outright. The module cannot become an APPLY path by having a caller pass a different
# filename, which is exactly how it would happen.
#
# APPLY itself stays unbuilt pending Q1 (does APPLY ever auto-merge?). Surfacing is inside
# every possible answer: if APPLY never auto-merges, a digest a human reads is the whole
# delivery mechanism; if it does, the digest is what makes the gate reviewable.
#
# ═══ THE LOAD-BEARING RULE ═══
#   A DIGEST THAT COULD NOT SEE MUST NOT READ AS A DIGEST THAT SAW NOTHING.
#
# Same rule as agos_cycle's, and it needs restating here because this is the layer a HUMAN
# actually reads. Every prior phase reports its blindness in a dict that only code sees. If
# the digest renders a blind cycle as "no pending proposals", the entire third-state
# discipline of the three modules underneath it is discarded at the last step — by the one
# component whose output anybody looks at.

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import agos_propose as P          # noqa: E402

# Reuse the PROPOSE deny list rather than restating it. A second copy is a second thing to
# forget to update, and the paths this module must never write are exactly the paths PROPOSE
# must never target.
DENY_PATHS = P.DENY_PATHS


class ApplyBoundary(Exception):
    """Raised when a caller asks this module to write something only APPLY may write."""


def _normalise(path):
    return P._normalise(path)


def check_destination(dest, targets):
    """Refuse destinations that would make this module an APPLY path.

    `targets` is every target named by the proposals being reported on. Passing them in
    (rather than re-querying) keeps the check honest about the SPECIFIC set being surfaced:
    a digest can only enact a proposal it is actually carrying.
    """
    token = P.denied(dest)
    if token is not None:
        raise ApplyBoundary(
            "refusing to write %r: matches deny-path %r" % (dest, token)
        )
    norm = _normalise(dest)
    for t in targets:
        if _normalise(t) == norm:
            raise ApplyBoundary(
                "refusing to write %r: it is the TARGET of a proposal in this digest — "
                "writing it would enact the proposal, which is APPLY" % dest
            )
    return dest


def render(lessons, proposals, refused=(), cycle_report=None):
    """Build the digest text. Writes nothing.

    `cycle_report` is optional but strongly encouraged: without it this function has no way
    to know whether the empty lists it was handed mean "healthy" or "blind", and it says so
    rather than guessing.
    """
    out = ["# Agent OS — self-improvement digest", ""]

    if cycle_report is None:
        out.append(
            "_Provenance unknown: no cycle report was supplied, so this digest CANNOT say "
            "whether the counts below reflect a healthy run or a blind one._"
        )
        out.append("")
    elif cycle_report.get("blind"):
        out.append(
            "**CANNOT-ASSESS — this cycle read 0 of %d sources.** The counts below are not "
            "evidence of a quiet system; nothing was observed. Fix the sources before "
            "reading anything into an empty digest."
            % cycle_report.get("sources_total", 0)
        )
        out.append("")
        for name, st in sorted(cycle_report.get("sources", {}).items()):
            out.append("- source `%s`: %s%s"
                       % (name, st.get("state"),
                          (" — " + st["error"]) if st.get("error") else ""))
        out.append("")
    else:
        srcs = cycle_report.get("sources", {})
        unread = [n for n, st in srcs.items() if st.get("state") != "read"]
        out.append("Sources read: %d of %d.%s"
                   % (cycle_report.get("sources_read", 0),
                      cycle_report.get("sources_total", 0),
                      (" Not read: " + ", ".join(sorted(unread)) + ".") if unread else ""))
        malformed = cycle_report.get("observed", {}).get("malformed_lines", 0)
        if malformed:
            out.append("**%d malformed line(s)** were skipped — this digest is partial."
                       % malformed)
        out.append("")

    out.append("## Lessons (recurred, promoted by COMPARE)")
    if lessons:
        for l in lessons:
            out.append("- `%s` **%s** — seen %d×. %s"
                       % (l.get("sig_id"), l.get("type"),
                          l.get("occurrences", 0), l.get("detail", "")))
    else:
        out.append("_None promoted._")
    out.append("")

    out.append("## Pending proposals (EMITTED — none of these have been applied)")
    if proposals:
        for p in proposals:
            out.append("- `%s` → `%s`" % (p.get("prop_id"), p.get("target")))
            for line in (p.get("body") or "").strip().splitlines():
                out.append("  > %s" % line)
    else:
        out.append("_None._")
    out.append("")

    # Refusals are surfaced, never omitted. A guardrail that fires where nobody can see it
    # has, for every practical purpose, not fired.
    out.append("## Refused by the deny list")
    if refused:
        for r in refused:
            out.append("- `%s` → `%s` — deny-path `%s`"
                       % (r.get("sig_id"), r.get("target"), r.get("token")))
    else:
        out.append("_None._")
    out.append("")
    out.append("_Generated by the self-improvement loop. APPLY is not implemented; "
               "nothing here has changed any file._")
    return "\n".join(out) + "\n"


def emit(dest, text, proposals=(), refused=()):
    """Write the digest, after proving the destination is not an APPLY.

    Returns the path written. The boundary check happens HERE and not in the caller,
    because a check the caller can forget is not a boundary.
    """
    targets = [p.get("target") for p in proposals] + [r.get("target") for r in refused]
    check_destination(dest, [t for t in targets if t])
    parent = os.path.dirname(os.path.abspath(dest))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write(text)
    return dest


def surface(dest, lesson_store, proposal_store, cycle_report=None):
    """Read both stores, render, emit. The one file-writing entry point in the loop."""
    lessons = lesson_store.lessons()
    proposals = proposal_store.by_status(P.EMITTED)
    refused = [
        {"sig_id": r["sig_id"], "target": r["target"],
         "token": (r.get("reason") or "").replace("deny-path: ", "")}
        for r in proposal_store.by_status(P.REFUSED)
    ]
    text = render(lessons, proposals, refused, cycle_report)
    return emit(dest, text, proposals, refused), text
