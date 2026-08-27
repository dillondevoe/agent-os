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
            # ISO formats, stated rather than defaulted. khal's built-in dateformat is
            # `%d.%m.` (European day-first) and its longdateformat `%d.%m.%Y`, so the
            # `YYYY-MM-DD HH:MM` this battery constructs below was unparseable by the very
            # khal it was invoking: `critical: Could not parse "2026-08-27 11:48 ..."`.
            # The battery builds the string, so the battery must declare the format.
            timeformat = %H:%M
            dateformat = %Y-%m-%d
            longdateformat = %Y-%m-%d
            datetimeformat = %Y-%m-%d %H:%M
            longdatetimeformat = %Y-%m-%d %H:%M
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
    # SELF-DISARM, NOW GATED. Until this commit the branch below was an unconditional exit 0:
    # on a host with neither binary the battery announced SKIP and reported success, so wiring
    # it into CI would have bought a green that proved nothing. That is the shape the ledger
    # check calls `[self-disarming]`, and it is why the eight remaining debt entries could not
    # simply be wired.
    #
    # The gate is AGENT_OS_STRICT, NOT `CI` — deliberately, and it is the second instance of a
    # convention this repo already has (tests/agent-loop-dispatch-battery.py, whose comment
    # states the reasoning: one exit code has to serve two callers). `CI` is ambient: GitHub
    # sets it for every step, including steps that legitimately run on a host without khal, so
    # a `CI` gate cannot distinguish "this invocation is authoritative" from "this happens to
    # be running on a runner". AGENT_OS_STRICT is set by exactly the callers that mean it.
    #
    # Note what this does and does not fix. It closes the battery's half: the battery knows
    # what it needs and now refuses to report success without it. It does NOT close the
    # caller's half — nothing here can verify the caller set the variable, and a wired step
    # that forgets it gets the old meaningless green back with no diagnostic. That hole is
    # real, it is checkable from the workflow side, and it is not fixed in this commit.
    strict = os.environ.get("AGENT_OS_STRICT") == "1"
    msg = "neither agos-cal nor khal on PATH (image not built in this env)"
    if strict:
        sys.exit("FAIL (AGENT_OS_STRICT=1) calendar-battery: " + msg + " — refusing to exit 0; "
                 "under strict this means the caller promised the calendar backend and did not "
                 "stage it.")
    print("  SKIP calendar-battery: " + msg + " — run on a host with the image or with khal "
          "installed, or set AGENT_OS_STRICT=1 to make the absence fatal.")
    print("  (Dell gate validates the live half — see tasks/active/294-...)")
    sys.exit(0)

# 1) now → JSON-ish instant, epoch is int, tz present
rc, out, err = cal("now")
try:
    # agos-cal emits JSON; `date -Iseconds` (fallback) is plain text — accept both
    if out.startswith("{"):
        d = json.loads(out); epoch_ok = isinstance(d.get("epoch"), int) and d.get("tz")
        check("now → JSON instant with epoch(int)+tz", epoch_ok, out[:60])
    else:
        # fallback `date`: looks like 2026-08-09T19:35:00-05:00
        # `out[:4] == "20"` was the original and it is unsatisfiable: a four-character
        # slice never equals a two-character literal, so this arm failed on every input
        # including correct ones. It printed the conforming timestamp as its own FAIL
        # detail — the assertion and the evidence disagreeing in the same line.
        check("now → ISO instant", out[:2] == "20" and "T" in out, out)
except Exception as e:
    check("now parses", False, str(e))

# 2) cals → lists the agent collection
rc, out, err = cal("cals")
ok = ("agent" in out) or ("agent" in err)
check("cals → lists agent collection", ok, (out or err)[:60])

# 3) add → agenda shows it
import datetime
start = (datetime.datetime.now() + datetime.timedelta(hours=2)).strftime("%Y-%m-%d %H:%M")
rc, out, err = cal("add", start, "battery-test-event")
added_ok = ("ok" in out.lower()) or rc == 0
check("add → accepted", added_ok, (out or err)[:60])
rc, out, err = cal("agenda", "1")
shows = "battery-test-event" in out
check("agenda → shows the added event", shows, (out or err)[:80])

print("calendar-battery: " + ("ALL PASS" if EX == 0 else "FAILURES"))
sys.exit(EX)
