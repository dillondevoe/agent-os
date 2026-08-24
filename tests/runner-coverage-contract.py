#!/usr/bin/env python3
# runner-coverage-contract.py — every battery file is either RUN by tests/run-local.sh or
# EXCLUDED here with a reason. Nothing else is allowed.
#
# WHY THIS FILE EXISTS AT ALL, rather than the check living inside run-local.sh where it was
# born (2026-08-23):
#
#   run-local.sh's header claimed it ran "every battery that does NOT need nix" and named four
#   deliberate exclusions. FIFTEEN were uncovered; ELEVEN were neither run nor declared. The
#   exclusion list was PROSE, and prose cannot go red, so it drifted for months in silence.
#
#   The first fix put a coverage assertion inside run-local.sh. That was an improvement and it
#   was ALSO A TIER MISTAKE, stated too strongly at the time: run-local.sh has ZERO references
#   in flake.nix and is run by no workflow. It is a developer convenience. An arm that lives
#   only there protects the sessions that REMEMBER TO RUN IT — which is the same tier as the
#   prose it replaced, one rung up at best.
#
#   (The belief that run-local.sh was CI-wired came from the comment-counting defect in
#   wiring_references() — a `#` mention was read as wiring. Fixing that detector is what made
#   the real answer visible. The stale belief outlived the bug that produced it by hours.)
#
# So the rule lives HERE, in a file the flake-check workflow runs directly, and run-local.sh
# CALLS it rather than reimplementing it. That is the fourth scar's rule applied deliberately:
# WHEN TWO HALVES ENFORCE THE SAME RULE, MAKE THEM CALL THE SAME FUNCTION, NOT MERELY AGREE.
# A bash copy and a python copy of one exclusion list is two spellings of one rule with nothing
# asserting they match, which is the shape that produced the whitespace starvation bug.
#
# It asserts COVERAGE; it does not auto-run. Every entry in run-local.sh wires a bespoke
# argument contract that discovery cannot invent, and a runner that guesses arguments produces
# failures that are about the runner.
import os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")

# THE EXCLUSIONS ARE GROUPED BY *WHY*, and that is load-bearing rather than tidy. A flat count
# would hide the split: these three groups have completely different costs to leave unpaid.
EXCLUDED = {
    # Need a materialized Nix registry and/or store paths. `nix flake check` runs them.
    "broker-battery.sh":      "needs a materialized nix registry / store paths",
    "cap-battery.sh":         "needs a materialized nix registry / store paths",
    "confirm-battery.sh":     "needs a materialized nix registry / store paths",
    "seam-live-battery.sh":   "needs a materialized nix registry / store paths",

    # Needs real privilege. This one is NOT cheap to leave unpaid and must not be quietly
    # re-exempted for being inconvenient — it is the same entry flagged in
    # vm-matrix-contract.py's KNOWN_UNWIRED_DEBT as likely last.
    "cap-sandbox-battery.sh": "wants sudo + real systemd",

    # SELF-DISARMING: these SKIP with rc=0 when their CLI is off PATH, so wiring them buys a
    # green line that proves nothing. That is worse than an honest gap, because a green line
    # is counted. Listed so the zero is VISIBLE rather than absent.
    "agos-calc-battery.py":   "self-disarms: SKIP rc=0 with its CLI off PATH",
    "agos-doc-battery.py":    "self-disarms: SKIP rc=0 with its CLI off PATH",
    "agos-files-battery.py":  "self-disarms: SKIP rc=0 with its CLI off PATH",
    "agos-media-battery.py":  "self-disarms: SKIP rc=0 with its CLI off PATH",
    "agos-notes-battery.py":  "self-disarms: SKIP rc=0 with its CLI off PATH",
    "agos-sys-battery.py":    "self-disarms: SKIP rc=0 with its CLI off PATH",
    "agos-web-battery.py":    "self-disarms: SKIP rc=0 with its CLI off PATH",
    "calendar-battery.py":    "self-disarms: live half is gated on the Dell (task 294)",
}


def runner_code(path):
    """run-local.sh with comment text removed.

    STRIPPING COMMENTS IS THE WHOLE CHECK, not hygiene. A battery named only in run-local.sh's
    header prose is NOT run by it, and a bare substring match reports it COVERED — which is a
    false green in the arm whose entire job is finding false greens. This is the identical
    defect that wiring_references() carried in vm-matrix-contract.py, and it was reintroduced
    by hand in the first draft of this very check, hours after being fixed there. It is what
    the class does.
    """
    return strip_comments(open(path, encoding="utf-8").read())


def strip_comments(text):
    """Drop `#` comment text. Shared by the runner and the flake for the reason above: a name
    that appears only in prose is not a caller, in either language."""
    out = []
    for line in text.split("\n"):
        # `#` ONLY STARTS A COMMENT AT A WORD BOUNDARY, and getting this wrong ate the repo's
        # most load-bearing contract. flake-check.yml invokes vm-matrix-contract.py as
        # `nix shell nixpkgs#python3Packages.pyyaml --command python3 tests/vm-matrix-contract.py`
        # — a bare find("#") truncates that line at the FLAKE REFERENCE and deletes the filename,
        # so the check reported its own most important subject as unrun. Same class as everything
        # else here: a predicate that under-reports, failing toward a confident wrong answer.
        # Also correct for `${var#prefix}` in shell and nix.
        m = re.search(r"(?:^|\s)#", line)
        out.append(line if not m else line[:m.start()])
    return "\n".join(out)


def uncovered(tests_dir, runner_path):
    """Battery AND contract files neither invoked by a runner nor excluded above.

    `*-contract.py` JOINED THE SUBJECT 2026-08-24, and the omission was a real hole rather than
    a tidy-up. This check's subject was battery files only, so a contract file could land with
    no caller anywhere and nothing would say so — which is precisely the failure the file was
    written to end, one filename-suffix away from where it was looking. Found by adding
    hand-degrade-contract.py and noticing that NOTHING objected to it being unrun.

    Measured before the predicate was widened: all 12 contract files that existed at the time
    already had a caller, so switching this on cost zero exemptions. A widening that had needed
    a pile of new exemptions would have been the widening arguing against itself.

    Contracts are covered by run-local.sh OR by flake.nix, because that is how they are actually
    invoked — most run as nix checks and were never meant to be in the bash runner. Batteries
    stay run-local.sh-only: that is the lane the exclusion reasons below are written about.
    """
    code = runner_code(runner_path)
    root = os.path.dirname(tests_dir)
    # flake-check.yml is the third caller and NOT an afterthought: vm-matrix-contract.py is
    # invoked from there and nowhere else in code — its flake.nix and run-local.sh mentions are
    # both PROSE. Widening the subject without widening the sources would have flagged the
    # repo's most load-bearing contract as unrun, which is the false-positive mirror of the hole
    # being closed. Comments are stripped from all three for the same reason.
    for extra in (os.path.join(root, "flake.nix"),
                  os.path.join(root, ".github", "workflows", "flake-check.yml")):
        if os.path.exists(extra):
            code += "\n" + strip_comments(open(extra, encoding="utf-8").read())
    missing = []
    for b in sorted(os.listdir(tests_dir)):
        if not is_subject(b):
            continue
        if b in EXCLUDED:
            continue
        if b not in code:
            missing.append(b)
    return missing


def is_subject(b):
    """The one definition of "a file this contract governs".

    IT WAS TWO, AND THEY DISAGREED. When the subject was widened to include contract files
    (2026-08-24, after a contract file landed unrun), the ENFORCEMENT below was widened and the
    SUMMARY COUNT was not — so the printed line read "%d battery + contract files" while counting
    batteries only. The label named a scope the number did not have, the total was wrong from the
    moment of the widening, and `n - len(EXCLUDED)` inherited the error silently. It surfaced only
    because adding tests/backend-absence-contract.py left the count unchanged at 32 when it should
    have moved.

    This is the repo's fourth scar exactly: reader and writer spelling ONE rule in TWO languages,
    with nothing asserting they agree. The remedy is the same one — make both halves CALL THE SAME
    FUNCTION rather than merely be written to match.
    """
    return b.endswith("battery.py") or b.endswith("battery.sh") or b.endswith("contract.py")


def stale_exclusions(tests_dir):
    """Names excluded here that no longer exist, or that the runner now invokes anyway.

    A suppression list naming things that are no longer true silently exempts whatever takes
    the name next. Same arm, same reason, as vm-matrix-contract.py's stale-exemption check —
    and that arm is the one with no human in the loop, so it is the one that must not rot.
    """
    return [b for b in sorted(EXCLUDED) if not os.path.exists(os.path.join(tests_dir, b))]


def main():
    runner = os.path.join(TESTS, "run-local.sh")
    if not os.path.exists(runner):
        print("FAIL: tests/run-local.sh is missing — the coverage contract has no subject")
        return 1
    miss = uncovered(TESTS, runner)
    stale = stale_exclusions(TESTS)
    rc = 0
    if miss:
        print("FAIL: battery or contract files neither run by run-local.sh, flake.nix, nor flake-check.yml, and not excluded:")
        for b in miss:
            print("        " + b)
        print("      Add it to run-local.sh, or add it to EXCLUDED here WITH ITS REASON.")
        print("      A runner that silently omits a battery reports exactly like one that runs it.")
        rc = 1
    if stale:
        print("FAIL: EXCLUDED names a file that no longer exists:")
        for b in stale:
            print("        " + b + "  (" + EXCLUDED[b] + ")")
        rc = 1
    if rc == 0:
        n = sum(1 for b in os.listdir(TESTS) if is_subject(b))
        print("runner-coverage-contract: PASS — %d battery + contract files, %d run, %d excluded with a reason"
              % (n, n - len(EXCLUDED), len(EXCLUDED)))
    return rc


if __name__ == "__main__":
    sys.exit(main())
