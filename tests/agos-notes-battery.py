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
# Exit code, not just stdout. The hands have ONE uniform contract — `exit 2` is reserved
# for usage errors, and EVERY degraded path is `return 0` with an {ok:false} body — so any
# arm that is not probing usage must see rc 0. Until 2026-08-24 the only rc assertions on
# this surface were `rc == 2` ones: the batteries checked that bad input fails loudly and
# never once that good input succeeds quietly. That is exactly the gap `agos-notes list`
# lived in — valid JSON on stdout, rc 1 underneath, green lane.
check("`new Battery Test Note` exits 0", rc == 0, "rc=%s err=%s" % (rc, err[:60]))
try:
    d = json.loads(out)
    check("new -> valid JSON {ok:bool}", d.get("ok") in (True, False), out[:60])
    slug = d.get("slug")
    if d.get("ok") is True and slug:
        # read -> body contains the title
        rc, out, err = run([ncli, "read", slug])
        check("`read` exits 0", rc == 0, "rc=%s err=%s" % (rc, err[:60]))
        rd = json.loads(out)
        check("read -> ok:true + body", rd.get("ok") is True and "Battery" in str(rd.get("body","")), out[:60])
        # append -> ok true
        rc, out, err = run([ncli, "append", slug, "appended line"])
        check("`append appended line` exits 0", rc == 0, "rc=%s err=%s" % (rc, err[:60]))
        ad = json.loads(out)
        check("append -> ok:true", ad.get("ok") is True, out[:60])

        # THE FOURTH CHANNEL. Every arm above reads stdout or the exit code — both of which
        # are the hand's own REPORT of what it did. A write hand that returned {"ok":true}
        # and wrote nothing would pass all six. The store is the only witness that is not
        # the defendant, so read it directly. This is only assertable because the lane sets
        # AGOS_NOTES_DIR; before that override existed the write paths never ran at all.
        store = os.environ.get("AGOS_NOTES_DIR")
        if store:
            path = os.path.join(store, slug + ".md")
            check("on disk: the note file exists", os.path.isfile(path), path)
            try:
                disk = open(path, encoding="utf-8").read()
            except OSError as e:
                disk = ""
                check("on disk: readable", False, str(e))
            check("on disk: `new` wrote the title", "Battery Test Note" in disk, disk[:60])
            # Not merely "contains the text" — APPENDED. An implementation that truncated on
            # append would satisfy a containment check and lose the title.
            check("on disk: `append` added a line AFTER the title, and kept it",
                  disk.rstrip().endswith("appended line") and "Battery Test Note" in disk,
                  repr(disk[:80]))
        elif os.environ.get("AGENT_OS_STRICT") == "1":
            # The wired lane sets it. If it ever stops, these four arms would vanish in
            # silence and the battery would still print ALL PASS — a skip that looks like
            # coverage is the shape this repo keeps finding.
            check("strict: AGOS_NOTES_DIR must be set so the store can be inspected", False,
                  "unset — on-disk arms would have been skipped silently")
    elif os.environ.get("AGENT_OS_STRICT") == "1":
        # A SECOND DISARM BEHIND THE FIRST ONE. The strict block at the top of this file only
        # guarantees the CLI is on PATH; this branch could still swallow a broken write path
        # as a "skip" and print ALL PASS. That was unavoidable while the store was baked in at
        # build time — but the wired lane now sets AGOS_NOTES_DIR to a writable dir, so in
        # strict mode an un-writable store is a BUILD FAILURE, not a reason to pass.
        check("strict: write path must be exercised, not skipped", False,
              "ok=%r err=%r — AGOS_NOTES_DIR=%s" %
              (d.get("ok"), d.get("error"), os.environ.get("AGOS_NOTES_DIR", "<unset>")))
    else:
        print("  SKIP write-path detail: store not writable here (Dell gate owns /var/lib/agos-notes)")
except Exception as e:
    check("new parses", False, str(e))

# A degrade path, probed on all three channels. The battery had no arm at all for "the note
# does not exist" — the single most likely thing a caller hits — and the hands' contract makes
# that an {ok:false} on STDOUT with rc 0 and nothing on stderr, not an error.
rc, out, err = run([ncli, "read", "no-such-note-xyz"])
check("`read <missing>` exits 0", rc == 0, "rc=%s err=%s" % (rc, err[:60]))
check("read <missing> -> says nothing on stderr", err == "", repr(err[:80]))
try:
    md = json.loads(out)
    check("read <missing> -> ok:false + a reason on STDOUT",
          md.get("ok") is False and md.get("error"), out[:60])
except Exception as e:
    check("read-missing parses", False, str(e))

print("agos-notes-battery: " + ("ALL PASS" if EX == 0 else "FAILURES"))
sys.exit(EX)
