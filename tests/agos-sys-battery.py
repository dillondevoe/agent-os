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

# THE WRITE VERBS, added 2026-08-24. This battery had ZERO arms asserting the {ok:false}
# contract and exactly ONE asserting rc == 0 — the least-guarded hand in the repo by that
# measure, which is how it was picked to read next. What the reading found: `volume` and
# `brightness` printed NOTHING AT ALL, in either direction. Measured on this host, where there
# is no PipeWire and no backlight:
#
#     pre-fix, backend absent:  rc=127  stdout=''      <- the `agos-notes new` shape, third time
#     pre-fix, backend present: rc=0    stdout=''      <- and the SUCCESS path said nothing either
#
# The second line is the one worth staring at. Every other defect in this family was about a
# failure being disguised as success; here SUCCESS ITSELF was indistinguishable from a no-op, in
# a hand whose every other verb returns a body. Off the Dell there is no PipeWire and no
# backlight at all, so the degrade path is the NORMAL path everywhere except one machine.
#
# The arms assert the invariant that holds in BOTH directions — rc 0 and a parseable {ok:bool}
# body — because CI has no audio sink either and an arm demanding ok:true there would be red for
# the wrong reason. That invariant is precisely what empty stdout fails, in either direction.
if syscli:
    for verb, val in (("volume", "40"), ("volume", "mute"), ("brightness", "60")):
        rc, out, err = run([syscli, verb, val])
        label = "%s %s" % (verb, val)
        check("`%s` exits 0 (a missing backend is not a usage error)" % label, rc == 0,
              "rc=%s err=%s" % (rc, err[:60]))
        check("`%s` says something on stdout" % label, out != "",
              "empty stdout cannot distinguish 'done' from 'did nothing'")
        try:
            wb = json.loads(out) if out else None
            check("`%s` -> JSON with an ok bool" % label,
                  isinstance(wb, dict) and wb.get("ok") in (True, False), out[:80])
            check("`%s` -> echoes back what was asked for" % label,
                  isinstance(wb, dict) and wb.get("verb") == verb and wb.get("requested") == val,
                  out[:80])
            # A refusal must SAY why. An {ok:false} with no reason is a silent no-op wearing a
            # field name — the caller cannot see stderr and has nothing else to go on.
            if isinstance(wb, dict) and wb.get("ok") is False:
                check("`%s` -> a refusal names a reason" % label, bool(wb.get("error")), out[:80])
        except Exception as e:
            check("`%s` parses as JSON" % label, False, str(e) + " | " + out[:60])

    # The usage arms must NOT have moved: exit 2 is still reserved for bad input, and widening
    # the JSON contract to the write verbs is exactly the change that could have swallowed them
    # into {ok:false} bodies. Asserted here so the fix cannot quietly eat its own guard rails.
    for bad in (["volume", "abc"], ["brightness", ""], ["volume"], ["brightness"]):
        rc, out, err = run([syscli] + bad)
        check("`%s` is still a USAGE error (exit 2), not an {ok:false}" % " ".join(bad or ["<none>"]),
              rc == 2, "rc=%s out=%s" % (rc, out[:60]))

print("agos-sys-battery: " + ("ALL PASS" if EX == 0 else "FAILURES"))
sys.exit(EX)
