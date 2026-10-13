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
#   agentos-setup-window wait        agentos-seal.service, which every agent console and the agent's
#                                    user manager start after: return once the box is SEALED. While
#                                    the window is open in this boot it waits for the one detector
#                                    (the fetcher's own GET /api/tags on loopback lists the configured
#                                    model) and then seals; the tick ends the wait by sealing on the
#                                    budget. A window that is not open in this boot is sealed at once.
#   agentos-setup-window ended       that unit's ExecStopPost. If the wait TIMED OUT (the tick should
#                                    have sealed long before: it is the backstop behind the tick), seal.
#                                    Any other end does nothing: a shutdown stops the wait too, and a
#                                    reboot inside the window must reopen it with what is left. The
#                                    agent stays absent either way, because nothing wrote the token.
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
# is LOCK_WAIT (150 s): longer than the longest holder (the table at the limits below; 145 s).
# The window unit's stop timeout is set above both (setup-window.nix), or systemd would kill the
# shutdown stamp while it waits.
#
# SEALED MEANS NO EGRESS, not "no new connections". The ruleset carries a guard ahead of the
# conntrack accept: whatever the fetcher's uid sends off-box is dropped unless the uid is in the set.
# So the instant the set is flushed a download in flight is dead at the wall. Restarting the fetcher
# afterwards is tidying (it should not sit there retrying), not what makes the seal true.
#
# THE MARKER NEVER LIES. `sealed` is written only after nft has confirmed the flush. If the flush
# fails, nothing else in the seal happens and there is no marker: the budget is still spent, so the
# next tick tries again, a minute later, for as long as it takes.
#
# THE AGENT DOES NOT EXIST WHILE THE WINDOW IS OPEN (spec 2.4). The fetcher is the model server: with
# the window open it can reach DNS and 443, and anything that can talk to it on loopback can make it
# send chosen bytes to a chosen host (`/api/pull`). So the agent's account gets no process until the
# box is sealed. This program holds its half of that with one file, <run_dir>/login-ok:
#   - it is written ONLY under the state lock, ONLY when a valid marker is on disk AND nft has just
#     confirmed the set is empty. /run is a tmpfs, so every boot starts without it;
#   - every agent console's login program and the user manager's start condition require it
#     (setup-window.nix). Absent means closed, whatever else did or did not run;
#   - once it exists the window cannot open again in this boot: `recompute` refuses. Removing the
#     state files re-provisions the box at the NEXT boot, never under a running agent.
# <run_dir>/status is one line for the console (open and how long is left, or sealed and why). It is
# display only: nothing decides anything from it.
#
# THE RECORD (spec 2.7). Every transition of the window is one record in the audit chain, written
# through the same pinned `audit` wrapper the wall uses and signed as the same participant (or not at
# all, if the wall signs nothing). Two roles append, and only these two: `recompute` (the opens) and
# `tick` (the seal). The wait never does: it is the one role that reads bytes from a process that has
# been on the network, and its unit has no write access to the chain.
#   - OPENING needs its record FIRST: books, record, set. No record, no window, and no waiting out
#     a budget behind a set that never opened: a refused record, like a refused nft open, seals the
#     box at once (reason `refused`).
#   - SEALING never waits for the record. Whoever seals writes the marker; the tick, which runs once
#     a minute on a sealed box too, appends `window-sealed` built from the marker. Whether that has
#     been done is read from the CHAIN (a scan for this marker's boot id and time), never from a side
#     file: `recorded.json` only saves the scan. So a record can be late, never lost and never twice.
#
# FAIL DIRECTION. Anything this cannot read, parse or decide ends with the set empty. A state
# directory that cannot be written means the window does not open, and a tick that cannot write
# closes it. A `sealed` marker that is present but malformed is "the wall is DOWN" (seal-check acts on
# that); it is never treated as absent, because absent can open the window.
#
# Paths, the fetch uid, the window length and the tools are compiled in by setup-window.nix. The
# literal placeholder means "repo copy", and only the repo copy reads AGOS_WINDOW_CONFIG (tests).
import fcntl, hashlib, json, os, signal, subprocess, sys, time

CONFIG_JSON = "@AGOS_WINDOW_CONFIG@"

FLOOR = 60                 # seconds every boot that opens the window is charged, at least
LOCK_WAIT = 150            # seconds to wait for the state lock (a seal holds it while it restarts the fetcher)
# What a holder of the lock may spend on tools, each bounded: one nft, one restart, each stop. A seal
# is the longest holder (nft + restart + the stops), and it must finish inside LOCK_WAIT, or whoever
# waits behind it gives up: the shutdown stamp would then be skipped and the next boot would inherit
# a budget up to a minute too generous. Killing `systemctl` at its limit does not cancel the job it
# queued; the restart is tidying, never what makes the seal true.
NFT_LIMIT = 20
RESTART_LIMIT = 45
STOP_LIMIT = 10
# The audit chain (spec 2.7): one append, and one read-only scan of the log for a seal's record,
# each limited. While the window is open the agent does not exist, so nothing else writes the chain:
# ten seconds is a failure, not a queue.
AUDIT_LIMIT = 10
SCAN_LIMIT = 10
# The longest holders of the state lock, every tool at its limit (LOCK_WAIT is 150):
#   a seal                                   20 (flush) + 45 (restart) + 2 x 10 (stops)        =  85
#   a recompute whose open is refused        20 (measure) + 10 (record) + 20 (open) + a seal   = 135
#   a tick that seals and then records it    20 (flush) + a seal + 10 (scan) + 20 (measure) + 10 = 145
# The table assumes ONE unit to restart and TWO to stop, which is what setup-window.nix compiles in.
# `config()` computes the same bound from the lists it is given and refuses a configuration that
# would not fit: a third unit to stop would otherwise pass every test and overrun the lock.
LOCK_BUDGET = LOCK_WAIT                               # the design value (tests shorten LOCK_WAIT itself)


def worst_lock_seconds(c):
    seal_s = NFT_LIMIT + RESTART_LIMIT * len(c.get("restart_units", [])) + STOP_LIMIT * len(c.get("stop_units", []))
    return NFT_LIMIT + seal_s + SCAN_LIMIT + NFT_LIMIT + AUDIT_LIMIT
RECORD_SCHEMA = 1
SEAL_REASONS = ("complete", "budget", "deadline", "invalid state", "timeout", "not open", "refused")
STATE_MAX = 4096           # bytes of a state file
POLL = 5                   # seconds between looks while the seal waits for the model
PROBE_LIMIT = 15           # seconds, in all, one look at the fetcher's model list may take
TAGS_MAX = 4 << 20         # bytes of that list we are willing to read
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
    if worst_lock_seconds(c) >= LOCK_BUDGET:
        raise SystemExit("agos-window: with these units a seal could hold the state lock for %d s, and the others wait %d"
                         % (worst_lock_seconds(c), LOCK_BUDGET))
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
    if (not isinstance(m, dict) or set(m) != {"schema", "toplevel", "ruleset_sha256", "sealed_at", "reason", "boot_id"}
            or m.get("reason") not in SEAL_REASONS     # it goes into the audit chain verbatim (reconcile)
            or m["schema"] != SCHEMA or not isinstance(m["toplevel"], str) or not isinstance(m["boot_id"], str)
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


# ── what the rest of the box reads (/run: gone at every boot) ─────────────────────────────────────
def _run_path(c, name):
    d = c.get("run_dir")
    return os.path.join(d, name) if d else None


def logins_enabled(c):
    """Has this boot been sealed with the set confirmed empty? Anything at all at that path counts:
    the answer only ever REFUSES an open."""
    p = _run_path(c, "login-ok")
    return bool(p) and os.path.lexists(p)


def _allow_logins(c):
    """Write the token. Callers hold the state lock and have a valid marker and a confirmed flush."""
    p = _run_path(c, "login-ok")
    if not p:
        return True                                  # no consoles are gated on this box (tests of the core)
    try:
        import stat
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, 0o644)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):       # the gates test for a plain file
                raise OSError("not a plain file")
            os.fchmod(fd, 0o644)
        finally:
            os.close(fd)
        return True
    except OSError as e:
        log("sealed, but the login token could not be written (%s); the agent stays withheld" % e.__class__.__name__)
        return False


def _status(c, **fields):
    """One line for the console. Best effort, never decides anything."""
    p = _run_path(c, "status")
    if not p:
        return
    try:
        tmp = "%s.tmp.%d" % (p, os.getpid())
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC, 0o644)
        try:
            os.fchmod(fd, 0o644)
            os.write(fd, (json.dumps(fields, sort_keys=True) + "\n").encode("ascii"))
        finally:
            os.close(fd)
        os.rename(tmp, p)
    except OSError:
        pass


def _sealed_ok(c, flushed, reason):
    """A valid marker is on disk. 0 only if nft also confirmed the set is empty; only then may the
    agent's account log in."""
    if not flushed:
        return 1
    _status(c, state="sealed", reason=str(reason)[:40])
    return 0 if _allow_logins(c) else 1


# ── the one detector ────────────────────────────────────────────────────────────────────────────
class _Deadline(Exception):
    pass


def _alarm(*_):
    raise _Deadline()


def tags_list_model(body, want):
    """Does this /api/tags body list the model, by its exact name? A name with no tag is `:latest`.
    A half-pulled model is not in the list at all, so it cannot be mistaken for one."""
    if not isinstance(want, str) or not want or len(body) > TAGS_MAX:
        return False
    names = (want,) if ":" in want else (want, want + ":latest")
    try:
        doc = json.loads(body.decode("utf-8"))
    except ValueError:
        return False
    models = doc.get("models") if isinstance(doc, dict) else None
    if not isinstance(models, list):
        return False
    for m in models:
        if isinstance(m, dict) and any(isinstance(m.get(k), str) and m[k] in names for k in ("name", "model")):
            return True
    return False


def _ask(c):
    import http.client
    conn = http.client.HTTPConnection("127.0.0.1", int(c.get("tags_port", 11434)), timeout=5)
    try:
        conn.request("GET", "/api/tags", headers={"Connection": "close"})
        r = conn.getresponse()
        if r.status != 200:
            return False
        return tags_list_model(r.read(TAGS_MAX + 1), c.get("model"))
    finally:
        conn.close()


def model_present(c):
    """Ask the fetcher itself, on loopback, with a hard limit on the whole exchange. It has been on
    the network, so its answer is read as data from a stranger: bounded, parsed, and any doubt is
    "no". Lying "yes" seals the box early; lying "no" changes nothing, the budget closes the window.

    The limit is an alarm that raises. Wherever it lands, including in a cleanup, the answer is "no":
    the outer handler below is the only way out of here with an exception in flight."""
    old = signal.signal(signal.SIGALRM, _alarm)
    try:
        try:
            signal.setitimer(signal.ITIMER_REAL, PROBE_LIMIT)
            return _ask(c)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
    except Exception:                                # a refusal, a bad answer, the deadline: all "no"
        return False
    finally:
        signal.signal(signal.SIGALRM, old)


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
def _tool(argv, timeout=NFT_LIMIT):
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


def set_elements(c):
    """How many uids the KERNEL's set holds, or None if nft cannot say. Measured, never assumed: it is
    the one field of a record that says what the wall held, not what this program intended."""
    try:
        p = subprocess.run([c["nft"], "-j", "list", "set", "inet", "agentos_egress", "setup_uid"],
                           stdin=subprocess.DEVNULL, capture_output=True, timeout=NFT_LIMIT, env={})
        if p.returncode != 0:
            return None
        for item in json.loads(p.stdout.decode("utf-8"))["nftables"]:
            if isinstance(item, dict) and isinstance(item.get("set"), dict):
                elem = item["set"].get("elem", [])
                return len(elem) if isinstance(elem, list) else None
        return None
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        return None


def _generation(c):
    """(toplevel, sha256 of the rendered ruleset): the generation a record or a marker speaks for."""
    with open(c["ruleset_file"], "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    return os.path.realpath(c.get("toplevel", "/run/current-system")), digest


def _append_record(c, event, **fields):
    """One record to the audit chain. True only if `audit append` said it is durable. The signer and
    the pin are the wall's, compiled in and handed to the child explicitly: the child's environment
    is otherwise empty, and a record that silently lost its signature would break every later one."""
    if not c.get("audit"):
        return True                                  # no chain on this box (tests of the core)
    rec = {"src": "setup-window", "schema": RECORD_SCHEMA, "event": event, "fetch_uid": c["fetch_uid"]}
    rec.update(fields)
    env = {}
    if c.get("audit_signer"):
        env["AGENT_OS_AUDIT_SIGNER"] = c["audit_signer"]
    if c.get("audit_require_signed"):
        env["AGENT_OS_AUDIT_REQUIRE_SIGNED"] = c["audit_require_signed"]
    try:
        p = subprocess.run([c["audit"], "append"], input=(json.dumps(rec, sort_keys=True) + "\n").encode("ascii"),
                           capture_output=True, timeout=AUDIT_LIMIT, env=env)
    except (OSError, subprocess.SubprocessError):
        return False
    if p.returncode != 0:
        log("the audit chain refused the record %s (audit append exited %d)" % (event, p.returncode))
        return False
    return True


def _chain_has_seal(c, marker):
    """Is this marker's seal in the chain? True / False, or None if the log cannot be read, or not
    within SCAN_LIMIT (then the record stays owed: a log too long to read in that time is also too
    long for `audit append`, which reads all of it to find its head). The log is root's own ledger,
    read-only here; lines are appended whole, so a reader needs no lock."""
    import time as real_time                         # a deadline on the real clock, whatever a test did to `time`
    deadline = real_time.monotonic() + SCAN_LIMIT
    try:
        fd = os.open(c["audit_log"], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except FileNotFoundError:
        return False
    except OSError:
        return None
    try:
        import stat
        try:
            regular = stat.S_ISREG(os.fstat(fd).st_mode)
        except OSError:
            regular = False
        if not regular:                              # a FIFO there would hold the lock for ever
            os.close(fd)
            return None
        with os.fdopen(fd, "r", encoding="utf-8", errors="replace") as f:
            for n, line in enumerate(f):
                if n % 1024 == 0 and real_time.monotonic() > deadline:
                    return None
                if "window-sealed" not in line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if (isinstance(r, dict) and r.get("src") == "setup-window" and r.get("event") == "window-sealed"
                        and r.get("boot_id") == marker["boot_id"] and r.get("sealed_at") == marker["sealed_at"]):
                    return True
        return False
    except OSError:
        return None


def reconcile(c, marker):
    """Under the state lock, by the tick only: make sure this marker's seal is in the chain. True if
    it is (now or already). A False is a record still owed; the next tick tries again."""
    if not c.get("audit"):
        return True
    key = {"boot_id": marker["boot_id"], "sealed_at": marker["sealed_at"]}
    cache = os.path.join(c["state_dir"], "recorded.json")
    data, _ = _read(cache)
    try:
        if data is not None and json.loads(data.decode("ascii")) == key:
            return True                              # the cache only saves the scan below
    except ValueError:
        pass
    found = _chain_has_seal(c, marker)
    if found is None:
        return False
    if not found:
        elements = set_elements(c)
        if elements is None:
            return False
        if not _append_record(c, "window-sealed", boot_id=marker["boot_id"], toplevel=marker["toplevel"],
                              ruleset_sha256=marker["ruleset_sha256"], reason=marker["reason"],
                              sealed_at=marker["sealed_at"], elements=elements):
            return False
    try:
        _write(cache, key)
    except OSError:
        pass                                         # no cache: the next tick scans, finds it, and stops there
    return True


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
    status, marker = load_sealed(c)
    if status == "valid":
        return _sealed_ok(c, flushed, marker["reason"])
    if status == "malformed":
        log("seal refused: the sealed marker is malformed (wall DOWN)")
        _status(c, state="down")
        return 1
    if not flushed:
        log("seal: nft did not confirm the flush; NOT sealed (no marker); the next tick tries again")
        return 1
    if logins_enabled(c):
        # No marker, yet this boot WAS sealed (the token says so): someone with root removed the state
        # to re-provision the box and has not restarted it. Whoever asks (the wait, a timeout, a seal
        # by hand), a fresh marker here would undo that for good. The set is empty and stays empty
        # (recompute refuses to open); the next boot decides.
        log("seal refused: no marker, but this boot was already sealed; a re-provisioning takes effect at the next boot")
        return 1
    for unit in c.get("restart_units", []):
        _tool([c["systemctl"], "restart", unit], timeout=RESTART_LIMIT)
    for unit in c.get("stop_units", []):             # they also carry a condition on the marker below, so
        _tool([c["systemctl"], "stop", unit], timeout=STOP_LIMIT)    # they stay down across reboots (a runtime mask would not)
    try:
        toplevel, digest = _generation(c)
        marker = {"schema": SCHEMA, "toplevel": toplevel,
                  "ruleset_sha256": digest, "sealed_at": time.time(), "reason": reason, "boot_id": _boot_id(c)}
        _write(os.path.join(c["state_dir"], "sealed"), marker)
    except OSError as e:
        log("seal: the set is empty but the marker could not be written (%s)" % e.__class__.__name__)
        return 1
    log("sealed reason=%s set_flushed=1" % reason)
    return _sealed_ok(c, True, reason)


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
        flushed = close_set(c)
        if sealed[0] == "valid":
            return _sealed_ok(c, flushed, sealed[1]["reason"])
        return 0 if flushed else 1                   # sealed by build: no marker, no consoles gated here
    if act[0] == "down":
        close_set(c)
        log("recompute: " + act[1])
        _status(c, state="down")
        return 1
    if act[0] == "seal":
        return _seal(c, act[1])
    state, kind = act[1], act[2]
    if logins_enabled(c):
        # This boot was sealed and the agent's account may be logged in. The state on disk says "open"
        # (someone removed the marker): that is a re-provisioning, and it takes effect at the next boot.
        close_set(c)
        log("recompute: the state says open, but this boot was already sealed and logins are enabled; "
            "the window stays closed until a reboot")
        return 1
    try:
        _write(os.path.join(c["state_dir"], "window.json"), state)      # the books first: no state, no window
    except OSError as e:
        close_set(c)
        log("recompute: the state cannot be written (%s); the window stays closed" % e.__class__.__name__)
        return 1
    # The record BEFORE the set: books, record, set. It says the program authorised the open and is
    # about to attempt it; `elements` is what the kernel's set held at that moment. If the chain will
    # not take the record, or nft then refuses the open, there is no window in this boot and nothing
    # to wait for: seal now (the tick records that seal), instead of counting a budget against an
    # empty set for two hours.
    try:
        toplevel, digest = _generation(c)
    except OSError:
        toplevel = digest = None
    elements = set_elements(c)
    if (toplevel is None or elements is None
            or not _append_record(c, {"opened": "window-opened", "reopened": "window-reopened", "re-derived": "window-rederived"}[kind],
                                  boot_id=boot_id, toplevel=toplevel, ruleset_sha256=digest, elements=elements,
                                  remaining=int(state["remaining"]), budget_at_boot=int(state["budget_at_boot"]),
                                  boots=state["boots"])):
        log("recompute: the open could not be recorded; no record, no window: sealing")
        return _seal(c, "refused")
    if not open_set(c):
        log("recompute: nft refused the open: sealing")
        return _seal(c, "refused")
    log("window %s remaining=%d boots=%d" % (kind, int(state["remaining"]), state["boots"]))
    _status(c, state="open", remaining=int(state["remaining"]))
    return 0


def tick(c, final=False):
    """Write the remaining budget. If it is gone, the wall deadline has passed, or the books cannot be
    kept, close the window HERE."""
    with _Lock(c) as lock:
        if not lock.held:
            close_set(c)                             # cannot coordinate: closing is the one safe act
            log("tick: the state lock could not be taken; window closed")
            return 1
        r = _tick(c, final)
        if not final and not c.get("sealed_by_build"):
            # The seal's record (spec 2.7): whoever sealed, this is where it reaches the chain. Not
            # at shutdown (`stop`): a record is not worth holding a shutdown for, and the next boot's
            # first tick writes it. The tick's own result is the wall's state, not the record's.
            sealed = load_sealed(c)
            if sealed[0] == "valid" and not reconcile(c, sealed[1]):
                log("tick: the seal's record is still owed to the audit chain; trying again in a minute")
        return r


def _tick(c, final):
    sealed, window = load_sealed(c), load_window(c)
    if c.get("sealed_by_build"):
        return 0 if close_set(c) else 1              # sealed: nothing to count, keep the set empty
    if sealed[0] == "valid":
        return _sealed_ok(c, close_set(c), sealed[1]["reason"])
    if sealed[0] == "malformed":
        close_set(c)
        _status(c, state="down")
        return 1                                     # the wall is DOWN (seal-check acts on it)
    if logins_enabled(c):
        # No marker, but this boot was sealed: a re-provisioning waiting for its reboot (see _seal).
        # There is no window to count in this boot, so there are no books to keep and nothing to seal:
        # keep the set empty and say nothing, once a minute, until the machine restarts.
        return 0 if close_set(c) else 1
    if final:
        if window[0] == "valid" and window[1]["boot_id"] == _boot_id(c):
            try:                                     # the shutdown stamp: write the budget, nothing else
                _write(os.path.join(c["state_dir"], "window.json"), dict(window[1], remaining=remaining_now(window[1], time.monotonic())))
            except OSError:
                return 1
        return 0
    # The tick never OPENS. Whenever the window is not one this boot opened, it does what the per-boot
    # decision would do short of opening: a state that says "seal" is sealed here and now, not left
    # closed-but-unmarked until something happens to run recompute.
    boot_id = _boot_id(c)
    if window[0] != "valid" or not boot_id or window[1]["boot_id"] != boot_id:
        flushed = close_set(c)                       # reported: a tick that could not close has failed
        if window[0] == "absent" or not boot_id:
            return 0 if flushed else 1               # no window yet (recompute opens it), or no boot id
        act = decide(c, sealed, window, boot_id, time.monotonic(), time.time())
        if act[0] == "seal":
            return _seal(c, act[1])
        return 0 if flushed else 1
    s = window[1]
    left = remaining_now(s, time.monotonic())
    try:
        _write(os.path.join(c["state_dir"], "window.json"), dict(s, remaining=left))
    except OSError as e:
        close_set(c)                                 # an enforcer that cannot keep its books closes the window
        log("tick: the state cannot be written (%s); window closed" % e.__class__.__name__)
        return 1
    # The wall deadline too, by the same rule as `decide`: monotonic time stands still while a box is
    # suspended, so a box that slept through its deadline must not wake with its window still open.
    now_wall = time.time()
    late = now_wall > c["build_epoch"] and s["wall_deadline"] is not None and now_wall > s["wall_deadline"]
    if left > 0 and not late:
        _status(c, state="open", remaining=int(left))
    if left <= 0 or late:
        close_set(c)                                 # first, and by itself
        log("tick: the %s; window closed" % ("budget is spent" if left <= 0 else "deadline has passed"))
        return _seal(c, "budget" if left <= 0 else "deadline")
    return 0


def wait(c):
    """agentos-seal.service. Return 0 only when the box is sealed: a valid marker on disk, the set
    confirmed empty, the login token written. Everything that starts a process for the agent's
    account is ordered after this, so 0 is the one way the agent comes to exist.

    The wait ends at the FIRST seal it attempts, successful or not: a seal that fails leaves this
    unit failed and the agent withheld until something starts the unit again (the tick goes on
    closing the window on its budget). It never loops on a failing seal, which would restart the
    fetcher every few seconds."""
    if c.get("sealed_by_build"):
        return 0 if close_set(c) else 1
    r = _wait(c)
    if r != 0:
        _status(c, state="failed")                   # tty1 is told; its getty does not follow a failed wait
    return r


def _wait(c):
    while True:
        with _Lock(c) as lock:
            if not lock.held:
                close_set(c)
                log("wait: the state lock could not be taken; the agent stays withheld")
                return 1
            r = _look(c)
        if r is not None:
            return r
        if model_present(c):                         # outside the lock: it talks to the fetcher
            return seal(c, "complete")               # idempotent if the tick sealed in between
        _nap(POLL)


def _look(c):
    """Under the lock. 0 / 1 = the wait is over; None = the window is open in this boot, keep waiting."""
    sealed, window = load_sealed(c), load_window(c)
    if sealed[0] == "valid":
        return _sealed_ok(c, close_set(c), sealed[1]["reason"])
    if sealed[0] == "malformed":
        close_set(c)
        _status(c, state="down")
        return 1
    if logins_enabled(c):
        # No marker, but this boot WAS sealed: a re-provisioning in progress (see _seal, which refuses
        # the same state for every caller). Here it also keeps the wait from sitting on a window.json
        # that still says "open": there is nothing to wait for until the reboot.
        close_set(c)
        log("wait: no marker, but this boot was already sealed; a re-provisioning takes effect at the next boot")
        return 1
    boot_id = _boot_id(c)
    if boot_id:
        act = decide(c, sealed, window, boot_id, time.monotonic(), time.time())
        if act[0] == "seal":
            return _seal(c, act[1])
        if act[0] == "open" and act[2] == "re-derived":
            return None
    # The books do not say a window was opened in THIS boot (recompute did not run, failed, or
    # refused). Nothing can fetch a model, so there is nothing to wait for: seal.
    return _seal(c, "not open")


def ended(c):
    """ExecStopPost of agentos-seal.service. It runs however the wait ended, and acts in ONE case: the
    wait timed out. The unit's timeout is the budget plus a margin, so by then the tick should have
    sealed; this is the backstop behind a tick that did not.

    Every other end is left alone, on purpose. systemd stops the wait at every shutdown, and a reboot
    inside the window must reopen it with the budget that is left, not find the box sealed. A wait
    that was killed or crashed leaves the window to the tick. In all of those the agent is still
    absent: only a seal writes the login token, and none happened.

    (systemd also says "timeout" when a STOP times out. The wait dies on SIGTERM and holds nothing
    that outlives it, so its stop cannot take TimeoutStopSec; if it ever did, at a shutdown, the cost
    is the one this function exists to avoid: a sealed box. Safe direction, wrong outcome.)"""
    if os.environ.get("SERVICE_RESULT") != "timeout":
        return 0
    r = seal(c, "timeout")
    if r != 0:
        _status(c, state="failed")
    return r


def status(c):
    sealed, window = load_sealed(c), load_window(c)
    out = {"sealed": sealed[0], "window": window[0]}
    if sealed[0] == "valid":
        out["reason"] = sealed[1]["reason"]
        # Was the box sealed under the ruleset this generation loads? A rebuild legitimately changes
        # it, there is no ledger of past rulesets, and it is NOT a failure: seal-check logs it.
        try:
            with open(c["ruleset_file"], "rb") as f:
                out["ruleset"] = "same" if hashlib.sha256(f.read()).hexdigest() == sealed[1]["ruleset_sha256"] else "changed"
        except OSError:
            out["ruleset"] = "unknown"
    if window[0] == "valid":
        out["remaining"] = int(window[1]["remaining"]); out["boots"] = window[1]["boots"]
    sys.stdout.write(json.dumps(out, sort_keys=True) + "\n")
    return 0


def main(argv):
    role = argv[1] if len(argv) > 1 else ""
    if role in ("recompute", "tick", "stop", "status", "wait", "ended") and len(argv) == 2:
        c = config()
        return {"recompute": recompute, "tick": tick, "stop": lambda c: tick(c, final=True), "status": status,
                "wait": wait, "ended": ended}[role](c)
    if role == "seal" and len(argv) == 3 and argv[2] in ("complete", "budget", "deadline", "invalid state", "timeout"):
        return seal(config(), argv[2])
    sys.stderr.write("usage: agos_window.py recompute | tick | stop | wait | ended | seal <complete|budget|deadline|timeout> | status\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
