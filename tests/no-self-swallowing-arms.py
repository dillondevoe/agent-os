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

THE SHELL HALF, added 2026-09-02 after #251. This lint scanned 44 `.py` batteries and none of
the 21 `.sh` ones, and I said so in two comms before fixing it -- which is the same "a good
check asked of the wrong set" shape as #252 and #253, sitting in my own check.

The shell defect it covers is ONE class, the one there is EVIDENCE for, because #251 found it
live in `tests/agent-loop-battery.sh`:

    hasnt() { grep -qF -- "$2" "$1" && fail "$3"; return 0; }

On a missing or empty file grep fails, `&& fail` never fires, and the arm passes. "The bad
string is not in the output" is vacuously true of a run that produced NO output. Its neighbour
`has()` -- same shape with `||` -- is fail-SAFE for free on the same inputs, so the pair reads
symmetric while failing in opposite directions.

SCOPE, stated so a green is not over-read: this flags negative-assertion HELPERS (a shell
function whose body greps and fails on a MATCH, with no substrate test). It deliberately does
NOT flag inline negative assertions -- #252's arm I is one, and it is correct, because it
carries its own `[ -f ]` guard. Helpers are where the fail-open generalises across every call
site, which was #251's actual lesson: the guarantee lived in call-site ordering rather than in
the helper. Four other shell hazard classes were hand-audited clean the same day (fail-in-
subshell, silently-skipped guarded arms, self-disarm, unlisted batteries) and are NOT covered
here; that audit is in the comms, not in this file.

Exit 1 lists every site. `--selftest` runs the control arms.
"""
import ast
import pathlib
import re
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


# A shell function definition, one-line or braced across lines.
_SH_FUNC = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(\)\s*\{(.*?)\}\s*$',
                      re.MULTILINE | re.DOTALL)
# grep ... && <something that records a failure>
_NEG_ASSERT = re.compile(r'\bgrep\b[^\n;]*&&\s*(fail|bad|die|err)\b')
# any test that establishes the file exists / is non-empty before asserting about it
_SUBSTRATE = re.compile(r'\[\s*-[sfe]\s')


def scan_shell(text, label):
    """[(line, name)] for negative-assertion helpers with no substrate requirement.

    The failure mode is fail-OPEN: grep fails on a missing/empty file, `&& fail` never fires,
    the arm passes. Reported per HELPER, because a helper's guarantee is spent at every call
    site at once.
    """
    out = []
    for m in _SH_FUNC.finditer(text):
        name, body = m.group(1), m.group(2)
        if not _NEG_ASSERT.search(body):
            continue
        if _SUBSTRATE.search(body):
            continue
        out.append((text[:m.start()].count("\n") + 1, name))
    return out


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


# The REAL pre-fix helper from tests/agent-loop-battery.sh, and the REAL fix. Not an invented
# shape: a fixture written to match the detector proves only that I can write two things alike.
SH_BAD_FIXTURE = '''
has()   { grep -qF -- "$2" "$1" || fail "$3"; }
hasnt() { grep -qF -- "$2" "$1" && fail "$3"; return 0; }
'''
SH_GOOD_FIXTURE = '''
has()   { grep -qF -- "$2" "$1" || fail "$3"; }
hasnt() {
  [ -s "$1" ] || fail "$3 (substrate absent or empty: $1)"
  grep -qF -- "$2" "$1" && fail "$3"
  return 0
}
'''


def shell_selftest():
    ok = True
    bad = scan_shell(SH_BAD_FIXTURE, "<sh-bad>")
    # CONTROL: the real pre-fix hasnt MUST be caught, or the shell half is decorative.
    if [n for _, n in bad] == ["hasnt"]:
        print("  ok   SH CONTROL: the pre-fix hasnt() IS detected, and has() is not")
    else:
        print("  FAIL SH CONTROL: expected exactly ['hasnt'], got %r" % (bad,))
        ok = False
    good = scan_shell(SH_GOOD_FIXTURE, "<sh-good>")
    # PERMITTING: the shipped fix must NOT be flagged, or the lint reds the tree it just fixed.
    if not good:
        print("  ok   SH PERMITTING: the substrate-guarded hasnt() is NOT flagged")
    else:
        print("  FAIL SH PERMITTING: the fixed helper was flagged %r" % (good,))
        ok = False
    return ok


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
    return shell_selftest() and ok


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

    sh_findings = []
    sh_scanned = 0
    for p in sorted(root.glob("*.sh")):
        sh_scanned += 1
        for line, name in scan_shell(p.read_text(encoding="utf-8"), str(p)):
            sh_findings.append((p, line, name))

    # BOTH counts, always. "scanned 44 test files" was true and read as full coverage while
    # the shell half was zero -- the number has to name what it did not look at.
    print("\nscanned %d .py and %d .sh test files under %s" % (scanned, sh_scanned, root))
    for p, line, name in sh_findings:
        print("  FAIL %s:%d — %s() asserts an ABSENCE with no substrate requirement; grep "
              "fails on a missing or empty file, so `&& fail` never fires and the arm passes "
              "vacuously. Require `[ -s \"$file\" ]` first." % (p, line, name))
    if not findings and not sh_findings:
        print("no self-swallowing arms found")
        sys.exit(0)
    for p, line, caught in findings:
        print("  FAIL %s:%d — a try/except %s wraps a body that raises its own failure "
              "signal; the handler will report the failure as a pass" % (p, line, caught))
    if findings:
        print("\n%d python site(s). Use `else:` for the accepted case, and name WHICH rejection "
              "in the handler." % len(findings))
    if sh_findings:
        print("\n%d shell site(s). Require the substrate before asserting an absence."
              % len(sh_findings))
    sys.exit(1)


if __name__ == "__main__":
    main()
