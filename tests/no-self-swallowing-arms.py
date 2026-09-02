#!/usr/bin/env python3
"""Refuse a test arm that swallows its own failure signal.

THE DEFECT, found 2026-09-02 in providers-battery arm J and reproduced before fixing:

    try:
        load_providers(path)
        raise AssertionError("broken yaml should raise, not silently degrade")
    except Exception as e:
        check(True, "broken yaml raised %r (loud, not silent)" % type(e).__name__)

`AssertionError` IS an `Exception`, so the handler catches the arm's OWN failure signal and
reports it as the rejection under test. The arm printed PASS *precisely when* the module
silently degraded — a complete inversion, in the arm whose entire job was to catch that
degrade. Nothing about the code reads wrong; both halves are individually reasonable.

This is a LINT, not a battery, because the property is structural and cheap to check across
the whole suite, and because the pattern is invisible in a passing run by construction. Same
reasoning as the checks that assert on the shipped artifact rather than a copy: the failure
mode here is silence, so the control has to be something other than a green test run.

Exit 1 lists every site. `--selftest` runs the control arms.
"""
import ast
import pathlib
import sys

SWALLOWING = {"Exception", "BaseException"}


def _sentinel_raises(node):
    """Does this try-body raise AssertionError / assert False as a FAILURE SIGNAL?"""
    for n in ast.walk(node):
        if isinstance(n, ast.Raise):
            f = getattr(n.exc, "func", None)
            if getattr(f, "id", None) == "AssertionError":
                return True
            if getattr(n.exc, "id", None) == "AssertionError":
                return True
        if isinstance(n, ast.Assert) and isinstance(n.test, ast.Constant) and not n.test.value:
            return True
    return False


def scan_source(text, label):
    """Return [(line, handler-names)] for each try that swallows its own failure signal."""
    out = []
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        # ABSENT vs BROKEN: an unparseable test file is a finding, not a clean scan.
        return [(getattr(e, "lineno", 0), ["<unparseable: %s>" % e.msg])]
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try) or not _sentinel_raises(node):
            continue
        for h in node.handlers:
            t = h.type
            if t is None:
                names = ["<bare except>"]
            elif isinstance(t, ast.Name):
                names = [t.id]
            elif isinstance(t, ast.Tuple):
                names = [e.id for e in t.elts if isinstance(e, ast.Name)]
            else:
                names = []
            caught = [n for n in names if n in SWALLOWING or n == "<bare except>"]
            if caught:
                out.append((node.lineno, caught))
    return out


BAD_FIXTURE = '''
def arm():
    try:
        thing()
        raise AssertionError("thing should have raised")
    except Exception as e:
        check(True, "raised %r" % e)
'''

GOOD_FIXTURE = '''
def arm():
    try:
        thing()
    except ValueError as e:
        check("bad input" in str(e), "must name the rejection: %s" % e)
    else:
        check(False, "thing was accepted")

def unrelated():
    # A broad handler with no sentinel raise in the body is NOT this defect.
    try:
        risky()
    except Exception:
        pass
'''


def selftest():
    ok = True
    bad = scan_source(BAD_FIXTURE, "<bad>")
    # CONTROL ARM. Without it, a detector that found NOTHING — a broken walk, a typo in the
    # node type — would report a clean tree and every real site would pass unnoticed. An
    # absence assertion is vacuous until presence is shown detectable.
    if bad:
        print("  ok   CONTROL: the known-bad fixture IS detected (%r)" % (bad,))
    else:
        print("  FAIL CONTROL: the known-bad fixture was NOT detected — this lint is vacuous")
        ok = False
    good = scan_source(GOOD_FIXTURE, "<good>")
    # PERMITTING ARM. Without it, a detector that flagged EVERY try/except would pass the
    # control arm while making the lint unusable.
    if not good:
        print("  ok   PERMITTING: a correctly-written arm and a broad handler are NOT flagged")
    else:
        print("  FAIL PERMITTING: clean source was flagged %r" % (good,))
        ok = False
    return ok


def main():
    if "--selftest" in sys.argv:
        print("no-self-swallowing-arms selftest")
        sys.exit(0 if selftest() else 1)

    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "tests")
    if not root.is_dir():
        print("no-self-swallowing-arms: %s is not a directory" % root, file=sys.stderr)
        sys.exit(2)

    print("no-self-swallowing-arms selftest (the lint's own control arms)")
    if not selftest():
        sys.exit(1)

    findings = []
    scanned = 0
    for p in sorted(root.glob("*.py")):
        scanned += 1
        for line, caught in scan_source(p.read_text(encoding="utf-8"), str(p)):
            findings.append((p, line, caught))

    print("\nscanned %d test files under %s" % (scanned, root))
    if not findings:
        print("no self-swallowing arms found")
        sys.exit(0)
    for p, line, caught in findings:
        print("  FAIL %s:%d — a try/except %s wraps a body that raises its own failure "
              "signal; the handler will report the failure as a pass" % (p, line, caught))
    print("\n%d site(s). Use `else:` for the accepted case, and name WHICH rejection in the "
          "handler." % len(findings))
    sys.exit(1)


if __name__ == "__main__":
    main()
