#!/usr/bin/env python3
# agos-sys-battery.py — Phase 2 settings (agos-sys) acceptance harness.
# Verifies `agos-sys status` emits the stable JSON contract (network/audio/display/power).
# status is READ-ONLY and degrades to nulls when a backend is absent, so it runs anywhere.
# The mutating subcmds (volume/brightness) are NOT exercised — they change the box.
# Run: PYTHONPATH=modules python3 tests/agos-sys-battery.py
import subprocess, json, shutil, sys
import os

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
if not syscli and os.environ.get("AGENT_OS_STRICT") == "1":
    # Wired into flake.nix as `agos-sys-contract`, where the CLI IS on PATH. In that
    # lane an absent tool is a BUILD FAILURE, not a reason to pass: a battery that
    # exits 0 when its subject is missing reports a green about nothing.
    print("  FAIL agos-sys-battery: AGENT_OS_STRICT=1 and `agos-sys` is not on PATH.")
    sys.exit(1)
if not syscli:
    print("  SKIP agos-sys-battery: agos-sys not on PATH (image not built).")
    sys.exit(0)
print("  using REAL agos-sys CLI: " + syscli)

# status -> valid JSON with the four top-level sections
rc, out, err = run([syscli, "status"])
# `exit 2` is reserved for usage errors in every hand; a healthy `status` must exit 0.
# This site was skipped by the mechanical pass because the NEXT arm asserts rc == 2, and a
# lookahead window cannot tell "this call is checked" from "a nearby call is checked".
check("`status` exits 0", rc == 0, "rc=%s err=%s" % (rc, err[:60]))
# THE CHANNEL NOBODY WAS READING, and this hand is the one most exposed to it. `status` shells
# out to nmcli, wpctl, brightnessctl and upower, EVERY one of which is absent in the contract
# sandbox — so every one of those calls fails, and the documented behaviour is to swallow that
# and report null. Nothing asserted the swallowing. A hand that emitted four "command not
# found" lines on stderr and the identical all-null JSON on stdout passed this battery
# unchanged. The degraded case is not the exotic one here; it is the ONLY case CI ever runs.
check("`status` says nothing on stderr, even with every backend absent", err == "", repr(err[:120]))
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
# The MIRROR-IMAGE of the arm above, and why one without the other proves little: "silent on
# success" and "loud on usage error" are a single rule about WHERE output goes, and a hand that
# had been made silent everywhere would satisfy the success arm perfectly while telling a
# confused caller nothing. rc=2 says something is wrong; only stderr says what.
check("bad subcmd -> SAYS WHY, on stderr", err != "", repr(err[:80]))
check("bad subcmd -> stdout stays clean (the usage text is not JSON)", out == "", repr(out[:80]))

print("agos-sys-battery: " + ("ALL PASS" if EX == 0 else "FAILURES"))
sys.exit(EX)
