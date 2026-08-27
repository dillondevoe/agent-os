#!/usr/bin/env python3
"""Mutate the TREE and read the verdict off the process — the one instrument that reaches main().

Geist's third-instance row, written 2026-08-27 after #195 CONTROL 4, #200 R2 and #201 R4:

    a selftest proves the predicate; only a mutation at the call site proves the use — and the
    call site in main() is the one place no selftest reaches, because main() is what runs the
    selftests.

Every arm in vm-matrix-contract.py's own selftests drives a FUNCTION on a fixture row. None of
them drives `main()`. So all three of those findings share one shape: revert, delete or substitute
the call in `main()`, and the file goes green while the check it names has stopped happening.

This file is the other side. It builds a COPY of the real tree, plants exactly one fault in the
copy, runs vm-matrix-contract.py as a SUBPROCESS with the copy as its working directory, and
asserts a specific diagnostic came back on rc=1. Nothing here imports the checker: the only thing
observed is what the process printed, which is the only observation a neutered call site cannot
satisfy. That is member 17 — ANCHOR THE MUTATION, ANCHOR THE BASELINE, AND READ THE RESULT OFF THE
PROCESS THAT PRODUCED IT — applied to the checker instead of by it.

The CONTROL runs first and is not optional: an unmutated copy must come back rc=0. Without it
every arm below passes for the wrong reason, because a fixture that is broken on arrival reds no
matter what was planted. (It also pins the copy itself: the tree is copied rather than pointed at
with --flake/--tests-dir because flake.nix names its lanes as `tests/x.py`, relative to the repo
root — a relocated tests dir reds three ruling rows on paths alone. Same trap as the copied-file
baseline, one directory up.)

Coverage is TWO checks today, not nine. Extending is one entry in ARMS per check, and an arm that
cannot be isolated to a single check does not go in: fault B was first written as "comment the
ruling row's lane out of flake.nix", which reds the RULING selftest instead of the ruling_table
check, because ruling_conditions_selftest reads the same flake for its own control row. An arm
whose red comes from somewhere other than the check it names proves nothing about that check.
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CONTRACT = os.path.join(HERE, "vm-matrix-contract.py")
COPY = ("flake.nix", "tests", ".github")


def build_fixture(dest):
    """A copy of the tree, faithful enough that the checker says rc=0 about it."""
    os.makedirs(dest, exist_ok=True)
    for name in COPY:
        src = os.path.join(ROOT, name)
        dst = os.path.join(dest, name)
        if os.path.isdir(src):
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
    return dest


def run_contract(cwd, extra_args):
    p = subprocess.run([sys.executable, CONTRACT] + extra_args, cwd=cwd,
                       capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def drop_matrix_entry(fixture):
    """Remove one test-* from the vm-tests matrix — the shape `unlisted` exists to catch."""
    path = os.path.join(fixture, ".github/workflows/vm-tests.yml")
    src = open(path).read()
    entry = "          - test-seal-faildown\n"
    assert src.count(entry) == 1, "fixture anchor: %d matrix entries named test-seal-faildown" % src.count(entry)
    open(path, "w").write(src.replace(entry, "", 1))


def drop_ruled_file(fixture):
    """Delete a file a RULING_CONDITIONS row cites — an `enforced` row about a vanished lane."""
    path = os.path.join(fixture, "tests/identity-boot.nix")
    assert os.path.exists(path), "fixture anchor: tests/identity-boot.nix is gone from the tree"
    os.remove(path)


# (label, plant, packages_json, expected token). The token is the DIAGNOSTIC, not the rc: a check
# whose call site was neutered leaves some OTHER check to red the run, and an arm that only
# asserted rc=1 would sit there green through exactly the defect it was written for.
ARMS = (
    ("unlisted: a test-* package with no matrix entry",
     drop_matrix_entry, None, "test-seal-faildown"),
    ("unlisted: a package that exists nowhere in the tree",
     None, '["test-does-not-exist"]', "test-does-not-exist"),
    ("ruling_table: an 'enforced' row naming a file that no longer exists",
     drop_ruled_file, None, "tests/identity-boot.nix, which does not exist"),
)


def main():
    packages = sys.argv[1] if len(sys.argv) > 1 else None
    base = ["--packages-json", packages] if packages else []
    failures = []

    with tempfile.TemporaryDirectory() as tmp:
        rc, out = run_contract(build_fixture(os.path.join(tmp, "clean")), base)
        if rc != 0:
            failures.append("CONTROL: an UNMUTATED copy of the tree came back rc=%d, so every arm "
                            "below would red on the copy rather than on its fault:\n%s" % (rc, out))
            print("\n".join(failures), file=sys.stderr)
            return 1

    for i, (label, plant, packages_json, token) in enumerate(ARMS):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = build_fixture(os.path.join(tmp, "arm%d" % i))
            if plant:
                plant(fixture)
            args = ["--packages-json", packages_json] if packages_json else base
            rc, out = run_contract(fixture, args)
            if rc == 0:
                failures.append("ARM NOT CAUGHT (rc=0): %s" % label)
            elif token not in out:
                failures.append("ARM red for the WRONG REASON: %s\n  expected %r in the "
                                "diagnostic; got:\n%s" % (label, token, out))
            else:
                print("  arm red, named: %s" % label)

    if failures:
        print("FAIL: vm-matrix-contract.py's main() did not act on a planted tree fault — the",
              file=sys.stderr)
        print("      check exists and its selftest passes, but nothing calls it on the real run:",
              file=sys.stderr)
        for f in failures:
            print("  %s" % f, file=sys.stderr)
        return 1
    print("vm-matrix-call-site-arms: ALL PASS (%d arms, control green)" % len(ARMS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
