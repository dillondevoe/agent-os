#!/usr/bin/env python3
# agos-notes-battery.py — Phase 2 notes (agos-notes) acceptance harness.
# Verifies list/new/read/append over the plain-md store. agos-notes hardcodes
# /var/lib/agos-notes; on a host without a writable store the write path degrades
# gracefully (ok:false) — we assert the JSON contract either way, and SKIP the
# write-path detail if the store isn't writable (Dell gate owns the real store).
#
# THAT SENTENCE WAS PROSE UNTIL 2026-08-24. The hand did NOT degrade gracefully: under
# writeShellApplication's `set -euo pipefail`, `printf ... > "$path"` into an unwritable
# store killed the script before jq ran, so `new` emitted NOTHING and the JSON contract
# was broken exactly when a caller most needs to be told why. It was never caught because
# this battery ran in no CI lane — it was on KNOWN_UNWIRED_DEBT — and the only host that
# ran it was the one host where the store IS writable. Wiring it turned the claim red on
# the first execution.
# Run: PYTHONPATH=modules python3 tests/agos-notes-battery.py
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

ncli = shutil.which("agos-notes")
if not ncli and os.environ.get("AGENT_OS_STRICT") == "1":
    # Wired into flake.nix as `agos-notes-contract`, where the CLI IS on PATH. In that
    # lane an absent tool is a BUILD FAILURE, not a reason to pass: a battery that
    # exits 0 when its subject is missing reports a green about nothing.
    print("  FAIL agos-notes-battery: AGENT_OS_STRICT=1 and `agos-notes` is not on PATH.")
    sys.exit(1)
if not ncli:
    print("  SKIP agos-notes-battery: agos-notes not on PATH (image not built).")
    sys.exit(0)
print("  using REAL agos-notes CLI: " + ncli)

# list -> valid JSON array (empty store is fine)
rc, out, err = run([ncli, "list"])
try:
    arr = json.loads(out)
    check("list -> JSON array", isinstance(arr, list), out[:60])
except Exception as e:
    check("list parses", False, str(e))
# ...AND it must SUCCEED. This arm read stdout only until 2026-08-24, and that is exactly
# how it stayed green over a real failure: `find` on an absent store exits 1, its message is
# swallowed by 2>/dev/null, and under `set -euo pipefail` the pipeline's rc becomes 1 — but
# only AFTER jq has already printed a perfectly valid `[]`. Correct output, failing command.
# A caller that branches on the exit status treats "the store is empty" as "the hand broke".
check("list exits 0 (empty store is not an error)", rc == 0, "rc=%d err=%s" % (rc, err[:60]))

# new -> attempts create; assert it returns valid JSON with ok bool
rc, out, err = run([ncli, "new", "Battery Test Note"])
try:
    d = json.loads(out)
    check("new -> valid JSON {ok:bool}", d.get("ok") in (True, False), out[:60])
    slug = d.get("slug")
    if d.get("ok") is True and slug:
        # read -> body contains the title
        rc, out, err = run([ncli, "read", slug])
        rd = json.loads(out)
        check("read -> ok:true + body", rd.get("ok") is True and "Battery" in str(rd.get("body","")), out[:60])
        # append -> ok true
        rc, out, err = run([ncli, "append", slug, "appended line"])
        ad = json.loads(out)
        check("append -> ok:true", ad.get("ok") is True, out[:60])
    else:
        print("  SKIP write-path detail: store not writable here (Dell gate owns /var/lib/agos-notes)")
except Exception as e:
    check("new parses", False, str(e))

print("agos-notes-battery: " + ("ALL PASS" if EX == 0 else "FAILURES"))
sys.exit(EX)
