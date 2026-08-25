#!/usr/bin/env python3
"""Assert the vm-tests matrix and the flake's test-* packages are the same set.

WHY THIS EXISTS. .github/workflows/vm-tests.yml opens by describing its own origin: two
nixosTests that were written, reviewed and merged, lived in `packages`, and were built by no
scheduled job. "A regression test that does not execute does not prevent the regression; it
documents that someone once could have caught it." The workflow then states the rule meant to
stop that recurring — add the matrix entry in the same commit as the test — twice, in two
separate comments.

That rule was enforced by human memory. This file enforces it.

The failure it catches is the repo's own worst historical bug in miniature: a test package that
exists, evaluates, passes locally, and is named in no matrix entry, so CI never runs it. Nothing
goes red. `nix flake check` stays green because nixosTests are deliberately held OUT of `checks`
(they boot VMs; minutes, not seconds), so the fast lane never touches them either. The test is
present in every sense except the one that matters.

THREE THINGS ARE CHECKED, because they fail differently:

  * a tests/*.nix file that flake.nix never references -> it has no package at all, so it is
    absent from BOTH lists below and comparing them to each other passes. The file is
    committed and reads as coverage. This is the most silent of the three, and it was missed
    by the first version of this very file, which checked only the two below.
  * a test-* package with NO matrix entry  -> the test silently never runs. Silent by
    construction.
  * a matrix entry with NO test-* package  -> the job fails at `nix build` with a confusing
    attribute error. Not dangerous, but it should fail here with a sentence that says what is
    wrong instead of there with a resolution trace.

Note the progression: each check covers the gap left by the one after it. Comparing two derived
lists to each other cannot tell you that something never entered either.

DELIBERATELY NOT SILENT-SKIPPABLE. If PyYAML is missing this exits non-zero rather than passing.
A check that quietly degrades to a no-op when a dependency is absent is precisely the class of
bug it was written to catch — see docs/cancelled-boundaries.md, members 3, 8 and 10.

Local use:
    python3 tests/vm-matrix-contract.py

Control arms (each MUST fail — if one does not, that check is not a check):
    python3 tests/vm-matrix-contract.py --packages-json '["test-does-not-exist"]'
    touch tests/orphan.nix && python3 tests/vm-matrix-contract.py ; rm tests/orphan.nix
"""
import argparse
import glob
import re
import json
import ast
import os
import subprocess
import sys

WORKFLOW = ".github/workflows/vm-tests.yml"
FLAKE = "flake.nix"
TESTS_DIR = "tests"
SYSTEM = "x86_64-linux"

# Which extensions in tests/ are candidate test files. This was ".nix" ALONE until the sweep
# below, and that single-extension glob is what let six committed batteries run nowhere for as
# long as they have existed. The docstring above already argued the general case — "a test file
# that was never added to flake.nix at all has no package, so it is absent from BOTH sides and
# the comparison passes" — and that argument never had anything to do with the extension. The
# guard simply looked at one third of the directory it claimed to cover.
TEST_SUFFIXES = (".nix", ".py", ".sh")

# Files in tests/ that are deliberately NOT wired into flake.nix as a test — shared helpers,
# libraries, fixtures, and the local-only runner.
#
# This list is the opt-out, and it is explicit ON PURPOSE. The alternative — inferring
# "probably a helper" from a filename — would make the check quietly stop covering things
# as the tree grows, which is the exact failure this file exists to prevent. Adding an
# entry here should be a visible decision in a diff, not a pattern that swallows files.
UNWIRED_BY_DESIGN = frozenset({
    # "run-local.sh" was exempted here as "the manual at-a-box runner; it INVOKES batteries,
    # it is not one" — true, and stale as of 2026-08-24: flake-check.yml now runs it, so the
    # wiring check finds it on the merits and an exemption would only hide a future removal
    # of that step. THE ARM DELETED THIS ROW ITSELF, as it did its own row on 2026-08-23:
    # the stale-exemption check reported it the first time it ran with the third lane in.
    # vm-matrix-contract.py USED TO BE EXEMPTED HERE, with the true-but-unverified comment
    # "invoked directly by flake-check.yml, not via flake.nix". workflow_run_references() now
    # computes that, so the exemption went stale the moment it could be checked and the arm
    # deleted its own row. Note who forced this line out — not memory. Same move as the
    # identity-battery ledger row: the state "exempted while actually running" is unreachable,
    # and so is "exempted while the step that ran it was deleted".
})

# DEBT, NOT DESIGN — and the two must never share a list.
#
# Every entry here is a real battery, committed and passing locally, that NO CI lane builds. It
# is the repo's own worst historical bug ("a regression test that does not execute does not
# prevent the regression; it documents that someone once could have caught it") sitting live in
# the tree, and it was invisible because the guard written to end that bug globbed *.nix only.
#
# They are listed rather than silently exempted so that the count is a number someone can watch
# go down. THIS LIST MAY ONLY SHRINK. Wiring each one needs its own derivation with its own
# dependencies — separate work, per battery — but nothing new can join them: an unwired test that
# is NOT named here fails this check on the commit that adds it, which is the whole point.
#
# escalate-consent-battery.py deserves its own line: it is referenced by nothing at all, not even
# tests/run-local.sh, so before this check it was invisible to every reader as well as to CI.
KNOWN_UNWIRED_DEBT = frozenset({
    # What remains of the eight ambient-hand acceptance batteries. Each was named in flake.nix
    # by exactly one `builtins.pathExists` assert and its error string, and by nothing else in
    # the repository. The guard that names them proves they have not been DELETED; nothing
    # proved they RUN.
    #
    # 2026-08-24: SIX more left in one pass by the agos-calc route -- extract the `let`-bound
    # writeShellApplication into modules/pkgs/<name>.nix so something outside the module can
    # name it, add AGENT_OS_STRICT so the battery refuses to pass with its subject absent, then
    # depend on it from a cheap runCommand. Both halves are required, every time.
    #
    # agos-media took a third half. It has a SECOND self-disarm -- no fixture, exit 0 -- and its
    # own comment asserted a fixture "cannot [be] synthesize[d] headlessly". ffmpeg does exactly
    # that, in three lines, and the hand already carries ffmpeg-headless. Wired without it, the
    # battery would have sailed past strict mode and reported a vacuous green: A SECOND DISARM
    # BEHIND THE FIRST ONE IS STILL A DISARM, and the false claim in the comment is what made it
    # invisible. When you wire a battery, count its exit-0 paths, do not fix the first one.
    #
    # calendar-battery.py left on 2026-08-24, and it was NOT the same shape as that six: it
    # probes two tools (agos-cal AND khal) and so had TWO exit-0 paths that are not assertions,
    # not one. Closing only the final SKIP would have left the khal fallback to silently swap
    # the SUBJECT — exercising the backend and reporting a green for the hand, which was never
    # invoked. Strict mode now demands `agos-cal` specifically. Same third half as agos-media:
    # when you wire a battery, COUNT its exit-0 paths; do not fix the first one.
    # agos-calc-battery.py left on 2026-08-24 — the FIRST of these eight to run anywhere, and
    # the entry that shows why the group resisted for so long. Its reason ("self-disarms: SKIP
    # rc=0 with its CLI off PATH") named a property of the BATTERY, and the battery was only
    # half the problem: `agos-calc` was a `let` binding inside a NixOS module, so no expression
    # in the repo could put it on a PATH. Fixing only the disarm would have produced a red that
    # nothing could turn green; fixing only the packaging would have produced a vacuous green.
    # Now: modules/pkgs/agos-calc.nix + AGENT_OS_STRICT=1 + an agos-calc-contract derivation.
    # The other seven are the same shape and should follow mechanically.
    # anthropic-transport-battery.py and audit-signing-battery.py were here from the first
    # sweep (#153) with the reason "referenced only by tests/run-local.sh, a manual runner."
    # That reason was accurate and it named the fixable half out loud for a day and a half:
    # the batteries were fine, the RUNNER had no caller. On 2026-08-24 flake-check.yml gained
    # a `bash tests/run-local.sh` step, and all four run-local-only entries left this list
    # together — see escalate-consent and transport below.
    # bip340-battery.py was here until 2026-08-23, and it is the entry that should NOT have
    # been a routine line on this list. Its own header states, as fact, that it satisfies
    # "binding condition 2 of Geist's 2026-08-19 Path-A ruling: the FULL official test-vector
    # set runs in CI." It ran in no lane. The repo held both claims at once — "runs in CI" in
    # the file, "runs nowhere" in this list — and nothing ever made them meet.
    #
    # A RULING CONDITION DISCHARGED BY WRITING A FILE IS DISCHARGED BY PROSE. Condition 2 asks
    # for an EXECUTION; the only evidence of one is a lane that goes red when it stops. The
    # header was read as the receipt for four days.
    #
    # Now wired as `bip340-contract` in flake.nix. Note what specifically had not been running:
    # the must-fail vectors (5-15) and check I's control arm, i.e. the forgery-acceptance
    # coverage the ruling singled out — a verifier returning True unconditionally passes every
    # TRUE vector, so the unrun half was the half that matters.
    # escalate-consent-battery.py and transport-battery.py left on 2026-08-24 with the two
    # above. WIRED, not delisted: the workflow step runs run-local.sh, run-local.sh runs all
    # four, and run-local.sh exits 1 iff a battery exits nonzero — mutation-verified, not read.
    # A third detector, runner_lane_reference(), computes this transitively and REQUIRES the
    # workflow step to still exist; delete the step and these four correctly return to debt.
    # Verified before delisting: all four are self_disarms() == False, all four pass under
    # `env -i` with PATH=/usr/bin:/bin, and the whole suite is 34s.
    #
    # The general shape, and it is why this took four separate ticks to see: THE DEBT WAS NEVER
    # IN THESE FILES. Each entry described its own file as the problem, and the problem was one
    # missing caller shared by all four. A per-item ledger reads as four independent debts and
    # invites four independent fixes; the fix was one line in a workflow.
    # frontdoor-kick-battery.py was here until 2026-08-23. WIRED, not merely delisted: flake.nix
    # gained a `frontdoor-kick-contract` derivation that reconstructs tests/ + modules/ and runs
    # it. Verified three ways before the line was removed — wiring_references() finds a real
    # `python3 tests/...` invocation and not just a mention; self_disarms() is False; and the
    # derivation's exact file set was simulated by hand (nix is not available on the surface this
    # was written from), passing with the subject present and exiting 1 with modules/ absent.
    # That last one is the #155 question: a wired battery that exits 0 when its subject is missing
    # pays the debt on paper. This one does not.
    # ── ADDED 2026-08-23, AND THE LEDGER GOING UP HERE IS A CORRECTION, NOT A REGRESSION. ──
    # Neither of these was newly un-wired. Both were MASKED: the only mentions of them in
    # flake.nix are inside `#` comments, and until this same commit wiring_references() counted a
    # comment as possible wiring. The true debt was 15 the whole time while the ledger said 14.
    # The rule "this list may only shrink" is about never hiding a battery; it is not a reason to
    # keep two hidden ones off it, so they go on loudly and the delta is named in the commit.
    # Neither self-disarms, so each needs a derivation and nothing more — except that
    # cap-sandbox-battery.sh wants sudo and real systemd, which is why it is likely the LAST of
    # these to be payable and must not be quietly re-exempted for being inconvenient.
    #
    # identity-battery.py was PAID the same day it was revealed (identity-contract in flake.nix),
    # deliberately: an entry that reached this list by UNMASKING rather than by regression is the
    # one most easily tolerated as "not really new debt", and the way to not tolerate it is to pay
    # it first. Note who forced this line to be deleted — not memory. The STALE-EXEMPTION arm went
    # red the instant the derivation landed, so wiring a battery and forgetting its ledger row is
    # not a reachable state. That is the arm the `#`-comment defect had inverted.
    "cap-sandbox-battery.sh",
})


def matrix_entries(path):
    try:
        import yaml
    except ImportError:
        sys.exit(
            "FAIL: PyYAML is unavailable, so the matrix cannot be parsed.\n"
            "      Refusing to exit 0: a check that skips itself when a dependency is\n"
            "      missing is indistinguishable from a check that passed."
        )
    with open(path) as fh:
        wf = yaml.safe_load(fh)
    try:
        return set(wf["jobs"]["vm-test"]["strategy"]["matrix"]["test"])
    except (KeyError, TypeError) as exc:
        sys.exit(f"FAIL: could not read the matrix from {path}: {exc!r}")


WORKFLOWS_DIR = ".github/workflows"


def workflow_run_references(base, workflows_dir=WORKFLOWS_DIR):
    """Workflow `run:` steps that execute tests/<base>. Returns [(file, step-name), ...].

    WHY THIS EXISTS, and it is a correction to this file rather than a feature.

    `wiring_references()` answers "does flake.nix run it," and this file treated that as the
    whole of "does it run anywhere." It is not. A file can be executed by an explicit workflow
    STEP with no flake reference at all — and the proof is THIS FILE, which was exempted in
    UNWIRED_BY_DESIGN with the comment "invoked directly by flake-check.yml, not via flake.nix."

    That comment was TRUE and it was also PROSE. Nothing checked it. Delete the step from
    flake-check.yml and the exemption still stands, this contract runs in no lane, and the arm
    that exists to catch exactly that failure is the one silently exempting it. An exemption
    whose stated reason is unverified is a suppression list entry with a story attached.

    So the reason is now COMPUTED, the exemption is deleted, and if the step ever disappears
    this file starts failing its own check — which is the behaviour the comment claimed.

    PARSE THE YAML; DO NOT STRIP `#` COMMENTS BY HAND. That was my first draft and it is wrong
    here in a way that is worth recording, because the same reflex is correct one file over: in
    run-local.sh, cutting at the first `#` is the entire check. In YAML it DESTROYS the answer —
    the real invocation reads

        run: nix shell nixpkgs#python3Packages.pyyaml --command python3 tests/vm-matrix-contract.py

    and cutting at the first `#` truncates it at `nixpkgs`, dropping the reference and reporting
    the file unwired. A rule transplanted from the file where it was learned, into a file with
    different syntax, inverts. The parser drops comments correctly because it knows what a
    comment IS.
    """
    try:
        import yaml
    except ImportError:
        # Same refusal as matrix_entries(), and for a sharper reason here: without the parser
        # this function returns "no workflow runs it" for EVERY file, which reads exactly like
        # a repo where nothing is workflow-wired. That is a check silently skipping itself.
        sys.exit(
            "FAIL: PyYAML is unavailable, so workflow `run:` steps cannot be parsed.\n"
            "      Refusing to exit 0: without it this check cannot tell a file that runs in\n"
            "      a workflow from one that runs nowhere, and would report the latter."
        )
    hits = []
    if not os.path.isdir(workflows_dir):
        return hits
    needle = "tests/" + base
    for name in sorted(os.listdir(workflows_dir)):
        if not name.endswith((".yml", ".yaml")):
            continue
        path = os.path.join(workflows_dir, name)
        try:
            with open(path) as fh:
                doc = yaml.safe_load(fh)
        except (OSError, yaml.YAMLError):
            # A workflow this cannot parse must NOT read as "no reference" — that direction
            # silently turns a wired file into an unwired one and then into a new exemption.
            # Surface it and keep going; the caller's other evidence still applies.
            print("WARN: could not parse %s — not counted as wiring" % path, file=sys.stderr)
            continue
        if not isinstance(doc, dict):
            continue
        for job in (doc.get("jobs") or {}).values():
            if not isinstance(job, dict):
                continue
            for step in (job.get("steps") or []):
                if isinstance(step, dict) and needle in str(step.get("run", "")):
                    hits.append((name, step.get("name", "<unnamed step>")))
    return hits


RUNNER = "run-local.sh"


def runner_lane_reference(base, tests_dir, workflows_dir=WORKFLOWS_DIR):
    """Is <base> run by tests/run-local.sh, in a lane where CI actually runs run-local.sh?

    A THIRD way to be wired, and it is transitive — which is why neither of the other two
    detectors can see it. `wiring_references()` reads flake.nix; `workflow_run_references()`
    matches "tests/<base>" inside a `run:` step. A battery invoked by run-local.sh is named
    NOWHERE in either place, so both answer "runs nowhere" while CI runs it every push.

    BOTH HALVES ARE REQUIRED AND THE SECOND IS THE LOAD-BEARING ONE. Until 2026-08-24 no
    workflow invoked run-local.sh at all, and in that world "run-local.sh runs it" was a
    statement about a developer's habits — the exact tier this file exists to push things out
    of. Being named in the runner is only wiring while the runner itself is in a lane, so this
    asks that question every run instead of trusting that it stays true. Delete the workflow
    step and these files correctly go back to being unwired debt.

    Comment-stripping here is DELEGATED, not rewritten. run-local.sh is bash, so cutting at the
    first `#` is correct — and it is the entire check, because a battery named only in the
    runner's header prose is not run by it. That rule already has exactly one implementation,
    in runner-coverage-contract.py, and a second copy here would be two spellings of one rule
    with nothing asserting they agree. That is the shape of this surface's fourth scar.
    """
    if not workflow_run_references(RUNNER, workflows_dir):
        return []
    runner_path = os.path.join(tests_dir, RUNNER)
    contract = os.path.join(tests_dir, "runner-coverage-contract.py")
    if not (os.path.exists(runner_path) and os.path.exists(contract)):
        return []
    import importlib.util
    spec = importlib.util.spec_from_file_location("_rcc", contract)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        code = mod.runner_code(runner_path)
    except Exception as exc:  # noqa: BLE001 - see below
        # Deliberately NOT silent, and deliberately NOT fatal. Returning [] on a broken import
        # would quietly reclassify every runner-wired battery as unwired debt, which is a loud
        # wrong answer; crashing would take down a check that has two other working detectors.
        print("WARN: could not consult %s (%s) — runner lane not counted" % (contract, exc),
              file=sys.stderr)
        return []
    return [(RUNNER, "invoked by the runner")] if base in code else []


def wiring_references(flake_src, tests_dir, base):
    """Lines of flake.nix that reference tests/<base> in a way that could RUN it.

    A MENTION IS NOT WIRING, and the first version of this check could not tell the difference.
    It asked whether the string "tests/<base>" appeared in flake.nix at all. That is satisfied by

        assert lib.assertMsg (builtins.pathExists ./tests/calendar-battery.py)
          "agentos-open-imports: calendar-open battery missing (tests/calendar-battery.py deleted?).";

    which proves the file EXISTS and runs nothing. All eight ambient-hand acceptance batteries
    (calendar, agos-calc, agos-sys, agos-files, agos-notes, agos-doc, agos-media, agos-web) are
    referenced by nothing else anywhere in the repo. They were invisible to the very check written
    to find tests that never run — the check counted its own guard's existence-assert as coverage.

    Note what the pathExists guard says about itself: it was added because "a module's acceptance
    BATTERY could be deleted and the build would stay green — same silent-degrade class as a
    dropped import." It catches DELETION. It cannot catch UN-INVOCATION, which is the same
    silent degrade with the file left in place to reassure the reader.

    RESIDUAL SCOPE, STATED. This is a line-level heuristic over nix SOURCE, not an evaluation.
    It excludes three shapes that provably cannot execute a file — a `builtins.pathExists` test,
    a line that is purely a quoted message, and a `#` COMMENT — and counts everything else as
    possible wiring.

    THE COMMENT EXCLUSION IS NEW (2026-08-23) AND THE OLD DOCSTRING'S ARGUMENT FOR OMITTING IT
    WAS SOUND FOR EXACTLY ONE OF THIS FUNCTION'S TWO CALLERS. It read: "a mention inside a `#`
    comment still counts, so this under-reports. It cannot over-report, which is the direction
    that matters: it will never call a wired test unwired." True — for the UNWIRED arm, where
    over-counting is the safe direction.

    But this function acquired a second caller, the STALE-EXEMPTION arm, where the direction is
    INVERTED: there, counting a comment mention as wiring makes an honest exemption look stale
    and turns CI red over a file nobody wired. It fired for real — PR #161 added a derivation
    whose COMMENT named `tests/run-local.sh` and `tests/vm-matrix-contract.py` while explaining
    the debt split, and flake-check went red claiming both exemptions were stale.

    This is #155's finding one level up: ONE RETURN VALUE SERVING TWO CALLERS WITH OPPOSITE
    CORRECT ANSWERS, and a failure-direction argument that was written about one of them. A
    comment cannot execute a file, so excluding it is right for both arms; the old behaviour was
    a safety margin in one direction that was a false positive in the other.
    """
    needle = f"{tests_dir}/{base}"
    hits = []
    for line in flake_src.splitlines():
        if needle not in line:
            continue
        stripped = line.strip()
        if "pathExists" in line:
            continue          # proves existence; executes nothing
        if stripped.startswith('"'):
            continue          # an assert's message string, not code
        if stripped.startswith("#"):
            continue          # a comment; it cannot run anything — see the docstring
        hits.append(stripped)
    return hits


# A battery that exits 0 when the thing it tests is absent. All eight ambient-hand batteries do
# this — `shutil.which("agos-calc")` returns None, the file prints "SKIP ... (image not built)"
# and exits 0 — and that is CORRECT for a manual at-a-box runner, where the alternative is a red
# that means nothing. It stops being correct the moment the file is wired into CI, because then a
# green check attests to nothing but the absence of the CLI it was written to exercise.
#
# THIS IS THE TRAP LAID FOR WHOEVER PAYS THE DEBT ABOVE, AND IT IS BAITED. Wiring one of these
# eight into `checks` is a two-line change that turns the check green, DELETES the entry from
# KNOWN_UNWIRED_DEBT (the ledger someone watches go down), and adds zero coverage. The debt would
# be paid on paper and the test would still run nowhere — the same defect, now with a passing
# badge and no line item. The docstring of this file calls that "present in every sense except
# the one that matters"; this is that sentence applied to its own remediation.
#
# So the debt list is NOT homogeneous, and the count alone hides the split. Measured 2026-08-23:
# Measured 2026-08-23: the list now stands at 15 — 8 self-disarming, 7 not. It moved twice that
# day and the two moves have opposite meanings: frontdoor-kick-battery.py was WIRED and removed
# (14 -> 13), then identity-battery.py and cap-sandbox-battery.sh were UNMASKED and added
# (13 -> 15) when wiring_references() stopped counting `#` comments, and then identity-battery.py
# was WIRED and removed (15 -> 14). Three moves, two directions: two paid, two revealed. Do not
# net them into one number — the netted figure (14, same as the morning) is the one reading that
# describes none of what happened.
# Wiring one of the non-self-disarming ones needs a derivation. Wiring one of the 8
# needs a derivation AND a guarantee its CLI is on PATH inside that derivation.
SELF_DISARM_WINDOW = 3
_SH_EXIT = re.compile(r"^\s*exit 0\s*(#.*)?$")


def self_disarms(path):
    """True if the file has a `sys.exit(0)` reachable on a "the tool is not here" path.

    HEURISTIC, AND DELIBERATELY NARROW. It requires a SKIP-announcing print within the three
    lines before the exit — the shape every one of the eight actually has. A plain `sys.exit(0)`
    at the end of a successful run does not match, which is the false-positive that would matter:
    this check's failure arm turns CI red, so it is tuned to under-report. A battery that
    self-disarms in some other spelling is missed here and stays missed, exactly as before.
    """
    try:
        with open(path) as fh:
            lines = fh.read().splitlines()
    except OSError:
        return False
    if any("AGENT_OS_STRICT" in l for l in lines):
        # The file has a strict mode: the skip is opt-out, and the derivation that wires it opts
        # out. Named by convention rather than proven here — this check reads source, it does not
        # evaluate the derivation, so it cannot confirm the env var is actually set. What it can
        # do is stop pointing at a file whose author has already answered the question.
        return False
    # For PYTHON files, only lines holding a REAL `sys.exit(0)` CALL count. The scan below is
    # textual, and text does not know what is code: on 2026-08-25 this check turned CI red on
    # tests/skip-exit-swallows-arms-contract.py, whose `print("SKIP")` / `sys.exit(0)` pairs are
    # triple-quoted FIXTURES -- the control arms of the battery written to catch this very shape.
    # A detector that reads its own test data as a confession. The docstring above tunes this
    # check to UNDER-report precisely because its failure arm turns CI red, and a false positive
    # here costs a wrong fix: the only ways out were an exemption entry (a suppression list
    # grown to hide a detector bug) or deleting the control arms (removing the evidence the
    # battery works). Both are worse than parsing. `.sh` has no parser here and keeps the text
    # scan, so this narrowing is Python-only and the shell arm is unchanged.
    code_exit_lines = None
    if path.endswith(".py"):
        try:
            tree = ast.parse("\n".join(lines))
        except SyntaxError:
            code_exit_lines = None   # unparseable: fall back to text rather than pass silently
        else:
            code_exit_lines = set()
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not node.args:
                    continue
                fn = node.func
                name = (fn.attr if isinstance(fn, ast.Attribute)
                        else fn.id if isinstance(fn, ast.Name) else None)
                if name != "exit":
                    continue
                a = node.args[0]
                if isinstance(a, ast.Constant) and a.value == 0:
                    code_exit_lines.add(node.lineno)

    for i, line in enumerate(lines):
        if code_exit_lines is not None and (i + 1) not in code_exit_lines:
            continue
        # Two spellings, because the check is named for a BEHAVIOUR and the behaviour is not
        # Python's. Shipped .py-only in #155 — its own disguise-8, a name wider than its scope,
        # in the check written to catch scope/claim mismatches. Swept the 12 shell batteries by
        # hand at the same time and found none, so this arm is dormant on today's tree; it is
        # here so the NEXT one is not found by hand.
        if "sys.exit(0)" not in line and not _SH_EXIT.match(line):
            continue
        window = lines[max(0, i - SELF_DISARM_WINDOW):i]
        if any("SKIP" in w for w in window):
            return True
    return False


# Phrases by which a file asserts that CI enforces it. Deliberately narrow: each one is a claim
# about EXECUTION, not a description of intent. "should run in CI" and "belongs in CI" are not
# here, because they describe a wish and a wish is not a false receipt.
CI_CLAIM_PATTERNS = (
    "runs in ci",
    "run in ci",
    "enforced in ci",
    "ci-enforced",
    "checked in ci",
    "goes red in ci",
)


def ci_claim_lines(path):
    """Lines in which this file claims CI executes it. Case-insensitive, substring."""
    try:
        with open(path) as fh:
            text = fh.read()
    except OSError:
        return []
    hits = []
    for i, line in enumerate(text.splitlines(), 1):
        low = line.lower()
        for pat in CI_CLAIM_PATTERNS:
            if pat in low:
                hits.append((i, line.strip()))
                break
    return hits


def false_ci_claims(tests_dir, flake_src):
    """Test files that CLAIM CI runs them while being wired into no lane.

    THE BUG THIS IS MADE OF, 2026-08-23. tests/bip340-battery.py opened with:

        Binding condition 2 of Geist's 2026-08-19 Path-A ruling: the FULL official test-vector
        set runs in CI, INCLUDING the must-fail verification vectors, control-armed.

    It ran in no lane, and had been on KNOWN_UNWIRED_DEBT the whole time. The repository held
    BOTH statements — "runs in CI" in the file's header, "runs nowhere" in the ledger — and
    nothing ever required them to be true at the same moment. Neither was hidden.

    A RULING CONDITION DISCHARGED BY WRITING A FILE IS DISCHARGED BY PROSE. The condition asks
    for an EXECUTION; the only evidence of one is a lane that goes red when it stops. For four
    days the header was read as the receipt — by me, while working down the very list that
    contradicted it.

    Note this is NOT subsumed by the unwired check above. That check exempts everything on
    KNOWN_UNWIRED_DEBT, which is exactly where a file like this hides: acknowledged as debt in
    one place while advertising itself as enforced in another. This arm has NO exemption list on
    purpose — a file may be unwired, and a file may claim CI runs it, but not both. Delete the
    claim or wire the file.

    SCOPE, stated rather than quietly chosen: tests/ only. modules/identity.py and
    modules/bip340.py carry the same kind of marker, but their claim is that some OTHER file
    asserts it, and mapping a module to its asserting battery means guessing. Both were checked
    by hand and by mutation on 2026-08-23 (strip the marker -> the battery goes red), and both
    batteries are now wired. A guessed mapping would make this arm fuzzy, and a fuzzy arm is one
    a human has to adjudicate, which puts it back in the tier it was written to escape.

    KNOWN LIMITATION, stated because hiding it is the failure this file is about: this is a
    SUBSTRING match and it cannot read negation. Written honestly, "I called this a CI-enforced
    arm. It is not." matches. That exact line exists in run-local.sh, and it is the reason the
    UNWIRED_BY_DESIGN skip above is a real fix rather than a convenient one — run-local.sh is a
    runner, so it is out of scope on the merits, not because the match was inconvenient.

    For an actual battery, a negated claim would still be a false hit. The remedy is to fix the
    prose, not to teach this function to parse English: a matcher that tries to decide which
    "not" applies to which clause is a matcher whose verdict a human must check, and that is the
    tier this arm exists to escape. Erring toward a loud false positive is the correct direction
    here — the opposite error is silence about a file advertising enforcement it does not have.
    """
    bad = []
    for suffix in TEST_SUFFIXES:
        for path in sorted(glob.glob(os.path.join(tests_dir, "*" + suffix))):
            base = os.path.basename(path)
            # UNWIRED_BY_DESIGN is skipped; KNOWN_UNWIRED_DEBT is NOT, and the asymmetry is the
            # whole design. "By design" means the file is not a test at all — run-local.sh is a
            # RUNNER — so prose in it is not a false receipt about its own execution. "Debt"
            # means it IS a test that runs nowhere, which is exactly the state that must never
            # coexist with a claim of CI enforcement.
            if base in UNWIRED_BY_DESIGN:
                continue
            claims = ci_claim_lines(path)
            if not claims:
                continue
            if (wiring_references(flake_src, tests_dir, base)
                    or workflow_run_references(base)
                    or runner_lane_reference(base, tests_dir)):
                continue
            bad.append((path, claims))
    return bad


def unwired_test_files(tests_dir, flake_path):
    """tests/*.nix files that flake.nix never references.

    THE GAP THIS CLOSES. The matrix check below compares the flake's `test-*` packages against
    the workflow matrix — but a test file that was never added to flake.nix at all has no
    package, so it is absent from BOTH sides and the comparison passes. The file exists, it is
    committed, it reviews as coverage, and it runs nowhere. That is the same failure as a
    missing matrix entry, one level further up, and it is the one that survives a check that
    only compares the two downstream lists to each other.

    Written after noticing the omission in this very file — see docs/cancelled-boundaries.md,
    which is a ledger of guards that did not cover what they appeared to.
    """
    try:
        with open(flake_path) as fh:
            flake_src = fh.read()
    except OSError as exc:
        sys.exit(f"FAIL: could not read {flake_path}: {exc!r}")

    present = set()
    unwired = []
    vacuous = []
    for suffix in TEST_SUFFIXES:
        for path in sorted(glob.glob(os.path.join(tests_dir, "*" + suffix))):
            base = os.path.basename(path)
            present.add(base)
            if base in UNWIRED_BY_DESIGN or base in KNOWN_UNWIRED_DEBT:
                continue
            # ANY of three lanes counts as running it: a flake derivation, an explicit
            # workflow step, or the runner — when a workflow runs the runner. Before 2026-08-23
            # only the first did, so a workflow-run contract had to be hand-exempted, which put
            # a genuinely-running file on the same list as files that run nowhere and made the
            # list stop meaning one thing. The third lane was added 2026-08-24 with the workflow
            # step that made it real; four batteries moved off the debt list on its strength.
            if not (wiring_references(flake_src, tests_dir, base)
                    or workflow_run_references(base)
                    or runner_lane_reference(base, tests_dir)):
                unwired.append(path)
            elif self_disarms(path):
                # Wired AND self-disarming: green proves the CLI was absent, nothing more.
                vacuous.append(path)

    # A STALE EXEMPTION IS ITSELF THE BUG THIS FILE IS ABOUT. An entry naming a file that no
    # longer exists, or one that has since been wired up, keeps a name on a suppression list
    # for no reason — and the next file to take that name inherits the exemption silently.
    # Both lists are checked, because both suppress.
    stale = []
    for base in sorted(UNWIRED_BY_DESIGN | KNOWN_UNWIRED_DEBT):
        if base not in present:
            stale.append((base, "no such file in %s/" % tests_dir))
        elif wiring_references(flake_src, tests_dir, base):
            stale.append((base, "is wired into %s now — remove the exemption" % flake_path))
        elif workflow_run_references(base):
            where = ", ".join("%s: %s" % h for h in workflow_run_references(base))
            stale.append((base, "is RUN by a workflow now (%s) — remove the exemption" % where))
        elif runner_lane_reference(base, tests_dir):
            stale.append((base, "is run by tests/%s, which CI now runs — remove the exemption"
                          % RUNNER))
    return unwired, vacuous, stale


def bare_top_level_bindings(pkgs_dir="modules/pkgs"):
    """modules/pkgs/*.nix whose body has a `name = value;` binding at top level.

    THE DEFECT THIS EXISTS FOR (e5d5c5c, 2026-08-24). Six hands were extracted out of NixOS
    modules into standalone package files. Three of them carried a helper binding along --
    `trafilatura = pkgs.python3Packages.trafilatura;` and friends -- which had been legal
    inside the module's `let` block and became a SYNTAX ERROR once emitted at the top level
    of a file that no longer had one. `error: syntax error, unexpected '=', expecting end of
    file`, four times, both lanes red.

    Why the extraction's own verification missed it: that commit diffed each extracted hand
    BYTE-IDENTICAL against `git show HEAD:`, and the diff was sliced from the
    `pkgs.writeShellApplication` token onward. The proof covered the hand exactly and excluded
    the prefix, which is the only region that broke. THE HALF YOU ARE NOT STARING AT -- the
    same class as the five scars in the mirror-tick guard, arriving in a different file.

    There is no `nix` binary on the surface this repo is built from, so nix syntax is
    unverifiable locally and CI is the only parser. That makes a cheap structural arm like
    this one worth more here than it would be somewhere `nix-instantiate --parse` is a
    keystroke away.
    """
    bad = []
    for path in sorted(glob.glob(os.path.join(pkgs_dir, "*.nix"))):
        try:
            with open(path) as fh:
                lines = fh.read().split("\n")
        except OSError as exc:
            bad.append((path, "unreadable: %r" % (exc,)))
            continue
        # everything after the argument line is the body
        argi = None
        for i, line in enumerate(lines):
            if re.match(r"^\{[^}]*\}:\s*$", line):
                argi = i
                break
        if argi is None:
            bad.append((path, "no `{ ... }:` argument line"))
            continue
        depth = 0
        for line in lines[argi + 1:]:
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                continue
            if depth == 0 and re.match(r"^[A-Za-z_][\w-]* = ", line):
                bad.append((path, "top-level binding outside any `let`: %s" % stripped[:60]))
                break
            if re.match(r"^\s*let\s*$", line) or stripped == "let":
                depth += 1
            elif stripped == "in" or stripped.startswith("in "):
                depth = max(0, depth - 1)
    return bad


# 39 -> 27 on 2026-08-24. THIS IS A UNIT CHANGE, NOT A COVERAGE CHANGE — read
# rc_assertion_census() before touching it. The old counter counted every `rc <op> N` anywhere
# in a battery, including `if` guards and `or` disjuncts that assert nothing; the new one counts
# only comparisons a check() can fail on. Not one assertion was deleted to get here. A ratchet
# whose units change must be restated in the new units, loudly, in the commit that changes them
# — quietly lowering it to get green is the move this whole file exists to make impossible.
RC_ASSERTION_FLOOR = 27


# Sentinels this detector has SEEN and that are known-explained. It may only go DOWN,
# and that is now ENFORCED by stale_sentinel_exemptions() rather than asserted here.
#
# IT TOOK A DAY AND A COMM FROM PAGE TO NOTICE, AND THE PRECEDENT WAS 460 LINES UP.
# `unwired_and_stale()` has carried "A STALE EXEMPTION IS ITSELF THE BUG THIS FILE IS ABOUT"
# in a comment for weeks, and checks BOTH of the older lists for entries that name a missing
# file or an already-wired one. I added a third suppression list to the same file and gave it
# a comment where the other two have a check. Proximity of precedent is not transfer of it:
# the rule was in scope, in the same module, and it still had to arrive from another surface.
#
# So: ANY new exemption list added below needs a stale-check before it needs entries.
# The one entry is fixed in PR #159 (`brain.do_tool = lambda *a, **k: fired.append((a, k))`,
# a sentinel that RECORDS instead of raising) and is listed here rather than tuned out of the
# detector, because an exemption you can read is a different object from a detector that
# quietly cannot see something.
KNOWN_UNMUTATED_SENTINELS = frozenset({
    ("frontdoor-kick-battery.py", "fired"),
})

_MUTATING_METHODS = frozenset({
    "append", "extend", "add", "update", "insert", "setdefault", "pop", "remove", "clear",
})


def _scan_sentinels(tests_dir=TESTS_DIR, with_invisible=False):
    """Names bound to an EMPTY collection (or to 0/False), asserted on, and mutated by nothing.

    The shape PR #159 found in frontdoor-kick-battery: `fired = []`, an executor stub that
    RAISED instead of recording, and `check("...nothing fired", ... and not fired)`. `not fired`
    was true at the moment of binding and true forever — the arm read as proof that no executor
    ran while being unable to observe an executor running at all. A sentinel that cannot be
    tripped is an assertion about nothing, which is this file's whole subject.

    DELIBERATELY UNDER-REPORTS, in three named ways, because its failure arm turns CI red:

      - A name passed as an ARGUMENT to any call is skipped. `deliver_once(root, sink=sink2)`
        mutates `sink2` from inside the callee, and `sink2 == []` there is a real assertion
        about a real double-fire. Two of these live in agos-comms-shadow-contract.py and they
        are correct; a detector that reds them would be trained away within a day.
      - Any REBINDING of the name counts as mutation, without checking what it was rebound to.
      - Only module- and function-level `check()` conditions are scanned for the assertion,
        the same discovery rule the rc census uses.
      - A `global`/`nonlocal` declaration counts as mutation without following where it goes.
    """
    hits = []
    invisible = set()
    for base in sorted(os.listdir(tests_dir)):
        if not base.endswith(".py") or base == "vm-matrix-contract.py":
            continue
        try:
            tree = ast.parse(open(os.path.join(tests_dir, base), encoding="utf-8").read())
        except (OSError, SyntaxError):
            continue
        empties, mutated, asserted, passed_as_arg = {}, set(), set(), set()
        for node in ast.walk(tree):
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)):
                name = node.targets[0].id
                val = node.value
                # 0 and False are the same sentinel wearing a different type: `seen = False`
                # under a stub that raises, `n = 0` under one that never increments. Added
                # 2026-08-24 after the collection version shipped, and today's tree has ZERO of
                # them — a MEASURED zero, not an assumed one: the probe was run against injected
                # instances first (a dead flag is reported; a flag that flips and a counter that
                # increments are both silent) before the number was believed.
                is_empty = (isinstance(val, (ast.List, ast.Set)) and not val.elts) or (
                    isinstance(val, ast.Dict) and not val.keys) or (
                    isinstance(val, ast.Constant) and val.value in (0, False)
                    and not isinstance(val.value, str))
                if is_empty and name not in empties:
                    empties[name] = node.lineno
                elif name in empties or not is_empty:
                    mutated.add(name)          # a rebinding is a mutation, unexamined
            if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
                mutated.add(node.target.id)
            # `global EX` / `nonlocal` hands the name to a function this walk cannot follow.
            # Every battery in this repo binds `EX = 0` and mutates it from inside check().
            if isinstance(node, (ast.Global, ast.Nonlocal)):
                mutated.update(node.names)
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Subscript) and isinstance(tgt.value, ast.Name):
                        mutated.add(tgt.value.id)
            if isinstance(node, ast.Call):
                if (isinstance(node.func, ast.Attribute) and node.func.attr in _MUTATING_METHODS
                        and isinstance(node.func.value, ast.Name)):
                    mutated.add(node.func.value.id)
                # An out-parameter is mutated where this file cannot see it.
                for arg in list(node.args) + [k.value for k in node.keywords]:
                    if isinstance(arg, ast.Name):
                        passed_as_arg.add(arg.id)
                if (isinstance(node.func, ast.Name) and node.func.id == "check"
                        and len(node.args) > 1):
                    for sub in ast.walk(node.args[1]):
                        if isinstance(sub, ast.Name):
                            asserted.add(sub.id)
        for name, lineno in sorted(empties.items()):
            if name in asserted and name not in mutated and name not in passed_as_arg:
                hits.append((base, lineno, name))
            elif name in asserted and name in passed_as_arg:
                # NOT a finding, and NOT evidence of a fix either — see stale_sentinel_exemptions().
                invisible.add((base, name))
    return (hits, invisible) if with_invisible else hits


def unmutated_sentinels(tests_dir=TESTS_DIR):
    """The live findings — every dead sentinel MINUS the ones on the exemption list."""
    return [h for h in _scan_sentinels(tests_dir)
            if (h[0], h[2]) not in KNOWN_UNMUTATED_SENTINELS]


def stale_sentinel_exemptions(tests_dir=TESTS_DIR):
    """Exemptions that no longer describe anything — and this is the half that was PROSE.

    `KNOWN_UNMUTATED_SENTINELS` carried the comment "It may only go DOWN" and a NOTE line
    saying so in the CI log, and NOTHING enforced either. An exemption is subtracted from the
    findings before they are printed, so a stale one is invisible BY CONSTRUCTION: the day
    PR #159 lands and `fired` starts recording, the entry stops describing a real defect and
    becomes a permanent hole in the detector that reads, in the log, as an accounted-for one.

    That is this file's own subject aimed at this file's newest ledger. `KNOWN_UNWIRED_DEBT`
    has a human who watches it shrink; a suppression list nobody watches only ever grows,
    because the cost of adding an entry is one line and the cost of removing one is noticing.

    So the ratchet is a CHECK, not a comment: an entry that no longer names a live dead
    sentinel fails the lane and must be deleted. Deleting it is the whole point.
    THE TWO DIRECTIONS WANT OPPOSITE BIASES, and that is the trap this function walked into.
    `_scan_sentinels()` deliberately UNDER-reports so its failure arm never reddens a correct
    assertion. On the findings side that bias is safe. On THIS side it inverts: a sentinel that
    becomes invisible to the scanner for an unrelated reason — someone passes `fired` to a
    helper, so the out-parameter rule drops it — is not a fixed sentinel, but a naive diff reads
    it as one and tells the author to DELETE an exemption still guarding a live defect. The
    under-report that protects one arm silently arms the other.

    Page found the same shape on their surface within the hour (2026-08-24, `pytest.mark.skip`):
    three exemptions reading "<module> archived" would have failed a naive existence check
    because orphaned `__pycache__` shells still sit on disk. Their rule, adopted: **a stale
    check needs a predicate for why the claim no longer holds, not merely the claim's absence.**

    So an arg-passed name counts as PRESENT here. Mutation and outright disappearance (file
    renamed or deleted) still count as stale, because those ARE evidence about the claim.
    """
    hits, invisible = _scan_sentinels(tests_dir, with_invisible=True)
    live = {(base, name) for base, _, name in hits} | invisible
    return sorted(e for e in KNOWN_UNMUTATED_SENTINELS if e not in live)


def rc_assertion_census(tests_dir=TESTS_DIR):
    r"""Count exit-code assertions across the ambient batteries, by PARSING, not grepping.

    This exists because I hand-counted the same number wrong twice in two hours, in OPPOSITE
    directions, and both times the wrong number went into a commit message and onto the bus.

      1. `grep 'rc == 0\|rc != 0'` reported ZERO assertions. There were three — all spelled
         `rc == 2`, which the pattern could not match. An instrument too NARROW to see the
         thing being counted.
      2. `grep -c 'rc *[=!]= *[0-9]'` then reported 24. The real figure was 18: the pattern
         matched the explanatory COMMENT I had just written about `rc == 2` assertions. An
         instrument too BROAD, counting prose about assertions as assertions.

    Both are the same defect — a census taken with a tool that cannot distinguish code from
    text about code — and the fix is not a better regex. It is to ask Python what is actually
    there. `ast` knows the difference between an expression and a comment; grep never will.

    The floor is enforced rather than printed. A NOTE nobody asserts is the shape this whole
    file exists to catch: it reads as a measurement while being unable to fail. Exit codes are
    now checked on every non-usage arm, and this number going DOWN means a check was deleted.
    """
    total = 0
    for base in sorted(os.listdir(tests_dir)):
        # MISTAKE 3, and it took two goes to actually retire. It began as `agos-*-battery.py`,
        # which could not see calendar-battery.py — an ambient-hand battery by every property
        # that matters (it drives agos-cal, it runs in a contract lane), invisible purely
        # because the hand's battery is not named after the hand. Widening to `*-battery.py`
        # fixed that INSTANCE and left the class alone: tests/ also holds ten `agos-*-contract.py`
        # files with 328 check() arms between them, and the census could not see any of them
        # either. On today's tree that costs nothing — they are source-inspection contracts that
        # assert the ABSENCE of subprocess, so they have no exit codes to assert and the total is
        # 27 under either rule (measured, not assumed). But the blind spot is the same one, and
        # the next contract file that drives a CLI would land in it silently.
        #
        # So discover by BEHAVIOUR, not by filename: a file that calls `check()` is a battery.
        # A naming convention is an enumeration wearing a discovery's clothes, and this
        # instrument has now been caught by that three times.
        if not base.endswith(".py") or base == "vm-matrix-contract.py":
            continue
        try:
            tree = ast.parse(open(os.path.join(tests_dir, base), encoding="utf-8").read())
        except (OSError, SyntaxError):
            continue
        # MISTAKE 4, 2026-08-24, and it is mistake 2 again one level in: `ast.walk` counts
        # EVERY `rc <op> N` in the file, wherever it sits. An `if rc == 0:` guard is control
        # flow. `added_ok = (...) or rc == 0` is an assignment — and worse, a DISJUNCT, which
        # cannot fail the arm it feeds no matter what rc is. Neither asserts anything, and both
        # were in the count. The floor was 39 and the true number of exit-code ASSERTIONS was
        # 27; the gap was eleven uses of `rc` that this instrument was reading as claims about
        # rc. It surfaced the honest way: deleting one tautological disjunct (5833bf9, a
        # `("ok" in out) or rc == 0` sitting under an arm that already asserted `rc == 0`)
        # dropped the count below the floor and turned CI red for improving the test.
        #
        # An assertion is a condition a `check()` will FAIL on. So: walk only `check()`'s
        # condition argument, and refuse anything beneath an `or` — a disjunct is satisfiable
        # without the rc comparison ever being true, which is exactly the arm just deleted.
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "check" and len(node.args) > 1):
                total += _rc_assertions_in(node.args[1])
    return total


def _rc_assertions_in(cond, under_or=False):
    """Exit-code comparisons in `cond` that can actually fail the check.

    `and` is transparent (every conjunct must hold, so an rc comparison inside one is load-
    bearing); `or` is opaque (the arm passes on the other side, so the comparison is not a
    claim). Anything else — a call, a subscript, a comprehension — is walked through, because
    the shape a future battery uses is not knowable from here and the alternative is mistake 1:
    an instrument too narrow to see the thing it counts.
    """
    if (isinstance(cond, ast.Compare) and isinstance(cond.left, ast.Name)
            and cond.left.id == "rc"):
        return 0 if under_or else 1
    if isinstance(cond, ast.BoolOp):
        nested = under_or or isinstance(cond.op, ast.Or)
        return sum(_rc_assertions_in(v, nested) for v in cond.values)
    return sum(_rc_assertions_in(c, under_or) for c in ast.iter_child_nodes(cond))


def flake_test_packages(system):
    """Attribute NAMES only — this evaluates the package set's keys, it builds nothing."""
    proc = subprocess.run(
        ["nix", "eval", "--json", f".#packages.{system}", "--apply", "builtins.attrNames"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        sys.exit(f"FAIL: `nix eval` of packages.{system} failed:\n{proc.stderr.strip()}")
    return json.loads(proc.stdout)


def main():
    ap = argparse.ArgumentParser()
    # Injection points exist ONLY so the failing arm can be exercised without corrupting the
    # repo. The doctrine this file serves says a guard whose red path has never been seen is a
    # comment with a CI badge, so the guard ships with a way to see it red.
    ap.add_argument("--workflow", default=WORKFLOW)
    ap.add_argument("--flake", default=FLAKE)
    ap.add_argument("--tests-dir", default=TESTS_DIR)
    ap.add_argument("--pkgs-dir", default="modules/pkgs",
                    help="package dir to scan for top-level bindings; the failing "
                         "arm is exercised by pointing this at a fixture")
    ap.add_argument("--packages-json", default=None,
                    help="JSON array of package names, for testing the failing arm")
    args = ap.parse_args()

    names = json.loads(args.packages_json) if args.packages_json else flake_test_packages(SYSTEM)
    tests = {n for n in names if n.startswith("test-")}
    matrix = matrix_entries(args.workflow)

    unlisted = sorted(tests - matrix)
    dangling = sorted(matrix - tests)
    unwired, vacuous, stale = unwired_test_files(args.tests_dir, args.flake)
    try:
        with open(args.flake) as _fh:
            _flake_src = _fh.read()
    except OSError as exc:
        sys.exit(f"FAIL: could not read {args.flake}: {exc!r}")
    false_claims = false_ci_claims(args.tests_dir, _flake_src)
    bare_bindings = bare_top_level_bindings(args.pkgs_dir)
    rc_count = rc_assertion_census(args.tests_dir)
    rc_regressed = rc_count < RC_ASSERTION_FLOOR
    dead_sentinels = unmutated_sentinels(args.tests_dir)
    stale_exemptions = stale_sentinel_exemptions(args.tests_dir)

    if (not unlisted and not dangling and not unwired and not vacuous and not stale
            and not false_claims and not bare_bindings and not rc_regressed
            and not dead_sentinels and not stale_exemptions):
        print(f"OK: {len(tests)} test-* package(s), all present in the vm-tests matrix:")
        for name in sorted(tests):
            print(f"  {name}")
        print(f"OK: every {'/'.join(TEST_SUFFIXES)} in {args.tests_dir}/ is referenced by "
              f"{args.flake}, exempt by design, or on the known-debt list.")
        print(f"NOTE: {len(KNOWN_UNWIRED_DEBT)} known-unwired batter(ies) still run in NO CI lane "
              f"(KNOWN_UNWIRED_DEBT). This number may only go down.")
        disarming = sorted(b for b in KNOWN_UNWIRED_DEBT
                           if self_disarms(os.path.join(args.tests_dir, b)))
        print(f"      {len(disarming)} of them ALSO self-disarm (exit 0 when their CLI is absent),"
              f" so wiring one is not enough on its own — see self_disarms().")
        for base in sorted(KNOWN_UNWIRED_DEBT):
            print(f"  DEBT {base}" + ("  [self-disarming]" if base in disarming else ""))
        print(f"OK: no sentinel (collection, 0, or False) is asserted on while nothing can trip it "
              f"({len(KNOWN_UNMUTATED_SENTINELS)} known-explained, and every one of those still "
              f"names a LIVE finding — the 'may only go down' is enforced, not printed).")
        print(f"NOTE: {rc_count} exit-code assertion(s) across the ambient batteries "
              f"(floor {RC_ASSERTION_FLOOR}). This number may only go UP. Counted by parsing, "
              f"not grepping — see rc_assertion_census().")
        return 0

    if stale_exemptions:
        print("FAIL: KNOWN_UNMUTATED_SENTINELS entries that no longer name a live finding:",
              file=sys.stderr)
        for base, name in stale_exemptions:
            print(f"  {base}  `{name}`", file=sys.stderr)
        print("      Either the sentinel was fixed (good — DELETE the entry) or the file was",
              file=sys.stderr)
        print("      renamed or deleted. A suppression that describes nothing is a permanent",
              file=sys.stderr)
        print("      hole in the detector that reads, in this log, as an accounted-for one.",
              file=sys.stderr)

    if dead_sentinels:
        print("FAIL: sentinel collection(s) asserted on that NOTHING can ever mutate:",
              file=sys.stderr)
        for base, lineno, name in dead_sentinels:
            print(f"  {base}:{lineno}  `{name}`", file=sys.stderr)
        print("      The arm reads as proof that the thing never happened. It is true at the",
              file=sys.stderr)
        print("      moment of binding and true forever, and cannot observe the thing happening",
              file=sys.stderr)
        print("      at all. Make the stub RECORD instead of raise — see PR #159.", file=sys.stderr)

    if rc_regressed:
        print(f"FAIL: exit-code assertions dropped to {rc_count}, below the floor of "
              f"{RC_ASSERTION_FLOOR}.", file=sys.stderr)
        print("      A deleted rc check is invisible in a diff review and silent in CI: the arm",
              file=sys.stderr)
        print("      that remains still reads stdout and still prints PASS. `agos-notes list`",
              file=sys.stderr)
        print("      printed a valid [] and exited 1 for as long as nobody asserted the code.",
              file=sys.stderr)

    if false_claims:
        print("FAIL: test file(s) that CLAIM CI runs them while being wired into no lane:",
              file=sys.stderr)
        for path, claims in false_claims:
            print(f"  {path}", file=sys.stderr)
            for lineno, text in claims:
                print(f"    line {lineno}: {text}", file=sys.stderr)
        print("  -> wire it, or delete the claim. Not both states at once.", file=sys.stderr)
        print("     A ruling condition discharged by writing a file is discharged by prose:",
              file=sys.stderr)
        print("     the condition asks for an EXECUTION, and the only evidence of one is a lane",
              file=sys.stderr)
        print("     that goes red when it stops. Being on KNOWN_UNWIRED_DEBT does NOT exempt a",
              file=sys.stderr)
        print("     file here — acknowledged-as-debt while advertising itself as CI-enforced is",
              file=sys.stderr)
        print("     precisely the state this arm exists to make unreachable.", file=sys.stderr)
    if stale:
        print("FAIL: stale exemption(s) in this file — a suppression list that names things no",
              file=sys.stderr)
        print("      longer true silently exempts whatever takes the name next:", file=sys.stderr)
        for base, why in stale:
            print(f"  {base}: {why}", file=sys.stderr)
    if unwired:
        print(f"FAIL: test file(s) in {args.tests_dir}/ that {args.flake} never references —",
              file=sys.stderr)
        print("      these have no package, so they run NOWHERE and no other check sees them:",
              file=sys.stderr)
        for path in unwired:
            print(f"  {path}", file=sys.stderr)
        print(f"  -> wire each into {args.flake} as a test-* package and add its matrix entry,",
              file=sys.stderr)
        print("     or, if it is a shared helper rather than a test, add it to",
              file=sys.stderr)
        print("     UNWIRED_BY_DESIGN in this file so the exemption is visible in a diff.",
              file=sys.stderr)
    if bare_bindings:
        print("FAIL: package file(s) with a binding at top level, outside any `let` — this is",
              file=sys.stderr)
        print("      a nix SYNTAX ERROR (unexpected '=', expecting end of file), and there is",
              file=sys.stderr)
        print("      no nix binary here to catch it, so CI is the only other parser:",
              file=sys.stderr)
        for path, why in bare_bindings:
            print(f"  {path}: {why}", file=sys.stderr)
        print("  -> wrap the helper binding(s) in `let ... in`, or inline them.",
              file=sys.stderr)
    if vacuous:
        print(f"FAIL: test file(s) wired into {args.flake} that EXIT 0 when the thing they test",
              file=sys.stderr)
        print("      is absent — a green here proves the CLI was missing, not that it works:",
              file=sys.stderr)
        for path in vacuous:
            print(f"  {path}", file=sys.stderr)
        print("  -> guarantee the CLI is on PATH inside the derivation, and make the battery",
              file=sys.stderr)
        print("     FAIL rather than skip when it is not. Wiring alone pays the debt on paper.",
              file=sys.stderr)
    if unlisted:
        print("FAIL: test-* package(s) with NO vm-tests matrix entry — these NEVER RUN in CI:",
              file=sys.stderr)
        for name in unlisted:
            print(f"  {name}", file=sys.stderr)
        print(f"  -> add each to the matrix in {args.workflow}", file=sys.stderr)
    if dangling:
        print("FAIL: vm-tests matrix entr(ies) naming no such package:", file=sys.stderr)
        for name in dangling:
            print(f"  {name}", file=sys.stderr)
        print("  -> the job would fail at `nix build` with an attribute error", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
