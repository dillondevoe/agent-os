#!/usr/bin/env python3
# calendar-battery.py — Phase 2 calendar (agos-cal) acceptance harness.
# Proves the agent-facing calendar contract (now / agenda / add / cals) headlessly.
# khal (the backend) runs WITHOUT a display, so this is CI-runnable now; the GUI +
# CalDAV round-trip is a separate Dell gate (see tasks/active/294-...).
#
# Detection (intentional, NOT a dead SKIP like wiring-battery's old branch):
#   - `agos-cal` on PATH  → exercise the REAL CLI the brain drives.
#   - else `khal` on PATH → replicate the contract via a temp khal vdir (proves the
#     underlying backend the nix module wraps).
#   - else → SKIP with a clear note (this env simply hasn't built the image; not a
#     silent degrade — the Dell gate catches the live half separately).
# Run: PYTHONPATH=modules python3 tests/calendar-battery.py   (on a host with the image,
# or any host with `khal` installed for the fallback path).
import importlib.util, os, sys, tempfile, textwrap, subprocess, json, shutil

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
        return 127, "", "command not found: " + " ".join(cmd)
    except Exception as e:
        return 1, "", str(e)

# ── locate the calendar backend ──
agos = shutil.which("agos-cal")
khal = shutil.which("khal")

if not agos and os.environ.get("AGENT_OS_STRICT") == "1":
    # Wired into flake.nix as `agos-cal-contract`, where `agos-cal` IS on PATH. This battery
    # has TWO exit-0 paths that are not assertions — the khal fallback and the final SKIP —
    # and strict mode has to close BOTH, not the first one. The khal fallback is a genuine
    # convenience on a dev box, but in the wired lane it would silently swap the SUBJECT:
    # it exercises khal, the backend, and would report a green for `agos-cal` while the hand
    # itself was never invoked. That is the vacuous-green shape this file exists to catch.
    print("  FAIL calendar-battery: AGENT_OS_STRICT=1 and `agos-cal` is not on PATH "
          "(the khal fallback exercises the backend, not the hand).")
    sys.exit(1)

if agos:
    print("  using REAL agos-cal CLI: " + agos)
    def cal(*args): return run([agos, *args])
elif khal:
    print("  agos-cal absent — fallback: khal + temp vdir")
    vdir = tempfile.mkdtemp(prefix="agos-cal-battery-")
    khalconf = os.path.join(vdir, "khal.conf")
    with open(khalconf, "w") as f:
        f.write(textwrap.dedent(f"""
            [calendars]
            [[agent]]
            path = {vdir}/agent
            type = calendar
            [locale]
            default_timezone = America/Chicago
            local_timezone = America/Chicago
            [default]
            default_calendar = agent
        """))
    os.makedirs(vdir + "/agent", exist_ok=True)
    def cal(*args):
        # map agos-cal verbs onto khal invocations
        if args and args[0] == "now":
            rc, out, err = run(["date", "-Iseconds"]); return rc, out, err
        if args and args[0] == "cals":
            rc, out, err = run([khal, "-c", khalconf, "printcalendars"]); return rc, out, err
        if args and args[0] == "add":
            # agos-cal add "<YYYY-MM-DD HH:MM>" "<summary>" [end]
            start = args[1].split(" "); summary = args[2]
            cmd = [khal, "-c", khalconf, "new", "-a", "agent"] + start + [summary]
            return run(cmd)
        if args and args[0] == "agenda":
            days = args[1] if len(args) > 1 else "7"
            return run([khal, "-c", khalconf, "list", "today", days + "d"])
        return 127, "", "unknown verb"
else:
    print("  SKIP calendar-battery: neither agos-cal nor khal on PATH (image not built in this env).")
    print("  (Dell gate validates the live half — see tasks/active/294-...)")
    sys.exit(0)

# 1) now → JSON-ish instant, epoch is int, tz present
rc, out, err = cal("now")
# Exit code, not just stdout. The hands have ONE uniform contract — `exit 2` is reserved for
# usage errors, and EVERY degraded path is `return 0` with an {ok:false} body — so any arm that
# is not probing usage must see rc 0. This surface is more exposed than most: `agenda` and
# `cals` pipe khal into jq under `set -euo pipefail`, which is the exact shape that made
# `agos-notes list` print a valid `[]` and exit 1 for the whole life of the repo.
check("`now` exits 0", rc == 0, "rc=%s err=%s" % (rc, err[:60]))
try:
    # agos-cal emits JSON; `date -Iseconds` (fallback) is plain text — accept both
    if out.startswith("{"):
        d = json.loads(out); epoch_ok = isinstance(d.get("epoch"), int) and d.get("tz")
        check("now → JSON instant with epoch(int)+tz", epoch_ok, out[:60])
    else:
        # fallback `date`: looks like 2026-08-09T19:35:00-05:00
        check("now → ISO instant", out[:4] == "20" and "T" in out, out)
except Exception as e:
    check("now parses", False, str(e))

# 2) cals → lists the agent collection
rc, out, err = cal("cals")
check("`cals` exits 0", rc == 0, "rc=%s err=%s" % (rc, err[:60]))
ok = ("agent" in out) or ("agent" in err)
check("cals → lists agent collection", ok, (out or err)[:60])

# 3) add → agenda shows it
import datetime
start = (datetime.datetime.now() + datetime.timedelta(hours=2)).strftime("%Y-%m-%d %H:%M")
rc, out, err = cal("add", start, "battery-test-event")
check("`add` exits 0", rc == 0, "rc=%s err=%s" % (rc, err[:60]))
added_ok = ("ok" in out.lower()) or rc == 0
check("add → accepted", added_ok, (out or err)[:60])
rc, out, err = cal("agenda", "1")
# The pipefail arm proper: khal | jq | jq -s. If the first stage exits non-zero the script dies
# AFTER stdout is already correct, so only the exit code can see it.
check("`agenda 1` exits 0", rc == 0, "rc=%s err=%s" % (rc, err[:60]))
shows = "battery-test-event" in out
check("agenda → shows the added event", shows, (out or err)[:80])

print("calendar-battery: " + ("ALL PASS" if EX == 0 else "FAILURES"))
sys.exit(EX)
