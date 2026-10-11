#!/usr/bin/env python3
# modules/agos_window.py — the setup window (docs/design/first-login-and-setup-window.md §2). Stdlib only.
#
# THE WALL IS ALWAYS THE SEALED ONE. The ruleset the image loads has one named set, `setup_uid`, and
# two rules that accept DNS and 443 for the uids in it. An empty set is the sealed box. This program is
# the ONLY writer of that set: "open" puts one uid in it (the model fetcher's), "close" flushes it.
#
#   agentos-setup-window recompute   every boot, and after every nftables reload or restart (which
#                                    recreate the set empty): read the state, end with the set either
#                                    empty or holding the fetch uid. Never anything else.
#   agentos-setup-window tick        once a minute while open: write the remaining budget; when it is
#                                    gone, EMPTY THE SET FIRST, then seal. Closing the window never
#                                    depends on anything else succeeding.
#   agentos-setup-window stop        at shutdown: write the remaining budget.
#   agentos-setup-window seal WHY    flush the set, end the fetcher's open connections (restart it),
#                                    stop the pull and its retry timer (which refuse to start again once
#                                    the marker exists), write the `sealed` marker. Idempotent.
#   agentos-setup-window status      print the state (read-only).
#
# THE WINDOW IS A BUDGET OF OPEN SECONDS, not a wall-clock deadline: a box with no RTC battery boots
# near 1970, and time sync is itself behind the wall. `remaining = budget_at_boot - max(FLOOR,
# seconds the window has been open in this boot)`, RECOMPUTED from the monotonic clock, never
# successively subtracted, so writing it twice gives the same number. A run under the same kernel
# boot id is a re-derivation and charges nothing; a new boot id first settles the previous boot at
# its last written value. The floor means every boot that opens the window costs at least a minute,
# so a reboot loop cannot hold it open. A wall-clock deadline is kept as a second, closing-only limit
# and is honoured only when the clock is believable (later than the image's build date), then and now.
#
# ONE AT A TIME. recompute, tick and seal each read the state, decide, and then act on the set. Two of
# them interleaved could decide "open" from a state the other was about to seal, and open the set
# after it was closed. Every one of them therefore holds an exclusive lock on the state directory from
# its first read to its last effect, and re-reads nothing it decided on outside it. Whoever cannot get
# the lock may still CLOSE the set (closing is always safe) but never opens it. The wait for the lock
# is LOCK_WAIT (150 s): longer than the one slow thing a holder does, restarting the fetcher (120 s).
# The window unit's stop timeout is set above both (setup-window.nix), or systemd would kill the
# shutdown stamp while it waits.
#
# THE MARKER NEVER LIES. `sealed` is written only after nft has confirmed the flush. If the flush
# fails, nothing else in the seal happens and there is no marker: the budget is still spent, so the
# next tick tries again, a minute later, for as long as it takes.
#
# FAIL DIRECTION. Anything this cannot read, parse or decide ends with the set empty. A state
# directory that cannot be written means the window does not open, and a tick that cannot write
# closes it. A `sealed` marker that is present but malformed is "the wall is DOWN" (seal-check acts on
# that); it is never treated as absent, because absent can open the window.
#
# Paths, the fetch uid, the window length and the tools are compiled in by setup-window.nix. The
# literal placeholder means "repo copy", and only the repo copy reads AGOS_WINDOW_CONFIG (tests).
import fcntl, hashlib, json, os, subprocess, sys, time

CONFIG_JSON = "@AGOS_WINDOW_CONFIG@"

FLOOR = 60                 # seconds every boot that opens the window is charged, at least
LOCK_WAIT = 150            # seconds to wait for the state lock (a seal holds it while it restarts the fetcher)
STATE_MAX = 4096           # bytes of a state file
SCHEMA = 1


def config():
    raw = os.environ.get("AGOS_WINDOW_CONFIG", "") if CONFIG_JSON.startswith("@") else CONFIG_JSON
    try:
        c = json.loads(raw)
    except ValueError:
        raise SystemExit("agos-window: not configured on this box")
    if not isinstance(c.get("fetch_uid"), int) or isinstance(c["fetch_uid"], bool) or c["fetch_uid"] <= 0:
        raise SystemExit("agos-window: the fetch uid must be a positive number (never root)")
    if not 0 < c["length"] <= 4 * 3600:
        raise SystemExit("agos-window: the window length must be at most 4 hours")
    return c


def log(line):
    try:
        import syslog
        syslog.openlog("agos-window", 0, syslog.LOG_AUTHPRIV)
        syslog.syslog(syslog.LOG_NOTICE, line)
    except Exception:
        pass


# ── state files ──────────────────────────────────────────────────────────────────────────────────
def _read(path):
    """(bytes, None), (None, 'absent') or (None, reason)."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except FileNotFoundError:
        return None, "absent"
    except OSError as e:
        return None, "unreadable (%s)" % e.__class__.__name__
    try:
        st = os.fstat(fd)
        import stat
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or st.st_size > STATE_MAX:
            return None, "not a plain file of ours"
        return os.read(fd, STATE_MAX + 1), None
    except OSError as e:
        return None, "unreadable (%s)" % e.__class__.__name__
    finally:
        os.close(fd)


def _write(path, obj):
    d = os.path.dirname(path)
    tmp = os.path.join(d, ".%s.tmp.%d" % (os.path.basename(path), os.getpid()))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        os.write(fd, (json.dumps(obj, sort_keys=True) + "\n").encode("ascii"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.rename(tmp, path)
    dfd = os.open(d, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def load_window(c):
    """('absent', None) | ('valid', state) | ('malformed', reason)."""
    data, why = _read(os.path.join(c["state_dir"], "window.json"))
    if why == "absent":
        return "absent", None
    if data is None:
        return "malformed", why
    try:
        s = json.loads(data.decode("ascii"))
    except ValueError:
        return "malformed", "not JSON"
    need = {"schema", "budget_at_boot", "remaining", "boot_id", "opened_mono", "opened_at", "wall_deadline", "boots"}
    if not isinstance(s, dict) or set(s) != need or s["schema"] != SCHEMA:
        return "malformed", "wrong fields"
    if not (_num(s["budget_at_boot"]) and _num(s["remaining"]) and _num(s["opened_mono"])
            and isinstance(s["boot_id"], str) and isinstance(s["boots"], int) and not isinstance(s["boots"], bool)
            and (s["opened_at"] is None or _num(s["opened_at"]))
            and (s["wall_deadline"] is None or _num(s["wall_deadline"]))):
        return "malformed", "wrong types"
    if not (0 <= s["remaining"] <= s["budget_at_boot"] <= c["length"]) or s["boots"] < 1:
        return "malformed", "out of range"
    return "valid", s


def load_sealed(c):
    """('absent', None) | ('valid', marker) | ('malformed', reason). Valid = the schema parses, the
    fields are well formed and the toplevel is a store path. It does NOT mean the ruleset hash
    matches the running one: a rebuild legitimately changes that (logged, not a failure)."""
    data, why = _read(os.path.join(c["state_dir"], "sealed"))
    if why == "absent":
        return "absent", None
    if data is None:
        return "malformed", why
    try:
        m = json.loads(data.decode("ascii"))
    except ValueError:
        return "malformed", "not JSON"
    if (not isinstance(m, dict) or set(m) != {"schema", "toplevel", "ruleset_sha256", "sealed_at", "reason"}
            or m["schema"] != SCHEMA or not isinstance(m["toplevel"], str)
            or not m["toplevel"].startswith(c.get("store_prefix", "/nix/store/"))
            or not isinstance(m["ruleset_sha256"], str) or len(m["ruleset_sha256"]) != 64
            or any(ch not in "0123456789abcdef" for ch in m["ruleset_sha256"])
            or not _num(m["sealed_at"]) or not isinstance(m["reason"], str) or not m["reason"]):
        return "malformed", "wrong fields"
    return "valid", m


class _Lock:
    """An exclusive flock on <state_dir>/.lock. `held` is False if it could not be taken in time, or
    the directory cannot hold a lock file at all."""
    def __init__(self, c):
        self.fd, self.held = None, False
        try:
            self.fd = os.open(os.path.join(c["state_dir"], ".lock"), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        except OSError:
            return
        end = time.monotonic() + LOCK_WAIT
        while True:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.held = True
                return
            except OSError:
                if time.monotonic() >= end:
                    return
                _nap(0.2)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        if self.fd is not None:
            try:
                os.close(self.fd)                    # closing the descriptor releases the lock
            except OSError:
                pass


def _nap(seconds):
    import select
    select.select([], [], [], seconds)


# ── the decision (pure: no clocks, files or tools inside) ───────────────────────────────────────
def remaining_now(state, now_mono):
    """What is left of the budget for a window opened in THIS boot. Idempotent."""
    return max(0.0, state["budget_at_boot"] - max(FLOOR, now_mono - state["opened_mono"]))


def decide(c, sealed, window, boot_id, now_mono, now_wall):
    """-> ('empty', None) | ('open', new_state, kind) | ('seal', reason) | ('down', reason).
    `sealed` and `window` are the (status, value) pairs from load_sealed / load_window."""
    clock_known = now_wall > c["build_epoch"]
    if c.get("sealed_by_build"):
        return ("empty", None)
    if sealed[0] == "valid":
        return ("empty", None)
    if sealed[0] == "malformed":
        return ("down", "the sealed marker is malformed: " + str(sealed[1]))
    if window[0] == "malformed":
        return ("seal", "invalid state")
    if window[0] == "absent":                        # first boot
        opened_at = now_wall if clock_known else None
        state = {"schema": SCHEMA, "budget_at_boot": float(c["length"]), "remaining": float(c["length"]) - FLOOR,
                 "boot_id": boot_id, "opened_mono": now_mono, "opened_at": opened_at,
                 "wall_deadline": (opened_at + c["length"]) if opened_at is not None else None, "boots": 1}
        return ("open", state, "opened")
    s = window[1]
    if clock_known and s["wall_deadline"] is not None and now_wall > s["wall_deadline"]:
        return ("seal", "deadline")
    if s["boot_id"] == boot_id:                      # a re-derivation in the same boot: charges nothing
        left = remaining_now(s, now_mono)
        if left <= 0:
            return ("seal", "budget")
        return ("open", dict(s, remaining=left), "re-derived")
    if s["remaining"] <= 0:                          # a new boot: the previous one is settled at what it wrote
        return ("seal", "budget")
    state = dict(s, budget_at_boot=s["remaining"], boot_id=boot_id, opened_mono=now_mono, boots=s["boots"] + 1)
    state["remaining"] = remaining_now(state, now_mono)
    if state["remaining"] <= 0:                      # less than one floor was left
        return ("seal", "budget")
    return ("open", state, "reopened")


# ── effects ──────────────────────────────────────────────────────────────────────────────────────
def _tool(argv, timeout=60):
    try:
        return subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout, env={}).returncode
    except (OSError, subprocess.SubprocessError):
        return 1


def close_set(c):
    """Flush the set. One transaction. True if nft said so."""
    return _tool([c["nft"], "-f", c["close_file"]]) == 0


def open_set(c):
    """Flush, then add the one fetch uid: one transaction, so the set is never anything else."""
    return _tool([c["nft"], "-f", c["open_file"]]) == 0


def _boot_id(c):
    try:
        with open(c.get("boot_id_path", "/proc/sys/kernel/random/boot_id")) as f:
            return f.read(64).strip()
    except OSError:
        return ""


def seal(c, reason):
    """Seal, holding the state lock. Without the lock: flush only (always safe), and report failure."""
    with _Lock(c) as lock:
        if not lock.held:
            close_set(c)
            log("seal: the state lock could not be taken; the set was flushed, nothing else was done")
            return 1
        return _seal(c, reason)


def _seal(c, reason):
    """The fixed sequence, under the lock: (1) flush the set, (2) restart the fetcher so it comes up
    with the set already empty and any download in flight is gone (flushing does not end an
    established connection), (3) stop the pull and its retry timer, (4) write the marker. If the flush
    is not confirmed NOTHING else happens and no marker is written. Returns 0 if the marker is on disk."""
    flushed = close_set(c)
    if c.get("sealed_by_build"):
        return 0 if flushed else 1
    status, _ = load_sealed(c)
    if status == "valid":
        return 0 if flushed else 1
    if status == "malformed":
        log("seal refused: the sealed marker is malformed (wall DOWN)")
        return 1
    if not flushed:
        log("seal: nft did not confirm the flush; NOT sealed (no marker); the next tick tries again")
        return 1
    for unit in c.get("restart_units", []):
        _tool([c["systemctl"], "restart", unit], timeout=120)
    for unit in c.get("stop_units", []):             # they also carry a condition on the marker below,
        _tool([c["systemctl"], "stop", unit])        # so they stay down across reboots (a runtime mask would not)
    try:
        with open(c["ruleset_file"], "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        marker = {"schema": SCHEMA, "toplevel": os.path.realpath(c.get("toplevel", "/run/current-system")),
                  "ruleset_sha256": digest, "sealed_at": time.time(), "reason": reason}
        _write(os.path.join(c["state_dir"], "sealed"), marker)
    except OSError as e:
        log("seal: the set is empty but the marker could not be written (%s)" % e.__class__.__name__)
        return 1
    log("sealed reason=%s set_flushed=1" % reason)
    return 0


def recompute(c):
    """Read the state; end with the set empty or holding the fetch uid. 0 unless the wall is down."""
    with _Lock(c) as lock:
        if not lock.held:
            close_set(c)
            log("recompute: the state lock could not be taken; the window stays closed")
            return 1
        return _recompute(c)


def _recompute(c):
    sealed, window = load_sealed(c), load_window(c)
    boot_id = _boot_id(c)
    if not boot_id:
        close_set(c)
        log("recompute: no boot id; the window stays closed")
        return 1
    act = decide(c, sealed, window, boot_id, time.monotonic(), time.time())
    if act[0] == "empty":
        return 0 if close_set(c) else 1
    if act[0] == "down":
        close_set(c)
        log("recompute: " + act[1])
        return 1
    if act[0] == "seal":
        return _seal(c, act[1])
    state, kind = act[1], act[2]
    try:
        _write(os.path.join(c["state_dir"], "window.json"), state)      # the books first: no state, no window
    except OSError as e:
        close_set(c)
        log("recompute: the state cannot be written (%s); the window stays closed" % e.__class__.__name__)
        return 1
    if not open_set(c):
        close_set(c)
        log("recompute: nft refused the open; the window stays closed")
        return 1
    log("window %s remaining=%d boots=%d" % (kind, int(state["remaining"]), state["boots"]))
    return 0


def tick(c, final=False):
    """Write the remaining budget. If it is gone, the wall deadline has passed, or the books cannot be
    kept, close the window HERE."""
    with _Lock(c) as lock:
        if not lock.held:
            close_set(c)                             # cannot coordinate: closing is the one safe act
            log("tick: the state lock could not be taken; window closed")
            return 1
        return _tick(c, final)


def _tick(c, final):
    sealed, window = load_sealed(c), load_window(c)
    if c.get("sealed_by_build") or sealed[0] != "absent" or window[0] != "valid":
        close_set(c)                                 # sealed, down, or no window: nothing to count
        return 0 if (c.get("sealed_by_build") or sealed[0] == "valid") else (1 if sealed[0] == "malformed" else 0)
    s = window[1]
    if s["boot_id"] != _boot_id(c):
        close_set(c)                                 # not opened in this boot (or no boot id): recompute
        return 0                                     # owns opening; closing is always safe
    left = remaining_now(s, time.monotonic())
    try:
        _write(os.path.join(c["state_dir"], "window.json"), dict(s, remaining=left))
    except OSError as e:
        close_set(c)                                 # an enforcer that cannot keep its books closes the window
        log("tick: the state cannot be written (%s); window closed" % e.__class__.__name__)
        return 1
    if final:
        return 0
    # The wall deadline too, by the same rule as `decide`: monotonic time stands still while a box is
    # suspended, so a box that slept through its deadline must not wake with its window still open.
    now_wall = time.time()
    late = now_wall > c["build_epoch"] and s["wall_deadline"] is not None and now_wall > s["wall_deadline"]
    if left <= 0 or late:
        close_set(c)                                 # first, and by itself
        log("tick: the %s; window closed" % ("budget is spent" if left <= 0 else "deadline has passed"))
        return _seal(c, "budget" if left <= 0 else "deadline")
    return 0


def status(c):
    sealed, window = load_sealed(c), load_window(c)
    out = {"sealed": sealed[0], "window": window[0]}
    if sealed[0] == "valid":
        out["reason"] = sealed[1]["reason"]
    if window[0] == "valid":
        out["remaining"] = int(window[1]["remaining"]); out["boots"] = window[1]["boots"]
    sys.stdout.write(json.dumps(out, sort_keys=True) + "\n")
    return 0


def main(argv):
    role = argv[1] if len(argv) > 1 else ""
    if role in ("recompute", "tick", "stop", "status") and len(argv) == 2:
        c = config()
        return {"recompute": recompute, "tick": tick, "stop": lambda c: tick(c, final=True), "status": status}[role](c)
    if role == "seal" and len(argv) == 3 and argv[2] in ("complete", "budget", "deadline", "invalid state", "timeout"):
        return seal(config(), argv[2])
    sys.stderr.write("usage: agos_window.py recompute | tick | stop | seal <complete|budget|deadline|timeout> | status\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
