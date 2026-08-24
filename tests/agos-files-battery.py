#!/usr/bin/env python3
# agos-files-battery.py — Phase 2 files (agos-files) acceptance harness.
# Verifies the read-only contract: list <dir> -> {ok,dir,count,entries[]}; stat <path>.
# READ-ONLY by design (never creates/moves/deletes) — safe to run anywhere on /tmp.
# Run: PYTHONPATH=modules python3 tests/agos-files-battery.py
import subprocess, json, shutil, sys, tempfile, os

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

fcli = shutil.which("agos-files")
if not fcli and os.environ.get("AGENT_OS_STRICT") == "1":
    # Wired into flake.nix as `agos-files-contract`, where the CLI IS on PATH. In that
    # lane an absent tool is a BUILD FAILURE, not a reason to pass: a battery that
    # exits 0 when its subject is missing reports a green about nothing.
    print("  FAIL agos-files-battery: AGENT_OS_STRICT=1 and `agos-files` is not on PATH.")
    sys.exit(1)
if not fcli:
    print("  SKIP agos-files-battery: agos-files not on PATH (image not built).")
    sys.exit(0)
print("  using REAL agos-files CLI: " + fcli)

d = tempfile.mkdtemp(prefix="agos-files-battery-")
open(os.path.join(d, "a.txt"), "w").close()
open(os.path.join(d, "b.log"), "w").close()

# list -> ok true, count int, entries array
rc, out, err = run([fcli, "list", d])
# Exit code, not just stdout. The hands have ONE uniform contract — `exit 2` is reserved
# for usage errors, and EVERY degraded path is `return 0` with an {ok:false} body — so any
# arm that is not probing usage must see rc 0. Until 2026-08-24 the only rc assertions on
# this surface were `rc == 2` ones: the batteries checked that bad input fails loudly and
# never once that good input succeeds quietly. That is exactly the gap `agos-notes list`
# lived in — valid JSON on stdout, rc 1 underneath, green lane.
check("`list` exits 0", rc == 0, "rc=%s err=%s" % (rc, err[:60]))
try:
    j = json.loads(out)
    check("list -> ok:true", j.get("ok") is True, out[:60])
    check("list -> count is int", isinstance(j.get("count"), int) and j["count"] >= 2, str(j.get("count")))
    check("list -> entries is array", isinstance(j.get("entries"), list) and len(j["entries"]) >= 2)
except Exception as e:
    check("list parses", False, str(e) + " | out=" + out[:80])

# stat existing -> exists true
rc, out, err = run([fcli, "stat", d])
check("`stat` exits 0", rc == 0, "rc=%s err=%s" % (rc, err[:60]))
try:
    j = json.loads(out)
    check("stat dir -> exists:true", j.get("exists") is True, out[:60])
except Exception as e:
    check("stat parses", False, str(e))

# stat missing -> exists false (graceful, not a crash)
rc, out, err = run([fcli, "stat", os.path.join(d, "nope")])
check("`stat nope` exits 0", rc == 0, "rc=%s err=%s" % (rc, err[:60]))
try:
    j = json.loads(out)
    check("stat missing -> exists:false (graceful)", j.get("exists") is False, out[:60])
except Exception as e:
    check("stat-missing parses", False, str(e))

print("agos-files-battery: " + ("ALL PASS" if EX == 0 else "FAILURES"))
sys.exit(EX)
