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
    "flake-retry-decide-battery.sh": "flake-check.yml",  # the census emitter's battery; pure bash
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
    # The eight ambient-hand acceptance batteries. Each is named in flake.nix by exactly one
    # `builtins.pathExists` assert and its error string, and by nothing else in the repository.
    # The guard that names them proves they have not been DELETED; nothing proves they RUN.
    "calendar-battery.py",
    "agos-calc-battery.py",
    "agos-sys-battery.py",
    "agos-files-battery.py",
    "agos-notes-battery.py",
    "agos-doc-battery.py",
    "agos-media-battery.py",
    "agos-web-battery.py",
    # Found in the first sweep (#153): referenced only by tests/run-local.sh, a manual runner.
    "anthropic-transport-battery.py",
    "audit-signing-battery.py",
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
    "escalate-consent-battery.py",
    "transport-battery.py",
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

    unlisted = sorted(tests - matrix)
    dangling = sorted(matrix - tests)
    unwired, vacuous, stale = unwired_test_files(args.tests_dir, args.flake)
    debt_added, debt_removed = debt_ratchet(KNOWN_UNWIRED_DEBT, args.debt_baseline)

    # Ruled item 3. The selftest runs FIRST and unconditionally: if the checker cannot be shown
    # going red, its green verdict on the real table below means nothing.
    ruling = exemption_staleness_selftest()
    ruling += ruling_conditions_selftest()
    ruling += check_ruling_conditions(open(args.flake).read(), args.tests_dir)

    # Both sides of this merge added an INDEPENDENT failure mode to the same guard, so the
    # resolution is a union and not a choice. Dropping either term is the silent-pass direction:
    # the checker would still exit 0 while one of its own checks had findings.
    if (not unlisted and not dangling and not unwired and not vacuous and not stale
            and not ruling and not debt_added and not debt_removed):
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

    if debt_added:
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
    if debt_removed:
        print("FAIL: KNOWN_UNWIRED_DEBT shrank but the baseline still lists these — which is the",
              file=sys.stderr)
        print("      right direction and the wrong commit. Update the baseline in the SAME commit,",
              file=sys.stderr)
        print("      or it goes stale and silently re-permits every name it still holds:",
              file=sys.stderr)
        for base in debt_removed:
            print(f"  -{base}", file=sys.stderr)
    if ruling:
        print("FAIL: RULING_CONDITIONS — a row claims more than the repo can show:",
              file=sys.stderr)
        for problem in ruling:
            print(f"  {problem}", file=sys.stderr)
        print("  -> either wire the lane so it can go red, cite the run id that proves it ran,",
              file=sys.stderr)
        print("     or downgrade the row to 'half'/'prose'. A ruling condition discharged by a",
              file=sys.stderr)
        print("     table entry is discharged by prose, which is what this table is FOR.",
              file=sys.stderr)
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
