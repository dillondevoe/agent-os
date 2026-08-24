#!/usr/bin/env python3
# hand-degrade-contract.py — a STATIC contract about the agos-* hands' degrade paths.
#
# WHY THIS FILE EXISTS, and it is not "a linter would be nice". One defect shape has now been
# found and fixed in THREE hands on THREE separate occasions:
#
#   agos-notes list   rc=1, stdout `[]`                       (fixed 2026-08-24, morning)
#   agos-cal  agenda  rc=1, stdout `[]`                       (fixed 2026-08-24, midday)
#   agos-files list   rc=1, stdout {"ok":true,"count":0,...}  (fixed 2026-08-24, afternoon)
#
# Each fix was correct and none of them stopped the next one. That is the definition of a class
# rather than an incident, and a class needs a check, not three commits.
#
# THE SHAPE: an external producer as a NON-FINAL stage of a pipeline, under `set -euo pipefail`,
# with its output uncaptured. jq (or whatever the final stage is) succeeds on empty or partial
# input and PRINTS A COMPLETE, PLAUSIBLE ANSWER before pipefail can set the exit code. The caller
# reading stdout is not merely uninformed — it is misinformed. The agos-files instance is the
# worst of the three because the final jq stamps `ok:true` onto the lie; the other two at least
# returned a bare value that claimed nothing.
#
# WHAT THIS CHECK IS NOT. It cannot tell whether a hand handles failure WELL — that needs a live
# degrade probe, which is the batteries' job (see the unreadable-dir arm in agos-files-battery.py
# and the AGOS_CAL_CONF arm in calendar-battery.py). This is the cheap static half: it finds the
# structural precondition, which is the part that recurs.
#
# Run: python3 tests/hand-degrade-contract.py
import os, re, sys, textwrap

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
# Overridable so the scanner can be pointed at a RECONSTRUCTED tree of known-bad hands. That is
# not a convenience: a detector reporting zero is worthless until it has been shown reporting
# non-zero on instances that really existed, and the only such instances are in git history.
PKGS = os.environ.get("AGOS_PKGS_DIR") or os.path.join(ROOT, "modules", "pkgs")

EX = 0
def check(name, cond, detail=""):
    global EX
    print(("  PASS " if cond else "  FAIL ") + name + (("  — " + detail) if detail else ""))
    if not cond: EX = 1

# Commands whose failure cannot produce a false answer downstream, or which do not read the
# world at all. `printf`/`echo` emit what they were given; `jq`/`sed`/`tr`/`sort` TRANSFORM an
# upstream stream rather than produce one, so a failure there is not the shape (and they are
# almost always the final stage anyway). Everything else is a PRODUCER: it goes out to the
# filesystem, the network, or a subprocess, and it is the thing that can fail while the rest of
# the pipeline carries on printing.
TRANSFORMERS = {
    "printf", "echo", "cat", "jq", "sed", "awk", "tr", "sort", "head", "tail",
    "cut", "uniq", "wc", "grep", "rev", "column", "fold", "nl", "tee", "true", "xargs",
}

# Exemptions: (hand basename, 1-indexed line of the pipeline) -> why it is not the shape.
# EMPTY, and that is a MEASURED zero — the sweep below was run against the three known
# historical instances (reconstructed from git) and reported all three before this list was
# believed. A suppression list that has never suppressed anything is indistinguishable from a
# broken scanner.
KNOWN_SWALLOWED_PRODUCERS = {}

# A STALE EXEMPTION IS ITSELF THE BUG. This is the third file in this repo to need this
# paragraph, and the second to get it only after shipping the list without it. An exemption is
# subtracted from the findings BEFORE they print, so a stale one is invisible BY CONSTRUCTION:
# suppressing findings is its whole job. Page's rule, adopted verbatim — A STALE CHECK NEEDS A
# PREDICATE FOR WHY THE CLAIM NO LONGER HOLDS, NOT MERELY THE CLAIM'S ABSENCE. Here the
# predicate is exact: the hand no longer exists, or that line is no longer a flagged pipeline.

def hand_bodies():
    """Every agos-* hand's shell body, dedented, with line continuations joined.

    Returns {basename: [(lineno, text), ...]}. Line numbers are 1-indexed WITHIN the shell
    body, which is what the report prints — not the .nix file's numbering, and the report says
    so, because a line number that silently means something else is its own small lie.
    """
    out = {}
    if not os.path.isdir(PKGS):
        return out
    for fn in sorted(os.listdir(PKGS)):
        if not (fn.startswith("agos-") and fn.endswith(".nix")):
            continue
        src = open(os.path.join(PKGS, fn), encoding="utf-8").read()
        m = re.search(r"text\s*=\s*''\n(.*?)\n\s*''\s*;", src, re.S)
        if not m:
            continue
        body = textwrap.dedent(m.group(1)).replace("''${", "${")
        joined, buf, start = [], "", None
        # HEREDOC BODIES ARE NOT CODE. The usage text of every hand is a `cat <<'USAGE'` block
        # describing the hand's OWN OUTPUT — `media_type in image|video|audio|other` reads as a
        # pipeline to anything scanning bytes, and was this check's first false positive. Prose
        # about pipelines is not a pipeline.
        heredoc = None
        for i, line in enumerate(body.split("\n"), 1):
            if heredoc is not None:
                if line.strip() == heredoc:
                    heredoc = None
                continue
            hd = re.search(r"<<-?\s*'?\"?([A-Za-z_][A-Za-z0-9_]*)'?\"?\s*$", line)
            if hd:
                heredoc = hd.group(1)
                continue
            if start is None:
                start = i
            if line.rstrip().endswith("\\"):
                buf += line.rstrip()[:-1] + " "
                continue
            joined.append((start, buf + line))
            buf, start = "", None
        if buf:
            joined.append((start or 1, buf))
        out[fn] = joined
    return out

def swallowed_producers():
    """Pipelines whose first stage is an uncaptured external producer."""
    hits = []
    for fn, lines in hand_bodies().items():
        for lineno, raw in lines:
            s = raw.strip()
            if "|" not in s or s.startswith("#"):
                continue
            # `||` is not a pipeline, and `|&` is bash's stderr-merging pipe (still one).
            if re.match(r"^[^|]*\|\|", s):
                continue
            first = s.split("|")[0].strip()
            # Already guarded: inside a command substitution, an `if`/`while` condition, or
            # negated. Any of these means the exit status is REACHABLE by the author.
            if "$(" in first or "`" in first:
                continue
            if re.match(r"^\s*(if|while|until)\b", first) or first.startswith("!"):
                continue
            words = re.sub(r"^\s*(local|declare|export)\s+", "", first).split()
            if not words:
                continue
            head = os.path.basename(words[0])
            # Block closers and shell keywords are not producers. `done | jq` is a `while` loop
            # feeding a pipeline, and the loop's real producer is several lines up where this
            # line-oriented scan cannot follow it. That is a KNOWN BLIND SPOT, stated rather than
            # papered over: this check finds the structural precondition on a single logical
            # line, and a green here is not a proof that no hand swallows a producer. The live
            # degrade arms in the batteries are the other half, and neither half substitutes for
            # the other. (agos-notes' loop is in fact guarded — `{ find ...; } || true` — which
            # is how the blind spot came to light without also being a defect.)
            if head in ("done", "fi", "esac", "}", ")", "then", "do", "else", "elif", "in"):
                continue
            if head in TRANSFORMERS or "=" in head:
                continue
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_.-]*$", head):
                continue
            hits.append((fn, lineno, head, s[:100]))
    return hits

def live_findings():
    return [h for h in swallowed_producers() if (h[0], h[1]) not in KNOWN_SWALLOWED_PRODUCERS]

def stale_exemptions():
    """Entries that no longer describe anything — the half that would otherwise stay prose."""
    present = set(hand_bodies())
    flagged = {(fn, lineno) for fn, lineno, _, _ in swallowed_producers()}
    stale = []
    for (fn, lineno), why in sorted(KNOWN_SWALLOWED_PRODUCERS.items()):
        if fn not in present:
            stale.append(((fn, lineno), "no such hand in modules/pkgs/"))
        elif (fn, lineno) not in flagged:
            stale.append(((fn, lineno), "line %d is no longer a flagged pipeline" % lineno))
    return stale

def main():
    bodies = hand_bodies()
    # THE VACUITY CONTROL, and it is the arm this whole file would be worthless without. If the
    # `text = ''...''` regex stops matching — a nix formatting change, a hand moved to a
    # different builder — every loop below iterates over nothing and this check prints a
    # confident ALL PASS about a repo it never read. That is the same false-answer shape the
    # file exists to catch, one level up, pointed at itself.
    check("the sweep actually read the hands", len(bodies) >= 5,
          "found %d hand bodies in modules/pkgs/ (%s)" % (len(bodies), ", ".join(sorted(bodies))))
    piped = sum(1 for ls in bodies.values() for _, l in ls if "|" in l)
    check("the sweep actually saw pipelines", piped >= 5, "%d piped lines" % piped)

    findings = live_findings()
    stale = stale_exemptions()

    if not findings and not stale:
        print("OK: no agos-* hand pipes an uncaptured external producer into a stage that can "
              "print a plausible answer over its failure (%d hands, %d piped lines, %d exemptions)."
              % (len(bodies), piped, len(KNOWN_SWALLOWED_PRODUCERS)))
    for fn, lineno, head, s in findings:
        check("%s: `%s` is a non-final pipeline stage and its failure is unreachable "
              "(body line %d)" % (fn, head, lineno), False, s)
    for (fn, lineno), why in stale:
        check("stale exemption %s:%d — %s" % (fn, lineno, why), False,
              "Either the hand was fixed (good — DELETE the entry) or it was renamed or removed.")

    print("hand-degrade-contract: " + ("ALL PASS" if EX == 0 else "FAILURES"))
    return EX

if __name__ == "__main__":
    sys.exit(main())
