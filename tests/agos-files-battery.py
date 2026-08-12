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
if not fcli:
    print("  SKIP agos-files-battery: agos-files not on PATH (image not built).")
    sys.exit(0)
print("  using REAL agos-files CLI: " + fcli)

d = tempfile.mkdtemp(prefix="agos-files-battery-")
open(os.path.join(d, "a.txt"), "w").close()
open(os.path.join(d, "b.log"), "w").close()

# list -> ok true, count int, entries array
rc, out, err = run([fcli, "list", d])
try:
    j = json.loads(out)
    check("list -> ok:true", j.get("ok") is True, out[:60])
    check("list -> count is int", isinstance(j.get("count"), int) and j["count"] >= 2, str(j.get("count")))
    check("list -> entries is array", isinstance(j.get("entries"), list) and len(j["entries"]) >= 2)
except Exception as e:
    check("list parses", False, str(e) + " | out=" + out[:80])

# stat existing -> exists true
rc, out, err = run([fcli, "stat", d])
try:
    j = json.loads(out)
    check("stat dir -> exists:true", j.get("exists") is True, out[:60])
except Exception as e:
    check("stat parses", False, str(e))

# stat missing -> exists false (graceful, not a crash)
rc, out, err = run([fcli, "stat", os.path.join(d, "nope")])
try:
    j = json.loads(out)
    check("stat missing -> exists:false (graceful)", j.get("exists") is False, out[:60])
except Exception as e:
    check("stat-missing parses", False, str(e))

print("agos-files-battery: " + ("ALL PASS" if EX == 0 else "FAILURES"))
sys.exit(EX)
