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
    "vm-matrix-contract.py",  # this file; invoked directly by flake-check.yml, not via flake.nix
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
    "bip340-battery.py",
    "escalate-consent-battery.py",
    "frontdoor-kick-battery.py",
    "transport-battery.py",
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
        if "sys.exit(0)" not in line:
            continue
        window = lines[max(0, i - SELF_DISARM_WINDOW):i]
        if any("SKIP" in w for w in window):
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
            if base in UNWIRED_BY_DESIGN or base in KNOWN_UNWIRED_DEBT:
                continue
            if not wiring_references(flake_src, tests_dir, base):
                unwired.append(path)
            elif base.endswith(".py") and self_disarms(path):
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
    return unwired, vacuous, stale


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
    ap.add_argument("--packages-json", default=None,
                    help="JSON array of package names, for testing the failing arm")
    args = ap.parse_args()

    names = json.loads(args.packages_json) if args.packages_json else flake_test_packages(SYSTEM)
    tests = {n for n in names if n.startswith("test-")}
    matrix = matrix_entries(args.workflow)

    unlisted = sorted(tests - matrix)
    dangling = sorted(matrix - tests)
    unwired, vacuous, stale = unwired_test_files(args.tests_dir, args.flake)

    if not unlisted and not dangling and not unwired and not vacuous and not stale:
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
        return 0

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
