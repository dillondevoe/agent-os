#!/usr/bin/env python3
# agos-web-battery.py — Phase 2 web (agos-web) acceptance harness.
# Verifies the read-only contract: fetch <url> -> {ok,url,title,text,chars,author,date}.
# Two checks, one needs no network:
#   1) http(s)-only guard: fetch 'file://...' -> ok:false "url must be http(s)" (OFFLINE, always runs)
#   2) live fetch of a public URL -> valid JSON with ok bool (either true or false is valid;
#      a network failure surfaces as ok:false, which is still the correct contract)
# Runs SKIP if agos-web isn't on PATH (image not built in this env).
# Run: PYTHONPATH=modules python3 tests/agos-web-battery.py
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

wcli = shutil.which("agos-web")
if not wcli and os.environ.get("AGENT_OS_STRICT") == "1":
    # Wired into flake.nix as `agos-web-contract`, where the CLI IS on PATH. In that
    # lane an absent tool is a BUILD FAILURE, not a reason to pass: a battery that
    # exits 0 when its subject is missing reports a green about nothing.
    print("  FAIL agos-web-battery: AGENT_OS_STRICT=1 and `agos-web` is not on PATH.")
    sys.exit(1)
if not wcli:
    print("  SKIP agos-web-battery: agos-web not on PATH (image not built).")
    sys.exit(0)
print("  using REAL agos-web CLI: " + wcli)

# 1) http(s)-only guard — OFFLINE, always assertable
rc, out, err = run([wcli, "fetch", "file:///etc/hostname"])
# Exit code, not just stdout. The hands have ONE uniform contract — `exit 2` is reserved
# for usage errors, and EVERY degraded path is `return 0` with an {ok:false} body — so any
# arm that is not probing usage must see rc 0. Until 2026-08-24 the only rc assertions on
# this surface were `rc == 2` ones: the batteries checked that bad input fails loudly and
# never once that good input succeeds quietly. That is exactly the gap `agos-notes list`
# lived in — valid JSON on stdout, rc 1 underneath, green lane.
check("`fetch file:///etc/hostname` exits 0", rc == 0, "rc=%s err=%s" % (rc, err[:60]))
try:
    d = json.loads(out)
    # Third channel, on the one degrade path this battery can reach without a network.
    check("fetch file:// -> says nothing on stderr", err == "", repr(err[:80]))
    check("fetch file:// -> ok:false + http(s) refusal", d.get("ok") is False and "http(s)" in str(d.get("error","")), out[:60])
except Exception as e:
    check("guard parses", False, str(e))

# 2) live fetch — valid JSON with ok bool (network may or may not succeed; both are the contract)
rc, out, err = run([wcli, "fetch", "https://example.com"])
check("`fetch https://example.com` exits 0", rc == 0, "rc=%s err=%s" % (rc, err[:60]))
try:
    d = json.loads(out)
    check("fetch https -> valid JSON {ok:bool}", d.get("ok") in (True, False), out[:60])
    check("fetch https -> has url field", d.get("url") == "https://example.com", str(d.get("url")))
except Exception as e:
    check("live fetch parses", False, str(e) + " | out=" + out[:60])

print("agos-web-battery: " + ("ALL PASS" if EX == 0 else "FAILURES"))
sys.exit(EX)
