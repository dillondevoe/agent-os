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
# The fourth channel on the one verb where it is answerable from any host: `now` is pure
# printf+date in the hand and pure `date` in the fallback — no khal, no jq, nothing that has
# a legitimate reason to write to stderr. Every other verb here shells into khal, which may
# warn for real reasons, so this arm is deliberately NOT generalised: an arm written blind is
# how a flaky red ships.
check("`now` says nothing on stderr", err == "", repr(err[:80]))
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
# THE CHANNEL DISJUNCTION. This arm read `("agent" in out) or ("agent" in err)` until
# 2026-08-24, and that `or` is the whole defect: the contract (agos-cal.nix, `cals`) is
# `khal printcalendars | jq -R -s -c 'split("\n") | map(select(length>0))'` — a JSON ARRAY on
# STDOUT. A hand that emitted NOTHING on stdout and merely named the calendar in a khal warning
# on stderr satisfied it. That is `agos-notes list` again with the channels swapped: the output
# looks right to a human reading the log and is unusable to the caller that parses it.
# The disjunction was there to serve the khal FALLBACK, whose output is raw text, not JSON —
# so split the paths instead of loosening the arm for both.
if agos:
    try:
        cl = json.loads(out)
        check("cals -> a JSON array on STDOUT", isinstance(cl, list), out[:80])
        check("cals -> the agent collection is IN that array",
              isinstance(cl, list) and any("agent" in str(x) for x in cl), out[:80])
    except Exception as e:
        check("cals parses as JSON", False, str(e) + " | out=" + out[:80])
else:
    check("cals (khal fallback) -> names the agent collection", "agent" in (out or err), (out or err)[:60])

# 2b) agenda on an EMPTY calendar. This arm is a PROBE, written 2026-08-24 and shipped
# without a local verdict: khal is not installed on the host this was authored on, so CI is
# the only instrument that can answer it. The contract says `agenda` returns a JSON ARRAY;
# the hand builds it with `khal list ... | jq -R -c 'select(length>0) | split($sep) | ...'
# | jq -s -c '.'`, and what khal prints when a range holds no events decides whether the
# empty case is `[]` or a one-element array wrapping a human sentence like "No events".
# The second is the same class as `agos-notes list` — a caller iterating the result gets a
# fabricated event — and no arm anywhere would have seen it, because every existing arm
# runs AFTER something has been added.
if agos:
    rc, out, err = cal("agenda", "1")
    check("`agenda` on an empty calendar exits 0", rc == 0, "rc=%s err=%s" % (rc, err[:60]))
    try:
        empty = json.loads(out)
        check("agenda (empty) -> JSON array", isinstance(empty, list), out[:80])
        check("agenda (empty) -> EMPTY, not a human sentence wrapped in an object",
              empty == [], out[:120])
    except Exception as e:
        check("agenda (empty) parses", False, str(e) + " | out=" + out[:80])

# 3) add → agenda shows it
import datetime
_now = datetime.datetime.now()
_start_dt = _now + datetime.timedelta(hours=2)
start = _start_dt.strftime("%Y-%m-%d %H:%M")
# `agenda N` is a CALENDAR-DAY window, so the span this arm needs depends on whether now+2h
# crossed midnight. It was hardcoded `1` until 2026-08-24, which is correct for 22 hours a
# day and WRONG for the two hours after 22:00: the event lands on tomorrow, `agenda 1` is
# asked only about today, and it correctly returns [] while the arm reads that as a broken
# `add`. It had never fired because nothing had been pushed in that window -- CI caught it
# on the first commit that was, run 32784416155 at 22:25Z, `add` reporting ok:true for
# 2026-08-25 00:25 and `agenda 1` returning [].
#
# A LITERAL THAT ENCODES AN ASSUMPTION NOTHING STATES: "now+2h is still today". Deriving
# the span from the event that was actually added is the version that cannot go stale, and
# it removes the wall-clock from the arm's correctness entirely.
_span = (_start_dt.date() - _now.date()).days + 1
rc, out, err = cal("add", start, "battery-test-event")
check("`add` exits 0", rc == 0, "rc=%s err=%s" % (rc, err[:60]))
# A TAUTOLOGY, until 2026-08-24: `("ok" in out.lower()) or rc == 0`, one line below an arm
# that already asserts `rc == 0`. The second disjunct is therefore TRUE whenever the previous
# arm passed, so this check could not fail independently of it — it read as a second
# assertion and was a restatement of the first. Same family as an unexecuted arm: it is not
# unreachable, it is unfalsifiable. The hand printfs `{"ok":true,"start":..,"title":..}`, so
# under the real CLI there is an exact contract to assert; the khal fallback prints prose.
if agos:
    try:
        ad = json.loads(out)
        check("add -> ok:true on STDOUT", ad.get("ok") is True, out[:80])
        check("add -> echoes the title back", ad.get("title") == "battery-test-event", out[:80])
    except Exception as e:
        check("add parses as JSON", False, str(e) + " | out=" + out[:80])
else:
    check("add (khal fallback) -> khal accepted the event", "battery-test-event" in (out or err), (out or err)[:60])
rc, out, err = cal("agenda", str(_span))
# The pipefail arm proper: khal | jq | jq -s. If the first stage exits non-zero the script dies
# AFTER stdout is already correct, so only the exit code can see it.
check("`agenda %d` exits 0" % _span, rc == 0, "rc=%s err=%s" % (rc, err[:60]))
shows = "battery-test-event" in out
check("agenda → shows the added event (span=%d, event %s)" % (_span, start), shows,
      (out or err)[:80])

# 4) THE DEGRADE PATH — added 2026-08-24, and it is two known shapes at once, both of them
# already fixed once in `agos-notes` and both re-found here on a second hand.
#
# Probed on this host with a khal stub that exits 1 (the hand's shell body extracted from the
# .nix and run directly, since writeShellApplication prepends its own runtimeInputs and a
# stub cannot be shadowed onto PATH from outside). Before the fix, all three khal-backed
# verbs failed the contract:
#
#     agenda  rc=1  stdout=[]      <- the `agos-notes list` shape, and WORSE
#     cals    rc=1  stdout=[]
#     add     rc=1  stdout=''      <- the `agos-notes new` shape
#
# `agenda` is worse than the notes case it rhymes with. There, a caller got a CORRECT value
# under a failing rc. Here `jq` succeeds on empty input and prints `[]` BEFORE pipefail can
# set the code, so a broken calendar store answers "no events today" — a PLAUSIBLE, BELIEVABLE
# answer that is a lie. A caller reading stdout is not merely uninformed, it is misinformed.
#
# The arm below is a PROBE in the same sense as 2b: no khal here, so CI returns the verdict.
# `rc == 0` is the load-bearing assertion — it is what the pre-fix code fails — and the
# ok:false check is deliberately conditional, because if khal turns out to TOLERATE a bogus
# collection this arm must not go red for the wrong reason. What it must never do is pass
# while the hand exits non-zero.
if agos:
    bogus = tempfile.mkdtemp(prefix="agos-cal-degrade-")
    conf = os.path.join(bogus, "khal.conf")
    with open(conf, "w", encoding="utf-8") as fh:
        fh.write("[calendars]\n[[agent]]\npath = /proc/no-such-vdir-agos\ntype = calendar\n")
    denv = dict(os.environ, AGOS_CAL_CONF=conf)
    for verb, argv in (("agenda", ["1"]), ("cals", []), ("add", ["2099-01-01 09:00", "degrade-probe"])):
        try:
            o = subprocess.run([agos, verb] + argv, capture_output=True, text=True, timeout=30, env=denv)
            drc, dout = o.returncode, o.stdout.strip()
        except Exception as e:
            drc, dout = 1, ""
            check("degrade: `%s` ran" % verb, False, str(e))
        # THE CONTRACT: exit 2 is reserved for usage errors; every degraded path is rc 0 with
        # an {ok:false} body. A broken store is not a usage error.
        check("degrade: `%s` on a broken store exits 0, not 1" % verb, drc == 0,
              "rc=%s out=%s" % (drc, dout[:60]))
        check("degrade: `%s` still says SOMETHING on stdout" % verb, dout != "",
              "empty stdout is the `agos-notes new` shape")
        try:
            db = json.loads(dout) if dout else None
        except Exception as e:
            db = None
            check("degrade: `%s` -> parseable JSON" % verb, False, str(e) + " | " + dout[:60])
        if isinstance(db, dict):
            check("degrade: `%s` -> ok:false + a reason" % verb,
                  db.get("ok") is False and db.get("error"), dout[:80])
        elif db == []:
            # Not a failure by itself — khal may have tolerated the config and found nothing.
            # But it is the exact silhouette of the defect, so say so rather than pass quietly.
            print("  NOTE %s returned [] under a bogus store — verify khal actually failed" % verb)

# ---------------------------------------------------------------------------
# ABSENT BACKEND -- and this hand is the reason the arm exists at all. agos-cal was the
# HONEST one of the four fixed in 619d3a9: it named khal and never blamed the calendar. It
# still got the guard, because "nearly right" is the state in which a rule quietly stops
# applying to you -- a caller could not tell "khal is not installed" from "khal rejected the
# request" without parsing prose. And carrying that guard across hands is exactly where the
# bad fix landed: `|| return 0` inside a top-level `case` arm is not a function return, so it
# printed correct JSON and exited 2, the code reserved for USAGE errors, telling every caller
# it had held the tool wrong. tests/backend-absence-contract.py saw a guard and was satisfied.
# Static cannot see an exit code. This arm can, and unlike the block above it needs no khal.
if agos:
    denv = dict(os.environ, AGOS_CAL_KHAL="no-such-khal-binary-xyz")
    for _verb, _argv in (("agenda", ["1"]), ("cals", []),
                         ("add", ["2099-01-01 09:00", "absent-backend-probe"])):
        o = subprocess.run([agos, _verb] + _argv, capture_output=True, text=True, timeout=30, env=denv)
        drc, dout = o.returncode, o.stdout.strip()
        check("absent backend: `%s` exits 0, not 2 (it is not a usage error)" % _verb, drc == 0,
              "rc=%s out=%s" % (drc, dout[:60]))
        try:
            db = json.loads(dout)
        except Exception as e:
            db = None
            check("absent backend: `%s` -> parseable JSON on STDOUT" % _verb, False,
                  str(e) + " | " + dout[:60])
        if isinstance(db, dict):
            check("absent backend: `%s` -> ok:false + a reason naming the BACKEND" % _verb,
                  db.get("ok") is False and "absent" in str(db.get("error", "")).lower(), dout[:80])

print("calendar-battery: " + ("ALL PASS" if EX == 0 else "FAILURES"))
sys.exit(EX)
