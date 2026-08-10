#!/usr/bin/env python3
# agos-calc-battery.py — Phase 2 calculator (agos-calc) acceptance harness.
# Verifies the eval contract: agos-calc eval '<expr>' -> {input,result,ok,messages}.
# qalc (libqalculate) is pure math, no backend — fully testable headless.
# Run: PYTHONPATH=modules python3 tests/agos-calc-battery.py
import subprocess, json, shutil, sys

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

calc = shutil.which("agos-calc")
qalc = shutil.which("qalc")

if calc:
    print("  using REAL agos-calc CLI: " + calc)
    def ev(expr): return run([calc, "eval", expr])
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
try:
    d = json.loads(out)
    check("eval '(2+3)*4' -> ok:true", d.get("ok") is True, out[:60])
    check("eval '(2+3)*4' -> result=20", "20" in str(d.get("result", "")), str(d.get("result")))
except Exception as e:
    check("eval parses", False, str(e))

# 2) irrational -> ok true, result non-empty
rc, out, err = ev("sqrt(2)")
try:
    d = json.loads(out)
    check("eval 'sqrt(2)' -> ok:true + non-empty result", d.get("ok") is True and str(d.get("result","")).strip(), out[:60])
except Exception as e:
    check("sqrt parses", False, str(e))

# 3) no expression -> usage, exit 2 (graceful, never a crash)
rc, out, err = ev("")
check("eval with no arg -> exit 2 (usage)", rc == 2, "rc=%s" % rc)

print("agos-calc-battery: " + ("ALL PASS" if EX == 0 else "FAILURES"))
sys.exit(EX)
