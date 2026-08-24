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

def fingerprint(root):
    """Names + sizes + mtimes of the fixture tree — the channel no arm here reads.

    agos-files is a READ-ONLY hand, and until 2026-08-24 that was prose: every arm
    below reads stdout or the exit code, both of which are the hand's own account of
    itself. An implementation that listed a directory and also truncated a file in it
    would pass all of them. The inverse of the agos-notes case fixed the same day —
    there the side effect MUST happen, here it must NOT — and the same missing channel.
    """
    out = []
    for base in sorted(os.listdir(root)):
        st = os.stat(os.path.join(root, base))
        out.append((base, st.st_size, st.st_mtime_ns))
    return out

before = fingerprint(d)

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
    # `>= 2` was the wrong comparison and had been since this file was written. The fixture
    # tree is BUILT BY THIS TEST and holds exactly two files, so the exact number is known,
    # not guessed — and `>=` passes a hand that listed the parent directory, followed a
    # symlink out, or leaked an extra entry, which is the failure most worth catching in a
    # directory-listing hand. An inequality is the right shape only when the true value is
    # unknown; here it was a habit standing in for a fact the test already had.
    check("list -> count is exactly the 2 files the fixture has",
          isinstance(j.get("count"), int) and j["count"] == 2, str(j.get("count")))
    check("list -> entries is an array of exactly 2",
          isinstance(j.get("entries"), list) and len(j["entries"]) == 2, str(len(j.get("entries") or [])))
    # The hand reports the same fact twice and nothing made the two agree. `count` and
    # `len(entries)` are independent fields in the JSON, so a hand that counted one way and
    # listed another would satisfy both arms above separately while contradicting itself in
    # a single response — and a caller that trusted `count` for pagination would silently
    # read past the end of `entries`.
    check("list -> count agrees with the entries actually returned",
          j.get("count") == len(j.get("entries") or []),
          "count=%s len(entries)=%s" % (j.get("count"), len(j.get("entries") or [])))
    # Not merely "two of something". A hand that reported the right COUNT of names it
    # invented would satisfy every arm above; the fixture names are the only thing that
    # ties the output to the directory actually on disk.
    names = {e.get("name") if isinstance(e, dict) else e for e in (j.get("entries") or [])}
    check("list -> entries name the fixture files", {"a.txt", "b.log"} <= names, str(sorted(names))[:80])
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
    # THE THIRD CHANNEL. stdout and the exit code are asserted above; stderr was read by
    # nothing. It matters most on a DEGRADE path: the hands' contract is that a failure
    # becomes {ok:false} ON STDOUT, which is only worth relying on if the diagnosis is not
    # also being written somewhere the caller is not looking. Measured, not assumed — every
    # degrade path reachable off the Dell was probed on 2026-08-24 and all were silent.
    check("stat missing -> says nothing on stderr", err == "", repr(err[:80]))
except Exception as e:
    check("stat-missing parses", False, str(e))

check("read-only: the fixture tree is untouched", fingerprint(d) == before,
      "before=%s after=%s" % (before, fingerprint(d)))

print("agos-files-battery: " + ("ALL PASS" if EX == 0 else "FAILURES"))
sys.exit(EX)
