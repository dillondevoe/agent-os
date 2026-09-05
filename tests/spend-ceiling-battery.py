#!/usr/bin/env python3
# MUTATES_SHARED_STATE — Geist's law, 2026-09-05: a box-runnable battery never mutates
# human-shared state. Read as DATA by tests/vm-matrix-contract.py, not as a comment.
MUTATES_SHARED_STATE = False

"""spend-ceiling-battery.py — the cumulative spend ceilings, armed in BOTH directions.

Rabbot's contract (2026-08-31, GO on item 3) asked for armed-red both ways: the ceiling
trips at N, AND a sentinel/typo value does not SILENTLY DISABLE it. The second half is the
one that matters, because a disabled ceiling and a working ceiling look identical until the
bill arrives — the `_think_budget()` None lesson, one layer up.

  A  under the day ceiling → allowed
  B  at the day ceiling → refused, and the reason names the number
  C  at the cumulative ceiling → refused, even with the day counter at zero
  D  MISSING counter → UNAVAILABLE, never "zero spent". Deleting the file is how you
     would reset a budget you were not allowed to reset.
  E  CORRUPT counter → UNAVAILABLE. An erroring safety stage must not read as approval.
  F  UNWRITABLE counter → record() raises. Unrecorded spend is unbounded spend.
  G  TYPO/SENTINEL ceilings do not silently disable: "", "off", "none", "0", "-1",
     "true", "1e6" each either refuse or are treated as unset — never as a working cap.
     `true` is called out because isinstance(True, int) is True in Python, so a bool
     would otherwise pass as the number 1: a cap that looks set and is nonsense.
  H  a rollover resets the DAY counter and NEVER the cumulative one. If it reset both,
     the cumulative ceiling would be a second per-day ceiling wearing its name.
  I  a WALL-CLOCK JUMP FORWARD inside one boot does not open the day window — rollover
     needs monotonic corroboration too.
  J  CONTROL — with NO ceiling configured the stage is inert and returns allowed without
     ever touching a counter (the counter is missing here, which would be UNAVAILABLE if
     the stage were running). Without J, every other arm could be passing because the
     stage refuses unconditionally.
  K  CONTROL — the brain's _spend_gate() refuses when a ceiling is configured but the
     spend_ceiling module is absent. An absent guard must not read as an absent need.
  L  never-spill: a tripped ceiling routes to the LOCAL FLOOR, not to another provider.

Run standalone (tests/run-local.sh) and in the flake (spend-ceiling-contract).
"""
import importlib.util, json, os, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
MODS = os.path.join(os.path.dirname(HERE), "modules")
sys.path.insert(0, MODS)
import spend_ceiling as sc

FAILS = []
def check(arm, cond, detail):
    print(("  ok   " if cond else "  FAIL ") + arm + " — " + detail)
    if not cond: FAILS.append(arm)

TMP = tempfile.mkdtemp()
def counter(day=0, cum=0, **over):
    p = os.path.join(TMP, "c-%d.json" % len(os.listdir(TMP)))
    st = {"schema": sc.SCHEMA, "boot_id": sc._boot_id(), "day_start_wall": time.time(),
          "day_start_boot": sc._boot_elapsed(), "day_spent": day, "cumulative_spent": cum}
    st.update(over)
    with open(p, "w") as f: json.dump(st, f)
    return p

def env(day=None, cum=None):
    e = {}
    if day is not None: e["AGENT_OS_SPEND_DAY_TOKENS"] = str(day)
    if cum is not None: e["AGENT_OS_SPEND_CUMULATIVE_TOKENS"] = str(cum)
    return e

print("A/B/C — the ceilings trip")
ok, why = sc.check(env(day=1000), counter(day=999))
check("A", ok, "under the day ceiling is allowed, got %r" % (why,))
ok, why = sc.check(env(day=1000), counter(day=1000))
check("B", not ok and "1000" in (why or ""), "at the day ceiling refuses and names it: %r" % (why,))
ok, why = sc.check(env(cum=5000), counter(day=0, cum=5000))
check("C", not ok and "cumulative" in (why or ""), "cumulative trips with day at zero: %r" % (why,))

print("D/E/F — fail-closed")
ok, why = sc.check(env(day=1000), os.path.join(TMP, "nope.json"))
check("D", not ok and "UNAVAILABLE" in (why or ""), "missing counter is UNAVAILABLE: %r" % (why,))
bad = os.path.join(TMP, "corrupt.json")
open(bad, "w").write("{not json")
ok, why = sc.check(env(day=1000), bad)
check("E", not ok and "UNAVAILABLE" in (why or ""), "corrupt counter is UNAVAILABLE: %r" % (why,))
ro = os.path.join(TMP, "ro", "c.json")
os.makedirs(os.path.dirname(ro)); 
import shutil; shutil.copy(counter(day=0), ro); os.chmod(os.path.dirname(ro), 0o500)
# record() raises Unavailable for SEVEN distinct reasons (bad ceiling value, missing counter,
# unreadable, bad schema, bad field, unwritable, bad token count). Asserting only that
# Unavailable was raised lets any of the other six stand in for the one under test — the arm
# goes green while the unwritable branch is unreachable. So match the message, not the class.
why = None
try:
    sc.record(10, env(day=1000), ro)
except sc.Unavailable as e: why = str(e)
except Exception as e: why = "WRONG-TYPE %s: %s" % (type(e).__name__, e)
finally: os.chmod(os.path.dirname(ro), 0o700)
check("F", why is not None and "is not writable" in why,
      "unwritable counter must raise Unavailable NAMING the write failure, got %r" % (why,))
# F0 — the permitting arm for F. Without it, a record() that raised "is not writable" at
# every opportunity would pass F while being wholly broken.
wr = counter(day=0)
try:
    sc.record(10, env(day=1000), wr); f0 = None
except Exception as e: f0 = "%s: %s" % (type(e).__name__, e)
check("F0", f0 is None, "a WRITABLE counter must accept record(), got %r" % (f0,))

print("G — typo/sentinel ceilings never silently disable")
for v in ["off", "none", "0", "-1", "true", "1e6", "1000 "]:
    e = {"AGENT_OS_SPEND_DAY_TOKENS": v}
    ok, why = sc.check(e, counter(day=10**9))
    # Refusing is necessary but not sufficient: a refusal for an unrelated reason would let
    # this arm pass on a module that never parsed the ceiling at all. Two admissible
    # rejections, and the arm names which one it got rather than accepting any failure.
    reason = ("over-ceiling" if "1000" in (why or "") or "ceiling" in (why or "").lower()
              else "unavailable" if "UNAVAILABLE" in (why or "") or "positive integer" in (why or "")
              else "UNCLASSIFIED")
    check("G", (not ok) and reason != "UNCLASSIFIED",
          "ceiling %r must be refused as over-ceiling or as UNAVAILABLE, got allowed=%r reason=%s why=%r"
          % (v, ok, reason, why))

print("H — rollover resets the day, never the cumulative")
old = time.time() - (sc.DAY_SECONDS + 60)
p = counter(day=999, cum=4000, day_start_wall=old,
            day_start_boot=(sc._boot_elapsed() or 0) - (sc.DAY_SECONDS + 60))
st = sc.record(1, env(day=1000, cum=5000), p)
check("H", st["day_spent"] == 1, "day counter reset by rollover, got %r" % (st["day_spent"],))
check("H", st["cumulative_spent"] == 4001, "cumulative SURVIVES rollover, got %r" % (st["cumulative_spent"],))

print("I — a clock jump forward does not open the window")
# Wall clock says a day has passed; the boot clock, same boot, says seconds have.
p = counter(day=1000, cum=0, day_start_wall=time.time() - (sc.DAY_SECONDS + 60),
            day_start_boot=sc._boot_elapsed())
ok, why = sc.check(env(day=1000), p)
check("I", not ok, "day ceiling still trips despite the wall-clock jump: allowed=%r why=%r" % (ok, why))

print("J — CONTROL: no ceiling configured → inert, counter never consulted")
ok, why = sc.check({}, os.path.join(TMP, "definitely-absent.json"))
check("J", ok and why is None, "inert with no ceiling set, got ok=%r why=%r" % (ok, why))

print("K — CONTROL: the brain refuses when the module is missing but a ceiling is set")
spec = importlib.util.spec_from_file_location("brain_spend", os.path.join(MODS, "agent-brain.py"))
brain = importlib.util.module_from_spec(spec); spec.loader.exec_module(brain)
saved_mod, saved_env = brain._spend, dict(os.environ)
try:
    brain._spend = None
    os.environ["AGENT_OS_SPEND_DAY_TOKENS"] = "1000"
    ok, why = brain._spend_gate()
    check("K", not ok and "UNAVAILABLE" in (why or ""),
          "missing module with a ceiling set refuses: %r" % (why,))
    os.environ.pop("AGENT_OS_SPEND_DAY_TOKENS")
    ok, why = brain._spend_gate()
    check("K", ok, "and is inert when no ceiling is set, got ok=%r" % (ok,))
finally:
    brain._spend = saved_mod
    os.environ.clear(); os.environ.update(saved_env)

print("L — never-spill: a tripped ceiling goes to the LOCAL FLOOR")
saved_env = dict(os.environ)
try:
    os.environ["AGENT_OS_SPEND_DAY_TOKENS"] = "1000"
    os.environ["AGENT_OS_SPEND_COUNTER"] = counter(day=1000)
    brain._PROVIDERS = {"roles": {"escalate": "anthropic", "floor": "ollama"},
                        "providers": {"anthropic": {"kind": "claude", "model": "x"},
                                      "ollama": {"kind": "ollama", "model": "y"}}}
    brain._providers_resolve = lambda p, role, unavailable=frozenset(): ("anthropic", p["providers"]["anthropic"], None)
    route = brain._route_for_turn("session")
    check("L", route["role"] == "floor", "role is floor, got %r" % (route["role"],))
    check("L", route["provider"] == brain.ACTIVE_PROVIDER,
          "provider is the LOCAL floor, not another metered one, got %r" % (route["provider"],))
    check("L", "ceiling" in (route.get("degraded") or ""),
          "and the degrade is VISIBLE, got %r" % (route.get("degraded"),))
finally:
    os.environ.clear(); os.environ.update(saved_env)

if FAILS:
    print("\nFAILED arms: " + ", ".join(sorted(set(FAILS)))); sys.exit(1)
print("\nall arms green")
