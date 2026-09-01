#!/usr/bin/env python3
"""spend_ceiling.py — the two HARD budgets that stand between an unattended brain and a bill.

Rabbot's contract, 2026-08-31 (GO on item 3), built BEFORE any credential exists, which is
the bleed scar's ordering: the ceiling has to be load-bearing on the day the first key
lands, not added after the first surprise.

WHAT THIS IS NOT. `max_output_tokens_per_turn` already exists in agent-brain.py and is a
RATE LIMITER, not a budget: it bounds one turn and forgets. An unattended brain in a
respawn loop can pay that cap over and over forever and never trip anything. This file adds
the two ceilings that DO accumulate.

UNIT: **output tokens on metered (escalate-role) turns.** Named here because Rabbot asked
for the unit to be in the file. Output tokens are what agent-brain.py already counts
(`spent += usage`), so the ceiling composes with the per-turn cap instead of needing a
second, separately-wrong accounting. Local-floor turns cost nothing and are NOT counted —
the ceiling exists to bound SPEND, and refusing free local work would be a different and
worse product.

  AGENT_OS_SPEND_DAY_TOKENS         per-day ceiling
  AGENT_OS_SPEND_CUMULATIVE_TOKENS  cumulative-since-reset ceiling
  AGENT_OS_SPEND_COUNTER            counter path (default ~/memory/spend-counter.json)

Both ceilings are optional; with NEITHER set this stage is inert and escalate routing is
unchanged. Configure one and it is enforced — there is deliberately no "warn" mode, because
a ceiling you can be over is not a ceiling.

## FAIL-CLOSED, and what that costs
`feedback_an_erroring_safety_stage_reads_as_approval_scar_2026-08-17`. If a ceiling is
configured and the counter cannot be read, parsed, or written, this returns UNAVAILABLE and
the caller degrades to the LOCAL FLOOR. It never returns "allowed" on an error path. The
price is real and worth stating: a corrupt counter file takes escalate offline until an
operator looks at it. That is the correct trade — the alternative is a safety stage whose
failure mode is spending money.

A MISSING counter file is UNAVAILABLE too, not "zero spent". Deleting the file is exactly
how you would reset a budget you were not supposed to reset, and nothing in the file itself
can distinguish "never existed" from "just deleted". First use is an explicit operator act:
`init_counter()` (bin/agent-os-budget init). Deliberate, logged, and not something a respawn
loop does by accident.

## BOUND TO BOOT, NOT WALL CLOCK
`feedback_liveness_lease_must_bind_to_boot_not_clock_scar_2026-08-21`. Two distinct attacks:

  a respawn loop resetting the counter — closed by persisting to disk, so a process that
  dies and comes back reads the same number it left.

  a clock jump opening the day window early — closed by requiring MONOTONIC corroboration:
  within one boot, the day rolls over only when BOTH the wall clock AND CLOCK_BOOTTIME show
  a full day elapsed, so setting the clock forward advances nothing. A backward jump cannot
  shrink the window either, because rollover needs elapsed >= DAY and a negative elapsed
  never satisfies it.

LIMIT OF THAT GUARANTEE, stated rather than left to be discovered: across a REBOOT the boot
clock restarts, so the monotonic half is unavailable and rollover falls back to the wall
clock alone. An operator with root who reboots and sets the clock forward can advance the
day window. That person can also edit the counter file. This defends against a clock jump
and a respawn loop; it is not a defense against the machine's owner, and it does not pretend
to be. The CUMULATIVE ceiling has no window at all and is unaffected by any of it — which is
precisely why the contract asks for two ceilings and not one.
"""
import json, os, time

DAY_SECONDS = 86400
SCHEMA = 1

DEFAULT_COUNTER = os.path.join(os.path.expanduser("~/memory"), "spend-counter.json")


class Unavailable(Exception):
    """The budget stage could not do its job. Callers MUST treat this as a refusal."""


def counter_path(env=None):
    env = os.environ if env is None else env
    return env.get("AGENT_OS_SPEND_COUNTER") or DEFAULT_COUNTER


def _limit(env, key):
    """A ceiling, or None if unset. A garbled ceiling REFUSES rather than defaulting —
    same rule as _turn_limits() in agent-brain.py: a config that thinks it capped spending
    and didn't is worse than one that fails to start.

    `bool` is rejected explicitly. `isinstance(True, int)` is True in Python, so a
    ceiling of `true` would otherwise sail through as the number 1 and read as a working
    cap set absurdly low — a value that looks configured and is nonsense."""
    raw = (env.get(key) or "").strip()
    if not raw:
        return None
    try:
        n = int(raw)
    except ValueError:
        raise Unavailable(f"{key} must be a positive integer, got {raw!r}")
    if isinstance(n, bool) or n <= 0:
        raise Unavailable(f"{key} must be a positive integer, got {raw!r}")
    return n


def limits(env=None):
    env = os.environ if env is None else env
    return (_limit(env, "AGENT_OS_SPEND_DAY_TOKENS"),
            _limit(env, "AGENT_OS_SPEND_CUMULATIVE_TOKENS"))


def _boot_id():
    """Identifies THIS boot. Changes across a reboot, which is how we know the boot clock
    is no longer comparable to the one the counter was written under."""
    try:
        with open("/proc/sys/kernel/random/boot_id") as f:
            return f.read().strip()
    except Exception:
        return ""


def _boot_elapsed():
    # CLOCK_BOOTTIME counts suspend, CLOCK_MONOTONIC does not. A laptop closed for six
    # hours HAS spent six hours of the operator's day; using MONOTONIC here would hold the
    # window open across a suspend and is the subtler of the two wrong answers.
    try:
        return time.clock_gettime(time.CLOCK_BOOTTIME)
    except Exception:
        return None


def _read(path):
    try:
        with open(path) as f:
            st = json.load(f)
    except FileNotFoundError:
        raise Unavailable(f"spend counter {path} is missing — run `agent-os-budget init`")
    except Exception as e:
        raise Unavailable(f"spend counter {path} is unreadable: {e}")
    if not isinstance(st, dict) or st.get("schema") != SCHEMA:
        raise Unavailable(f"spend counter {path} has an unrecognised schema")
    for k in ("day_spent", "cumulative_spent", "day_start_wall"):
        v = st.get(k)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
            raise Unavailable(f"spend counter {path} field {k!r} is not a non-negative number")
    return st


def _write(path, st):
    # Atomic: a torn write is an unreadable counter, and an unreadable counter takes
    # escalate offline. Replace-through-temp so a crash mid-write cannot cause that.
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(tmp, "w") as f:
            json.dump(st, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception as e:
        raise Unavailable(f"spend counter {path} is not writable: {e}")


def init_counter(path=None, env=None):
    """Create a zeroed counter. An explicit operator act — see the module docstring."""
    path = path or counter_path(env)
    st = {"schema": SCHEMA, "boot_id": _boot_id(), "day_start_wall": time.time(),
          "day_start_boot": _boot_elapsed(), "day_spent": 0, "cumulative_spent": 0}
    _write(path, st)
    return st


def _rolled(st):
    """Has a full day elapsed since the window opened? Requires monotonic corroboration
    within one boot; see BOUND TO BOOT in the module docstring for what this does and does
    not defend against."""
    if (time.time() - st["day_start_wall"]) < DAY_SECONDS:
        return False
    same_boot = st.get("boot_id") and st["boot_id"] == _boot_id()
    now_boot, then_boot = _boot_elapsed(), st.get("day_start_boot")
    if same_boot and now_boot is not None and isinstance(then_boot, (int, float)):
        return (now_boot - then_boot) >= DAY_SECONDS
    return True


def check(env=None, path=None):
    """May a metered turn run? Returns (True, None) or (False, reason).

    Raises NOTHING: every failure is converted to a refusal with a reason, because a
    caller that has to remember to catch an exception to stay safe is a caller that will
    one day forget."""
    env = os.environ if env is None else env
    try:
        day, cum = limits(env)
        if day is None and cum is None:
            return True, None                      # no ceiling configured — stage inert
        st = _read(path or counter_path(env))
        day_spent = 0 if _rolled(st) else st["day_spent"]
        if day is not None and day_spent >= day:
            return False, f"per-day spend ceiling reached ({day_spent}/{day} output tokens)"
        if cum is not None and st["cumulative_spent"] >= cum:
            return False, (f"cumulative spend ceiling reached "
                           f"({st['cumulative_spent']}/{cum} output tokens since reset)")
        return True, None
    except Unavailable as e:
        return False, f"spend ceiling UNAVAILABLE — {e}"


def record(tokens, env=None, path=None):
    """Add a metered turn's output tokens to both counters. Returns the new state.

    Raises Unavailable on any failure: unlike check(), a caller that cannot RECORD spend
    must know, because silently unrecorded spend is unbounded spend."""
    env = os.environ if env is None else env
    if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0:
        raise Unavailable(f"tokens must be a non-negative int, got {tokens!r}")
    p = path or counter_path(env)
    st = _read(p)
    if _rolled(st):
        st["day_start_wall"] = time.time()
        st["day_start_boot"] = _boot_elapsed()
        st["boot_id"] = _boot_id()
        st["day_spent"] = 0
    st["day_spent"] += tokens
    st["cumulative_spent"] += tokens          # NEVER reset by a rollover. That is the point.
    _write(p, st)
    return st
