#!/usr/bin/env python3
# agos-sys-battery.py — Phase 2 settings (agos-sys) acceptance harness.
# Verifies `agos-sys status` emits the stable JSON contract (network/audio/display/power).
# status is READ-ONLY and degrades to nulls when a backend is absent, so it runs anywhere.
# The mutating subcmds (volume/brightness) are NOT exercised — they change the box.
# Run: PYTHONPATH=modules python3 tests/agos-sys-battery.py
# MUTATES_SHARED_STATE — Geist's law, 2026-09-05: a box-runnable battery never mutates
# human-shared state. Read as DATA by tests/vm-matrix-contract.py, not as a comment.
MUTATES_SHARED_STATE = False

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

syscli = shutil.which("agos-sys")
if not syscli:
    print("  SKIP agos-sys-battery: agos-sys not on PATH (image not built).")
    sys.exit(0)
print("  using REAL agos-sys CLI: " + syscli)

# status -> valid JSON with the four top-level sections
rc, out, err = run([syscli, "status"])
try:
    d = json.loads(out)
    for k in ("network", "audio", "display", "power"):
        check("status JSON has '%s'" % k, k in d, out[:50])
    # network.state should be a string (e.g. "connected"/"unknown")
    check("network.state is a string", isinstance(d.get("network", {}).get("state"), str), str(d.get("network")))
except Exception as e:
    check("status parses as JSON", False, str(e) + " | out=" + out[:80])

# bad subcmd -> usage, exit 2 (graceful)
rc, out, err = run([syscli, "bogus"])
check("bad subcmd -> exit 2 (usage)", rc == 2, "rc=%s" % rc)

print("agos-sys-battery: " + ("ALL PASS" if EX == 0 else "FAILURES"))
sys.exit(EX)
