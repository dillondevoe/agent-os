#!/usr/bin/env python3
# agos-calc-battery.py — Phase 2 calculator (agos-calc) acceptance harness.
# Verifies the eval contract: agos-calc eval '<expr>' -> {input,result,ok,messages}.
# qalc (libqalculate) is pure math, no backend — fully testable headless.
# Run: PYTHONPATH=modules python3 tests/agos-calc-battery.py
#
# THE FALLBACK DOES NOT SPEAK FOR THE WHOLE CONTRACT, AND NOW IT SAYS SO.
# When agos-calc is absent this file drives qalc and WRAPS its output in a JSON
# envelope written right here. Any arm that asserts a property of that envelope is
# comparing the shim against the shim — the second operand supplied by the same hand
# that wrote the comparison (docs/cancelled-boundaries.md member 18, arriving inside
# the remedy for member 18). Such arms are SKIP-ARM'd on the fallback path, by name,
# with the reason printed. The arms that survive are the ones qalc actually answers.
# Ruled by geist 2026-08-27 (option B over "model the contract in the shim"): the cost
# is that coverage differs by path, and the cost is payable BECAUSE IT IS PRINTED.
#
# `SKIP-ARM` is deliberately NOT spelled `SKIP`. The battery-level `SKIP` line below
# means "this file asserted nothing at all" and is what the vm-matrix contract's
# self_disarms() heuristic looks for near a sys.exit(0). An arm-level skip is a
# different claim, and one grep should not return two meanings.
import subprocess, json, shutil, sys

EX = 0
DRIVEN = 0
SKIPPED = 0

def check(name, cond, detail=""):
    global EX, DRIVEN
    DRIVEN += 1
    print(("  PASS " if cond else "  FAIL ") + name + (("  — " + detail) if detail else ""))
    if not cond: EX = 1

def skip_arm(name, why):
    global SKIPPED
    SKIPPED += 1
    print("  SKIP-ARM " + name + "  — " + why)

def run(cmd):
    try:
        o = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return o.returncode, o.stdout.strip(), o.stderr.strip()
    except FileNotFoundError:
        return 127, "", "not found: " + " ".join(cmd)
    except Exception as e:
        return 1, "", str(e)

calc = shutil.which("agos-calc")
qalc = shutil.which("qalc")
FALLBACK = False

if calc:
    print("  using REAL agos-calc CLI: " + calc)
    def ev(expr): return run([calc, "eval", expr])
elif qalc:
    print("  agos-calc absent — fallback: qalc (envelope arms are SKIP-ARM'd, see header)")
    FALLBACK = True
    def ev(expr):
        r = run([qalc, "-t", expr])
        return r[0], '{"input":"%s","result":%s,"ok":%s,"messages":null}' % (
            expr, json.dumps(r[1]), "true" if r[0] == 0 and r[1] else "false"), ""
else:
    print("  SKIP agos-calc-battery: neither agos-calc nor qalc on PATH (image not built).")
    sys.exit(0)

# `ok` is computed by the shim on the fallback path, so asserting it there asserts
# nothing about the backend. Named once, used by both envelope arms.
_ENVELOPE_WHY = "`ok` is computed by this file's shim, not by a calculator"

# 1) known arithmetic -> ok true, result contains 20
rc, out, err = ev("(2+3)*4")
try:
    d = json.loads(out)
    if FALLBACK:
        skip_arm("eval '(2+3)*4' -> ok:true", _ENVELOPE_WHY)
    else:
        check("eval '(2+3)*4' -> ok:true", d.get("ok") is True, out[:60])
    check("eval '(2+3)*4' -> result=20", "20" in str(d.get("result", "")), str(d.get("result")))
except Exception as e:
    check("eval parses", False, str(e))

# 2) irrational -> ok true, result non-empty. SPLIT: the `ok` half is the envelope,
# the non-empty half is qalc actually computing something. They were one assert and
# a single verdict over two operands hid which one the fallback could speak to.
rc, out, err = ev("sqrt(2)")
try:
    d = json.loads(out)
    if FALLBACK:
        skip_arm("eval 'sqrt(2)' -> ok:true", _ENVELOPE_WHY)
    else:
        check("eval 'sqrt(2)' -> ok:true", d.get("ok") is True, out[:60])
    check("eval 'sqrt(2)' -> non-empty result", bool(str(d.get("result", "")).strip()), out[:60])
except Exception as e:
    check("sqrt parses", False, str(e))

# 3) no expression -> usage, exit 2 (graceful, never a crash).
# This is agos-calc's OWN usage contract. qalc has no such contract and the shim
# passes its rc straight through, so under fallback this arm would be testing a
# number the shim relays from an unrelated program. Skipped by name, not modelled.
if FALLBACK:
    skip_arm("eval with no arg -> exit 2 (usage)",
             "qalc has no usage contract; modelling one in the shim would test the shim")
else:
    rc, out, err = ev("")
    check("eval with no arg -> exit 2 (usage)", rc == 2, "rc=%s" % rc)

TOTAL = DRIVEN + SKIPPED
trailer = "agos-calc-battery: " + ("ALL PASS" if EX == 0 else "FAILURES")
trailer += " (%d/%d arms" % (DRIVEN, TOTAL)
if SKIPPED:
    trailer += "; %d SKIP-ARM under fallback" % SKIPPED
trailer += ")"
print(trailer)
sys.exit(EX)
