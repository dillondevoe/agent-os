#!/usr/bin/env python3
"""Assert every locked flake input is nixpkgs — the 2026-07-28 clean-room directive, enforced.

WHY THIS EXISTS. The clean-room directive is "nixpkgs + our code only, no third-party
runtimes." Today the repo satisfies it perfectly: flake.nix declares exactly one input and
flake.lock has exactly one non-root node, `github:NixOS/nixpkgs`. That is the good case, and
it is precisely why this file is worth writing now rather than after it breaks.

Because NOTHING ENFORCED IT. The directive lived in prose — in docs, in module headers, in
comm threads — and a single line

    inputs.somelib.url = "github:someone/somelib";

would have been fetched, locked, built into the system closure, and merged with every lane
green. modules/clean-room.nix is a *runtime egress* wall and is behaviourally tested; it says
nothing about where the bytes in the closure came from at BUILD time. Those are two different
axes and only one of them had a control. This is the same class the repo keeps re-deriving:
a rule enforced by human memory is documented, not enforced. Cf. tests/vm-matrix-contract.py,
which exists for the identical reason one axis over.

Method borrowed from the task-342 Camelid eval, where `nix-store -qR` on a build result turned
"claims no runtime deps" into seven named store paths. Provenance is checkable; checking it is
cheap; so not checking it is a choice.

THREE THINGS ARE CHECKED, because they fail differently:

  * a locked node that is not nixpkgs and not in ALLOWED_NON_NIXPKGS -> a third-party
    dependency entered the closure. The headline case.
  * an ALLOWED_NON_NIXPKGS entry naming a node that no longer exists -> a stale exemption.
    Suppression lists go stale silently and then read as accounted-for; page's finding,
    2026-08-24, ported here. An exemption must keep describing reality or it is noise.
  * ZERO non-root nodes found -> the walker is blind, not the tree clean. Without this the
    check passes vacuously on a parse change, an empty file, or a schema bump, and reports
    on itself rather than on the world. This is the arm most likely to be the one that
    actually fires someday.

A nixpkgs node is identified by its LOCKED coordinates (type/owner/repo), never by the node's
NAME. A node named `nixpkgs` pointing at a fork is exactly the input this file exists to catch,
and it would sail past a name check.

THE RATCHET. Adding a non-nixpkgs input is not forbidden here — it is made to cost a
machine-checkable claim: a name, a reason, and a decision-maker, in ALLOWED_NON_NIXPKGS below.
An empty dict is the honest starting state and the fact worth defending.

DELIBERATELY NOT SILENT-SKIPPABLE. Missing flake.lock is a FAILURE, not a skip. A check that
degrades to a no-op when its input is absent is the bug it was written to catch.

Local use:
    python3 tests/flake-input-provenance-contract.py

Control arms: tests/flake-input-provenance-battery.sh, run by flake-check.yml on every push.

    This block used to LIST three arms, under the header "each MUST fail — if one does not, that
    check is not a check". They were command lines in a docstring: a MUST, in prose, executed by
    nobody, four lines under the paragraph directly above about checks that degrade to no-ops.
    They now run, along with three more and both MUST-PASS directions (a battery of only
    MUST-FAILs is satisfied by a checker that rejects everything, and a crash makes every
    MUST-FAIL arm "pass" — the MUST-PASS arms are what tell red-by-verdict from red-by-crash).

    The list is gone rather than kept-and-annotated on purpose. A reader who found three arms
    here would have reasonably taken them for the complete set, and the battery is where the set
    is defined. Two places naming one set, with nothing making them agree, is this repo's
    recurring scar. (Geist, residual on the #185 gate.)
"""
import argparse
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The only provenance that satisfies "nixpkgs-only". Matched on locked coordinates.
NIXPKGS = {"type": "github", "owner": "NixOS", "repo": "nixpkgs"}

# Exemptions. name -> reason. Each entry is a claim that must keep describing reality:
# a stale one fails this contract just as loudly as an unlisted input.
# EMPTY IS THE CORRECT STATE. Do not add an entry to make a lane green.
ALLOWED_NON_NIXPKGS: dict = {}


def _is_nixpkgs(locked: dict) -> bool:
    return all(locked.get(k) == v for k, v in NIXPKGS.items())


def _describe(locked: dict) -> str:
    if not locked:
        return "<no locked source>"
    t = locked.get("type", "?")
    if t == "github":
        return f"github:{locked.get('owner','?')}/{locked.get('repo','?')}"
    return locked.get("url") or locked.get("path") or t


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lock-json", help="control arm: use this lock content instead of flake.lock")
    args = ap.parse_args()

    if args.lock_json:
        lock = json.loads(args.lock_json)
        src = "--lock-json (control arm)"
    else:
        path = os.path.join(REPO, "flake.lock")
        if not os.path.exists(path):
            print(f"FAIL: {path} does not exist. This contract does not skip; see the module "
                  f"docstring on silent degradation.")
            return 1
        with open(path) as fh:
            lock = json.load(fh)
        src = path

    # THE ROOT NODE IS NAMED BY THE LOCK, NOT BY THIS FILE. `lock["root"]` holds the key of the
    # root node; it is `"root"` in every lock nix has written, which is exactly why the literal
    # went unnoticed. The failure if it ever differs is not an error — it is a SILENT WIDENING:
    # the real root would stop being filtered and get walked as though it were an input, and the
    # actual root key would be filtered out of a set it was never in. The first half is the
    # dangerous one, since the root node carries no `locked` and would trip the "no locked key"
    # arm, turning a healthy lock red for a reason the message would not explain.
    # Reading the field costs nothing and removes the assumption. (Geist, P3 on the #163 gate.)
    root_key = lock.get("root", "root")
    nodes = {k: v for k, v in lock.get("nodes", {}).items() if k != root_key}
    failures = []

    # Check 3 first: it is the one that decides whether the other two mean anything.
    if not nodes:
        failures.append(
            "ZERO non-root nodes found in " + src + ". A flake with no inputs at all is not a "
            "state this repo is in, so this is the walker being blind rather than the tree "
            "being clean — and a blind walker makes the checks below pass vacuously.")

    for name, node in sorted(nodes.items()):
        locked = node.get("locked", {})
        if _is_nixpkgs(locked):
            continue
        if name in ALLOWED_NON_NIXPKGS:
            continue
        failures.append(
            f"input '{name}' is {_describe(locked)}, which is not github:NixOS/nixpkgs.\n"
            f"    The 2026-07-28 clean-room directive is nixpkgs + our code only. If this input\n"
            f"    is genuinely required, add it to ALLOWED_NON_NIXPKGS with a reason and who\n"
            f"    approved it — the exemption is the cost, and it is checked for staleness.")

    for name, reason in sorted(ALLOWED_NON_NIXPKGS.items()):
        if name not in nodes:
            failures.append(
                f"stale exemption: ALLOWED_NON_NIXPKGS['{name}'] ({reason}) names an input that "
                f"is no longer in the lock. Remove it — an exemption that no longer describes "
                f"reality reads as accounted-for and suppresses nothing but attention.")

    for f in failures:
        print("FAIL: " + f)
    if failures:
        return 1

    listed = ", ".join(sorted(nodes))
    print(f"PASS flake-input-provenance: {len(nodes)} locked input(s), all github:NixOS/nixpkgs "
          f"({listed}). {len(ALLOWED_NON_NIXPKGS)} exemption(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
