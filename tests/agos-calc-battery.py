#!/usr/bin/env python3
# agos-calc-battery.py — Phase 2 calculator (agos-calc) acceptance harness.
# Verifies the eval contract: agos-calc eval '<expr>' -> {input,result,ok,messages}.
# qalc (libqalculate) is pure math, no backend — fully testable headless.
# Run: PYTHONPATH=modules python3 tests/agos-calc-battery.py
import subprocess, json, os, shutil, sys

EX = 0
def check(name, cond, detail=""):
    global EX
    print(("  PASS " if cond else "  FAIL ") + name + (("  — " + detail) if detail else ""))
    if not cond: EX = 1

def run(cmd):
    try:
        o = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return o.returncode, o.stdout.strip(), o.stderr.strip()
    except FileNotFoundError:
        return 127, "", "not found: " + " ".join(cmd)
    except Exception as e:
        return 1, "", str(e)

# STRICT MODE (AGENT_OS_STRICT=1), added 2026-08-24 with the derivation that wires this file.
# Without it, wiring this battery would buy nothing: it exits 0 when `agos-calc` is absent, so a
# derivation that silently lost the dependency would stay green while testing NOTHING. That is
# the state vm-matrix-contract.py calls `vacuous` — wired and self-disarming — and it is worse
# than the unwired debt it replaces, because the debt at least appears on a ledger someone reads.
#
# Strict mode ALSO kills the qalc fallback, which is the sharper half. That fallback is right for
# a developer at a half-built box, and wrong for CI: qalc is libqalculate, i.e. someone else's
# program. A green from the fallback says the math library works and says nothing about
# `agos-calc`, which is the only thing in this repo under test. THE SUBJECT MUST BE THE SUBJECT.
STRICT = os.environ.get("AGENT_OS_STRICT") == "1"

calc = shutil.which("agos-calc")
qalc = shutil.which("qalc")

if calc:
    print("  using REAL agos-calc CLI: " + calc)
    def ev(expr): return run([calc, "eval", expr])
elif STRICT:
    print("  FAIL agos-calc-battery: AGENT_OS_STRICT=1 and `agos-calc` is not on PATH.")
    print("       The fallback is deliberately NOT taken here: qalc is libqalculate, not the")
    print("       hand under test. Whatever was meant to put agos-calc on PATH did not.")
    sys.exit(1)
elif qalc:
    print("  agos-calc absent — fallback: qalc")
    def ev(expr):
        r = run([qalc, "-t", expr])
        return r[0], '{"input":"%s","result":%s,"ok":%s,"messages":null}' % (
            expr, json.dumps(r[1]), "true" if r[0] == 0 and r[1] else "false"), ""
else:
    print("  SKIP agos-calc-battery: neither agos-calc nor qalc on PATH (image not built).")
    sys.exit(0)

# 1) known arithmetic -> ok true, result contains 20
rc, out, err = ev("(2+3)*4")
check("`eval (2+3)*4` exits 0", rc == 0, "rc=%s err=%s" % (rc, err[:60]))
# THE CHANNEL NOBODY WAS READING. `err` has been captured by run() since this file was
# written and, on every success path, thrown away — it appeared only inside failure DETAIL
# strings, which are printed only when some OTHER arm has already gone red. A hand that
# answered correctly on stdout while spraying a backend's warnings on stderr would pass
# every arm in this file. The contract is that a healthy call is SILENT there.
check("eval '(2+3)*4' -> says nothing on stderr", err == "", repr(err[:80]))
try:
    d = json.loads(out)
    check("eval '(2+3)*4' -> ok:true", d.get("ok") is True, out[:60])
    # The LABEL says `result=20`; the condition said `"20" in result`, which is a different
    # and weaker claim — "120", "20.5" and "3.1420" all satisfy it. Same disguise as the
    # agos-web chars arm: a label promising more than its condition checks. Equality is safe
    # to assert rather than approximate because the hand builds this field with `qalc -t`,
    # whose whole purpose is to print the value and nothing else; CI confirms it emits
    # exactly "20". Verified against the log before tightening, not assumed from the flag.
    check("eval '(2+3)*4' -> result=20", str(d.get("result", "")).strip() == "20", repr(d.get("result")))
except Exception as e:
    check("eval parses", False, str(e))

# 2) irrational -> ok true, result non-empty
rc, out, err = ev("sqrt(2)")
check("`eval sqrt(2)` exits 0", rc == 0, "rc=%s err=%s" % (rc, err[:60]))
try:
    d = json.loads(out)
    check("eval 'sqrt(2)' -> ok:true + non-empty result", d.get("ok") is True and str(d.get("result","")).strip(), out[:60])
except Exception as e:
    check("sqrt parses", False, str(e))

# 3) no expression -> usage, exit 2 (graceful, never a crash).
#
# SPLIT INTO TWO 2026-08-24, because the single check's LABEL described an invocation it did
# not make. It said "no arg" and passed ev("") — which sends `agos-calc eval ""`, i.e. ONE
# empty argument. The hand's `[ "$#" -eq 0 ]` guard therefore never fired on this path, and
# the two cases were never distinguished by anything. The empty-expression case was a real
# rc=0 and had been since the hand was written; the zero-argument case, the one the label
# named, was never tested at all.
rc, out, err = run([calc, "eval"]) if calc else (2, "", "")
if calc:
    check("eval with ZERO arguments -> exit 2 (usage)", rc == 2, "rc=%s" % rc)
else:
    print("  n/a  eval with ZERO arguments: needs the real agos-calc CLI, not the qalc fallback")

rc, out, err = ev("")
check("eval with an EMPTY expression -> exit 2 (usage)", rc == 2, "rc=%s" % rc)
# The MIRROR-IMAGE of the arm above, and the reason both are needed: "silent on success" and
# "loud on usage error" are one rule about where output goes, and a hand that was silent
# EVERYWHERE would satisfy the success arm perfectly. rc=2 tells the caller something went
# wrong; only stderr tells them what. Asserted against the real CLI only — the qalc fallback
# fabricates an empty stderr in its shim, so this arm would be measuring the shim, not a hand.
if calc:
    check("eval with an EMPTY expression -> SAYS WHY, on stderr", err != "", repr(err[:80]))

# The degrade path a caller actually hits, and it had no arm of any kind: an expression that
# does not evaluate. Deliberately NOT asserting ok:false — whether qalc rejects "2+" or coerces
# it is qalc's business and unmeasurable from the host this arm was written on, and an arm
# written blind is how a flaky red ships. What IS the hand's business, and is asserted, is that
# a bad expression stays inside the uniform contract instead of crashing out of it: rc 0, a
# silent stderr, parseable JSON, a boolean ok, and the input echoed back.
if calc:
    rc, out, err = ev("2+")
    check("`eval 2+` (bad expression) exits 0, not a crash", rc == 0, "rc=%s err=%s" % (rc, err[:60]))
    check("eval '2+' -> says nothing on stderr", err == "", repr(err[:80]))
    try:
        bad = json.loads(out)
        check("eval '2+' -> JSON with a boolean ok", bad.get("ok") in (True, False), out[:80])
        check("eval '2+' -> echoes the input back", bad.get("input") == "2+", out[:80])
    except Exception as e:
        check("eval '2+' parses", False, str(e) + " | out=" + out[:80])

print("agos-calc-battery: " + ("ALL PASS" if EX == 0 else "FAILURES"))
sys.exit(EX)
