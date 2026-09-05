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
import ast
import glob
import re
import json
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
    "run-local.sh",           # the manual at-a-box runner; it INVOKES batteries, it is not one
})

# WIRED, BUT BY A WORKFLOW STEP RATHER THAN A flake.nix PACKAGE — and the claim is CHECKED.
# (Geist, gate on #184, 2026-08-27.) Until now "invoked directly by flake-check.yml" was a
# comment beside a name in UNWIRED_BY_DESIGN: a prose exemption. Delete the workflow step and
# the comment stays true-looking while the battery stops running — the exact shape of the debt
# this file exists to catch, wearing the exemption list as a coat. So the mapping is data, and
# `unwired_tests()` fails unless a NON-COMMENT line of the named workflow references the file.
# #184's battery tripped this check on the commit that added it, wired into flake-check.yml
# exactly as the ledger demands — the ledger simply had no vocabulary for that kind of wiring.
WIRED_VIA_WORKFLOW = {
    "vm-matrix-contract.py":        "flake-check.yml",  # this file
    # The call-site arms for the file above: it plants a fault in a COPY of the tree and reads
    # the verdict off a subprocess, so it is wired exactly where its subject is and for the same
    # reason — nix is on PATH there, which is what lets the fixture run without --packages-json.
    "vm-matrix-call-site-arms.py":  "flake-check.yml",
    "flake-retry-decide-battery.sh": "flake-check.yml",  # the census emitter's battery; pure bash
    # The time-to-connect probe's battery. Same lane and same reason as the line above: pure
    # bash, no nix, no VM. Wired into flake-check rather than vm-tests DELIBERATELY — vm-tests
    # carries a paths filter, so a scripts-only change could green with the battery never run,
    # which is the shape this whole file exists to catch. It tripped this check on the commit
    # that added it (PR #211), which is the check doing its job: I had wired it to a workflow
    # step and never told the registry, and "wired somewhere I can see" is not "wired where the
    # ledger can see." The claim below is CHECKED against a non-comment line of that workflow.
    "vm-connect-probe-battery.sh": "flake-check.yml",
    # The installer flake-pin freshness check (PR #234). It CANNOT be a flake.nix test-* package
    # and that is not a preference: it asserts the pin is an ANCESTOR of main and measures commit
    # DISTANCE, both of which need git history — and a nix build sandbox has no .git at all. Wired
    # into flake-check.yml, which fetches origin/main explicitly before running it. It tripped
    # THIS check on the commit that added it, exactly as the two entries above did; a check about
    # controls that never run, caught by the control that catches controls that never run.
    "pin-freshness.sh": "flake-check.yml",
    # The personal-data gate's battery (PR #222). Pure bash, no nix, no VM — same lane as the
    # two above. Wired into its OWN workflow rather than flake-check.yml because the gate is a
    # publication control: its workflow runs on pull_request AND on push to main, so the battery
    # is exercised on exactly the events where a leak could land. It has no paths filter, for the
    # reason stated three entries up.
    #
    # This entry exists because the contract check FAILED on the commit that added the battery,
    # which is the check doing its job and is worth recording rather than quietly fixing: I had
    # wired the battery into CI and satisfied myself it ran, and "wired somewhere I can see" is
    # still not "wired where the ledger can see." A gate whose own battery is invisible to the
    # repo's test registry is one workflow deletion away from being decorative — the same shape
    # the gate itself guards against, aimed back at me on my first commit.
    "personal-data-gate-battery.sh": "personal-data-gate.yml",
    # Migrated here on the #163 merge (2026-08-27). This branch had independently grown a
    # WORKFLOW_INVOKED frozenset + _named_in_any_workflow() making the SAME claim — filed 08-23,
    # four days before #184 landed WIRED_VIA_WORKFLOW. Convergence, not conflict. Keeping both
    # would be two registries for one rule, which is this file's own recurring scar (reader and
    # writer spelling one rule in two languages, with nothing making them agree). Main's is
    # strictly stronger on both axes mine was weak: it names WHICH workflow rather than accepting
    # any, and it ignores `#` lines rather than counting a comment as wiring. So mine goes.
    "flake-input-provenance-contract.py": "flake-check.yml",  # text contract on flake.lock
    # Its control arms — docstring prose until 2026-08-27, now executed. Wired in the same
    # commit that made them run: a battery landing unwired is the bug one line up.
    "flake-input-provenance-battery.sh": "flake-check.yml",
    # FIRST ENTRY EVER TAKEN OFF known-unwired-debt.txt. Debt paid, not re-labelled: the
    # baseline shrinks in this same commit, which is what the ratchet demands and what makes
    # the diff to that file the reviewable act.
    "frontdoor-kick-battery.py": "flake-check.yml",
    # Second entry off the ledger (13->12 was #187, this is 12->11). Host-side python, no nix,
    # no VM, no network. Checked before wiring, because member 16's third fix is that nobody had
    # asked what a WIRED battery does when its subject is absent — two of them exited 0. This one
    # exits 1 both ways, and the two ways are not the same evidence: subject absent gives a
    # FileNotFoundError traceback (red-by-crash), a broken arm gives "transport-battery: FAIL"
    # (red-by-verdict). CI cannot tell them apart from the exit code alone; a reader of the log
    # can. Wiring it is still right — a red for the wrong reason beats no red at all — but the
    # distinction is the same one #160 records, so it is written down rather than assumed away.
    "transport-battery.py": "flake-check.yml",
    # Third entry off the ledger (11->10). The pair-mate of the line above, and the pair the
    # bounded predicate below was written to protect — wiring it is the first time that fix has
    # a live customer on BOTH names at once rather than one.
    #
    # Subject-absent checked before wiring, as with the entry above: it is red-by-CRASH
    # (FileNotFoundError from py_compile on modules/agent-brain.py), while a broken arm is
    # red-by-VERDICT ("FAIL B. env://... rejected" + "anthropic-transport-battery: FAIL").
    # Both exit 1; only the log separates them.
    "anthropic-transport-battery.py": "flake-check.yml",
    # Fourth and fifth off the ledger (10->8), taken on Geist's steer: of the ten remaining,
    # EIGHT self-disarm (exit 0 when their CLI is absent), so wiring one of those buys a green
    # that proves nothing until the disarm is fixed first. These two are the ones that do not.
    # Verified independently rather than taken on faith — both exit 1 with their subject absent.
    #
    # Both are red-by-CRASH when the subject is gone and red-by-VERDICT on a broken arm, the
    # same split recorded on the two entries above. One texture worth naming, because it is a
    # third thing and not a restatement: audit-signing's subject-absent crash lands on
    # `open(LOG)` for a scratch-dir audit.log that the MISSING BINARY NEVER WROTE. The
    # traceback names the symptom, not the cause. A reader debugging that red goes looking in
    # /tmp for a log file, not in bin/ for the binary. Red is still better than no red, but
    # "it went red" and "the red points at the fault" are not the same claim.
    "audit-signing-battery.py": "flake-check.yml",
    "escalate-consent-battery.py": "flake-check.yml",

    # THE FIRST SELF-DISARMING BATTERY TO BE WIRED, AND IT TOOK THREE COMMITS, NOT ONE.
    # #193 gave it a strict gate so it can refuse to exit 0 when khal is absent; #194 fixed the
    # two defects that made it red WITH khal present (a khal.conf that never declared the date
    # format the battery constructs, and `out[:4] == "20"`, a four-char slice against a two-char
    # literal that no input could satisfy). Only now is wiring it worth anything: the step below
    # installs khal AND sets AGENT_OS_STRICT=1, so a runner without the backend goes red by name
    # instead of announcing SKIP and passing. That combination is what strict_callers_unarmed()
    # below exists to keep true — the battery half was checkable from here already, the caller
    # half was not, and a wired-but-unarmed step buys back the exact green the gate removed.
    "calendar-battery.py": "flake-check.yml",

    # THE SECOND, AND THE LAST ONE THE LEDGER CAN GIVE UP WITHOUT THE IMAGE. Its step stages
    # qalc via `nix shell nixpkgs#libqalculate` and sets AGENT_OS_STRICT=1, same pair as
    # calendar's. The six names left below are NOT a queue behind it: each does a bare
    # `shutil.which("agos-X")` with no second binary to fall back to, so strict there is a
    # guaranteed red outside the image lane. They are blocked on the image, not the pattern —
    # a count of seven read as a backlog and was not one.
    "agos-calc-battery.py": "flake-check.yml",
}

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
    # The six remaining ambient-hand acceptance batteries. Each is named in flake.nix by
    # exactly one `builtins.pathExists` assert and its error string, and by nothing else in the
    # repository. The guard that names them proves they have not been DELETED; nothing proves
    # they RUN. calendar-battery.py was the eighth and agos-calc-battery.py the seventh; both
    # left this list in the commit that wired them.
    #
    # THESE SIX ARE NOT A QUEUE. Each does `shutil.which("agos-X")` and has NO second binary,
    # so there is nothing a workflow step could stage and strict on them would be a guaranteed
    # red outside the image lane. They are blocked on the built image (Dell gate), not on the
    # strict pattern — which is why the two that left did so out of order with this list.
    "agos-sys-battery.py",
    "agos-files-battery.py",
    "agos-notes-battery.py",
    "agos-doc-battery.py",
    "agos-media-battery.py",
    "agos-web-battery.py",
    # Found in the first sweep (#153): referenced only by tests/run-local.sh, a manual runner.
    # bip340-battery.py was here until today, and it is the entry that should NOT have been a
    # routine line on this list. Its own header states, as fact, that it satisfies "binding
    # condition 2 of Geist's 2026-08-19 Path-A ruling: the FULL official test-vector set runs
    # in CI." It ran in no lane. The repo held both claims at once -- "runs in CI" in the file,
    # "runs nowhere" in this list -- and nothing ever made them meet.
    #
    # A RULING CONDITION DISCHARGED BY WRITING A FILE IS DISCHARGED BY PROSE. Condition 2 asks
    # for an EXECUTION; the only evidence of one is a lane that goes red when it stops. The
    # header was read as the receipt.
    #
    # Now wired as `bip340-contract` in flake.nix. Note what specifically had not been running:
    # the must-fail vectors (5-15) and check I's control arm, i.e. the forgery-acceptance
    # coverage the ruling singled out -- a verifier returning True unconditionally passes every
    # TRUE vector, so the unrun half was exactly the half that matters.
})

# NON-FILE DEBT, recorded here because this is the ledger people read, and NOT added to the set
# above because the set means "a test file exists and no lane runs it". This debt has no test file
# at all, so listing it would make the count claim coverage that was never written. A ledger whose
# entries mean two different things is a ledger nobody can act on.
#
# PARTICIPANTS_DIR IS NOT MODE-CHECKED. Geist, 2026-08-27 (P3, gate on #172). As of #172 the boot
# self-test's reference point is `participants/<name>.md` — the recorded npub — but `preflight()`
# stats only KEYS_DIR and the `*.key` modes. PARTICIPANTS_DIR is created 0o755 and never checked.
#
# Why this is P3 and not a blocker, stated so the next reader does not have to re-derive it: a
# principal with registry-write but not key-write can make every boot refuse with IDENTITY DRIFT.
# That is fail-CLOSED and loud — a denial, not a bypass. A principal with key-write already owns
# the box. The trust class is unchanged by #172; what changed is that a second path can now cause
# a loud refusal.
#
# The real question underneath is spec 2.1's: re-attestation, and whether the registry should be
# SIGNED rather than merely mode-guarded. Mode-guarding participants/ would close the denial path
# without answering that, which is why this is recorded rather than patched. Owned by whoever
# builds the RULING_CONDITIONS table (ruled item 3).


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
    It excludes two shapes that provably cannot execute a file — a `builtins.pathExists` test and
    a line that is purely a quoted message — and counts everything else as possible wiring. A
    mention inside a `#` comment still counts, so this under-reports. It cannot over-report,
    which is the direction that matters: it will never call a wired test unwired.
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
# 8 of the 14 self-disarm, 6 do not. Wiring one of the 6 needs a derivation. Wiring one of the 8
# needs a derivation AND a guarantee its CLI is on PATH inside that derivation.
# A READ, NOT A MENTION. `os.environ.get("AGENT_OS_STRICT")` and `os.environ["AGENT_OS_STRICT"]`
# are the two spellings that actually consult the variable; prose about it consults nothing.
_STRICT_READ = re.compile(r'os\.environ(?:\.get\(|\[)\s*["\']AGENT_OS_STRICT["\']')


def reads_strict_env_lines(lines):
    """True if any NON-COMMENT line reads AGENT_OS_STRICT from the environment."""
    return any(_STRICT_READ.search(l) for l in lines if not l.lstrip().startswith("#"))


def reads_strict_env(path):
    try:
        with open(path) as fh:
            return reads_strict_env_lines(fh.read().splitlines())
    except OSError:
        return False


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
    if reads_strict_env_lines(lines):
        # The file has a strict mode: the skip is opt-out, and the caller that wires it opts out.
        # Named by convention rather than proven here — this check reads source, it does not run
        # the step, so it cannot confirm the env var is actually set. What it CAN do is stop
        # pointing at a file whose author has already answered the question, and separately
        # check the caller (strict_callers_unarmed(), below).
        #
        # THIS WAS A TOKEN SCAN UNTIL THIS COMMIT AND THAT WAS A DEFECT WITH A DEMONSTRATION.
        # Geist appended `# TODO: consider AGENT_OS_STRICT` to main's calendar-battery.py and
        # self_disarms() returned False: a COMMENT claiming to think about a strict mode
        # disarmed the check that checks disarming. The exemption now requires the shape of a
        # READ on a non-comment line, and the selftest below carries the comment-only mention as
        # its control arm — without that arm this tightening could be reverted and every case
        # would still pass.
        return False
    for i, line in enumerate(lines):
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



def _named_on_a_running_line(lines, base):
    """True if `base` appears on a line that is not a comment. Prose does not run.

    One predicate, two callers (the WIRED_VIA_WORKFLOW verifier and the debt-staleness sweep).
    Two spellings of one rule with nothing making them agree is this file's recurring scar.
    """
    # BOUNDED, not a substring test. `transport-battery.py` is a suffix of
    # `anthropic-transport-battery.py`; a bare `base in ln` would let a line wiring the longer
    # file satisfy a WIRED_VIA_WORKFLOW claim for the shorter one — a claim passing on a file
    # that runs nowhere, the silent direction. Found at the #187 gate (Geist); both names sat on
    # the debt ledger so nothing had tripped it yet.
    pat = re.compile(r"(?<![A-Za-z0-9_.-])" + re.escape(base) + r"(?![A-Za-z0-9_])")
    return any(pat.search(ln) and not ln.lstrip().startswith("#") for ln in lines)


def _wired_by_any_workflow(base, tests_dir):
    """True if ANY workflow names `base` on a running line.

    Deliberately ANY rather than a named workflow: this answers "is this exemption stale",
    and an exemption is stale the moment the file runs ANYWHERE. WIRED_VIA_WORKFLOW asks the
    stricter question (does the workflow it CLAIMS actually run it) and keeps its own check.
    """
    workflows_dir = os.path.join(os.path.dirname(os.path.abspath(tests_dir)), ".github", "workflows")
    for wf in sorted(glob.glob(os.path.join(workflows_dir, "*.yml"))
                     + glob.glob(os.path.join(workflows_dir, "*.yaml"))):
        try:
            with open(wf) as fh:
                lines = fh.read().splitlines()
        except OSError:
            continue
        if _named_on_a_running_line(lines, base):
            return True
    return False

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
            if base in UNWIRED_BY_DESIGN or base in KNOWN_UNWIRED_DEBT or base in WIRED_VIA_WORKFLOW:
                continue
            if not wiring_references(flake_src, tests_dir, base):
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
        elif _wired_by_any_workflow(base, tests_dir):
            # THE EXEMPTION LISTS WERE ONLY EVER CHECKED AGAINST flake.nix. A file wired by a
            # WORKFLOW stayed on the debt ledger with nothing objecting — and workflow wiring is
            # how every battery in this repo actually runs, so the blind spot covered the normal
            # case rather than an exotic one. The ledger would have gone on claiming debt that
            # had already been paid, which understates progress in the harmless direction and,
            # in the harmful one, keeps a live suppression on a name: the NEXT file to take that
            # name inherits an exemption nobody granted it. That is this function's own docstring
            # turned on the list instead of the file. Found 2026-08-27 wiring the first entry OFF
            # this ledger — the check went silent at precisely the moment it had something to say.
            stale.append((base, "is wired by a workflow now — remove the exemption"))
    # A WORKFLOW-WIRING CLAIM IS VERIFIED, NOT TRUSTED: the named workflow must reference the
    # file on a line that is not a comment. A mention inside `# ...` is prose; prose does not run.
    workflows_dir = os.path.join(os.path.dirname(os.path.abspath(tests_dir)), ".github", "workflows")
    for base, wf in sorted(WIRED_VIA_WORKFLOW.items()):
        if base not in present:
            stale.append((base, "no such file in %s/" % tests_dir))
            continue
        if wiring_references(flake_src, tests_dir, base):
            stale.append((base, "is wired into %s now — remove it from WIRED_VIA_WORKFLOW" % flake_path))
            continue
        wf_path = os.path.join(workflows_dir, wf)
        try:
            with open(wf_path) as fh:
                lines = fh.read().splitlines()
        except OSError:
            stale.append((base, "claims wiring via %s, which cannot be read" % wf_path))
            continue
        if not _named_on_a_running_line(lines, base):
            stale.append((base, "claims wiring via %s but NO non-comment line there names it — "
                                "the battery runs NOWHERE" % wf))
    return unwired, vacuous, stale


DEBT_BASELINE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "known-unwired-debt.txt")


def _read_debt_baseline(path):
    """The recorded debt ledger. Missing file is a FAILURE, never a skip.

    A ratchet whose baseline can vanish is not a ratchet: `rm` would silently restore
    exactly the freedom the file exists to remove, and this check would go green while
    doing so. Same rule as flake-input-provenance's missing-lock case.
    """
    try:
        with open(path) as fh:
            text = fh.read()
    except OSError as exc:
        sys.exit(
            f"FAIL: the debt ratchet baseline {path} could not be read: {exc!r}\n"
            "      KNOWN_UNWIRED_DEBT is documented as may-only-shrink. That rule lives\n"
            "      in this file; without it the list is unbounded and this check would\n"
            "      pass while the debt grew. Refusing to exit 0."
        )
    return {ln.strip() for ln in text.splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")}


def debt_ratchet(debt, baseline_path=DEBT_BASELINE):
    """Compare KNOWN_UNWIRED_DEBT against the recorded baseline.

    EQUALITY, not a count. A count alone permits a silent SWAP — remove one battery from
    the list, add a different one, total unchanged — which is precisely a new unwired test
    being made invisible while the ledger someone watches reads as flat.

    Growth is the violation. Shrinkage is the goal, but it still fails here, deliberately:
    the baseline must be updated in the SAME commit, or it goes stale and quietly re-permits
    every name it still holds. A stale exemption is the bug this file is already about.
    """
    recorded = _read_debt_baseline(baseline_path)
    added = sorted(debt - recorded)
    removed = sorted(recorded - debt)
    return added, removed


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


# ---------------------------------------------------------------------------------------------
# RULING_CONDITIONS — ruled item 3 (Geist, 2026-08-27)
#
# WHY THIS TABLE EXISTS, and it is one specific incident. `tests/bip340-battery.py` opened by
# stating as settled fact that it satisfied "binding condition 2 ... the FULL official test-vector
# set runs in CI." It ran in no lane for eight days, while THIS FILE's debt list simultaneously
# recorded it as running nowhere. The repo asserted both things at once and nothing made them meet.
# Geist's rule from that: A RULING CONDITION NAMING AN EXECUTION IS DISCHARGED ONLY BY A LANE THAT
# GOES RED. A header is not a receipt, and neither is a table — which is why every row below is
# checked against the same flake.nix the debt ledger is checked against, by the same function.
#
# THE ROW RULE, ruled: a row may say `enforced` ONLY with a run id. Anything else is `half` (one
# of two halves discharged) or `prose` (claimed but not executed anywhere). The status column is
# the claim; `run_ids` is the evidence; the checker below refuses to let them disagree.
RULING_CONDITIONS = (
    {
        "id": "condition-2",
        "ruling": "Geist 2026-08-19 Path-A",
        "claim": "the boot identity self-test executes, and the full official BIP-340 vector set "
                 "(INCLUDING must-fail vectors) runs in CI",
        "status": "enforced",
        "lanes": ("bip340-battery.py", "identity-boot.nix"),
        # Two halves, two runs, deliberately cited separately: they were discharged eight days
        # apart by different work and a single id would hide that.
        "run_ids": ("33033802300", "33035705963"),
        "note": "vectors-execute half: main flake-check 33033802300 (#170). Negative-arm half: "
                "main vm-tests 33035705963 (#172), leg 9 — a corrupted agent key now makes the "
                "boot self-test go red. Before #172 it went GREEN on a substituted key, because "
                "it verified against a pubkey DERIVED from the key file: a tautology.",
    },
    {
        "id": "condition-3",
        "ruling": "Geist 2026-08-19 Path-A",
        "claim": "the vendored non-timing-hardened BIP-340 implementation stays off any network "
                 "reach path; network exposure makes libsecp256k1 (Path B) mandatory",
        "status": "enforced",
        "lanes": ("bip340-exposure-contract.py",),
        "run_ids": ("33030523828",),
        "note": "Importer tripwire (#169). Sees IMPORTS, not shell-outs — the subprocess gap is "
                "recorded in that file's docstring as P3, deliberately not armed while no module "
                "on the reach path shells out.",
    },
)

_RUN_ID = re.compile(r"^\d{6,}$")
_VALID_STATUS = ("enforced", "half", "prose")


_BLOCK = re.compile(r"(?<![^\s])/\*.*?\*/", re.S)


def _blank_block_comments(src):
    """Replace every `/* ... */` span with spaces, PRESERVING newlines and column count.

    `#` is not Nix's only comment. A test named inside a block comment is prose that the
    line-start `#` filter cannot see, and prose discharging a ruling condition is the exact
    failure the table one level up was built to end. flake.nix carries zero block comments as of
    2026-08-27, so this closes a LATENT hole — written against a synthetic source in the selftest
    rather than against a mention that happens to exist today.

    Blanking rather than deleting keeps line structure intact, so a wiring statement sharing the
    closing line (`*/ checks.x = ...`) survives. Nix block comments do not nest.

    AN UNTERMINATED `/*` IS NOT TREATED AS A COMMENT, and that is not the timid choice — it is
    the one the repo forced. The first version blanked from an unpaired `/*` to EOF, on the
    reasoning that over-stripping fails loudly. It did fail loudly, immediately: flake.nix line
    ~315 contains the shell glob `for f in ${./modules}/*.nix`, which is not a comment at all,
    and the whole file downstream of it went blank — CONTROL 1 of the selftest rejected a row
    that should pass. So the two cases are not symmetric here. A truly unterminated block comment
    makes flake.nix a syntax error that `nix` refuses to evaluate, so it cannot reach a green
    check; a bare `/*` inside a string demonstrably does exist. Pairing is required, and the glob
    line is pinned as a control arm below so this cannot regress into the version that was wrong.
    """
    return _BLOCK.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), src)


def executing_references(flake_src, tests_dir, base):
    """`wiring_references()` minus comment lines.

    STRICTER THAN THE DEBT LEDGER, ON PURPOSE, and the difference is load-bearing here.
    `wiring_references` counts a mention inside a `#` comment as possible wiring. For the debt
    ledger that bias is safe: it can only make the ledger UNDER-report debt, never call a wired
    test unwired. For a row claiming `enforced` the same bias is exactly wrong — it would let a
    ruling condition be discharged by a COMMENT MENTIONING THE TEST, which is the prose-as-receipt
    failure this table was built to end, one level up.

    This is not hypothetical in this repo: `tests/bip340-battery.py` is named on flake.nix line
    ~425 inside a comment block AND wired for real below it. A row citing it must pass on the
    second, not the first.
    """
    return [ln for ln in wiring_references(_blank_block_comments(flake_src), tests_dir, base)
            if not ln.lstrip().startswith("#")]


def check_ruling_conditions(flake_src, tests_dir, rows=None, debt=None):
    """Returns a list of failure strings; empty means every row is backed by what it claims."""
    rows = RULING_CONDITIONS if rows is None else rows
    debt = KNOWN_UNWIRED_DEBT if debt is None else debt
    problems = []
    for row in rows:
        rid = row.get("id", "<unnamed row>")
        status = row.get("status")
        run_ids = tuple(row.get("run_ids", ()))
        lanes = tuple(row.get("lanes", ()))

        if status not in _VALID_STATUS:
            problems.append(f"{rid}: status {status!r} is not one of {_VALID_STATUS}")
        if not lanes:
            problems.append(f"{rid}: names no lane at all, so nothing can ever make it go red")

        # THE ROW RULE.
        if status == "enforced" and not run_ids:
            problems.append(
                f"{rid}: says 'enforced' but cites NO run id. A condition is enforced by a run "
                f"that happened, not by a row that says so — mark it 'half' or 'prose'.")
        for r in run_ids:
            if not _RUN_ID.match(r):
                problems.append(
                    f"{rid}: run id {r!r} is not a run id. 'see CI' and 'green on main' are the "
                    f"prose this table replaces; cite the number so a reader can open it.")

        for base in lanes:
            path = os.path.join(tests_dir, base)
            if not os.path.exists(path):
                problems.append(f"{rid}: names {path}, which does not exist")
                continue
            if not executing_references(flake_src, tests_dir, base):
                problems.append(
                    f"{rid}: names {path}, which no lane in {FLAKE} executes. The condition is "
                    f"discharged by prose — this is the bip340-battery shape, again.")
            if status == "enforced" and base in debt:
                problems.append(
                    f"{rid}: says 'enforced' while {base} is on KNOWN_UNWIRED_DEBT, which is the "
                    f"repo asserting 'runs in CI' and 'runs nowhere' at the same time.")
    return problems


# THE CALLER HALF. #193 closed the battery's half of the self-disarm hole: a strict-gated
# battery knows what it needs and refuses to exit 0 without it. It could not close the caller's
# half — nothing inside a battery can verify that whoever ran it set the variable — and a wired
# step that forgets `AGENT_OS_STRICT: "1"` gets the old meaningless green back with no diagnostic
# at all. That hole was named in #193's own commit message as real, checkable from the workflow
# side, and not fixed there.
#
# It is checkable from HERE because this file already reads the workflow text for other reasons.
# One more predicate on the same read: for every battery that reads AGENT_OS_STRICT in code and
# is named on a running line of a workflow, the step that names it must arm it.
#
# WHAT THIS DOES NOT DO, stated so nobody reads it as more: it does not evaluate the workflow,
# does not resolve `${{ }}`, and does not know whether the CLI the battery wants is actually on
# PATH inside that step. It asserts one textual fact — the step that runs a strict-gated battery
# says so — which is exactly the fact whose absence is silent.
_STEP_START = re.compile(r"^\s*-\s+name:")
# Two spellings arm a step: the YAML `env:` form (`AGENT_OS_STRICT: "1"`) and the inline shell
# form on a run line (`run: AGENT_OS_STRICT=1 python3 ...`) — the latter is how flake.nix's own
# agent-loop-dispatch derivation spells it, so a workflow author copying that would be armed in
# fact and, under the `:`-only, end-anchored first version, reported unarmed. A trailing comment
# after the value was the same false red. Both were LOUD failures (a red on an armed step), never
# silent, which is why they are folded in here rather than filed — but a check that cries wolf on
# the reference spelling teaches people to add exemptions. The value must still be exactly 1:
# `"10"` and `1x` do not match (Geist gate fix, #195).
_ARMS_STRICT = re.compile(r"""AGENT_OS_STRICT\s*[:=]\s*["']?1["']?(?=\s|$)""")


def _workflow_steps(lines):
    """[(step_name, block_lines)] — crude, textual, and sufficient for the one fact asked of it."""
    starts = [i for i, l in enumerate(lines) if _STEP_START.match(l)]
    out = []
    for n, i in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        name = lines[i].split("name:", 1)[1].strip()
        out.append((name, lines[i:end]))
    return out


def _armed(block):
    return any(_ARMS_STRICT.search(l) for l in block if not l.lstrip().startswith("#"))


def strict_unarmed_in(lines, strict_bases):
    """[(base, step_name)] for steps that RUN a strict-gated battery without arming it.

    A job-level `env:` counts: it arms every step in the job, and refusing to see it would make
    this check demand a redundant line and be wrong about it.
    """
    steps = _workflow_steps(lines)
    covered = set()
    starts = [i for i, l in enumerate(lines) if _STEP_START.match(l)]
    if starts:
        covered = set(range(starts[0], len(lines)))
    job_level = [l for i, l in enumerate(lines) if i not in covered]
    if any(_ARMS_STRICT.search(l) for l in job_level if not l.lstrip().startswith("#")):
        return []
    found = []
    for name, blk in steps:
        if _armed(blk):
            continue
        for base in strict_bases:
            if _named_on_a_running_line(blk, base):
                found.append((base, name))
    return found


def wired_but_disarming(tests_dir):
    """[base] for WIRED_VIA_WORKFLOW batteries that STILL self-disarm — an ungated exit 0.

    FOUND BY A CONTROL THAT CAME BACK GREEN. Reverting `strict = os.environ.get(...)` to
    `strict = False` in a wired battery left the whole tree passing, and the same mutation
    on calendar-battery.py — wired since #195 — passed too, so the hole was PRE-EXISTING and
    not introduced by the battery that exposed it.

    The mechanism is that `self_disarms()` was only ever consulted over KNOWN_UNWIRED_DEBT,
    in the OK-branch print loop. Wiring a battery therefore removed it from the only sweep
    that would have noticed its gate going away — the ledger check stopped watching the file
    at the exact moment the file started running in CI. A wired battery that self-disarms is
    strictly worse than an unwired one: it reports success in a lane somebody trusts.

    Nothing here re-checks the CALLER; that is strict_callers_unarmed(). This is the battery
    half, asked of the wired set instead of the debt set.
    """
    return sorted(b for b in WIRED_VIA_WORKFLOW
                  if self_disarms(os.path.join(tests_dir, b)))


def wired_disarm_selftest(tmpdir_lines=None):
    """Drive wired_but_disarming()'s predicate on files, not on the repo's current answer.

    The repo's real answer is [] and must stay [], so a check asserting only that would pass
    on a constant-empty implementation. These arms write two real files and call self_disarms()
    directly — the same function wired_but_disarming() maps over.
    """
    import tempfile
    failures = []
    gated = ('import os, sys\n'
             'if not shutil.which("x"):\n'
             '    strict = os.environ.get("AGENT_OS_STRICT") == "1"\n'
             '    if strict:\n'
             '        sys.exit("FAIL")\n'
             '    print("  SKIP thing: not here")\n'
             '    sys.exit(0)\n')
    ungated = ('import sys\n'
               'if not shutil.which("x"):\n'
               '    print("  SKIP thing: not here")\n'
               '    sys.exit(0)\n')
    with tempfile.TemporaryDirectory() as d:
        g = os.path.join(d, "gated.py"); open(g, "w").write(gated)
        u = os.path.join(d, "ungated.py"); open(u, "w").write(ungated)
        if self_disarms(g):
            failures.append("selftest: a STRICT-GATED battery was reported as self-disarming "
                            "— the exemption is broken and every wired battery would red")
        # THE ARM. Without it a self_disarms() that returned False unconditionally would pass
        # the line above and this whole check would be decorative.
        if not self_disarms(u):
            failures.append("selftest: an UNGATED skip-then-exit-0 was NOT reported as "
                            "self-disarming — wired_but_disarming() can no longer see the "
                            "shape it exists to catch")
    return failures


def flake_wired_batteries(tests_dir, flake_text):
    """Test files named in flake.nix that are in NEITHER ledger.

    `wired_but_disarming()` above sweeps WIRED_VIA_WORKFLOW, and the OK-branch print loop
    sweeps KNOWN_UNWIRED_DEBT. Those two sets are 10 of the 51 test files flake.nix names;
    the other 41 run as `nix flake check` derivations and were outside BOTH sweeps.

    LIMIT, stated so a green is not over-read: membership is decided by the basename appearing
    anywhere in flake.nix, the same substring notion the debt ledger's own comment uses. A file
    named only inside a comment would count as wired here. That over-INCLUDES, which is the safe
    direction for this check -- it can add a battery to the sweep that did not need it, never
    drop one that did.
    """
    return sorted(b for b in os.listdir(tests_dir)
                  if b.endswith((".py", ".sh"))
                  and b in flake_text
                  and b not in WIRED_VIA_WORKFLOW
                  and b not in KNOWN_UNWIRED_DEBT)


def flake_wired_but_disarming(tests_dir, flake_text):
    """[base] for flake.nix-wired batteries that self-disarm -- an ungated exit 0.

    The battery half of `wired_but_disarming()`, asked of the LANE THAT CARRIES THE BULK.
    That function's own docstring argues a wired battery which self-disarms "reports success
    in a lane somebody trusts" and is strictly worse than an unwired one. Every word of that
    applies to `nix flake check`, which is the lane the gate actually reads -- so the argument
    was already written down for a set it was not being asked about.

    The repo's answer is [] today and I verified that BEFORE writing this, by running
    `self_disarms()` over all 43 unswept files and getting zero. So this is NOT a live defect;
    it is the same shape as #251 and #252 -- a real guarantee that nothing was watching. The
    hole in `wired_but_disarming()` was itself FOUND BY A CONTROL THAT CAME BACK GREEN (see
    its docstring), which is the reason not to wait for this one to go red on its own.
    """
    return sorted(b for b in flake_wired_batteries(tests_dir, flake_text)
                  if self_disarms(os.path.join(tests_dir, b)))


def flake_wired_disarm_selftest():
    """Drive the predicate and the MEMBERSHIP on files, not on the repo's current answer.

    Two failure modes, two arms. `wired_disarm_selftest()` already arms `self_disarms()`
    itself, so what is new here is the SET: a membership function that returned nothing would
    make this check pass forever with the predicate in perfect working order. That is the
    #246 shape -- the detector is fine, it is just never handed anything.
    """
    import tempfile
    failures = []
    ungated = ('import shutil, sys\n'
               'if not shutil.which("x"):\n'
               '    print("  SKIP thing: not here")\n'
               '    sys.exit(0)\n')
    gated = ('import os, shutil, sys\n'
             'if not shutil.which("x"):\n'
             '    if os.environ.get("AGENT_OS_STRICT") == "1":\n'
             '        sys.exit("FAIL")\n'
             '    print("  SKIP thing: not here")\n'
             '    sys.exit(0)\n')
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "planted-battery.py"), "w").write(ungated)
        open(os.path.join(d, "gated-battery.py"), "w").write(gated)
        open(os.path.join(d, "unnamed-battery.py"), "w").write(ungated)
        flake_text = "bash tests/planted-battery.py\npython3 tests/gated-battery.py\n"

        # CONTROL: a flake-named battery with an ungated exit 0 MUST be caught.
        got = flake_wired_but_disarming(d, flake_text)
        if "planted-battery.py" not in got:
            failures.append("selftest: a flake-NAMED battery with an ungated skip-then-exit-0 "
                            "was NOT reported -- flake_wired_but_disarming() cannot see the "
                            "shape it exists to catch")
        # PERMITTING: a strict-gated one must NOT be, or every check goes red and the sweep
        # gets reverted rather than believed.
        if "gated-battery.py" in got:
            failures.append("selftest: a STRICT-GATED battery was reported as self-disarming "
                            "-- the exemption is broken")
        # MEMBERSHIP ARM: a self-disarming battery flake.nix does NOT name is out of scope, and
        # saying so is what proves the set is computed rather than returned wholesale.
        if "unnamed-battery.py" in got:
            failures.append("selftest: a battery NOT named in flake.nix was swept -- the "
                            "membership function is not reading flake.nix at all")
        # AND THE ARM THAT STOPS THE THREE ABOVE PASSING ON AN EMPTY SET: a membership
        # function returning [] satisfies two of them for free. Assert the set is populated.
        members = flake_wired_batteries(d, flake_text)
        if sorted(members) != ["gated-battery.py", "planted-battery.py"]:
            failures.append("selftest: flake_wired_batteries() returned %r, expected exactly "
                            "the two files flake.nix names -- an empty or wholesale set makes "
                            "the arms above vacuous" % (members,))
    return failures


def strict_callers_unarmed(tests_dir, workflows_dir=None):
    """Sweep the real tree: every strict-gated battery, every workflow that runs it."""
    if workflows_dir is None:
        workflows_dir = os.path.join(os.path.dirname(os.path.abspath(tests_dir)),
                                     ".github", "workflows")
    # THIS FILE IS EXCLUDED FROM ITS OWN SWEEP, and the reason is not tidiness. Its selftest
    # carries `os.environ.get("AGENT_OS_STRICT")` as a FIXTURE STRING — the arm that proves the
    # exemption predicate recognises a real read — so a source-reading check finds a read here
    # and concludes this contract is a strict-gated battery. It is not; it is the checker. Found
    # by running the sweep before trusting it: the first output was this file naming the step
    # that runs it. A predicate that reads source cannot distinguish code from a string that
    # looks like code, and the honest fix is to say which file that costs.
    me = os.path.basename(os.path.abspath(__file__))
    strict = sorted(os.path.basename(f) for f in glob.glob(os.path.join(tests_dir, "*.py"))
                    if os.path.basename(f) != me and reads_strict_env(f))
    out = []
    for wf in sorted(glob.glob(os.path.join(workflows_dir, "*.yml"))
                     + glob.glob(os.path.join(workflows_dir, "*.yaml"))):
        try:
            with open(wf) as fh:
                lines = fh.read().splitlines()
        except OSError:
            continue
        for base, step in strict_unarmed_in(lines, strict):
            out.append((base, os.path.basename(wf), step))
    return out


def strict_caller_selftest():
    """Show the caller-half check red and green on synthetic input, every run.

    The NEGATIVE arm is the point (Geist, #193 §5): wired-but-unarmed must be NAMED. The three
    controls stop a checker that reports everything, or nothing, from passing this.
    """
    failures = []
    unarmed = ["      - name: calendar battery",
               "        run: python3 tests/calendar-battery.py"]
    armed = ["      - name: calendar battery",
             "        env:",
             '          AGENT_OS_STRICT: "1"',
             "        run: python3 tests/calendar-battery.py"]
    commented = ["      - name: calendar battery",
                 '        # env: AGENT_OS_STRICT: "1"',
                 "        run: python3 tests/calendar-battery.py"]
    B = ["calendar-battery.py"]
    # ARM: wired, strict-gated, not armed -> named.
    if not strict_unarmed_in(unarmed, B):
        failures.append("selftest arm NOT caught: a step running a strict-gated battery without "
                        "AGENT_OS_STRICT was not reported — the wired-but-unarmed green is back")
    # CONTROL 1: armed -> silent. Without this a check that reported every step would pass above.
    if strict_unarmed_in(armed, B):
        failures.append("selftest control: an ARMED step was reported unarmed")
    # CONTROL 2: the arming line in a COMMENT arms nothing. Same class as the self_disarms
    # token-scan defect this commit also fixes; prose does not set an environment variable.
    if not strict_unarmed_in(commented, B):
        failures.append("selftest control: a COMMENTED env line counted as arming the step")
    # CONTROL 3: a battery that does not read the variable is none of this check's business.
    if strict_unarmed_in(unarmed, ["some-other-battery.py"]):
        failures.append("selftest control: a step was reported for a battery it does not name")
    # CONTROL 4: the exemption predicate itself — a MENTION is not a READ. This is the arm that
    # would have caught the token scan, and it is here rather than in a comment.
    if reads_strict_env_lines(["# TODO: consider AGENT_OS_STRICT"]):
        failures.append("selftest control: a comment-only mention of AGENT_OS_STRICT counted as "
                        "a read — self_disarms() is back to a token scan")
    if not reads_strict_env_lines(['    strict = os.environ.get("AGENT_OS_STRICT") == "1"']):
        failures.append("selftest arm: a real os.environ.get read was not recognised, so "
                        "CONTROL 4 above passes vacuously")
    # CONTROL 5: CONTROL 4 tests the PREDICATE; this tests its USE. Reverting self_disarms() to
    # `any("AGENT_OS_STRICT" in l ...)` at the call site leaves reads_strict_env_lines() correct
    # and CONTROL 4 green — measured at the #195 gate: that exact revert, contract rc=0. An
    # off-path mutation: the guarded thing moved, the guard's fixture did not. So drive
    # self_disarms() itself on a file whose only strict-looking line is a comment, and again on
    # one with a real read. Real files, because self_disarms() takes a path.
    import tempfile
    body = ['import sys', 'print("  SKIP x-battery: tool not on PATH")', 'sys.exit(0)']
    with tempfile.TemporaryDirectory() as td:
        mention = os.path.join(td, "mention-battery.py")
        with open(mention, "w") as fh:
            fh.write("\n".join(["# TODO: consider AGENT_OS_STRICT"] + body) + "\n")
        if not self_disarms(mention):
            failures.append("selftest control: self_disarms() exempted a file on a COMMENT-ONLY "
                            "mention of AGENT_OS_STRICT — the call site is back to a token scan "
                            "(CONTROL 4 cannot see this; it tests the predicate, not its use)")
        read = os.path.join(td, "read-battery.py")
        with open(read, "w") as fh:
            fh.write("\n".join(['import os', 'strict = os.environ.get("AGENT_OS_STRICT") == "1"']
                               + body) + "\n")
        if self_disarms(read):
            failures.append("selftest arm: self_disarms() did not exempt a file with a real "
                            "AGENT_OS_STRICT read, so CONTROL 5 above passes vacuously")
    # CONTROL 6 + 7: the two spellings that ARM in fact must be silent — inline shell form on the
    # run line (flake.nix's spelling) and a trailing comment after the YAML value. Both were false
    # reds in the first version. And the value must be exactly 1: "10" does not arm.
    inline = ["      - name: calendar battery",
              "        run: AGENT_OS_STRICT=1 nix shell nixpkgs#khal --command python3 tests/calendar-battery.py"]
    trailing = ["      - name: calendar battery",
                "        env:",
                '          AGENT_OS_STRICT: "1"  # armed on purpose',
                "        run: python3 tests/calendar-battery.py"]
    ten = ["      - name: calendar battery",
           "        env:",
           '          AGENT_OS_STRICT: "10"',
           "        run: python3 tests/calendar-battery.py"]
    if strict_unarmed_in(inline, B):
        failures.append("selftest control: the inline `AGENT_OS_STRICT=1` run-line spelling was "
                        "reported unarmed")
    if strict_unarmed_in(trailing, B):
        failures.append("selftest control: an armed env line with a trailing comment was "
                        "reported unarmed")
    if not strict_unarmed_in(ten, B):
        failures.append('selftest arm: AGENT_OS_STRICT: "10" counted as armed — the value '
                        "check widened past exactly 1")
    return failures


def exemption_staleness_selftest(tmpdir_lines=None):
    """Show the WORKFLOW-AWARE exemption check red and green, on synthetic input.

    The check it guards was blind for as long as it existed: exemptions were only ever tested
    against flake.nix, while workflow wiring is how every battery in this repo actually runs.
    A check with that shape passes quietly forever, so this arm exists to make the blindness
    reproducible rather than remembered.
    """
    failures = []
    running = ["      - name: x", "        run: python3 tests/some-battery.py"]
    commented = ["      # run: python3 tests/some-battery.py"]
    # ARM: a running line names it -> stale.
    if not _named_on_a_running_line(running, "some-battery.py"):
        failures.append("selftest arm NOT caught: a running `run:` line did not count as wiring")
    # CONTROL 1: a comment naming it must NOT count. Prose does not run, and without this arm
    # the predicate could be `base in text` and every arm above would still pass.
    if _named_on_a_running_line(commented, "some-battery.py"):
        failures.append("selftest control: a COMMENTED line counted as wiring, so the arm above "
                        "passed for the wrong reason")
    # CONTROL 2: a file nobody names must NOT be reported stale, or the sweep would mark every
    # exemption stale at once and read as a spectacular success.
    if _named_on_a_running_line(running, "unnamed-battery.py"):
        failures.append("selftest control: an unnamed file counted as wired")
    # CONTROL 3: a running line naming a LONGER file that merely ENDS in this name must NOT
    # count. Without it the predicate could be a bare substring test and every arm above passes.
    if _named_on_a_running_line(["        run: python3 tests/anthropic-some-battery.py"], "some-battery.py"):
        failures.append("selftest control: a SUFFIX collision counted as wiring "
                        "(anthropic-some-battery.py satisfied some-battery.py) — substring test")
    return failures


def ruling_conditions_selftest():
    """Show the checker RED before trusting it green. Runs on every invocation, by design.

    A table-checker that has only ever been observed passing is the same instrument this table
    exists to distrust. Each arm below is a row the checker MUST reject; the final control is a
    row it must ACCEPT, without which a checker that rejected everything would pass this selftest.
    """
    src = open(FLAKE).read() if os.path.exists(FLAKE) else ""
    good = {"id": "arm", "status": "enforced", "lanes": ("bip340-exposure-contract.py",),
            "run_ids": ("33030523828",)}
    arms = [
        ("enforced with no run id", {**good, "run_ids": ()}),
        ("run id that is prose", {**good, "run_ids": ("green on main",)}),
        ("names a file that does not exist", {**good, "lanes": ("no-such-battery.py",)}),
        ("names no lane at all", {**good, "lanes": ()}),
        ("bogus status", {**good, "status": "probably"}),
        ("enforced while on the debt list", {**good, "lanes": ("transport-battery.py",)}),
    ]
    failures = []
    for label, row in arms:
        if not check_ruling_conditions(src, TESTS_DIR, rows=(row,)):
            failures.append(f"selftest arm NOT caught: {label}")
    # CONTROL 1: the checker accepts a well-formed row.
    if check_ruling_conditions(src, TESTS_DIR, rows=(good,)):
        failures.append("selftest control: a well-formed row was REJECTED, so every arm above "
                        "passed for the wrong reason")
    # CONTROL 2 / PRE-FIX ARM: the comment-only reference. `wiring_references` accepts it and
    # `executing_references` must not, or the strictness this table depends on is not there.
    lenient = [ln for ln in wiring_references(src, TESTS_DIR, "bip340-battery.py")]
    strict = executing_references(src, TESTS_DIR, "bip340-battery.py")
    if len(strict) >= len(lenient):
        failures.append(
            "selftest pre-fix arm: executing_references() dropped NOTHING that "
            "wiring_references() kept, so the comment-vs-code distinction is unproven here. "
            "If flake.nix no longer mentions bip340-battery.py in a comment, re-point this arm "
            "at another commented mention rather than deleting it.")

    # BLOCK-COMMENT ARM. `#` is not Nix's only comment: `/* ... */` spans lines, and a name
    # inside one is prose the `#` filter cannot see. flake.nix has zero block comments today, so
    # this hole is LATENT — which is exactly why it gets a synthetic source rather than a
    # sentence in a docstring. A row claiming `enforced` must not be dischargeable by a name
    # sitting in a comment, whichever of the two spellings the comment uses.
    block_only = (
        "  # nothing here executes\n"
        "  /*\n"
        "     someday: checks.x = mk { script = \"python3 tests/latent-battery.py\"; };\n"
        "  */\n"
    )
    if executing_references(block_only, TESTS_DIR, "latent-battery.py"):
        failures.append("selftest arm NOT caught: a test named only inside a /* ... */ Nix "
                        "block comment was counted as executing wiring")
    # CONTROL 3: over-stripping fails LOUDLY (a real row would stop passing), under-stripping is
    # the silent direction — so bias toward stripping. These two arms pin the bias in place:
    # real wiring AFTER a closed block, and real wiring on the same line the block closes on.
    after_block = block_only + '  checks.y = mk { script = "python3 tests/latent-battery.py"; };\n'
    if not executing_references(after_block, TESTS_DIR, "latent-battery.py"):
        failures.append("selftest control: a real wiring line following a CLOSED block comment "
                        "was stripped, so the block-comment arm above passes by over-stripping")
    same_line = '  */ checks.z = mk { script = "python3 tests/latent-battery.py"; };\n'
    if not executing_references("  /*\n  x\n" + same_line, TESTS_DIR, "latent-battery.py"):
        failures.append("selftest control: wiring after `*/` on the closing line was stripped")
    # CONTROL 4 / REGRESSION PIN: an UNPAIRED `/*` is a glob, not a comment. flake.nix line ~315
    # has `for f in ${./modules}/*.nix`. Blanking to EOF from there erased the real file and
    # tripped CONTROL 1 — see `_blank_block_comments`. This arm holds that fix in place.
    glob = '  for f in ${./modules}/*.nix; do :; done\n' + after_block.split("*/\n")[-1]
    if not executing_references(glob, TESTS_DIR, "latent-battery.py"):
        failures.append("selftest control: an unpaired `/*` (a shell glob, not a comment) "
                        "blanked real wiring downstream of it")
    # CONTROL 5 (Geist, gate, 2026-08-27): the glob PAIRED with a LATER real block comment. A
    # non-greedy `/\*.*?\*/` pairs the glob's `/*` with the first `*/` in the file, and everything
    # between — real wiring included — goes blank SILENTLY: unlike CONTROL 4 nothing fails loudly
    # unless a cited row happens to sit in the erased span. Latent today (zero block comments);
    # it opens the day one is added below line ~315. The opener must be preceded by whitespace
    # or line start — a glob's `/*` is preceded by `}`.
    paired = ('  for f in ${./modules}/*.nix; do :; done\n'
              '  checks.w = mk { script = "python3 tests/latent-battery.py"; };\n'
              '  /* a real comment, later */\n')
    if not executing_references(paired, TESTS_DIR, "latent-battery.py"):
        failures.append("selftest control: a shell glob `/*` PAIRED with a later real `*/` "
                        "blanked the real wiring between them")
    return failures



# ---------------------------------------------------------------------------
# GEIST'S LAW, AS AMENDED 2026-09-05T13:05Z: A BOX-RUNNABLE BATTERY DECLARES ITS SIDE
# EFFECTS, AND THE CHECKER DETECTS THEM RATHER THAN TAKING THE DECLARATION AT ITS WORD.
#
# The occasion: agos-notes-battery ran `agos-notes new` against /var/lib/agos-notes, the store
# notes-open.nix documents as shared with the human, with no delete verb to undo it. Every run
# left a note behind permanently. It was found by hand, in a one-off sweep, because Augur made
# a remark on a different PR. Nothing in the repo would have found the next one.
#
# THE FIRST VERSION OF THIS CHECK WAS A BOOLEAN AND AUGUR KILLED IT, CORRECTLY. `MUTATES_SHARED_
# STATE = False` is TRUE of the pre-#279 agos-web-battery: that battery fetched a third-party
# host on every run and mutated no shared state whatsoever. So the field would have been
# satisfied, honestly, by the very battery whose defect started this sweep — a declaration that
# cannot be false on the founding case cannot control-arm it. The fix is not a better boolean;
# it is that "side effect" was never one axis. Geist's amendment names three.
#
# TWO FIELDS, both module-level literals:
#
#   SIDE_EFFECTS = []                        closed enum, see SIDE_EFFECT_KINDS
#   SIDE_EFFECTS_OWNER = "verb-battery.nix"  required iff SIDE_EFFECTS is non-empty
#
# The second field is MY reading, not Geist's text, and it is flagged here so a reviewer can
# overrule it rather than inherit it. He wrote one field (`SIDE_EFFECTS = []`) and one rule
# ("non-empty SIDE_EFFECTS must name the VM test owning those arms"). A list of enum members
# cannot also carry a filename without smuggling structure into the members, so the owner is
# its own field. A dict {kind: owner} was the alternative; it says less clearly that the owner
# owns ALL of them, and it departs further from the shape he actually wrote.
#
# WHAT MAKES THIS DIFFERENT FROM THE BOOLEAN, and it is Augur's condition verbatim: THE CHECKER
# DETECTS. An arm that only reads the field is satisfiable by writing the field. So each kind
# has a detector, and a battery whose source shows a side effect it does not declare is RED
# regardless of what it declares. The detectors are FLOORS and are described as such at each
# one; a battery declaring a kind the detector cannot see is NOT an error, because the detector
# is the weaker instrument and the declaration is allowed to know more than it does.
SIDE_EFFECT_KINDS = {
    "egress": "leaves the machine — any non-loopback host, any billed API",
    "shared-state": "outlives the run in state the human owns (e.g. /var/lib/agos-notes)",
    "box": "outlives the run in the machine (power, generation, settings)",
}
_SE_LEGAL = ("a list of " + "|".join(sorted(SIDE_EFFECT_KINDS)) +
             " (use [] for none), plus SIDE_EFFECTS_OWNER when non-empty")

# THE SHARED-STATE / BOX DETECTOR'S WHOLE KNOWLEDGE, and it is a table someone maintains by
# hand — which is the honest description of its limit. A mutating verb that is not listed here
# is INVISIBLE to the detector, so this table's incompleteness is the detector's blind spot,
# not a bug in the scan. It is the same string-counting weakness the egress detector carries
# below, and it fails in the same direction: silently, by not firing.
#
# Grounded in what is evidenced, not in what sounds plausible: notes `new`/`append` from the
# sweep that started this, `cal add` from agent-brain.py's dispatch, and the power verbs #277
# made first-class. Add a row when you meet a verb that outlives its run.
MUTATING_VERBS = {
    "agos-notes": {"new": "shared-state", "append": "shared-state"},
    "agos-cal": {"add": "shared-state"},
    "agos-sys": {"volume": "box", "reboot": "box", "poweroff": "box",
                 "restart": "box", "shutdown": "box", "update": "box"},
}

# DEBT, NOT DESIGN — same rule as KNOWN_UNWIRED_DEBT above, and for the same reason.
#
# A battery here has a REAL side-effecting arm covering a REAL product verb, and the VM test
# that should own it does not exist yet. Only a file named here may declare a non-empty
# SIDE_EFFECTS whose owner is missing; everything else must name an owner that exists. That
# keeps the ledger meaningful — it reads "known, ledgered, and someone is watching the count"
# rather than "opted out". THIS LIST MAY ONLY SHRINK.
#
# calendar-battery is NOT the notes case and the difference decided the remedy. Geist's notes
# ruling removed the write arms because the brain's notes hand is list|read only, so they
# covered verbs the product never advertises — cost zero. But the brain DOES advertise
# `calendar.add` (agent-brain.py:1386), so `cal("add", start, "battery-test-event")` covers a
# verb an agent can actually reach. Deleting it would trade a mutation for a coverage hole in
# a shipped verb. It moves to the VM arm instead, and waits for that arm to exist.
#
# Found 2026-09-05 while writing the declarations this check reads — which is the argument for
# the check. The notes case was found by hand; this one was found because something finally
# forced every battery to be looked at. Not confirmed by running it: agos-cal IS on the Dell,
# so running it to "prove" the mutation IS the mutation.
SIDE_EFFECT_DEBT = {
    "calendar-battery.py":
        'cal("add", start, "battery-test-event") — creates an event in the human calendar on '
        "any box with agos-cal (the Dell has it). Covers the real calendar.add verb, so it "
        "moves to tests/verb-battery.nix rather than being deleted; that file is not built yet.",
}


def _module_assign(path, name):
    """Module-level assignment of `name`, as a literal. None if absent/not a literal.

    ast, not import: importing a battery RUNS it, and running batteries is what this file
    exists to have opinions about.
    """
    try:
        tree = ast.parse(open(path, encoding="utf-8").read())
    except Exception:
        return ("__unparseable__", None)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    try:
                        return ("ok", ast.literal_eval(node.value))
                    except Exception:
                        return ("__notliteral__", None)
    return ("__absent__", None)


# AUGUR'S ARM (2026-09-05): THE EGRESS DETECTOR.
#
# agos-web-battery fetched a third-party host on every run — from the operator's IP, for a
# property the failure envelope satisfied anyway. It passed CI only because CI lacks the
# binary and the battery SKIPs. Augur's point on #279 is the one that matters: I fixed the
# arm and then wrote a comment accommodating "a future sweep grepping for non-loopback URLs",
# and THERE WAS NO SUCH SWEEP. The sweep was me, by hand, once. A rule with no trigger point
# does not fire at an unfamiliar surface. This is the trigger point.
#
# WHAT THIS DETECTOR IS: a floor, and a smaller one than it looks. It counts STRINGS, not
# facts. A host assembled at runtime, read from env, or built by concatenation is invisible to
# it — the same defect class as the independence gate that counted strings. It finding nothing
# means "no battery contains a literal non-loopback URL", which is strictly weaker than "no
# battery reaches the network". Do not promote the one into the other.
_URL_RE = re.compile(r"\bhttps?://([A-Za-z0-9_.\-\[\]:]+)")
_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]", "::1")
# RFC 6761/2606 reserve these so they can never resolve to a real server, which is a stronger
# guarantee than "we agreed not to fetch it". example.com is NOT here on purpose: it is reserved
# for DOCUMENTATION but it genuinely resolves and genuinely serves content, so a fixture using it
# is one refactor away from a live fetch. Fixtures were moved to .invalid rather than exempted —
# an allowlist entry retires the file from scrutiny, a non-resolving host retires the hazard.
_UNRESOLVABLE_TLDS = (".invalid", ".test", ".example", ".localhost")


def detect_egress(path):
    """Lines showing a literal non-loopback http(s) host. Floor: literals only."""
    hits = []
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except Exception as exc:
        return [(0, f"unreadable ({exc!r})")]
    for i, line in enumerate(lines, 1):
        for host in _URL_RE.findall(line):
            bare = host.split(":")[0] if not host.startswith("[") else host
            if bare in _LOOPBACK_HOSTS or host in _LOOPBACK_HOSTS:
                continue
            if bare.lower().endswith(_UNRESOLVABLE_TLDS):
                continue
            if "." not in bare:
                # A bare single label ("x") has no TLD and cannot be resolved by a stub
                # resolver; these appear as HTTPError() constructor args, never as targets.
                continue
            hits.append((i, host))
    return hits


# THE ALIAS PASS, AND IT EXISTS BECAUSE THE FIRST VERSION OF THIS DETECTOR WAS BLIND TO EVERY
# REAL BATTERY IN THE TREE. v1 wanted the CLI name and the verb on the same line. Not one
# battery is written that way: they all bind the tool once (`ncli = shutil.which("agos-notes")`)
# and then call `run([ncli, "new", ...])`. So v1 found nothing in the pre-fix agos-notes-battery
# — the founding case — while its own selftest fixture passed, because I had written the fixture
# with `# agos-notes` in a COMMENT on the call line. The fixture was written to the detector
# instead of to reality, and the green was the crutch, not the catch. Caught only by running
# Geist's control against the actual pre-fix sha rather than against something I made up.
_ALIAS_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=.*?[\"'](agos-[a-z]+)[\"']")


def _cli_aliases(lines):
    """Local names bound to an agos-* CLI. {name: cli}, plus each cli under its own name."""
    aliases = {}
    for line in lines:
        m = _ALIAS_RE.match(line)
        if m:
            aliases[m.group(1)] = m.group(2)
    return aliases


def detect_verb_effects(path):
    """Lines invoking a MUTATING_VERBS entry. Returns [(line, kind, "cli verb")].

    Floor, and the shape of the floor is worth stating: it resolves a module-level alias to
    its CLI and then wants the verb as a quoted literal on the calling line. A verb held in a
    variable, built by concatenation, or reached through a wrapper defined in another file
    does not register. Growing MUTATING_VERBS is how this gets stronger; nothing here can find
    a mutating verb the table does not name.
    """
    hits = []
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except Exception:
        return hits
    aliases = _cli_aliases(lines)
    for i, line in enumerate(lines, 1):
        # Which CLIs could this line be calling? Its own name, or any alias bound to it.
        candidates = set()
        for cli in MUTATING_VERBS:
            if cli in line:
                candidates.add(cli)
        for name, cli in aliases.items():
            if cli in MUTATING_VERBS and re.search(r"\b" + re.escape(name) + r"\b", line):
                candidates.add(cli)
        if not candidates:
            continue
        for cli in sorted(candidates):
            for verb, kind in MUTATING_VERBS[cli].items():
                if f'"{verb}"' in line or f"'{verb}'" in line:
                    hits.append((i, kind, f"{cli} {verb}"))
    return hits


def side_effect_declarations(tests_dir):
    """Every *-battery.py declares SIDE_EFFECTS legally AND consistently with the detectors."""
    problems = []
    for path in sorted(glob.glob(os.path.join(tests_dir, "*-battery.py"))):
        base = os.path.basename(path)
        state, value = _module_assign(path, "SIDE_EFFECTS")
        if state == "__unparseable__":
            problems.append(f"{base}: could not be parsed to read its declaration")
            continue
        if state == "__absent__":
            problems.append(f"{base}: no SIDE_EFFECTS declaration ({_SE_LEGAL})")
            continue
        if state == "__notliteral__":
            problems.append(f"{base}: SIDE_EFFECTS is computed, not a literal — a declaration "
                            f"a reader cannot evaluate is not a declaration")
            continue
        if not isinstance(value, list):
            problems.append(f"{base}: SIDE_EFFECTS = {value!r} is not a list ({_SE_LEGAL})")
            continue
        declared = set()
        for member in value:
            if member in SIDE_EFFECT_KINDS:
                declared.add(member)
            else:
                problems.append(f"{base}: SIDE_EFFECTS names {member!r}, which is not one of "
                                f"{sorted(SIDE_EFFECT_KINDS)} — the enum is closed so that a "
                                f"new kind is a deliberate edit here, not a free-text field")

        # The owner half of Geist's rule: arms with side effects live in a VM test that exists.
        ostate, owner = _module_assign(path, "SIDE_EFFECTS_OWNER")
        if declared:
            if ostate != "ok" or not isinstance(owner, str) or not owner:
                problems.append(f"{base}: declares {sorted(declared)} but no SIDE_EFFECTS_OWNER "
                                f"— non-empty side effects must name the VM test that owns "
                                f"those arms")
            elif not os.path.exists(os.path.join(tests_dir, owner)):
                if base not in SIDE_EFFECT_DEBT:
                    problems.append(f"{base}: names {owner!r} as the owner of its side-effecting "
                                    f"arms, but that file does not exist and {base} is not in "
                                    f"SIDE_EFFECT_DEBT. A missing owner is debt to be ledgered, "
                                    f"not a declaration to be believed")
        elif ostate == "ok":
            problems.append(f"{base}: SIDE_EFFECTS is empty but SIDE_EFFECTS_OWNER is set — an "
                            f"owner with nothing to own reads as coverage that is not there")

        # AUGUR'S CONDITION: detect, do not merely read. Undeclared-but-detected is RED.
        # Declared-but-undetected is NOT: the detectors are floors and the file may know more.
        for line, host in detect_egress(path):
            if "egress" not in declared:
                problems.append(f"{base}:{line}: literal non-loopback host {host!r} but "
                                f"SIDE_EFFECTS does not declare 'egress' — a battery that "
                                f"reaches out is armed on every box where its binary exists "
                                f"and green-by-accident everywhere it does not")
        for line, kind, what in detect_verb_effects(path):
            if kind not in declared:
                problems.append(f"{base}:{line}: invokes `{what}`, which outlives the run "
                                f"({kind}), but SIDE_EFFECTS does not declare {kind!r}")
    return problems


def stale_side_effect_debt(tests_dir):
    """SIDE_EFFECT_DEBT entries that no longer apply — the list must shrink, and be seen to."""
    stale = []
    for base in sorted(SIDE_EFFECT_DEBT):
        path = os.path.join(tests_dir, base)
        if not os.path.exists(path):
            stale.append(f"{base}: named in SIDE_EFFECT_DEBT but the file is gone")
            continue
        state, value = _module_assign(path, "SIDE_EFFECTS")
        if state != "ok" or not value:
            stale.append(f"{base}: in SIDE_EFFECT_DEBT but no longer declares any side effect "
                         f"— the debt was paid and the ledger entry outlived it")
            continue
        ostate, owner = _module_assign(path, "SIDE_EFFECTS_OWNER")
        if ostate == "ok" and isinstance(owner, str) and \
                os.path.exists(os.path.join(tests_dir, owner)):
            stale.append(f"{base}: in SIDE_EFFECT_DEBT but its owner {owner!r} now exists — the "
                         f"debt was paid and the ledger entry outlived it")
    return stale


def side_effect_declaration_selftest():
    """Drive the checks against fixtures. Without these the checks are assertions.

    THE ARM THAT MATTERS IS THE DETECTION ONE. A field-reading check is satisfiable by writing
    the field, so `declares [] while the source shows egress` is the case Augur's condition
    exists for, and it is the case the boolean predecessor could not express at all.
    """
    failures = []
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        def w(name, text):
            with open(os.path.join(d, name), "w") as fh:
                fh.write(text)

        # --- shape of the declaration ---
        w("a-battery.py", "SIDE_EFFECTS = []\n")
        if side_effect_declarations(d):
            failures.append("selftest: a clean empty declaration was reported as a problem — "
                            "the check would be red on every correct battery")
        w("b-battery.py", "x = 1\n")
        if not any("no SIDE_EFFECTS" in p for p in side_effect_declarations(d)):
            failures.append("selftest: an UNDECLARED battery was not reported — the check "
                            "cannot see the condition it exists for")
        os.remove(os.path.join(d, "b-battery.py"))
        w("c-battery.py", "SIDE_EFFECTS = ['network']\n")
        if not any("not one of" in p for p in side_effect_declarations(d)):
            failures.append("selftest: a member OUTSIDE the enum was accepted — the enum would "
                            "be a free-text field and 'network' vs 'egress' would both pass")
        os.remove(os.path.join(d, "c-battery.py"))
        w("d-battery.py", "SIDE_EFFECTS = 'egress'\n")
        if not any("is not a list" in p for p in side_effect_declarations(d)):
            failures.append("selftest: a bare string passed where a list is required — the "
                            "shape would drift back to the boolean it replaced")
        os.remove(os.path.join(d, "d-battery.py"))

        # --- the owner half: non-empty must name a VM test that exists ---
        w("e-battery.py", 'SIDE_EFFECTS = ["box"]\n')
        if not any("no SIDE_EFFECTS_OWNER" in p for p in side_effect_declarations(d)):
            failures.append("selftest: side effects with NO owner passed — declaring an effect "
                            "would discharge the duty to put it somewhere")
        w("e-battery.py", 'SIDE_EFFECTS = ["box"]\nSIDE_EFFECTS_OWNER = "nope.nix"\n')
        if not any("does not exist" in p for p in side_effect_declarations(d)):
            failures.append("selftest: a NON-EXISTENT owner passed — the owner field would be "
                            "a way to name anything and be believed")
        w("nope.nix", "")
        if side_effect_declarations(d):
            failures.append("selftest: naming an EXISTING owner still failed — the legal form "
                            "is unusable, so batteries would be pushed to declare []")
        os.remove(os.path.join(d, "nope.nix"))
        # CONTROL for the opposite mistake: an owner with nothing to own.
        w("e-battery.py", 'SIDE_EFFECTS = []\nSIDE_EFFECTS_OWNER = "nope.nix"\n')
        if not any("nothing to own" in p for p in side_effect_declarations(d)):
            failures.append("selftest: an owner on an EMPTY declaration passed — a file could "
                            "advertise VM coverage it does not have")
        os.remove(os.path.join(d, "e-battery.py"))

        # --- DEBT: a missing owner is legal ONLY for a ledgered file ---
        w("z-battery.py", 'SIDE_EFFECTS = ["shared-state"]\nSIDE_EFFECTS_OWNER = "later.nix"\n')
        if not any("not in SIDE_EFFECT_DEBT" in p and "z-battery" in p
                   for p in side_effect_declarations(d)):
            failures.append("selftest: an UNLEDGERED missing owner was accepted — the owner "
                            "field would become the opt-out the ruling forbids")
        SIDE_EFFECT_DEBT["z-battery.py"] = "selftest fixture"
        try:
            if any("z-battery" in p for p in side_effect_declarations(d)):
                failures.append("selftest: a LEDGERED missing owner was still rejected — the "
                                "ledger is unusable, so real arms would go undeclared instead")
            if any("z-battery" in x for x in stale_side_effect_debt(d)):
                failures.append("selftest: a live debt entry was called stale — the ratchet "
                                "would demand deletion of debt that still exists")
            w("z-battery.py", "SIDE_EFFECTS = []\n")
            if not any("debt was paid" in p and "z-battery" in p
                       for p in stale_side_effect_debt(d)):
                failures.append("selftest: a debt entry whose battery stopped declaring was not "
                                "reported stale — the ledger could only ever grow")
            # The OTHER way a debt is paid, and the one that actually happens here: the owner
            # gets built. Without this arm the ratchet only notices deletions.
            w("z-battery.py",
              'SIDE_EFFECTS = ["shared-state"]\nSIDE_EFFECTS_OWNER = "later.nix"\n')
            w("later.nix", "")
            if not any("now exists" in p and "z-battery" in p
                       for p in stale_side_effect_debt(d)):
                failures.append("selftest: a debt whose OWNER was built was not reported stale "
                                "— verb-battery.nix landing would leave the ledger entry behind")
            os.remove(os.path.join(d, "later.nix"))
        finally:
            del SIDE_EFFECT_DEBT["z-battery.py"]
        os.remove(os.path.join(d, "z-battery.py"))

        # --- AUGUR'S CONDITION: the checker DETECTS, it does not read the field ---
        # CONTROL: loopback and the reserved non-resolving TLDs must NOT trip, or the check is
        # a blanket ban and the arms written to avoid egress (agos-web 2a/2b) would fail it.
        w("f-battery.py",
          'SIDE_EFFECTS = []\nA = "http://127.0.0.1:8080/"\nB = "https://localhost/x"\n'
          'C = "http://127.0.0.1:%d/"\nD = "https://host.inv' + 'alid/x"\n')
        if side_effect_declarations(d):
            failures.append("selftest: a loopback-only battery was flagged — the check would "
                            "fail the very arms written to avoid egress (agos-web 2a/2b)")
        w("g-battery.py",
          'SIDE_EFFECTS = []\nU = "https://" + "cdn." + ("rea" + "lhost.net") + "/x"\n')
        if side_effect_declarations(d):
            failures.append("selftest: a CONCATENATED host tripped the literal scan — that is "
                            "not what this check claims to catch and a false positive here "
                            "teaches people to disable it")
        os.remove(os.path.join(d, "g-battery.py"))
        # THE ARM. This is the pre-#279 agos-web-battery in miniature: an honest [] beside a
        # source line that reaches the network. The boolean predecessor PASSED this exact
        # shape, which is why it was replaced.
        w("h-battery.py", 'SIDE_EFFECTS = []\nU = "https://cdn.' + 'rea' + 'lhost.net/x"\n')
        if not any("does not declare 'egress'" in p for p in side_effect_declarations(d)):
            failures.append("selftest: a battery declaring [] beside a literal non-loopback "
                            "host was NOT reported — the check reads the field instead of "
                            "detecting, which is the defect Augur named")
        # ...and declaring it truthfully clears it. Without this the check is a ban on egress
        # rather than a ban on UNDECLARED egress, and there would be no legal way to write one.
        w("h-battery.py",
          'SIDE_EFFECTS = ["egress"]\nSIDE_EFFECTS_OWNER = "own.nix"\n'
          'U = "https://cdn.' + 'rea' + 'lhost.net/x"\n')
        w("own.nix", "")
        if side_effect_declarations(d):
            failures.append("selftest: a TRUTHFULLY declared egress arm with a real owner was "
                            "still rejected — there would be no legal way to write one")
        os.remove(os.path.join(d, "h-battery.py"))
        os.remove(os.path.join(d, "own.nix"))

        # Same two directions for the verb detector — the notes case, in miniature.
        # THE REAL SHAPE, not a shape written to suit the detector: bind the tool once, call
        # it through the alias. v1 of the detector passed a fixture with `# agos-notes` in a
        # comment on the call line and found NOTHING in the actual pre-fix battery.
        w("i-battery.py", 'SIDE_EFFECTS = []\n'
                          'ncli = shutil.which("agos-notes")\nrun([ncli, "new", slug])\n')
        if not any("shared-state" in p and "agos-notes new" in p
                   for p in side_effect_declarations(d)):
            failures.append("selftest: `agos-notes new` beside [] was NOT reported — the verb "
                            "detector is vacuous and the founding case would still pass")
        w("i-battery.py", 'SIDE_EFFECTS = []\n'
                          'ncli = shutil.which("agos-notes")\nrun([ncli, "read", slug])\n')
        if side_effect_declarations(d):
            failures.append("selftest: a NON-mutating notes verb tripped the detector — the "
                            "table would be a ban on naming agos-notes at all, and the "
                            "read-only arms that replaced the mutating ones would fail")
        os.remove(os.path.join(d, "i-battery.py"))
    return failures


class Findings:
    """One place every check's output goes, so the OK verdict cannot forget a check.

    THE HOLE THIS CLOSES, found by geist as RED G on #198: main()'s OK-branch was guarded by a
    hand-maintained `and not X` over ten terms. Dropping one term left the finding COMPUTED,
    printed nowhere, and the tree green — and no selftest could reach it, because the selftests
    exercise predicates and that conjunction was the one piece of logic with no predicate. Every
    term added since #165 had the same exposure; the shape predates the check that exposed it.

    Registration is the same act as computation (`f.add(key, value)`), so a check that runs is a
    check that is guarded. Two directions are then closed by construction rather than by care:

      * `f[key]` on an unregistered key raises, so a print branch for a check nobody ran is a
        crash and not a silent skip.
      * `unread()` names keys that HAVE findings and were never looked at — the computed-but-
        never-printed direction, which is RED G's exact shape one level down.

    THE FIRST VERSION OF THIS CLASS DID NOT CLOSE RED G, and the reason is worth more than the
    fix. Registration-at-computation makes an un-registered check invisible to any(), so the OK
    branch is taken and RETURNS before the print branch that would have raised on the missing
    key. The crash I was relying on sat downstream of the verdict it was supposed to protect.
    Two things answer it, and neither is "care":

      * REQUIRED_CHECKS below is compared against the registered keys, so a check that stops
        registering is named — the OK path can no longer be reached by a shrinking set.
      * The selftests DO NOT flow through this collector (see main()). Routing them through it
        made a broken any() suppress the very arm that detects a broken any(): constant-False
        passed the tree green while findings_selftest was screaming into a list nobody read.
        A guard whose alarm is wired through the guard has no alarm.

    What it does NOT close: a check that never calls add() AND is never added to REQUIRED_CHECKS
    — i.e. a wholly new check whose author skips both. That residue is named here rather than
    papered over; it is smaller than the conjunction it replaces, and it is loud in review
    (a new check with no entry beside the others) rather than invisible.
    """

    def __init__(self):
        self._d = {}
        self._read = set()

    def add(self, key, value):
        if key in self._d:
            raise KeyError("finding %r registered twice — one key, one check" % key)
        self._d[key] = value
        return value

    def __getitem__(self, key):
        if key not in self._d:
            raise KeyError("no finding registered under %r — a print branch is reading a check "
                           "that never ran" % key)
        self._read.add(key)
        return self._d[key]

    def any(self):
        return any(bool(v) for v in self._d.values())

    def unread(self):
        return sorted(k for k, v in self._d.items() if v and k not in self._read)


# EVERY f-registered check, by key. Compared against what actually registered, so removing a
# check's add() call is a NAMED failure instead of a smaller conjunction. `ruling` is absent on
# purpose: the selftests are deliberately kept out of the collector — see Findings' docstring.
REQUIRED_CHECKS = {
    "unlisted", "dangling", "unwired", "vacuous", "stale",
    "debt_added", "debt_removed", "unarmed", "wired_disarming", "flake_wired_disarming",
    "ruling_table", "side_effects", "stale_se_debt",
}


def missing_checks(f):
    """Keys REQUIRED_CHECKS names that nothing registered, plus the reverse."""
    have = set(f._d)
    return sorted(REQUIRED_CHECKS - have), sorted(have - REQUIRED_CHECKS)


def findings_selftest():
    """Drive Findings itself. Without these arms the class is a container with opinions."""
    failures = []
    f = Findings()
    f.add("a", [])
    f.add("b", ["x"])
    if not f.any():
        failures.append("selftest: Findings.any() missed a non-empty finding — the OK branch "
                        "would be reached with a check reporting a problem (RED G's shape)")
    # CONTROL: all-empty must NOT trip any(), or the checker could never pass at all.
    if Findings().any():
        failures.append("selftest control: an EMPTY Findings reported findings — any() is "
                        "constant-True and the arm above proves nothing")
    if f.unread() != ["b"]:
        failures.append("selftest: unread() did not name a finding that was never read — "
                        "computed-but-never-printed goes silent again")
    f["b"]
    if f.unread():
        failures.append("selftest control: unread() still named a finding AFTER it was read, "
                        "so the arm above passes on a constant list")
    try:
        f["nope"]
        failures.append("selftest: reading an UNREGISTERED key was silent — a print branch for "
                        "a check that never ran would skip instead of crashing")
    except KeyError:
        pass
    g = Findings()
    for k in REQUIRED_CHECKS:
        g.add(k, [])
    if missing_checks(g) != ([], []):
        failures.append("selftest: a COMPLETE registration was reported as missing/extra — "
                        "missing_checks() would red every run and get exempted")
    h = Findings()
    for k in sorted(REQUIRED_CHECKS)[1:]:
        h.add(k, [])
    if not missing_checks(h)[0]:
        failures.append("selftest: a check that stopped registering was NOT named — RED G is "
                        "back, because an unregistered check is invisible to any()")
    try:
        f.add("a", [])
        failures.append("selftest: a duplicate key was accepted — two checks would share one "
                        "guard slot and the second would overwrite the first")
    except KeyError:
        pass
    # RED N's own arms. `missing_ruling` is what now stands between a dropped selftest and a
    # green tree, so it gets the same two directions `missing_checks` gets.
    if missing_ruling(REQUIRED_RULING) != ([], []):
        failures.append("selftest: a COMPLETE ruling chain was reported as missing/extra — "
                        "missing_ruling() would red every run and get exempted")
    if not missing_ruling(sorted(REQUIRED_RULING)[1:])[0]:
        failures.append("selftest: a selftest dropped from the chain was NOT named — RED N is "
                        "back, and the sum that guards the guard is hand-maintained again")
    if not missing_ruling(sorted(REQUIRED_RULING) + ["surprise"])[1]:
        failures.append("selftest: an UNDECLARED ruling term was not named — a term could run "
                        "without ever being required to")
    return failures


# THE SUM ONE LEVEL UP (Geist's RED N on #199). Retiring main()'s ten-term `and` left the
# selftest chain `_ruling += a(); _ruling += b(); ...` — six hand-maintained terms with exactly
# the exposure the Findings registry was built to close, and dropping one is silent: with
# `findings_selftest()` gone from the chain, forcing `any()` constant-False passes rc=0 again.
# So the terms are a tuple, the tuple is compared by name against REQUIRED_RULING, and an
# omission is a NAMED red rather than a shorter sum.
#
# The recursion terminates HERE, at the same residue as the checks registry: a new selftest whose
# author touches neither the tuple nor REQUIRED_RULING is invisible — but it is a two-line
# absence sitting beside five present ones, loud in review, not a silently dropped `+=`.
# Naming that floor is better than pretending a registry has no registry of its own.
SELFTESTS = (
    exemption_staleness_selftest,
    ruling_conditions_selftest,
    strict_caller_selftest,
    wired_disarm_selftest,
    flake_wired_disarm_selftest,
    findings_selftest,
    side_effect_declaration_selftest,
)

# SELFTESTS ONLY, now that `ruling_table` has moved to the checks registry where it belongs.
# It produced findings about the REAL TREE, not about the checker, and sitting in this chain it
# was the one term that could not be a bare function — which is what forced the tuple-literal
# join whose two halves let geist's R2 through. Written out rather than derived from SELFTESTS:
# a set computed from the tuple would shrink with it, and then nothing compares anything.
REQUIRED_RULING = {
    "exemption_staleness_selftest", "ruling_conditions_selftest", "strict_caller_selftest",
    "wired_disarm_selftest", "flake_wired_disarm_selftest", "findings_selftest",
    "side_effect_declaration_selftest",
}


def missing_ruling(names):
    """Names REQUIRED_RULING expects that nothing ran, plus the reverse."""
    have = set(names)
    return sorted(REQUIRED_RULING - have), sorted(have - REQUIRED_RULING)


def main():
    ap = argparse.ArgumentParser()
    # Injection points exist ONLY so the failing arm can be exercised without corrupting the
    # repo. The doctrine this file serves says a guard whose red path has never been seen is a
    # comment with a CI badge, so the guard ships with a way to see it red.
    ap.add_argument("--workflow", default=WORKFLOW)
    ap.add_argument("--flake", default=FLAKE)
    ap.add_argument("--tests-dir", default=TESTS_DIR)
    ap.add_argument("--debt-baseline", default=DEBT_BASELINE)
    ap.add_argument("--packages-json", default=None,
                    help="JSON array of package names, for testing the failing arm")
    args = ap.parse_args()

    names = json.loads(args.packages_json) if args.packages_json else flake_test_packages(SYSTEM)
    tests = {n for n in names if n.startswith("test-")}
    matrix = matrix_entries(args.workflow)

    f = Findings()
    unlisted = f.add("unlisted", sorted(tests - matrix))
    dangling = f.add("dangling", sorted(matrix - tests))
    _unwired, _vacuous, _stale = unwired_test_files(args.tests_dir, args.flake)
    unwired = f.add("unwired", _unwired)
    vacuous = f.add("vacuous", _vacuous)
    stale = f.add("stale", _stale)
    _added, _removed = debt_ratchet(KNOWN_UNWIRED_DEBT, args.debt_baseline)
    debt_added = f.add("debt_added", _added)
    debt_removed = f.add("debt_removed", _removed)

    # Ruled item 3. The selftest runs FIRST and unconditionally: if the checker cannot be shown
    # going red, its green verdict on the real table below means nothing.
    # SELFTESTS FIRST, AND OUTSIDE f. Routing them through the collector made a broken
    # collector suppress its own alarm: with any() forced constant-False the tree went green
    # while findings_selftest's failures sat unread in the same dict any() was lying about.
    # These decide before anything the collector touches.
    unarmed = f.add("unarmed", strict_callers_unarmed(args.tests_dir))
    wired_disarming = f.add("wired_disarming", wired_but_disarming(args.tests_dir))
    flake_text_for_sweep = open(args.flake).read()
    flake_wired_disarming = f.add(
        "flake_wired_disarming",
        flake_wired_but_disarming(args.tests_dir, flake_text_for_sweep))
    # A finding about the REAL TREE — an 'enforced' row citing no executing lane — so it belongs
    # with the checks, not with the selftests that prove the checker can go red.
    ruling_table = f.add("ruling_table",
                         check_ruling_conditions(open(args.flake).read(), args.tests_dir))
    # Geist's law, as data (2026-09-05), and Augur's trigger point for the egress sweep.
    side_effects = f.add("side_effects", side_effect_declarations(args.tests_dir))
    stale_se_debt = f.add("stale_se_debt", stale_side_effect_debt(args.tests_dir))

    # ONE ITERATION, NOT A SUM. Each term's NAME comes from the object that gets called, so a
    # term cannot be dropped from the chain while still counting as having run.
    terms = [(fn.__name__, fn) for fn in SELFTESTS]
    # NAME AND CALL IN ONE EXPRESSION (geist, R2 on #200): with `ran.append(name)` and
    # `ruling += fn()` as two statements, deleting the call line left every term recorded as
    # ran and none of them called — rc=0 green. `ran` is derived from the results, so a name
    # can be recorded only by a call that happened.
    results = [(name, fn()) for name, fn in terms]
    ran = [name for name, _ in results]
    ruling = [problem for _, problems in results for problem in problems]
    missing_terms, extra_terms = missing_ruling(ran)
    if missing_terms or extra_terms:
        print("FAIL: the ruling chain does not match REQUIRED_RULING. A selftest dropped from",
              file=sys.stderr)
        print("      the chain cannot go red, so nothing it guards is guarded — that is RED N:",
              file=sys.stderr)
        for k in missing_terms:
            print(f"  MISSING      {k}  — required, but nothing ran it", file=sys.stderr)
        for k in extra_terms:
            print(f"  UNDECLARED   {k}  — ran, but REQUIRED_RULING does not name it",
                  file=sys.stderr)
        return 1
    if ruling:
        print("FAIL: SELFTEST — the checker cannot be shown going red, so its verdict on the",
              file=sys.stderr)
        print("      real tree below means nothing:", file=sys.stderr)
        for problem in ruling:
            print(f"  {problem}", file=sys.stderr)
        return 1

    missing, extra = missing_checks(f)
    if missing or extra:
        print("FAIL: the registered check set does not match REQUIRED_CHECKS. A check that",
              file=sys.stderr)
        print("      stops registering is invisible to the OK guard — that is RED G:",
              file=sys.stderr)
        for k in missing:
            print(f"  MISSING  {k}", file=sys.stderr)
        for k in extra:
            print(f"  UNDECLARED  {k}", file=sys.stderr)
        return 1

    # ONE GUARD, AND IT IS NOT A LIST OF NAMES. This was a hand-maintained conjunction over ten
    # terms until #199; dropping a term left the finding computed, unprinted, and the tree green
    # (geist, RED G on #198). Every check now registers where it computes, so there is no second
    # place to keep in sync and nothing to forget.
    if not f.any():
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
        print(f"OK: {len(RULING_CONDITIONS)} ruling condition(s), each backed by an executing "
              f"lane; every 'enforced' row cites a run id:")
        for row in RULING_CONDITIONS:
            print(f"  {row['id']}: {row['status']} <- runs "
                  f"{', '.join(row['run_ids']) or '(none)'}  [{', '.join(row['lanes'])}]")
        return 0

    if f["ruling_table"]:
        print("FAIL: RULING_CONDITIONS — a row claims a ruling is enforced by something that",
              file=sys.stderr)
        print("      does not execute, or cites no run id proving it ran:", file=sys.stderr)
        for problem in ruling_table:
            print(f"  {problem}", file=sys.stderr)
        print("  -> either wire the lane so it can go red, cite the run id that proves it ran,",
              file=sys.stderr)
        print("     or downgrade the row to 'half'/'prose'. A ruling condition discharged by a",
              file=sys.stderr)
        print("     table entry is discharged by prose, which is what this table is FOR.",
              file=sys.stderr)
    if f["side_effects"]:
        print("FAIL: SIDE_EFFECTS — a battery does not declare what it does, declares it",
              file=sys.stderr)
        print("      illegally, or DOES something its declaration denies:", file=sys.stderr)
        for problem in side_effects:
            print(f"  {problem}", file=sys.stderr)
        print("  -> Geist 2026-09-05 (amended 13:05Z): a box-runnable battery never leaves the",
              file=sys.stderr)
        print("     machine and never outlives its run. Declare [], or move the arms into a VM",
              file=sys.stderr)
        print("     test and name it in SIDE_EFFECTS_OWNER.", file=sys.stderr)
        print("  -> The two halves are not equally strong, and the difference is the point.",
              file=sys.stderr)
        print("     Undeclared-but-DETECTED is a real finding: the source shows the effect.",
              file=sys.stderr)
        print("     Declared-but-undetected is deliberately NOT a finding: the detectors read",
              file=sys.stderr)
        print("     literal strings and a known table of verbs, so they are floors, and a file",
              file=sys.stderr)
        print("     is allowed to know more about itself than they can see. A clean run here",
              file=sys.stderr)
        print("     means 'no battery visibly contradicts its declaration', which is strictly",
              file=sys.stderr)
        print("     weaker than 'no battery has undeclared side effects'.", file=sys.stderr)
    if f["stale_se_debt"]:
        print("FAIL: SIDE_EFFECT_DEBT names a battery the debt no longer describes:",
              file=sys.stderr)
        for problem in stale_se_debt:
            print(f"  {problem}", file=sys.stderr)
        print("  -> remove the entry. A debt ledger that keeps paid entries stops being a count",
              file=sys.stderr)
        print("     anyone can watch go down, which is the only thing it was for.", file=sys.stderr)
    if f["wired_disarming"]:
        print("FAIL: a WIRED battery still SELF-DISARMS. It exits 0 announcing SKIP when its",
              file=sys.stderr)
        print("      backend is absent, and it runs in a CI lane, so that green is reported to",
              file=sys.stderr)
        print("      somebody who trusts it. Worse than an unwired battery, not better:",
              file=sys.stderr)
        for base in wired_disarming:
            print(f"  {base}  <-  {WIRED_VIA_WORKFLOW[base]}", file=sys.stderr)
        print("      Gate the skip on AGENT_OS_STRICT (see calendar-battery.py), or unwire it.",
              file=sys.stderr)
    if f["flake_wired_disarming"]:
        print("FAIL: a battery wired into `nix flake check` SELF-DISARMS. Same defect as the",
              file=sys.stderr)
        print("      block above, in the lane the merge gate actually reads:", file=sys.stderr)
        for base in flake_wired_disarming:
            print(f"  {base}  <-  named in flake.nix", file=sys.stderr)
        print("      Gate the skip on AGENT_OS_STRICT, or stop naming it in flake.nix.",
              file=sys.stderr)
    if f["unarmed"]:
        print("FAIL: a strict-gated battery is WIRED BUT UNARMED. The battery refuses to exit 0",
              file=sys.stderr)
        print("      on a missing backend only when AGENT_OS_STRICT=1; the step below runs it",
              file=sys.stderr)
        print("      without setting it, so an absent backend announces SKIP and the step is",
              file=sys.stderr)
        print("      green. That is the meaningless green the [self-disarming] tag names:",
              file=sys.stderr)
        for base, wf, step in unarmed:
            print(f"  {base}  <-  {wf}  step {step!r}", file=sys.stderr)
        print('      Add `env: {AGENT_OS_STRICT: "1"}` to that step — and make sure the step',
              file=sys.stderr)
        print("      actually stages the backend, which this check cannot see.", file=sys.stderr)
    if f["debt_added"]:
        print("FAIL: KNOWN_UNWIRED_DEBT GREW. It is documented as may-only-shrink, and these",
              file=sys.stderr)
        print("      names are not in the recorded baseline — each one is a test that runs in",
              file=sys.stderr)
        print(f"      NO CI lane and has just been made invisible to this check:", file=sys.stderr)
        for base in debt_added:
            print(f"  +{base}", file=sys.stderr)
        print(f"      Wire it up. Editing {os.path.basename(DEBT_BASELINE)} to admit it is a",
              file=sys.stderr)
        print("      reviewable act, not a formality.", file=sys.stderr)
    if f["debt_removed"]:
        print("FAIL: KNOWN_UNWIRED_DEBT shrank but the baseline still lists these — which is the",
              file=sys.stderr)
        print("      right direction and the wrong commit. Update the baseline in the SAME commit,",
              file=sys.stderr)
        print("      or it goes stale and silently re-permits every name it still holds:",
              file=sys.stderr)
        for base in debt_removed:
            print(f"  -{base}", file=sys.stderr)
    if f["stale"]:
        print("FAIL: stale exemption(s) in this file — a suppression list that names things no",
              file=sys.stderr)
        print("      longer true silently exempts whatever takes the name next:", file=sys.stderr)
        for base, why in stale:
            print(f"  {base}: {why}", file=sys.stderr)
    if f["unwired"]:
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
    if f["vacuous"]:
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
    if f["unlisted"]:
        print("FAIL: test-* package(s) with NO vm-tests matrix entry — these NEVER RUN in CI:",
              file=sys.stderr)
        for name in unlisted:
            print(f"  {name}", file=sys.stderr)
        print(f"  -> add each to the matrix in {args.workflow}", file=sys.stderr)
    if f["dangling"]:
        print("FAIL: vm-tests matrix entr(ies) naming no such package:", file=sys.stderr)
        for name in dangling:
            print(f"  {name}", file=sys.stderr)
        print("  -> the job would fail at `nix build` with an attribute error", file=sys.stderr)

    # THE OTHER DIRECTION. A finding can be registered — and so correctly turn the tree red —
    # while no print branch above ever names it, which leaves a red build with no diagnostic.
    # That is RED G one level down, and it is only reachable on this path, so it is checked here
    # rather than in a selftest.
    silent = f.unread()
    if silent:
        print("FAIL: check(s) reported findings that NOTHING printed — the build is red with no",
              file=sys.stderr)
        print("      diagnostic, which is the same silence RED G found in the OK guard:",
              file=sys.stderr)
        for key in silent:
            print(f"  {key}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
