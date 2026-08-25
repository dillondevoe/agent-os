#!/usr/bin/env python3
# skip-exit-swallows-arms-contract.py — a battery must not exit 0 after an arm has run.
#
# THE DEFECT, found 2026-08-24 in two files at once. A battery's skip path is written when
# nothing runs before it, so `sys.exit(0)` is correct. Later an arm is placed ABOVE it --
# often deliberately, to assert something that needs no fixture and can therefore run on
# every host -- and that exit silently discards the arm's verdict on exactly the hosts the
# arm was added for. Neither line is edited. Nothing goes red. The battery prints FAIL and
# reports success.
#
#   calendar-battery   : span arms above a no-CLI skip     -> FAIL printed, exit 0
#   agos-media-battery : absent-backend arms above the      -> FAIL printed, exit 0
#                        fixture skip, whose own comment
#                        says they are "DELIBERATELY PLACED
#                        ABOVE THE FIXTURE SKIP"
#
# I found both by grepping, and a hand sweep is the control this repo keeps watching fail:
# it is right the day it is run and silent every day after. This is the ratchet version.
#
# WHY `ast` AND NOT grep. My first sweep produced two false positives, and both would have
# forced a WRONG fix if I had trusted it:
#   1. agos-media-battery matched on the string `sys.exit(0)` inside a COMMENT I had just
#      written about sys.exit(0).
#   2. agent-loop-dispatch-battery:461 is a real `sys.exit(0)` textually below real check()
#      calls -- but those calls live inside functions invoked AFTER that line, so nothing
#      has run when it exits. Text order is not execution order, and only a parse knows.
# The detector therefore looks at MODULE-LEVEL statements only, and never descends into a
# function or class body.
#
# Run: python3 tests/skip-exit-swallows-arms-contract.py
import ast, os, sys

EX = 0
def check(name, cond, detail=""):
    global EX
    print(("  PASS " if cond else "  FAIL ") + name + (("  — " + detail) if detail else ""))
    if not cond: EX = 1

TESTS = os.environ.get("AGOS_TESTS_DIR") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)))

def _toplevel(body):
    """Yield statements reachable at module level, NOT descending into def/class."""
    for st in body:
        yield st
        if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for f in ("body", "orelse", "finalbody"):
            yield from _toplevel(getattr(st, f, []) or [])
        for h in getattr(st, "handlers", []) or []:
            yield from _toplevel(h.body)

def _is_check_call(node):
    return (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name) and node.value.func.id == "check")

def _exit_zero_lineno(node):
    """Return lineno if this statement is a module-level `sys.exit(0)` / `exit(0)`."""
    if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
        return None
    f = node.value.func
    name = (f.attr if isinstance(f, ast.Attribute) else
            f.id if isinstance(f, ast.Name) else None)
    if name != "exit":
        return None
    args = node.value.args
    if len(args) == 1 and isinstance(args[0], ast.Constant) and args[0].value == 0:
        return node.lineno
    return None

def offenders(src, path="<src>"):
    """(exit_lineno, first_arm_lineno) for every module-level exit(0) after an arm."""
    tree = ast.parse(src, filename=path)
    stmts = list(_toplevel(tree.body))
    arms = sorted(n.lineno for n in stmts if _is_check_call(n))
    if not arms:
        return []
    first = arms[0]
    out = []
    for n in stmts:
        ln = _exit_zero_lineno(n)
        if ln is not None and ln > first:
            out.append((ln, first))
    return out

files = sorted(f for f in os.listdir(TESTS) if f.endswith(".py"))
scanned, armed, found = 0, 0, []
for f in files:
    p = os.path.join(TESTS, f)
    try:
        src = open(p, encoding="utf-8").read()
    except OSError as e:
        check("readable: " + f, False, str(e)); continue
    try:
        tree = ast.parse(src, filename=p)
    except SyntaxError as e:
        # A battery that does not parse is not a pass. Say so rather than skipping it.
        check("parses: " + f, False, str(e)); continue
    scanned += 1
    if any(_is_check_call(n) for n in _toplevel(tree.body)):
        armed += 1
    for ln, first in offenders(src, p):
        found.append("%s:%d exits 0 after a module-level arm at line %d" % (f, ln, first))

for msg in found:
    check("no swallowed arms: " + msg.split(":")[0], False, msg)
check("no battery exits 0 after a module-level arm has run", not found,
      "%d offender(s)" % len(found))

# ---------------------------------------------------------------------------
# VACUITY ARMS. Every number above is a count, and a count of zero is the same shape whether
# the tree is clean or the walker is blind. Validate the measurement before believing it.
check("vacuity: the scan actually read a directory of batteries", scanned >= 20,
      "scanned=%d" % scanned)
check("vacuity: and found module-level arms in a real share of them", armed >= 8,
      "armed=%d of %d" % (armed, scanned))

# CONTROL ARM. Without this, a detector that returned [] for everything would print a clean
# green above and prove nothing. This is the pre-fix shape of calendar-battery, verbatim in
# structure: an arm, then a skip that exits 0.
_PRE_FIX = '''
import sys
def check(n, c, d=""): pass
check("an arm that runs on every host", False)
if not_on_path:
    print("SKIP")
    sys.exit(0)
check("an arm that needs the CLI", True)
'''
check("control: the detector FIRES on the pre-fix shape", len(offenders(_PRE_FIX)) == 1,
      "offenders=%r" % (offenders(_PRE_FIX),))

# CONTROL ARM 2, for the false positive that would have forced a wrong fix. agent-loop-
# dispatch-battery's exit(0) sits textually below check() calls that live inside functions
# called afterwards. A text-order detector flags it; this one must not.
_FALSE_POSITIVE = '''
import sys
def check(n, c, d=""): pass
def test_a():
    check("inside a function, runs later", True)
def main():
    if missing:
        print("SKIP")
        sys.exit(0)
    test_a()
main()
'''
check("control: and does NOT fire on arms that live inside functions",
      offenders(_FALSE_POSITIVE) == [], "offenders=%r" % (offenders(_FALSE_POSITIVE),))

# CONTROL ARM 3: an exit(0) BEFORE any arm is the legitimate shape -- most batteries open
# with a "subject not on PATH" skip and that one is correct. Flagging it would make the
# ratchet unshippable, and a rule that must be exempted everywhere is the wrong rule.
_LEGIT = '''
import sys
def check(n, c, d=""): pass
if not cli:
    print("SKIP")
    sys.exit(0)
check("every arm needs the CLI", True)
'''
check("control: and does NOT fire on a skip that precedes every arm",
      offenders(_LEGIT) == [], "offenders=%r" % (offenders(_LEGIT),))

print("skip-exit-swallows-arms-contract: " + ("ALL PASS" if EX == 0 else "FAILURES"))
sys.exit(EX)
