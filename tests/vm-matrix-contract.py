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

BOTH DIRECTIONS ARE CHECKED, because they fail differently:

  * a test-* package with NO matrix entry  -> the test silently never runs. This is the
    dangerous direction, and it is silent by construction.
  * a matrix entry with NO test-* package  -> the job fails at `nix build` with a confusing
    attribute error. Not dangerous, but it should fail here with a sentence that says what is
    wrong instead of there with a resolution trace.

DELIBERATELY NOT SILENT-SKIPPABLE. If PyYAML is missing this exits non-zero rather than passing.
A check that quietly degrades to a no-op when a dependency is absent is precisely the class of
bug it was written to catch — see docs/cancelled-boundaries.md, members 3, 8 and 10.

Local use:
    python3 tests/vm-matrix-contract.py

Control arm (this MUST fail — if it does not, the check is not a check):
    python3 tests/vm-matrix-contract.py --packages-json '["test-does-not-exist"]'
"""
import argparse
import json
import subprocess
import sys

WORKFLOW = ".github/workflows/vm-tests.yml"
SYSTEM = "x86_64-linux"


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
    ap.add_argument("--packages-json", default=None,
                    help="JSON array of package names, for testing the failing arm")
    args = ap.parse_args()

    names = json.loads(args.packages_json) if args.packages_json else flake_test_packages(SYSTEM)
    tests = {n for n in names if n.startswith("test-")}
    matrix = matrix_entries(args.workflow)

    unlisted = sorted(tests - matrix)
    dangling = sorted(matrix - tests)

    if not unlisted and not dangling:
        print(f"OK: {len(tests)} test-* package(s), all present in the vm-tests matrix:")
        for name in sorted(tests):
            print(f"  {name}")
        return 0

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
