#!/usr/bin/env python3
# tests/agos-window-battery.py — the CONTRACT BATTERY for modules/agos_window.py, the setup window
# (docs/design/first-login-and-setup-window.md §2.1-§2.3). Fake nft / systemctl on disk record their
# argv in order; the clocks and the boot id are the battery's. Everything is in a temp dir.
#
#   A. First boot: the window opens with the full budget less one floor; the wall deadline is recorded
#      only when the clock is believable (a 1970 clock opens on the budget alone).
#   B. The same boot: remaining is RECOMPUTED (budget_at_boot - max(floor, open seconds)); recomputing
#      or ticking twice at the same instant gives the same number; a re-derivation charges nothing.
#   C. A new boot settles the previous one at what it wrote and charges one floor; five sub-minute
#      boots cost at least five floors; a reboot loop ends in a seal, never in an open window.
#   D. Budget spent -> seal(budget). Wall deadline passed with a believable clock -> seal(deadline).
#      A 1970 clock never trips the deadline. Less than one floor left -> seal, not a zero-length open.
#   E. A valid `sealed` -> the set is emptied, whatever else is on disk. A malformed `sealed` -> DOWN
#      (never "absent": absent can open). A malformed window.json -> seal. Sealed-by-build -> empty.
#   F. `sealed` validity: empty, non-JSON, wrong fields, a toplevel outside the store, a bad hash, a
#      symlink are malformed; a well-formed one is valid whatever its hash.
#   G. window.json validity: wrong types (booleans are not numbers), ranges, extra keys.
#   H. recompute: the state is written BEFORE nft opens the set; a state directory that cannot be
#      written, an nft that refuses, or no boot id all end with the set flushed and nothing opened.
#   I. tick: when the budget is spent the set is flushed FIRST and then the seal runs; a tick that
#      cannot write the state flushes the set; `stop` writes the budget and never seals.
#   J. seal: flush, restart the fetcher, stop the pull and its retry timer, write the marker, in that
#      order; the marker names the toplevel and the sha256 of the ruleset; a second seal only flushes;
#      WITHOUT a confirmed flush there is no marker and nothing else happens, and every later tick
#      tries again until nft answers.
#   L. The tick enforces the wall deadline too (a box that slept through it must not wake open).
#   M. One at a time: while another holder has the state lock, recompute does not open (it flushes
#      and fails), the tick flushes, and seal only flushes; with the lock free they work.
#   O. The login token (<run_dir>/login-ok) exists only after a VALID marker AND a confirmed flush,
#      whoever got there (seal, recompute, tick); never while open, never with a malformed marker,
#      never when nft did not answer. The status line follows the state and decides nothing.
#   P. Once the token exists the window cannot open again in that boot (the state removed under a
#      running agent is a re-provisioning for the NEXT boot).
#   Q. wait (agentos-seal.service): 0 only when sealed, flushed and the token written. Already sealed
#      -> at once. Not opened in this boot -> sealed at once ("not open"), the detector never asked.
#      Open + model listed -> seal(complete), in the fixed order. Open + no model -> it keeps looking
#      until the budget is gone and then seals by itself, tick or no tick. A seal that fails ends the
#      wait with 1 (no loop restarting the fetcher). No lock -> 1.
#   R. ended (ExecStopPost): after a TIMEOUT it seals. After anything else (a clean wait, a stop at
#      shutdown, a kill) it does nothing at all: a reboot inside the window must not seal the box.
#   S. The detector: the exact model name in /api/tags (`name` or `model`), `:latest` for a bare name,
#      never a prefix, a substring or a half-pulled model; a real loopback server: 200 with the model
#      is yes; 404, a redirect, a closed port and a server that never answers (bounded) are no.
#   T. The console's progress program: only printable ASCII from the download line reaches the
#      terminal; it exits on sealed / failed / its limit; it reads no input; the installed copy
#      ignores the environment.
#   U. THE RECORD (spec 2.7), against the REAL bin/audit and a real identity: books, record, set, in
#      that order; the open records' fields; `elements` is what the fake kernel holds, not a constant;
#      re-derived and reopened; a refused record (a torn tail in the chain) or a record that hangs
#      seals at once, `refused`, and nothing opens; the seal's record is written by the TICK from the
#      marker, exactly once whatever is done to the cache, and late rather than lost when the chain is
#      broken; signed as the wall's participant when one is configured (verify passes under the pin)
#      and unsigned otherwise; the wait and the seal append nothing; every reason a seal can carry is
#      one of the seven the spec lists.
#   V. NOTHING THAT READS A FILE MAY RAISE, and a role that raises anyway fails CLOSED. Hostile
#      contents in every file a role reads (a number too large for a float, nesting too deep for the
#      parser, an offset too large to seek to, wrong types), under every role: no exception leaves the
#      role, the set ends EMPTY, there is no login token, and the seal's record is still written when
#      the hostile file is only the pointer. Then the net itself: an exception planted inside each
#      role's own logic ends with the set flushed and 1.
#   W. NO OPEN NEXT TO THE AGENT (spec 5.1), against a fake /proc and passwd. A process with a uid
#      that is not the system's own in ANY of its four uid slots (the agent's account; a normal
#      account; a subordinate uid; a Nix build user; `nobody`; but not a system account or a
#      DynamicUser= uid), before the open: nothing opens, no books, no `window-opened`; the box is
#      sealed `refused` and ONE `window-anomaly` (foreign-process, with the count) is in the chain
#      per boot, whatever is done to its pointer. The set is flushed BEFORE the record is attempted.
#      Nothing in the configuration turns the look off. A process that
#      appears between the open and the second look: the set is flushed BEFORE anything else.
#      What cannot be said counts as present (no /proc, a status with no Uid line, an account that
#      does not resolve or resolves to root, the walk past its limit); a process that EXITED
#      between the listing and the read does not (a first boot is full of them). The fetcher's
#      account with another uid than the set names: sealed, nothing opened. The tick records the
#      login token with no marker once per boot, with what the books look like, and does ONE job on
#      the chain per run.
#   K. config: the fetch uid must be a positive number (never root, never a name); the window is at
#      most 4 hours; the installed copy ignores AGOS_WINDOW_CONFIG; the placeholder appears once.
#
# stdlib only.

SIDE_EFFECTS = []  # a temp dir, removed at exit

import atexit, hashlib, importlib.util, json, os, shutil, sys, tempfile, types

try:
    os.fstat(0)
except OSError:
    os.open(os.devnull, os.O_RDONLY)

HERE = os.path.dirname(os.path.abspath(__file__))
MOD = os.path.abspath(os.path.join(HERE, "..", "modules", "agos_window.py"))
spec = importlib.util.spec_from_file_location("agos_window", MOD)
A = importlib.util.module_from_spec(spec); spec.loader.exec_module(A)

TMP = tempfile.mkdtemp(prefix="agos-window-battery.")
atexit.register(lambda: (os.chmod(STATE, 0o700), shutil.rmtree(TMP, True)))
STATE = os.path.join(TMP, "seal"); os.makedirs(STATE)
BIN = os.path.join(TMP, "bin"); os.makedirs(BIN)
LOG = os.path.join(TMP, "tools.log")
NFT, SYSTEMCTL = os.path.join(BIN, "nft"), os.path.join(BIN, "systemctl")
OPEN_F, CLOSE_F = os.path.join(TMP, "open.nft"), os.path.join(TMP, "close.nft")
RULESET = os.path.join(TMP, "ruleset.nft")
BOOTID = os.path.join(TMP, "boot_id")
TOP = os.path.join(TMP, "toplevel"); os.makedirs(TOP)
for p, text in ((OPEN_F, "flush set; add element\n"), (CLOSE_F, "flush set\n"), (RULESET, "table inet agentos_egress { }\n")):
    open(p, "w").write(text)
BUILD = 1_800_000_000          # the image's build date (epoch seconds)
LENGTH = 7200
passed = []


def check(c, msg):
    if not c:
        raise AssertionError(msg)


def ok(tag):
    passed.append(tag); print("  ok  " + tag)


CORE_PROC, CORE_PASSWD = os.path.join(TMP, "core-proc"), os.path.join(TMP, "core-passwd")
os.makedirs(CORE_PROC)                               # nobody at all: the program has no way to skip the look
open(CORE_PASSWD, "w").write("root:x:0:0::/root:/bin/sh\nollama:x:61:61::/var/empty:/nologin\n")


def cfg(**over):
    c = {"state_dir": STATE, "length": LENGTH, "build_epoch": BUILD, "fetch_uid": 61, "nft": NFT, "systemctl": SYSTEMCTL,
         "proc_path": CORE_PROC, "passwd_path": CORE_PASSWD, "fetch_user": "ollama",
         "open_file": OPEN_F, "close_file": CLOSE_F, "ruleset_file": RULESET, "boot_id_path": BOOTID, "toplevel": TOP,
         "restart_units": ["ollama.service"], "stop_units": ["agent-os-pull-model.timer", "agent-os-pull-model.service"],
         "store_prefix": TMP + "/"}                       # the battery's "store"; the installed copy has no such key
    c.update(over)
    return c


SETSTATE = os.path.join(TMP, "kernel-set.json")      # what the fake kernel's set holds
MEASURED = os.path.join(TMP, "measured.log")         # one line per `nft -j list set` (not in LOG: the
                                                     # arms below count the calls that CHANGE something)


def tool(path, rc=0, measure_rc=None, measure_says=None, open_rc=None):
    """A fake nft / systemctl. It logs its argv and exits rc. As nft it also keeps a set: `-f open`
    puts the uid in, `-f close` empties it, and `-j list set ...` reports it (measure_rc / measure_says
    override that answer)."""
    with open(path, "w") as f:
        f.write("""#!%s
import json, os, sys
a = sys.argv[1:]
if a[:1] == ["-j"]:
    open(%r, "a").write("m\\n")
    mrc = %r
    if mrc:
        sys.exit(mrc)
    says = %r
    elems = says if says is not None else (json.load(open(%r)) if os.path.exists(%r) else [])
    s = {"family": "inet", "name": "setup_uid", "table": "agentos_egress", "type": "uid"}
    if elems:
        s["elem"] = elems
    print(json.dumps({"nftables": [{"metainfo": {"json_schema_version": 1}}, {"set": s}]}))
    sys.exit(0)
open(%r, "a").write(json.dumps([%r] + a) + "\\n")
rc = %d
if a == ["-f", %r] and %r is not None:
    rc = %r
if rc == 0 and a == ["-f", %r]:
    json.dump([61], open(%r, "w"))
if rc == 0 and a == ["-f", %r]:
    json.dump([], open(%r, "w"))
sys.exit(rc)
""" % (sys.executable, MEASURED, rc if measure_rc is None else measure_rc, measure_says, SETSTATE, SETSTATE,
       LOG, os.path.basename(path), rc, OPEN_F, open_rc, open_rc, OPEN_F, SETSTATE, CLOSE_F, SETSTATE))
    os.chmod(path, 0o755)


def calls():
    if not os.path.exists(LOG):
        return []
    out = [json.loads(l) for l in open(LOG)]
    os.unlink(LOG)
    return out


class Clock:
    mono, wall = 100.0, BUILD + 86400.0

    @staticmethod
    def monotonic():
        return Clock.mono

    @staticmethod
    def time():
        return Clock.wall


A.time = Clock                      # the module's clocks are the battery's
A.LOCK_WAIT = 0.3                   # the lock arm waits this long; nothing else contends
A._nap = lambda seconds: setattr(Clock, "mono", Clock.mono + seconds)       # waiting moves the fake clock


def boot(n):
    open(BOOTID, "w").write("boot-%s\n" % n)


def reset():
    os.chmod(STATE, 0o700)
    for f in os.listdir(STATE):
        os.unlink(os.path.join(STATE, f))
    tool(NFT); tool(SYSTEMCTL); calls()
    for f in (SETSTATE, MEASURED):
        if os.path.exists(f):
            os.unlink(f)
    Clock.mono, Clock.wall = 100.0, BUILD + 86400.0
    boot(1)


def window():
    return json.load(open(os.path.join(STATE, "window.json")))


def state_files():
    return sorted(f for f in os.listdir(STATE) if f != ".lock")      # the lock file is not state


def put(name, obj):
    with open(os.path.join(STATE, name), "w") as f:
        f.write(obj if isinstance(obj, str) else json.dumps(obj))
    os.chmod(os.path.join(STATE, name), 0o600)


OPEN, CLOSE = ["nft", "-f", OPEN_F], ["nft", "-f", CLOSE_F]
GOOD_SEALED = {"schema": 1, "toplevel": TOP, "ruleset_sha256": "0" * 64,
               "sealed_at": 1.0, "reason": "complete", "boot_id": "boot-0"}


class FakeSyslog(types.ModuleType):
    LOG_AUTHPRIV, LOG_NOTICE = 80, 5

    def __init__(self):
        super().__init__("syslog"); self.lines = []

    def openlog(self, *a):
        pass

    def syslog(self, prio, line):
        self.lines.append(line)


SYS = FakeSyslog(); sys.modules["syslog"] = SYS

# A — first boot
reset()
check(A.recompute(cfg()) == 0 and calls() == [OPEN], "A: first boot must open")
w = window()
check(w["budget_at_boot"] == LENGTH and w["remaining"] == LENGTH - A.FLOOR and w["boots"] == 1 and w["boot_id"] == "boot-1",
      "A: first-boot state: %r" % w)
check(w["opened_at"] == Clock.wall and w["wall_deadline"] == Clock.wall + LENGTH, "A: the deadline with a believable clock: %r" % w)
check((os.stat(os.path.join(STATE, "window.json")).st_mode & 0o777) == 0o600, "A: state mode")
reset(); Clock.wall = 12.0                                  # no RTC battery: 1970
check(A.recompute(cfg()) == 0 and calls() == [OPEN], "A: a 1970 clock must still open (on the budget)")
w = window()
check(w["opened_at"] is None and w["wall_deadline"] is None and w["remaining"] == LENGTH - A.FLOOR, "A: 1970: no deadline recorded: %r" % w)
ok("A first boot opens on the full budget less one floor; a 1970 clock opens too and records no deadline")

# B — the same boot: recomputed, idempotent, a re-derivation charges nothing
reset(); A.recompute(cfg()); calls()
Clock.mono = 100.0 + 30                                       # 30 s open: still inside the floor
check(A.recompute(cfg()) == 0 and calls() == [OPEN] and window()["remaining"] == LENGTH - A.FLOOR, "B: inside the floor: %r" % window())
Clock.mono = 100.0 + 600
A.tick(cfg()); first = window()["remaining"]
A.tick(cfg()); A.recompute(cfg()); A.tick(cfg())
check(first == LENGTH - 600 and window()["remaining"] == first and window()["budget_at_boot"] == LENGTH and window()["boots"] == 1,
      "B: ticking and recomputing at one instant must give one number: %r %r" % (first, window()))
calls()
for _ in range(5):                                           # five nftables reloads at the same instant
    A.recompute(cfg())
check(window()["remaining"] == first and calls() == [OPEN] * 5, "B: a re-derivation charged something: %r" % window())
ok("B remaining is recomputed from the monotonic clock; same-boot re-derivations charge nothing")

# C — a new boot settles the previous one and charges one floor; a reboot loop ends in a seal
reset(); A.recompute(cfg()); Clock.mono = 100.0 + 1000; A.tick(cfg(), final=True)        # 1000 s open, then shutdown
check(window()["remaining"] == LENGTH - 1000, "C: the stop stamp: %r" % window())
boot(2); Clock.mono = 5.0; calls()                            # the monotonic clock starts again
check(A.recompute(cfg()) == 0 and calls() == [OPEN], "C: a reboot inside the window must reopen")
w = window()
check(w["budget_at_boot"] == LENGTH - 1000 and w["remaining"] == LENGTH - 1000 - A.FLOOR and w["boots"] == 2 and w["boot_id"] == "boot-2",
      "C: the new boot's books: %r" % w)
reset(); A.recompute(cfg())
for n in range(2, 7):                                         # five boots of 10 seconds each, no clean stop
    boot(n); Clock.mono = 10.0; A.recompute(cfg())
check(window()["remaining"] <= LENGTH - 6 * A.FLOOR and window()["boots"] == 6, "C: five short boots must cost five floors: %r" % window())
n, sealed_at = 7, None
while n < 400 and sealed_at is None:
    boot(n); Clock.mono = 1.0; calls(); A.recompute(cfg())
    if os.path.exists(os.path.join(STATE, "sealed")):
        sealed_at = n
    n += 1
check(sealed_at is not None and sealed_at <= 2 + LENGTH // A.FLOOR, "C: a reboot loop never sealed (boot %r)" % sealed_at)
check(json.load(open(os.path.join(STATE, "sealed")))["reason"] == "budget", "C: the loop's seal reason")
boot(999); calls()
check(A.recompute(cfg()) == 0 and calls() == [CLOSE], "C: after the seal a boot must not reopen")
ok("C a new boot settles the old one and costs a floor; a reboot loop ends sealed and stays sealed")

# D — budget, deadline, clocks
reset(); A.recompute(cfg()); Clock.mono = 100.0 + LENGTH + 5; calls()
check(A.recompute(cfg()) == 0 and json.load(open(os.path.join(STATE, "sealed")))["reason"] == "budget", "D: budget spent")
reset(); A.recompute(cfg()); boot(2); Clock.mono = 1.0; Clock.wall = BUILD + 86400.0 + LENGTH + 1; calls()
check(A.recompute(cfg()) == 0 and json.load(open(os.path.join(STATE, "sealed")))["reason"] == "deadline", "D: deadline passed")
reset(); A.recompute(cfg()); boot(2); Clock.mono = 1.0; Clock.wall = 12.0; calls()            # the clock fell back to 1970
check(A.recompute(cfg()) == 0 and calls() == [OPEN] and not os.path.exists(os.path.join(STATE, "sealed")),
      "D: a 1970 clock must not trip the deadline")
reset(); Clock.wall = 12.0; A.recompute(cfg()); boot(2); Clock.mono = 1.0; Clock.wall = BUILD + 10 ** 9; calls()
check(A.recompute(cfg()) == 0 and calls() == [OPEN], "D: opened under 1970 there is no deadline for a later clock to trip")
reset(); A.recompute(cfg()); Clock.mono = 100.0 + LENGTH - 30; A.tick(cfg(), final=True)          # 30 s left
boot(2); Clock.mono = 1.0; calls()
check(A.recompute(cfg()) == 0 and os.path.exists(os.path.join(STATE, "sealed")) and OPEN not in calls(), "D: under one floor left must seal, not open")
ok("D budget and deadline seal; a 1970 clock never trips the deadline; less than a floor left seals")

# E — sealed / malformed / sealed-by-build
reset(); A.recompute(cfg()); put("sealed", GOOD_SEALED); calls()
check(A.recompute(cfg()) == 0 and calls() == [CLOSE], "E: a valid sealed marker must empty the set")
reset(); put("sealed", ""); calls()
check(A.recompute(cfg()) == 1 and calls() == [CLOSE] and not os.path.exists(os.path.join(STATE, "window.json")),
      "E: a malformed sealed marker is DOWN, never a first boot")
reset(); put("window.json", "garbage"); calls()
check(A.recompute(cfg()) == 0 and json.load(open(os.path.join(STATE, "sealed")))["reason"] == "invalid state" and OPEN not in calls(),
      "E: a malformed window.json must seal")
reset(); calls()
check(A.recompute(cfg(sealed_by_build=True)) == 0 and calls() == [CLOSE] and state_files() == [], "E: sealed-by-build never opens and writes nothing")
check(A.tick(cfg(sealed_by_build=True)) == 0 and calls() == [CLOSE], "E: sealed-by-build tick")
ok("E a valid marker empties the set; a malformed one is DOWN; bad window state seals; sealed-by-build never opens")

# F — `sealed` validity
reset()
bads = {"empty": "", "not json": "{", "list": "[]", "extra": dict(GOOD_SEALED, x=1), "missing": {k: v for k, v in GOOD_SEALED.items() if k != "reason"},
        "schema": dict(GOOD_SEALED, schema=2), "toplevel outside the store": dict(GOOD_SEALED, toplevel="/etc"),
        "short hash": dict(GOOD_SEALED, ruleset_sha256="0" * 63), "upper hash": dict(GOOD_SEALED, ruleset_sha256="A" * 64),
        "time": dict(GOOD_SEALED, sealed_at=True), "reason": dict(GOOD_SEALED, reason="")}
for tag, body in bads.items():
    put("sealed", body)
    check(A.load_sealed(cfg())[0] == "malformed", "F %s: %r" % (tag, A.load_sealed(cfg())))
os.unlink(os.path.join(STATE, "sealed")); put("elsewhere", GOOD_SEALED); os.symlink(os.path.join(STATE, "elsewhere"), os.path.join(STATE, "sealed"))
check(A.load_sealed(cfg())[0] == "malformed", "F: a symlinked marker was followed")
os.unlink(os.path.join(STATE, "sealed")); os.unlink(os.path.join(STATE, "elsewhere"))
check(A.load_sealed(cfg())[0] == "absent", "F: absent")
put("sealed", dict(GOOD_SEALED, ruleset_sha256="f" * 64))
check(A.load_sealed(cfg())[0] == "valid", "F: a well-formed marker is valid whatever its hash")
ok("F the sealed marker: malformed in every bad shape, valid when well formed, absent only when absent")

# G — window.json validity
reset(); A.recompute(cfg()); good = window()
for tag, body in (("bool budget", dict(good, budget_at_boot=True)), ("string", dict(good, remaining="1")), ("negative", dict(good, remaining=-1)),
                  ("more than budget", dict(good, remaining=good["budget_at_boot"] + 1)), ("over length", dict(good, budget_at_boot=LENGTH + 1, remaining=1)),
                  ("boots 0", dict(good, boots=0)), ("bool boots", dict(good, boots=True)), ("extra", dict(good, x=1)),
                  ("schema", dict(good, schema=2)), ("boot id", dict(good, boot_id=7)), ("deadline", dict(good, wall_deadline="soon"))):
    put("window.json", body)
    check(A.load_window(cfg())[0] == "malformed", "G %s: %r" % (tag, A.load_window(cfg())))
ok("G window.json: wrong types, ranges and extra keys are malformed")

# H — recompute's order and its failures
reset()
order = []
real_write, real_tool = A._write, A._tool
A._write = lambda p, o: (order.append("write " + os.path.basename(p)), real_write(p, o))[1]
A._tool = lambda argv, timeout=60: (order.append(" ".join(os.path.basename(a) for a in argv[:3])), real_tool(argv, timeout))[1]
try:
    A.recompute(cfg())
finally:
    A._write, A._tool = real_write, real_tool
check(order == ["write window.json", "nft -f open.nft"], "H: the state must be on disk before the set is opened: %r" % order)
reset(); os.chmod(STATE, 0o500); calls()
check(A.recompute(cfg()) == 1 and calls() == [CLOSE], "H: an unwritable state directory must not open the window")
# An open that does not happen is not waited out: the box seals at once, reason `refused` (it used
# to sit "open" on its books with an empty set until the budget ran out, two hours on a real box).
SEAL_TAIL = [CLOSE, ["systemctl", "restart", "ollama.service"], ["systemctl", "stop", "agent-os-pull-model.timer"],
             ["systemctl", "stop", "agent-os-pull-model.service"]]
reset(); tool(NFT, open_rc=1); calls()
check(A.recompute(cfg()) == 0 and calls() == [OPEN] + SEAL_TAIL
      and json.load(open(os.path.join(STATE, "sealed")))["reason"] == "refused"
      and json.load(open(SETSTATE)) == [], "H: an nft that refuses the open must seal at once (refused), set empty")
w = window()
check(w["boots"] == 1 and w["remaining"] == LENGTH - A.FLOOR, "H: a refused open charges one floor, no more: %r" % w)
reset(); tool(NFT, rc=1); calls()
check(A.recompute(cfg()) == 1 and calls() == [CLOSE] and not os.path.exists(os.path.join(STATE, "sealed")),
      "H: an nft that answers nothing at all: no open attempted, no marker without a confirmed flush")
reset(); tool(NFT, measure_rc=1); calls()
check(A.recompute(cfg()) == 0 and calls() == SEAL_TAIL and json.load(open(os.path.join(STATE, "sealed")))["reason"] == "refused",
      "H: a set that cannot be measured is not opened")
reset(); os.unlink(BOOTID); calls()
check(A.recompute(cfg()) == 1 and calls() == [CLOSE] and state_files() == [], "H: no boot id must not open the window")
ok("H recompute writes the state first; every failure ends with the set flushed and nothing opened")

# I — tick
reset(); A.recompute(cfg()); Clock.mono = 100.0 + LENGTH + 1; calls()
rc = A.tick(cfg()); c = calls()
check(rc == 0 and c[0] == CLOSE and c[1] == CLOSE and c[2] == ["systemctl", "restart", "ollama.service"],
      "I: the budget is spent: flush FIRST, then the seal's own sequence: %r" % c)
check(json.load(open(os.path.join(STATE, "sealed")))["reason"] == "budget", "I: seal reason")
reset(); A.recompute(cfg()); Clock.mono = 100.0 + 500; os.chmod(STATE, 0o500); calls()
check(A.tick(cfg()) == 1 and calls() == [CLOSE], "I: a tick that cannot write the state must flush the set")
reset(); A.recompute(cfg()); Clock.mono = 100.0 + LENGTH + 1; calls()
check(A.tick(cfg(), final=True) == 0 and calls() == [] and window()["remaining"] == 0 and not os.path.exists(os.path.join(STATE, "sealed")),
      "I: the stop stamp writes the budget and never seals")
reset(); A.recompute(cfg()); boot(2); Clock.mono = 100.0 + 900; before = window(); calls()     # the clock has moved: a stamp would show
check(A.tick(cfg(), final=True) == 0 and calls() == [] and window() == before, "I: the stop stamp never touches a window from another boot, or the set")
reset(); A.recompute(cfg()); put("sealed", GOOD_SEALED); calls()
check(A.tick(cfg()) == 0 and calls() == [CLOSE], "I: sealed: a tick only flushes")
reset(); A.recompute(cfg()); boot(2); calls(); before = window()
check(A.tick(cfg()) == 0 and calls() == [CLOSE] and window() == before and not os.path.exists(os.path.join(STATE, "sealed")),
      "I: a window from another boot with budget left: the tick flushes, writes nothing, and does NOT open")
os.unlink(BOOTID); calls()
check(A.tick(cfg()) == 0 and calls() == [CLOSE] and window() == before, "I: no boot id: the tick flushes and writes nothing")
# Closed must not stay unmarked: where the per-boot decision would seal, the tick seals.
reset(); A.recompute(cfg()); Clock.mono = 100.0 + LENGTH - 30; A.tick(cfg(), final=True); boot(2); Clock.mono = 1.0; calls()
check(A.tick(cfg()) == 0 and json.load(open(os.path.join(STATE, "sealed")))["reason"] == "budget" and OPEN not in calls(),
      "I: a spent budget from an earlier boot must be SEALED by the tick, not left closed and unmarked")
reset(); A.recompute(cfg()); boot(2); Clock.mono = 1.0; Clock.wall = BUILD + 86400.0 + LENGTH + 1; calls()
check(A.tick(cfg()) == 0 and json.load(open(os.path.join(STATE, "sealed")))["reason"] == "deadline", "I: a passed deadline from an earlier boot must be sealed by the tick")
reset(); put("window.json", "garbage"); calls()
check(A.tick(cfg()) == 0 and json.load(open(os.path.join(STATE, "sealed")))["reason"] == "invalid state" and calls()[0] == CLOSE,
      "I: a malformed window.json must be sealed by the tick")
reset(); calls()
check(A.tick(cfg()) == 0 and calls() == [CLOSE] and state_files() == [], "I: no window yet: the tick flushes and creates nothing")
reset(); A.recompute(cfg()); tool(NFT, rc=1); put("sealed", GOOD_SEALED); calls()
check(A.tick(cfg()) == 1 and calls() == [CLOSE], "I: sealed but the flush fails: the tick must report it")
ok("I tick flushes before sealing, flushes when it cannot write, and its stop form never seals")

# J — the seal sequence and marker
reset(); A.recompute(cfg()); calls()
check(A.seal(cfg(), "complete") == 0, "J: seal rc")
check(calls() == [CLOSE, ["systemctl", "restart", "ollama.service"], ["systemctl", "stop", "agent-os-pull-model.timer"],
                  ["systemctl", "stop", "agent-os-pull-model.service"]], "J: the sequence")
m = json.load(open(os.path.join(STATE, "sealed")))
check(m["toplevel"] == os.path.realpath(TOP) and m["ruleset_sha256"] == hashlib.sha256(open(RULESET, "rb").read()).hexdigest()
      and m["reason"] == "complete" and m["schema"] == 1 and m["sealed_at"] == Clock.wall, "J: marker: %r" % m)
check(A.seal(cfg(), "budget") == 0 and calls() == [CLOSE] and json.load(open(os.path.join(STATE, "sealed"))) == m, "J: a second seal only flushes")
reset(); A.recompute(cfg()); tool(NFT, rc=1); calls()
check(A.seal(cfg(), "complete") == 1 and not os.path.exists(os.path.join(STATE, "sealed")) and calls() == [CLOSE],
      "J: no confirmed flush -> NO marker and nothing else done (the marker never lies)")
Clock.mono = 100.0 + LENGTH + 1                                # ...and the budget being spent, every tick tries again
check(A.tick(cfg()) == 1 and not os.path.exists(os.path.join(STATE, "sealed")) and calls() == [CLOSE, CLOSE], "J: the retry while nft still fails")
tool(NFT); calls()
check(A.tick(cfg()) == 0 and json.load(open(os.path.join(STATE, "sealed")))["reason"] == "budget", "J: the retry seals as soon as nft answers")
reset(); put("sealed", "x"); calls()
check(A.seal(cfg(), "complete") == 1 and calls() == [CLOSE] and open(os.path.join(STATE, "sealed")).read() == "x", "J: a malformed marker is not overwritten")
# The seal is the longest holder of the lock. Everything it may spend on tools must fit inside the
# time the others wait for the lock, or the shutdown stamp is skipped behind it.
spent = []
real_tool = A._tool
A._tool = lambda argv, timeout=A.NFT_LIMIT: (spent.append((os.path.basename(argv[0]), argv[1], timeout)), real_tool(argv, timeout))[1]
try:
    reset(); A.recompute(cfg()); del spent[:]
    check(A.seal(cfg(), "complete") == 0, "J: seal")
finally:
    A._tool = real_tool
check([t for _, _, t in spent] == [A.NFT_LIMIT, A.RESTART_LIMIT, A.STOP_LIMIT, A.STOP_LIMIT], "J: every tool call under the lock must carry its limit: %r" % spent)
# The longest holders, every tool at its limit (the table above LOCK_WAIT in the program):
seal_s = A.NFT_LIMIT + A.RESTART_LIMIT + 2 * A.STOP_LIMIT
check(seal_s == 85 and A.PROC_LIMIT + A.NFT_LIMIT + seal_s + A.SCAN_LIMIT + A.NFT_LIMIT + A.AUDIT_LIMIT == 150
      and A.PROC_LIMIT == 5
      and A.PROC_LIMIT + A.NFT_LIMIT + A.AUDIT_LIMIT + A.NFT_LIMIT + A.PROC_LIMIT + seal_s + A.SCAN_LIMIT + A.AUDIT_LIMIT == 165,
      "J: the lock-time table in the program no longer matches its constants")
import re
REAL_LOCK_WAIT = int(re.search(r"^LOCK_WAIT = (\d+)", open(MOD).read(), re.M).group(1))      # the battery shortens A.LOCK_WAIT
check(165 < REAL_LOCK_WAIT                                                                       # the longest holder of all
      and sum(t for _, _, t in spent) < REAL_LOCK_WAIT and A.NFT_LIMIT + REAL_LOCK_WAIT < 200,
      "J: a seal (%d s of tools at most) must finish inside the lock wait (%d s), and a stamp that waited for the lock must still fit the unit's 200 s stop timeout"
      % (sum(t for _, _, t in spent), REAL_LOCK_WAIT))
reset(); tool(NFT, rc=1); calls()
check(A.tick(cfg()) == 1, "J: a tick that cannot close (no window yet) must report it")
reset(); A.recompute(cfg()); boot(2); tool(NFT, rc=1); calls()
check(A.tick(cfg()) == 1 and not os.path.exists(os.path.join(STATE, "sealed")), "J: a tick that cannot close a window from another boot must report it")
ok("J seal: flush, restart the fetcher, stop the retry, write the marker; idempotent; a failed flush is reported")

# K — configuration
src = open(MOD).read()
check(src.count('CONFIG_JSON = "@AGOS_WINDOW_CONFIG@"') == 1, "K: the substitution target must appear exactly once")
for bad in (dict(cfg(), fetch_uid=0), dict(cfg(), fetch_uid=-1), dict(cfg(), fetch_uid=True), dict(cfg(), fetch_uid="ollama"),
            dict(cfg(), length=4 * 3600 + 1), dict(cfg(), length=0)):
    os.environ["AGOS_WINDOW_CONFIG"] = json.dumps(bad)
    try:
        A.config(); raised = False
    except SystemExit:
        raised = True
    check(raised, "K: config accepted fetch_uid=%r length=%r" % (bad["fetch_uid"], bad["length"]))
inst = src.replace('"@AGOS_WINDOW_CONFIG@"', json.dumps(json.dumps(cfg(fetch_uid=77))))
I = types.ModuleType("agos_window_installed"); exec(compile(inst, "agos_window_installed.py", "exec"), I.__dict__)
os.environ["AGOS_WINDOW_CONFIG"] = json.dumps(cfg(fetch_uid=1234))
check(I.config()["fetch_uid"] == 77, "K: the installed copy read AGOS_WINDOW_CONFIG")
os.environ.pop("AGOS_WINDOW_CONFIG")
try:
    A.config(); raised = False
except SystemExit:
    raised = True
check(raised, "K: an unconfigured copy did not refuse")
ok("K the fetch uid is a positive number, the window at most 4 hours, and the installed copy ignores the environment")

# L — the tick enforces the wall deadline (suspend: monotonic time stands still, the wall clock does not)
reset(); A.recompute(cfg()); Clock.mono = 100.0 + 120; Clock.wall = BUILD + 86400.0 + LENGTH + 5; calls()
check(A.tick(cfg()) == 0 and calls()[0] == CLOSE and json.load(open(os.path.join(STATE, "sealed")))["reason"] == "deadline",
      "L: a tick past the wall deadline must close and seal(deadline)")
reset(); Clock.wall = 12.0; A.recompute(cfg()); Clock.mono = 100.0 + 120; Clock.wall = BUILD + 10 ** 9; calls()
check(A.tick(cfg()) == 0 and calls() == [] and not os.path.exists(os.path.join(STATE, "sealed")), "L: opened under 1970 there is no deadline for the tick to enforce")
reset(); A.recompute(cfg()); Clock.mono = 100.0 + 120; Clock.wall = 12.0; calls()
check(A.tick(cfg()) == 0 and calls() == [] and not os.path.exists(os.path.join(STATE, "sealed")), "L: a 1970 clock must not trip the deadline in the tick")
ok("L the tick enforces the wall deadline by the same rule as the per-boot decision")

# M — one at a time
import fcntl
reset(); calls()
held = os.open(os.path.join(STATE, ".lock"), os.O_RDWR | os.O_CREAT, 0o600); fcntl.flock(held, fcntl.LOCK_EX)
try:
    check(A.recompute(cfg()) == 1 and calls() == [CLOSE] and not os.path.exists(os.path.join(STATE, "window.json")),
          "M: recompute opened the window without the state lock")
    check(A.tick(cfg()) == 1 and calls() == [CLOSE], "M: a tick without the lock must flush")
    check(A.seal(cfg(), "complete") == 1 and calls() == [CLOSE] and not os.path.exists(os.path.join(STATE, "sealed")),
          "M: a seal without the lock must only flush")
finally:
    os.close(held)
check(A.recompute(cfg()) == 0 and calls() == [OPEN], "M: with the lock free recompute opens")
check((os.stat(os.path.join(STATE, ".lock")).st_mode & 0o777) == 0o600, "M: lock file mode")
ok("M without the state lock nothing opens; everything still flushes")

# N — the command line: exactly these roles, nothing else
import io
reset()
os.environ["AGOS_WINDOW_CONFIG"] = json.dumps(cfg())
try:
    check(A.main(["x", "recompute"]) == 0 and calls() == [OPEN], "N: recompute")
    check(A.main(["x", "tick"]) == 0 and A.main(["x", "stop"]) == 0, "N: tick / stop")
    so, sys.stdout = sys.stdout, io.StringIO()
    try:
        rc = A.main(["x", "status"]); out = sys.stdout.getvalue()
    finally:
        sys.stdout = so
    st = json.loads(out)
    check(rc == 0 and st == {"sealed": "absent", "window": "valid", "remaining": LENGTH - A.FLOOR, "boots": 1}, "N: status: %r" % st)
    calls(); se, sys.stderr = sys.stderr, io.StringIO()
    try:
        for bad in (["x"], ["x", "open"], ["x", "recompute", "extra"], ["x", "seal"], ["x", "seal", "because"], ["x", "seal", "complete", "x"]):
            check(A.main(bad) == 2 and calls() == [], "N: %r must be refused and do nothing" % bad)
    finally:
        sys.stderr = se
    check(A.main(["x", "seal", "complete"]) == 0 and json.load(open(os.path.join(STATE, "sealed")))["reason"] == "complete", "N: seal complete")
    so, sys.stdout = sys.stdout, io.StringIO()
    try:
        A.main(["x", "status"]); st = json.loads(sys.stdout.getvalue())
    finally:
        sys.stdout = so
    check(st["sealed"] == "valid" and st["reason"] == "complete" and st["ruleset"] == "same", "N: status after the seal: %r" % st)
    open(RULESET, "a").write("# a rebuild changed the ruleset\n")
    so, sys.stdout = sys.stdout, io.StringIO()
    try:
        A.main(["x", "status"]); st2 = json.loads(sys.stdout.getvalue())
    finally:
        sys.stdout = so
    check(st2["sealed"] == "valid" and st2["ruleset"] == "changed" and A.load_sealed(cfg())[0] == "valid",
          "N: a sealed box whose ruleset was rebuilt is still sealed, and says the generation changed: %r" % st2)
    open(RULESET, "w").write("table inet agentos_egress { }\n")
finally:
    os.environ.pop("AGOS_WINDOW_CONFIG")
ok("N the command line accepts exactly its roles; status reports the state")

# ── slice 3b, part 1 ────────────────────────────────────────────────────────────────────────────
RUN = os.path.join(TMP, "run"); os.makedirs(RUN)
TOKEN, STATUS = os.path.join(RUN, "login-ok"), os.path.join(RUN, "status")
SEAL_SEQ = [CLOSE, ["systemctl", "restart", "ollama.service"], ["systemctl", "stop", "agent-os-pull-model.timer"],
            ["systemctl", "stop", "agent-os-pull-model.service"]]


def rcfg(**over):
    return cfg(**dict({"run_dir": RUN, "model": "qwen3.5:9b"}, **over))


def rreset():
    reset()
    for f in os.listdir(RUN):
        os.unlink(os.path.join(RUN, f))


def status_line():
    return json.load(open(STATUS)) if os.path.exists(STATUS) else None


def marker():
    return json.load(open(os.path.join(STATE, "sealed")))


# O — the token
rreset()
check(A.recompute(rcfg()) == 0 and not os.path.exists(TOKEN), "O: a token while the window is open")
check(status_line() == {"state": "open", "remaining": LENGTH - A.FLOOR}, "O: status while open: %r" % status_line())
Clock.mono = 100.0 + 600
check(A.tick(rcfg()) == 0 and status_line() == {"state": "open", "remaining": LENGTH - 600} and not os.path.exists(TOKEN), "O: the tick's status")
check(A.seal(rcfg(), "complete") == 0 and os.path.isfile(TOKEN) and (os.stat(TOKEN).st_mode & 0o777) == 0o644
      and status_line() == {"state": "sealed", "reason": "complete"}, "O: after a seal: token + status")
for who, call in (("recompute", A.recompute), ("tick", A.tick), ("seal", lambda c: A.seal(c, "budget"))):
    rreset(); put("sealed", GOOD_SEALED); tool(NFT, rc=1); calls()
    check(call(rcfg()) == 1 and not os.path.exists(TOKEN), "O: %s wrote the token although nft did not confirm the flush" % who)
    tool(NFT); calls()
    check(call(rcfg()) == 0 and os.path.isfile(TOKEN), "O: %s on a sealed box with nft answering must enable logins" % who)
for who, call in (("recompute", A.recompute), ("tick", A.tick), ("seal", lambda c: A.seal(c, "budget")), ("wait", A.wait)):
    rreset(); put("sealed", "{}"); calls()
    check(call(rcfg()) == 1 and not os.path.exists(TOKEN) and status_line() == {"state": "failed" if who == "wait" else "down"},
          "O: %s with a malformed marker must not enable logins: %r" % (who, status_line()))
rreset(); A.recompute(rcfg()); tool(NFT, rc=1); calls()
check(A.seal(rcfg(), "complete") == 1 and not os.path.exists(TOKEN), "O: a seal whose flush failed wrote the token")
rreset(); A.recompute(rcfg())
check(A.seal(rcfg(run_dir=os.path.join(TMP, "no-such-dir")), "complete") == 1 and os.path.exists(os.path.join(STATE, "sealed")),
      "O: sealed but the token cannot be written must be a failure (the agent stays withheld)")
rreset(); os.mkdir(TOKEN); put("sealed", GOOD_SEALED)
check(A.recompute(rcfg()) == 1 and os.path.isdir(TOKEN), "O: a token path that is not a plain file must not count as enabling logins")
os.rmdir(TOKEN); os.mkfifo(TOKEN)
check(A.recompute(rcfg()) == 1, "O: a FIFO at the token's path must fail at once (not block, not pass)")
reader = os.open(TOKEN, os.O_RDONLY | os.O_NONBLOCK)           # with a reader the open succeeds: still not a token
try:
    check(A.recompute(rcfg()) == 1, "O: a FIFO somebody is reading is not a plain file and must not enable logins")
finally:
    os.close(reader)
os.unlink(TOKEN)
victim = os.path.join(TMP, "victim")
rreset(); os.symlink(victim, TOKEN); put("sealed", GOOD_SEALED)
check(A.recompute(rcfg()) == 1 and os.path.islink(TOKEN) and not os.path.exists(victim), "O: the token must never be written through a symlink")
os.unlink(TOKEN)
rreset(); check(A.recompute(cfg()) == 0 and A.seal(cfg(), "complete") == 0 and os.listdir(RUN) == [], "O: without run_dir nothing is written to /run")
ok("O the login token needs a valid marker AND a confirmed flush; the status line follows")

# P — once logins are enabled the window cannot open again in that boot
rreset(); A.recompute(rcfg()); A.seal(rcfg(), "complete"); calls()
os.unlink(os.path.join(STATE, "sealed")); os.unlink(os.path.join(STATE, "window.json"))
check(A.recompute(rcfg()) == 1 and calls() == [CLOSE] and not os.path.exists(os.path.join(STATE, "window.json")),
      "P: with the state removed in a sealed boot, recompute opened the window under a running agent")
os.unlink(TOKEN); boot(2)                                      # the reboot: /run is new, and so is the boot id
check(A.recompute(rcfg()) == 0 and calls() == [OPEN] and window()["boots"] == 1, "P: after the reboot the same state is a first boot")
rreset(); A.recompute(rcfg()); A.seal(rcfg(), "complete"); os.unlink(os.path.join(STATE, "sealed")); calls()
check(A.recompute(rcfg()) == 1 and calls() == [CLOSE], "P: only the marker removed (same boot, budget left): it must not re-derive to open")
rreset(); os.symlink("/nonexistent", TOKEN)
check(A.recompute(rcfg()) == 1 and calls() == [CLOSE], "P: anything at the token's path refuses an open")
os.unlink(TOKEN)
# ...and the wait (a respawned getty runs it) must not answer a re-provisioning with a fresh marker.
for leave_window in (False, True):
    rreset(); A.recompute(rcfg()); A.seal(rcfg(), "complete"); os.unlink(os.path.join(STATE, "sealed"))
    if not leave_window:
        os.unlink(os.path.join(STATE, "window.json"))
    looks = []
    A.model_present = lambda c: looks.append(1) or False
    before = state_files(); calls()
    check(A.wait(rcfg()) == 1 and calls() == [CLOSE] and state_files() == before and not os.path.exists(os.path.join(STATE, "sealed")),
          "P: the state removed in a sealed boot (window.json left: %s): the wait wrote a new marker and undid the re-provisioning" % leave_window)
    check(not looks, "P: there is nothing to wait for in that state; the wait asked the detector %d times" % len(looks))
check(src.count("open_set(c)") == 2 and src.count("c[\"open_file\"]") == 1,
      "P: exactly one place may open the set (recompute, after the refusal above)")
# ...nor may anyone else: a seal by hand, the wait's timeout (both refuse, rc 1), and the tick. The
# tick runs once a minute for the rest of that boot, so it is QUIET there: it keeps the set empty,
# keeps no books (there is no window to count), seals nothing, says nothing, and does not fail.
for who, call, want in (("seal", lambda c: A.seal(c, "budget"), 1), ("ended after a timeout", A.ended, 1),
                        ("tick, budget spent", lambda c: A.tick(c), 0), ("stop", lambda c: A.tick(c, final=True), 0)):
    rreset(); A.recompute(rcfg()); A.seal(rcfg(), "complete"); os.unlink(os.path.join(STATE, "sealed"))
    Clock.mono = 100.0 + LENGTH + 1; os.environ["SERVICE_RESULT"] = "timeout"
    books = open(os.path.join(STATE, "window.json")).read(); line = status_line(); calls()
    rc = call(rcfg()); os.environ.pop("SERVICE_RESULT")
    seen = calls()
    check(rc == want and not os.path.exists(os.path.join(STATE, "sealed")) and seen and all(x == CLOSE for x in seen),
          "P: %s in a sealed boot whose marker was removed: rc=%r (want %r) %r" % (who, rc, want, seen))
    check(open(os.path.join(STATE, "window.json")).read() == books and (want == 1 or status_line() == line),
          "P: %s kept books, or told the console the window is open, in a boot that was sealed" % who)
rreset(); A.recompute(rcfg()); A.seal(rcfg(), "complete"); os.unlink(os.path.join(STATE, "sealed")); tool(NFT, rc=1); calls()
check(A.tick(rcfg()) == 1, "P: the quiet tick must still report a flush that failed")
rreset(); A.recompute(rcfg()); A.seal(rcfg(), "complete"); os.unlink(os.path.join(STATE, "sealed")); put("window.json", "garbage"); calls()
check(A.recompute(rcfg()) == 1 and not os.path.exists(os.path.join(STATE, "sealed")), "P: a garbage window.json in a sealed boot must wait for the reboot too")
calls()
check(A.tick(rcfg()) == 0 and not os.path.exists(os.path.join(STATE, "sealed")) and calls() == [CLOSE],
      "P: the tick on a garbage window.json in a sealed boot must not seal either")
# A refused seal whose flush ALSO failed is reported as the failed flush (the thing to repair first).
rreset(); A.recompute(rcfg()); A.seal(rcfg(), "complete"); os.unlink(os.path.join(STATE, "sealed")); tool(NFT, rc=1); syslog_lines = []
real_log, A.log = A.log, syslog_lines.append
try:
    check(A.seal(rcfg(), "budget") == 1 and any("did not confirm the flush" in l for l in syslog_lines)
          and not any("re-provisioning" in l for l in syslog_lines), "P: the log must name the failed flush: %r" % syslog_lines)
finally:
    A.log = real_log
ok("P once the token exists the window cannot open again in that boot")

# Q — wait
asked = []


def detector(answers):
    """answers: a list of bools, the last one repeating; or a callable(n) -> bool."""
    def f(c):
        asked.append(1)
        return answers(len(asked)) if callable(answers) else answers[min(len(asked), len(answers)) - 1]
    A.model_present = f
    del asked[:]


rreset(); put("sealed", GOOD_SEALED); detector([True]); calls()
check(A.wait(rcfg()) == 0 and calls() == [CLOSE] and os.path.isfile(TOKEN) and not asked, "Q: sealed already: flush, token, 0, no question asked")
rreset(); put("sealed", GOOD_SEALED); tool(NFT, rc=1); calls()
check(A.wait(rcfg()) == 1 and not os.path.exists(TOKEN), "Q: sealed but the flush is not confirmed must be 1")
rreset(); detector([True]); calls()
check(A.wait(rcfg()) == 0 and marker()["reason"] == "not open" and calls() == SEAL_SEQ and os.path.isfile(TOKEN) and not asked
      and not os.path.exists(os.path.join(STATE, "window.json")),
      "Q: no window opened in this boot: seal(not open) at once, without asking the detector and WITHOUT opening")
rreset(); A.recompute(rcfg()); boot(2); detector([True]); calls()            # a new boot whose recompute never ran
check(A.wait(rcfg()) == 0 and marker()["reason"] == "not open" and not asked and calls()[0] == CLOSE, "Q: a window from the previous boot is not open in this one")
rreset(); A.recompute(rcfg()); detector([True]); calls()
check(A.wait(rcfg()) == 0 and calls() == SEAL_SEQ and marker()["reason"] == "complete" and os.path.isfile(TOKEN) and len(asked) == 1,
      "Q: open + model listed: seal(complete) in the fixed order")
rreset(); A.recompute(rcfg()); detector([False, False, True]); calls(); t0 = Clock.mono
check(A.wait(rcfg()) == 0 and len(asked) == 3 and Clock.mono == t0 + 2 * A.POLL and marker()["reason"] == "complete",
      "Q: it must look again every POLL seconds until the model is listed")
rreset(); A.recompute(rcfg()); detector([False]); calls()
check(A.wait(rcfg()) == 0 and marker()["reason"] == "budget" and os.path.isfile(TOKEN), "Q: no model, no tick: the wait must seal on the budget by itself")
check(abs(len(asked) - LENGTH // A.POLL) <= 2, "Q: it asked %d times, expected about %d" % (len(asked), LENGTH // A.POLL))
rreset(); A.recompute(rcfg())


def third_look_ticks(n):
    if n == 3:
        Clock.mono = 100.0 + LENGTH + 1
        check(A.tick(rcfg()) == 0, "Q: the tick's seal")
    return False


detector(third_look_ticks); calls()
check(A.wait(rcfg()) == 0 and marker()["reason"] == "budget" and len(asked) == 3, "Q: the tick sealing ends the wait at its next look")
rreset(); A.recompute(rcfg()); detector([True]); tool(NFT, rc=1); calls()
check(A.wait(rcfg()) == 1 and not os.path.exists(os.path.join(STATE, "sealed")) and not os.path.exists(TOKEN) and len(asked) == 1 and calls() == [CLOSE],
      "Q: a seal that fails ends the wait with 1, once (no loop)")
check(status_line() == {"state": "failed"}, "Q: a failed wait must tell the console: %r" % status_line())
rreset(); A.recompute(rcfg()); detector([True]); calls()
held = os.open(os.path.join(STATE, ".lock"), os.O_RDWR | os.O_CREAT, 0o600); fcntl.flock(held, fcntl.LOCK_EX)
try:
    check(A.wait(rcfg()) == 1 and calls() == [CLOSE] and not asked and not os.path.exists(TOKEN), "Q: without the lock the wait flushes and fails")
finally:
    os.close(held)
rreset(); detector([True]); calls()
check(A.wait(rcfg(sealed_by_build=True)) == 0 and calls() == [CLOSE] and state_files() == [] and not asked, "Q: sealed by build: flush, nothing else")
ok("Q the wait returns 0 only when the box is sealed, flushed and logins are enabled")

# R — ended
rreset(); A.recompute(rcfg()); os.environ["SERVICE_RESULT"] = "timeout"; calls()
check(A.ended(rcfg()) == 0 and calls() == SEAL_SEQ and marker()["reason"] == "timeout" and os.path.isfile(TOKEN), "R: after a timeout `ended` is the seal")
for result in ("success", "signal", "exit-code", "core-dump", "", None):     # a shutdown stops the wait: "success"
    rreset(); A.recompute(rcfg()); calls()
    if result is None:
        os.environ.pop("SERVICE_RESULT", None)
    else:
        os.environ["SERVICE_RESULT"] = result
    check(A.ended(rcfg()) == 0 and calls() == [] and state_files() == ["window.json"] and not os.path.exists(TOKEN)
          and status_line()["state"] == "open",
          "R: SERVICE_RESULT=%r: `ended` touched the window (a reboot inside the window would find the box sealed)" % (result,))
rreset(); A.recompute(rcfg()); os.environ["SERVICE_RESULT"] = "timeout"; tool(NFT, rc=1); calls()
check(A.ended(rcfg()) == 1 and not os.path.exists(os.path.join(STATE, "sealed")) and status_line() == {"state": "failed"} and not os.path.exists(TOKEN),
      "R: a timeout whose seal cannot flush must fail, say so, and leave logins closed")
os.environ.pop("SERVICE_RESULT")
os.environ["AGOS_WINDOW_CONFIG"] = json.dumps(rcfg())
try:
    rreset(); put("sealed", GOOD_SEALED); calls(); se, sys.stderr = sys.stderr, io.StringIO()
    try:
        check(A.main(["x", "wait"]) == 0 and A.main(["x", "ended"]) == 0, "R: the roles on the command line")
        os.environ["SERVICE_RESULT"] = "timeout"; rreset(); A.recompute(rcfg()); calls()
        check(A.main(["x", "ended"]) == 0 and marker()["reason"] == "timeout", "R: `ended` from the command line after a timeout")
        os.environ.pop("SERVICE_RESULT"); rreset(); put("sealed", GOOD_SEALED)
        calls()
        check(A.main(["x", "wait", "now"]) == 2 and A.main(["x", "ended", "x"]) == 2 and calls() == [], "R: extra arguments must be refused")
    finally:
        sys.stderr = se
finally:
    os.environ.pop("AGOS_WINDOW_CONFIG")
ok("R ended: the seal after a timeout, nothing after anything else")

# S — the detector
T = A.tags_list_model
doc = lambda *models: json.dumps({"models": list(models)}).encode()
check(T(doc({"name": "qwen3.5:9b", "model": "qwen3.5:9b"}), "qwen3.5:9b"), "S: the exact name")
check(T(doc({"model": "qwen3.5:9b"}), "qwen3.5:9b") and T(doc({"name": "a:b"}, {"name": "qwen3.5:9b"}), "qwen3.5:9b"), "S: either key, any position")
check(T(doc({"name": "llama3:latest"}), "llama3") and not T(doc({"name": "llama3:8b"}), "llama3"), "S: a bare name means :latest")
for other in ("qwen3.5:9b-q4", "xqwen3.5:9b", "qwen3.5:9", "qwen3.5", "QWEN3.5:9B", "evil/qwen3.5:9b", " qwen3.5:9b"):
    check(not T(doc({"name": other}), "qwen3.5:9b"), "S: %r must not count as the model" % other)
for bad in (b"", b"not json", b"[]", b'{"models": null}', b'{"models": {"name": "qwen3.5:9b"}}', b'{"models": ["qwen3.5:9b"]}',
            b'{"models": [{"name": ["qwen3.5:9b"]}]}', b'{"models": [{"name": 5}]}', b"\xff\xfe", doc()):
    check(not T(bad, "qwen3.5:9b"), "S: %r must be no" % bad[:30])
check(not T(doc({"name": "qwen3.5:9b"}) + b" " * (A.TAGS_MAX + 1), "qwen3.5:9b"), "S: an oversize body must be no")
check(not T(doc({"name": ""}), "") and not T(doc({"name": "x"}), None), "S: no configured model is never present")

import http.server, socket, threading
# The real detector again: the Q arms replaced it on A.
spec2 = importlib.util.spec_from_file_location("agos_window_real", MOD)
R = importlib.util.module_from_spec(spec2); spec2.loader.exec_module(R)
R.PROBE_LIMIT = 1.0


class H(http.server.BaseHTTPRequestHandler):
    mode = "ok"

    def do_GET(self):
        H.seen.append(self.path)
        if H.mode == "hang":
            H.release.wait(10); return
        if H.mode == "404":
            self.send_response(404); self.end_headers(); return
        if H.mode == "redirect":
            self.send_response(302); self.send_header("Location", "http://127.0.0.1:%d/elsewhere" % PORT); self.end_headers(); return
        body = doc({"name": "qwen3.5:9b"}) if self.path == "/api/tags" else doc({"name": "other:1"})
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def log_message(self, *a):
        pass


H.seen, H.release = [], threading.Event()
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H); PORT = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
try:
    pc = rcfg(tags_port=PORT)
    check(R.model_present(pc) is True and H.seen == ["/api/tags"], "S: a real server listing the model: %r" % H.seen)
    check(R.model_present(rcfg(tags_port=PORT, model="absent:1")) is False, "S: a model that is not listed")
    H.mode = "404"; check(R.model_present(pc) is False, "S: a 404 is no")
    H.mode = "redirect"; del H.seen[:]
    check(R.model_present(pc) is False and H.seen == ["/api/tags"], "S: a redirect must not be followed: %r" % H.seen)
    H.mode = "hang"; import time as real_time; t = real_time.monotonic()
    check(R.model_present(pc) is False and real_time.monotonic() - t < 4, "S: a server that never answers must be no, within the limit")
    H.release.set(); H.mode = "ok"
    check(R.model_present(pc) is True, "S: and the next look works again (the alarm was cleared)")
    real_ask = R._ask                               # the deadline landing in a cleanup: still "no", never an exception


    def ask_then_alarm_in_cleanup(c):
        try:
            return real_ask(c)
        finally:
            raise R._Deadline()
    R._ask = ask_then_alarm_in_cleanup
    check(R.model_present(pc) is False, "S: an alarm that lands in a cleanup must be a no")
    R._ask = real_ask
    import signal as real_signal
    check(real_signal.getitimer(real_signal.ITIMER_REAL)[0] == 0 and real_signal.getsignal(real_signal.SIGALRM) == real_signal.SIG_DFL,
          "S: the alarm and its handler must be put back")
finally:
    srv.shutdown(); srv.server_close()
dead = socket.socket(); dead.bind(("127.0.0.1", 0)); closed_port = dead.getsockname()[1]; dead.close()
check(R.model_present(rcfg(tags_port=closed_port)) is False, "S: a closed port is no")
check(src.count('HTTPConnection("127.0.0.1"') == 1 and "HTTPSConnection" not in src and "urllib" not in src,
      "S: the detector must speak to the loopback literal only, with no client that follows redirects or reads proxies")
ok("S the detector: the exact model in the fetcher's own list, on loopback, bounded")

# T — the console's progress program
PMOD = os.path.abspath(os.path.join(HERE, "..", "modules", "agos_setup_progress.py"))
sp = importlib.util.spec_from_file_location("agos_setup_progress", PMOD)
P = importlib.util.module_from_spec(sp); sp.loader.exec_module(P)
psrc = open(PMOD).read()
dirty = "pulling \x1b[31m\x1b]0;owned\x07abc\r\n\u2595\u2588\u258f 45%\t3.0 GB/6.6 GB\x00\x9b2J" + "x" * 500
c = P.clean(dirty)
check(all(" " <= ch <= "~" for ch in c) and len(c) <= 70 and "45%" in c and "\x1b" not in c, "T: clean: %r" % c)
check(P.clean("a   b\n\nc") == "a b c" and P.clean("") == "", "T: clean collapses blanks")
PS, PP, PU = os.path.join(TMP, "p-status"), os.path.join(TMP, "p-pull"), os.path.join(TMP, "p-unsealed")
os.environ["AGOS_PROGRESS_STATUS"], os.environ["AGOS_PROGRESS_PULL"], os.environ["AGOS_PROGRESS_UNSEALED"] = PS, PP, PU


def pstatus(obj):
    open(PS, "w").write(obj if isinstance(obj, str) else json.dumps(obj))


for bad in ("", "x", "[]", '{"state": "weird"}', '{"state": ["open"]}', '{"remaining": 5}'):
    pstatus(bad); check(P.read_status(PS) == ("", None, ""), "T: status %r must read as nothing" % bad)
for rem in (True, -1, 99999, "60", 1.5):
    pstatus({"state": "open", "remaining": rem}); check(P.read_status(PS) == ("open", None, ""), "T: remaining=%r must be dropped" % (rem,))
pstatus({"state": "sealed", "reason": "com\x1b[2Jplete"}); check("\x1b" not in P.read_status(PS)[2], "T: the reason is cleaned too")


class Out:
    def __init__(self):
        self.text = ""

    def write(self, t):
        self.text += t

    def flush(self):
        pass


def run_progress(script, limit="100"):
    """script: a list of (status, pull) per second; the last repeats."""
    out, tick_no, naps = Out(), [0], []

    def sleep(sec):
        naps.append(sec); tick_no[0] += 1
        st, pull = script[min(tick_no[0], len(script) - 1)]
        pstatus(st); open(PP, "w").write(pull)
    st, pull = script[0]; pstatus(st); open(PP, "w").write(pull)
    rc = P.main(["x", limit], out=out, sleep=sleep, now=lambda: float(tick_no[0]))
    return rc, out.text, naps


rc, text, naps = run_progress([({"state": "open", "remaining": 3541}, "pulling 10%"), ({"state": "open", "remaining": 3541}, "pulling 10%"),
                               ({"state": "open", "remaining": 3481}, "pulling \x1b[2J40%"), ({"state": "sealed", "reason": "complete"}, "done")])
check(rc == 0 and text.count("pulling 10%") == 1 and "pulling [2J40%" in text and "about 60 min" in text and "about 59 min" in text
      and text.rstrip().endswith("Starting Agent OS.") and "\x1b[2J" not in text, "T: the open run: %r" % text)
check(set(text) <= set(chr(i) for i in range(32, 127)) | {"\r", "\n", "\x1b"} and text.count("\x1b") == text.count("\x1b[K"),
      "T: the only escape it ever writes is its own erase-line")
rc, text, naps = run_progress([({"state": "sealed", "reason": "budget"}, "")])
check(rc == 0 and "ran out" in text and "closed to the network" in text and "/var/lib/agent-os/seal/sealed" in text and naps == [8],
      "T: sealed without a model: say so, say how to set up again, leave it on screen: %r %r" % (text, naps))
rc, text, naps = run_progress([({"state": "failed"}, "")])
check(rc == 0 and "could not finish" in text and "Ctrl+Alt+F3" in text, "T: failed: %r" % text)
rc, text, naps = run_progress([("", "")], limit="5")
check(rc == 0 and "could not finish" in text and len(naps) == 5 and "getting its model" in text, "T: no status at all ends at the limit: %r" % naps)
se, sys.stderr = sys.stderr, io.StringIO()
try:
    check(P.main(["x"]) == 2 and P.main(["x", "soon"]) == 2, "T: the limit is required and a number")
    open(PU, "w").write("EGRESS SEAL DOWN\x1b[2J")          # seal-check's banner exists: the wall is down, whatever the status says
    for st in ({"state": "open", "remaining": 3000}, {"state": "sealed", "reason": "complete"}):
        rc, text, naps = run_progress([(st, "pulling 10%")])
        check(rc == 0 and "could not finish" in text and "Starting Agent OS" not in text and "\x1b[2J" not in text and naps == [0],
              "T: with seal-check's banner present the program must say setup could not finish and stop: %r" % text)
    os.unlink(PU)
finally:
    sys.stderr = se
check("stdin" not in psrc and "input(" not in psrc and "subprocess" not in psrc and "socket" not in psrc, "T: it must read no input and run nothing")
check(psrc.count('"@AGOS_PROGRESS_INSTALLED@"') == 1, "T: the substitution target must appear exactly once")
inst = psrc.replace('"@AGOS_PROGRESS_INSTALLED@"', '"yes"')
PI = types.ModuleType("agos_setup_progress_installed"); exec(compile(inst, "agos_setup_progress_installed.py", "exec"), PI.__dict__)
check(PI._paths() == ("/run/agentos-setup/status", "/run/agent-os/pull-status", "/run/agentos-unsealed") and P._paths() == (PS, PP, PU),
      "T: the installed copy read the environment")
os.environ.pop("AGOS_PROGRESS_STATUS"); os.environ.pop("AGOS_PROGRESS_PULL"); os.environ.pop("AGOS_PROGRESS_UNSEALED")
ok("T the progress program prints clean text, ends when the box is sealed, and reads no input")

# ── slice 3c: the record ────────────────────────────────────────────────────────────────────────
import ast, subprocess
REPO = os.path.abspath(os.path.join(HERE, ".."))
AUDIT_DIR, IDROOT = os.path.join(TMP, "audit"), os.path.join(TMP, "identity")
AUDIT_LOG = os.path.join(AUDIT_DIR, "audit.log")
AUDIT_WRAP = os.path.join(BIN, "audit")
AUDIT_ENV = {"AGENT_OS_AUDIT_DIR": AUDIT_DIR, "AGENT_OS_MODULES": os.path.join(REPO, "modules"), "AGENT_OS_IDENTITY_ROOT": IDROOT}
# The production wrapper's shape (modules/audit-pkg.nix): pin the ledger, the modules and the identity
# root, then the real primitive. Whatever else is in the environment comes from the caller, and for
# the window program that is the signer and the pin, or nothing.
with open(AUDIT_WRAP, "w") as f:
    f.write("#!/bin/sh\n%s\n[ ! -e %s/hang ] || exec %s -c 'import time; time.sleep(30)'\nexec %s -I %s \"$@\"\n" % (
        "\n".join("export %s=%s" % kv for kv in sorted(AUDIT_ENV.items())), TMP, sys.executable, sys.executable,
        os.path.join(REPO, "bin", "audit")))
os.chmod(AUDIT_WRAP, 0o755)
r = subprocess.run([sys.executable, "-I", "-c",
                    "import sys; sys.path.insert(0, %r); import identity; identity.ensure_boot_identities()" % AUDIT_ENV["AGENT_OS_MODULES"]],
                   env=AUDIT_ENV, stdin=subprocess.DEVNULL, capture_output=True, text=True)
check(r.returncode == 0, "U: fixture: could not mint the identities: " + r.stderr[-300:])


def acfg(**over):
    return rcfg(**dict({"audit": AUDIT_WRAP, "audit_log": AUDIT_LOG}, **over))


def areset():
    rreset()
    shutil.rmtree(AUDIT_DIR, True)
    if os.path.exists(os.path.join(TMP, "hang")):
        os.unlink(os.path.join(TMP, "hang"))


def chain():
    if not os.path.exists(AUDIT_LOG):
        return []
    return [json.loads(l) for l in open(AUDIT_LOG) if l.endswith("\n")]


def mine():
    return [r for r in chain() if r.get("src") == "setup-window"]


def verify(pin=None):
    env = dict(AUDIT_ENV)
    if pin:
        env["AGENT_OS_AUDIT_REQUIRE_SIGNED"] = pin
    return subprocess.run([AUDIT_WRAP, "verify"], env=env, stdin=subprocess.DEVNULL, capture_output=True).returncode


RULESET_SHA = hashlib.sha256(open(RULESET, "rb").read()).hexdigest()
# the order: books, record, set
areset(); order = []
real_write, real_tool, real_record = A._write, A._tool, A._append_record
A._write = lambda p, o: (order.append("write " + os.path.basename(p)), real_write(p, o))[1]
A._tool = lambda argv, timeout=A.NFT_LIMIT: (order.append(" ".join(os.path.basename(a) for a in argv[:3])), real_tool(argv, timeout))[1]
A._append_record = lambda c, event, **f: (order.append("record " + event), real_record(c, event, **f))[1]
try:
    check(A.recompute(acfg()) == 0, "U: recompute with a chain")
finally:
    A._write, A._tool, A._append_record = real_write, real_tool, real_record
check(order == ["write window.json", "record window-opened", "nft -f open.nft"], "U: the order must be books, record, set: %r" % order)
recs = mine()
check(len(recs) == 1 and recs[0]["seq"] == 0 and {k: recs[0][k] for k in recs[0] if k not in ("seq", "prev", "ts")} == {
    "src": "setup-window", "schema": 1, "event": "window-opened", "boot_id": "boot-1", "fetch_uid": 61,
    "toplevel": os.path.realpath(TOP), "ruleset_sha256": RULESET_SHA, "elements": 0,
    "remaining": LENGTH - A.FLOOR, "budget_at_boot": LENGTH, "boots": 1}, "U: the first record: %r" % recs)
check(verify() == 0, "U: the chain must verify")
# re-derived: the set is put back in the same boot. `elements` is MEASURED: the fake kernel still
# holds the uid here (a real reload would have emptied it), and the record says 1, not a constant 0.
check(A.recompute(acfg()) == 0 and [r["event"] for r in mine()] == ["window-opened", "window-rederived"]
      and mine()[1]["elements"] == 1 and mine()[1]["boots"] == 1, "U: re-derived: %r" % mine())
tool(NFT, measure_says=[61, 62, 63]); calls()
check(A.recompute(acfg()) == 0 and mine()[-1]["elements"] == 3, "U: `elements` must be what nft reported: %r" % mine()[-1])
tool(NFT); boot(2); Clock.mono = 5.0
check(A.recompute(acfg()) == 0 and mine()[-1]["event"] == "window-reopened" and mine()[-1]["boots"] == 2
      and mine()[-1]["boot_id"] == "boot-2" and mine()[-1]["budget_at_boot"] < LENGTH, "U: reopened: %r" % mine()[-1])

# the seal: whoever seals, the TICK records it, from the marker, once.
areset(); A.recompute(acfg()); n0 = len(chain())
detector([True]); calls()
check(A.wait(acfg()) == 0 and len(chain()) == n0, "U: the wait appended to the chain (it must never)")
mk = marker()
check(mk["boot_id"] == "boot-1" and set(mk) == {"schema", "toplevel", "ruleset_sha256", "sealed_at", "reason", "boot_id"}, "U: the marker: %r" % mk)
check(A.seal(acfg(), "budget") == 0 and len(chain()) == n0, "U: `seal` appended to the chain (only the tick may)")
Clock.wall += 500                                              # the tick comes later: the record must carry the SEAL's time
check(A.tick(acfg()) == 0, "U: the tick on the sealed box")
sealed_recs = [r for r in mine() if r["event"] == "window-sealed"]
# What is signed: the tick's OWN measurements on top (the boot it runs in, the generation it runs from,
# what the kernel's set holds), and what the marker says under `marker`, as the marker's.
check(len(sealed_recs) == 1 and {k: sealed_recs[0][k] for k in sealed_recs[0] if k not in ("seq", "prev", "ts")} == {
    "src": "setup-window", "schema": 1, "event": "window-sealed", "boot_id": "boot-1", "fetch_uid": 61,
    "toplevel": os.path.realpath(TOP), "ruleset_sha256": RULESET_SHA, "elements": 0,
    "marker": {"reason": "complete", "sealed_at": mk["sealed_at"], "boot_id": mk["boot_id"],
               "toplevel": mk["toplevel"], "ruleset_sha256": mk["ruleset_sha256"]}},
      "U: the seal's record: the tick's measurements, and the marker quoted: %r" % sealed_recs)
CACHE = os.path.join(STATE, "recorded.json")


def seals():
    return [r for r in mine() if r["event"] == "window-sealed"]


def pointer():
    return json.load(open(CACHE))


check(not os.path.exists(CACHE), "U: the pointer is written by the tick that FINDS the record, not by the one that appends it")
check(A.tick(acfg()) == 0 and len(seals()) == 1, "U: the next tick must find it and stop")
raw = open(AUDIT_LOG, "rb").read()
line = [l for l in raw.splitlines(True) if b"window-sealed" in l][0]
check(pointer() == {"boot_id": mk["boot_id"], "sealed_at": mk["sealed_at"], "offset": raw.index(line), "sha256": hashlib.sha256(line).hexdigest()},
      "U: the pointer must name the line: %r" % pointer())
good_pointer = pointer()
for what, plant in (("again", None), ("pointer deleted", "delete"), ("pointer garbage", "x"),
                    ("pointer of another seal", dict(good_pointer, boot_id="boot-9", sealed_at=5.0)),
                    ("pointer with the wrong offset", dict(good_pointer, offset=0)),
                    ("pointer with the wrong hash", dict(good_pointer, sha256="0" * 64)),
                    ("pointer past the end", dict(good_pointer, offset=10 ** 9)),
                    ("pointer with a negative offset", dict(good_pointer, offset=-1)),
                    ("pointer with only the key", {"boot_id": mk["boot_id"], "sealed_at": mk["sealed_at"]})):
    if plant == "delete":
        os.unlink(CACHE)
    elif plant is not None:
        put("recorded.json", plant)
    check(A.tick(acfg()) == 0 and len(seals()) == 1, "U: %s: the seal must be in the chain exactly once: %r" % (what, len(seals())))
    check(pointer() == good_pointer, "U: %s: the pointer must be put right" % what)
# THE POINTER DECIDES NOTHING. It says "recorded", and the chain does not have the record (its tail was
# lost after the fact): the record is written again. A file that merely said so would hide it for good.
keep = raw[:raw.index(line)]
open(AUDIT_LOG, "wb").write(keep)
check(pointer() == good_pointer and len(seals()) == 0, "U: fixture: the chain lost the record, the pointer still claims it")
check(A.tick(acfg()) == 0 and len(seals()) == 1, "U: a pointer to a record the chain no longer has must not stop the record being written")
check(A.tick(acfg()) == 0 and len(seals()) == 1 and verify() == 0, "U: ...once")
# a forged pointer for a seal that was never recorded at all
areset(); A.recompute(acfg()); A.seal(acfg(), "complete"); mk2 = marker()
put("recorded.json", {"boot_id": mk2["boot_id"], "sealed_at": mk2["sealed_at"], "offset": 0, "sha256": hashlib.sha256(open(AUDIT_LOG, "rb").readline()).hexdigest()})
check(A.tick(acfg()) == 0 and len(seals()) == 1, "U: a pointer at some OTHER line of the chain must not pass for the seal's record")
# what the marker says is quoted, never adopted: a marker naming another generation (a rebuild since
# the seal, or a forgery) leaves the tick's own measurements on top.
areset(); A.recompute(acfg()); A.seal(acfg(), "complete"); mk3 = marker()
put("sealed", dict(mk3, toplevel=TMP + "/another-generation", ruleset_sha256="f" * 64)); boot(2)
check(A.tick(acfg()) == 0, "U: tick with a marker from another generation")
r3 = seals()[-1]
check(r3["toplevel"] == os.path.realpath(TOP) and r3["ruleset_sha256"] == RULESET_SHA and r3["boot_id"] == "boot-2"
      and r3["marker"]["toplevel"] == TMP + "/another-generation" and r3["marker"]["ruleset_sha256"] == "f" * 64 and r3["marker"]["boot_id"] == "boot-1",
      "U: the record's own fields must be the tick's measurements, the marker's under `marker`: %r" % r3)
# a marker is validated field by field before any of it is quoted or used as a key.
for label, bad in (("NaN time", dict(mk3, sealed_at=float("nan"))), ("infinite time", dict(mk3, sealed_at=float("inf"))),
                   ("negative time", dict(mk3, sealed_at=-1.0)), ("time as text", dict(mk3, sealed_at="1.0")), ("time as a boolean", dict(mk3, sealed_at=True)),
                   ("boot id with a newline", dict(mk3, boot_id="boot\n1")), ("boot id with a space", dict(mk3, boot_id="boot 1")),
                   ("boot id too long", dict(mk3, boot_id="a" * 65)), ("boot id not text", dict(mk3, boot_id=5)),
                   ("toplevel with a space", dict(mk3, toplevel=TMP + "/a b")), ("toplevel with a control byte", dict(mk3, toplevel=TMP + "/a\x1bb")),
                   ("toplevel too long", dict(mk3, toplevel=TMP + "/" + "a" * 600)), ("toplevel not ASCII", dict(mk3, toplevel=TMP + "/\u00e9"))):
    rreset(); put("sealed", json.dumps(bad))
    check(A.load_sealed(cfg())[0] == "malformed", "U: a marker with %s must be malformed" % label)
areset(); A.recompute(acfg()); A.seal(acfg(), "complete"); put("sealed", json.dumps(dict(marker(), sealed_at=float("nan")))); n_nan = len(chain())
check(A.tick(acfg()) == 1 and A.tick(acfg()) == 1 and len(chain()) == n_nan,
      "U: a marker whose time equals nothing must never reach the chain (it would be recorded again every minute)")
areset(); A.recompute(acfg()); A.seal(acfg(), "complete"); A.tick(acfg()); A.tick(acfg()); mk = marker()
# a second provisioning cycle on the same chain: the earlier seal's record is not this marker's.
os.unlink(os.path.join(STATE, "sealed")); os.unlink(os.path.join(STATE, "window.json")); os.unlink(TOKEN)
boot(3); Clock.mono = 9.0; Clock.wall += 1000
check(A.recompute(acfg()) == 0 and A.seal(acfg(), "budget") == 0 and A.tick(acfg()) == 0, "U: second cycle")
both = seals()
check(len(both) == 2 and both[1]["boot_id"] == "boot-3" and both[1]["marker"]["sealed_at"] == marker()["sealed_at"] != both[0]["marker"]["sealed_at"]
      and both[1]["marker"]["reason"] == "budget", "U: a new marker's seal must be recorded although an older seal is in the chain: %r" % both)
A.tick(acfg()); os.unlink(CACHE)
check(A.tick(acfg()) == 0 and len(seals()) == 2, "U: ...and still once each")
# the process dies between the append and the cache write: still exactly one.
areset(); A.recompute(acfg()); A.seal(acfg(), "complete")
real_write = A._write


def no_cache(p, o):
    if os.path.basename(p) == "recorded.json":
        raise OSError("the pointer cannot be written")
    return real_write(p, o)


A._write = no_cache
try:
    check(A.tick(acfg()) == 0 and A.tick(acfg()) == 0 and A.tick(acfg()) == 0 and not os.path.exists(CACHE) and len(seals()) == 1,
          "U: with no pointer at all every tick scans, finds the record, and writes nothing")
finally:
    A._write = real_write
check(A.tick(acfg()) == 0 and A.tick(acfg()) == 0 and len(seals()) == 1 and os.path.exists(CACHE),
      "U: with the pointer's write failing at first there must still be exactly one record")
# a log that cannot be read through in time: the record stays owed (nothing appended, nothing invented).
areset(); A.recompute(acfg()); A.seal(acfg(), "complete"); n_scan = len(chain()); A.SCAN_LIMIT, scan0 = -1, A.SCAN_LIMIT
try:
    check(A.tick(acfg()) == 0 and len(chain()) == n_scan and not os.path.exists(CACHE), "U: a scan past its limit must leave the record owed")
finally:
    A.SCAN_LIMIT = scan0
check(A.tick(acfg()) == 0 and len(chain()) == n_scan + 1, "U: ...and it is written by the next tick that can read the log")
bad_reason = dict(GOOD_SEALED, reason="because"); rreset(); put("sealed", bad_reason)
check(A.load_sealed(cfg())[0] == "malformed", "U: a marker whose reason is not one of the seven is malformed (it would go into the chain verbatim)")
# `stop` (the shutdown stamp) and a sealed-by-build box record nothing.
areset(); A.recompute(acfg()); A.seal(acfg(), "complete"); n1 = len(chain())
check(A.tick(acfg(), final=True) == 0 and len(chain()) == n1, "U: the shutdown stamp must not append")
check(verify() == 0, "U: the chain must verify")

# a chain that refuses the record: a torn tail (audit append exits 4 until an operator repairs it).
areset(); os.makedirs(AUDIT_DIR); open(AUDIT_LOG, "w").write('{"torn":'); calls()
check(A.recompute(acfg()) == 0 and calls() == SEAL_SEQ and marker()["reason"] == "refused" and json.load(open(SETSTATE)) == []
      and window()["remaining"] == LENGTH - A.FLOOR,
      "U: no record, no window: a refused record must seal at once (refused) and never open")
check(A.tick(acfg()) == 0 and mine() == [], "U: the tick must not fail the unit for an owed record, and must not invent one")
open(AUDIT_LOG, "w").close()                                   # the operator's repair
check(A.tick(acfg()) == 0 and [(r["event"], r["marker"]["reason"], r["marker"]["sealed_at"]) for r in mine()] == [("window-sealed", "refused", marker()["sealed_at"])],
      "U: an owed record must land once the chain accepts it, with the time of the seal: %r" % mine())
# recorded, then nft refuses the open: the chain reads opened, then sealed(refused).
areset(); tool(NFT, open_rc=1); calls()
check(A.recompute(acfg()) == 0 and marker()["reason"] == "refused" and A.tick(acfg()) == 0
      and [(r["event"], r.get("marker", {}).get("reason")) for r in mine()] == [("window-opened", None), ("window-sealed", "refused")],
      "U: opened then refused: %r" % mine())
# a record that hangs is a refused record, inside its limit.
areset(); open(os.path.join(TMP, "hang"), "w").close(); A.AUDIT_LIMIT, limit0 = 1, A.AUDIT_LIMIT
import time as wall_clock
t = wall_clock.monotonic()
try:
    check(A.recompute(acfg()) == 0 and marker()["reason"] == "refused" and wall_clock.monotonic() - t < 8, "U: a hung append must be a refused one, within the limit")
finally:
    A.AUDIT_LIMIT = limit0
    os.unlink(os.path.join(TMP, "hang"))
check(limit0 == 10, "U: AUDIT_LIMIT")
# the bound is computed from the units the program is given: one more to stop would not fit.
check(A.worst_lock_seconds(cfg()) == 165 and A.LOCK_BUDGET == REAL_LOCK_WAIT == 175, "U: the worst case under the lock: %r" % A.worst_lock_seconds(cfg()))
for label, over, fits in (("as shipped", {}, True), ("a third unit to stop", {"stop_units": ["a", "b", "c"]}, False),
                          ("a second unit to restart", {"restart_units": ["a", "b"]}, False), ("nothing to restart or stop", {"restart_units": [], "stop_units": []}, True)):
    os.environ["AGOS_WINDOW_CONFIG"] = json.dumps(cfg(**over))
    try:
        A.config(); accepted = True
    except SystemExit:
        accepted = False
    os.environ.pop("AGOS_WINDOW_CONFIG")
    check(accepted == fits, "U: %s: config() %s it" % (label, "accepted" if accepted else "refused"))
# a FIFO where the log should be must not hold the tick (and the state lock) for ever.
areset(); A.recompute(acfg()); A.seal(acfg(), "complete"); os.makedirs(AUDIT_DIR, exist_ok=True)
os.rename(AUDIT_LOG, AUDIT_LOG + ".real"); os.mkfifo(AUDIT_LOG)
check(A._find_seal(acfg(), marker()) is None and A._pointer_holds(acfg(), marker()) is False, "U: a FIFO at the log's path must read as 'cannot say', at once")
os.unlink(AUDIT_LOG); os.rename(AUDIT_LOG + ".real", AUDIT_LOG)

# signed as the wall's participant, or not at all.
areset(); signed = acfg(audit_signer="agent", audit_require_signed="agent")
check(A.recompute(signed) == 0 and A.seal(signed, "complete") == 0 and A.tick(signed) == 0, "U: signed run")
check(len(mine()) == 2 and all(r.get("signer") == "agent" and len(r.get("sig", "")) == 128 for r in mine()), "U: every record must be signed as the wall's participant: %r" % mine())
check(verify(pin="agent") == 0 and verify(pin="dillon") != 0, "U: the signed chain must verify under the pin, and only under the right one")
# CONTROL: a window program that was NOT handed the signer appends unsigned, and the chain breaks.
areset(); A.recompute(signed); boot(2); Clock.mono = 5.0
check(A.recompute(acfg()) == 0 and "sig" not in mine()[-1] and verify(pin="agent") != 0 and verify() != 0,
      "U: CONTROL: an unsigned record after a signed one must break verify (so the signer really came from the configuration)")
areset(); A.recompute(acfg(audit_signer="", audit_require_signed=None))
check("sig" not in mine()[0] and verify() == 0, "U: no signer configured: unsigned, and the chain verifies")

# what the child is handed: the signer AND the pin when the wall has them, and nothing at all otherwise.
ENVDUMP = os.path.join(TMP, "audit-env.json")
ENVWRAP = os.path.join(BIN, "audit-env")
with open(ENVWRAP, "w") as f:
    f.write("#!%s\nimport json, os, sys\njson.dump({k: v for k, v in os.environ.items() if k != 'LC_CTYPE'}, open(%r, 'w'))\nsys.stdin.read()\nprint('0' * 64)\n" % (sys.executable, ENVDUMP))
os.chmod(ENVWRAP, 0o755)                     # (LC_CTYPE: the child interpreter sets it for itself at startup)
areset()
check(A.recompute(acfg(audit=ENVWRAP, audit_signer="agent", audit_require_signed="agent")) == 0
      and json.load(open(ENVDUMP)) == {"AGENT_OS_AUDIT_SIGNER": "agent", "AGENT_OS_AUDIT_REQUIRE_SIGNED": "agent"},
      "U: the child's environment with a signing wall: %r" % json.load(open(ENVDUMP)))
areset()
check(A.recompute(acfg(audit=ENVWRAP)) == 0 and json.load(open(ENVDUMP)) == {}, "U: the child's environment with no signer must be EMPTY: %r" % json.load(open(ENVDUMP)))

# only recompute and the tick append; every reason a seal can carry is one of the seven.
tree = ast.parse(src := open(MOD).read())
callers = {}
for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
    for call in [n for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]:
        callers.setdefault(call.func.id, set()).add(fn.name)
check(callers.get("_append_record") == {"_recompute", "reconcile", "note_anomaly"} and callers.get("reconcile") == {"tick"}
      and callers.get("note_anomaly") == {"_recompute", "tick"},
      "U: only recompute (the opens, a refusal) and the tick (the seal, an anomaly) may append: %r %r %r"
      % (callers.get("_append_record"), callers.get("reconcile"), callers.get("note_anomaly")))
reasons = set()
for call in [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ("_seal", "seal")]:
    reasons |= {c.value for a in call.args[1:] for c in ast.walk(a) if isinstance(c, ast.Constant) and isinstance(c.value, str)}
for tup in [n for n in ast.walk(tree) if isinstance(n, ast.Tuple) and len(n.elts) == 2 and isinstance(n.elts[0], ast.Constant) and n.elts[0].value == "seal"]:
    reasons |= {tup.elts[1].value} if isinstance(tup.elts[1], ast.Constant) else set()
check(reasons == set(A.SEAL_REASONS) and len(A.SEAL_REASONS) == 7, "U: the reasons a seal can carry: %r vs %r" % (sorted(reasons), A.SEAL_REASONS))
check(all(r in P.REASONS or r == "complete" for r in A.SEAL_REASONS), "U: the console must have words for every reason")
bad = dict(GOOD_SEALED); del bad["boot_id"]; rreset(); put("sealed", bad)
check(A.load_sealed(cfg())[0] == "malformed", "U: a marker without its boot id is malformed")
ok("U the record: books, record, set; refused seals at once; the tick records the seal once; signed as the wall")

# ── V: hostile files, and the net under them ─────────────────────────────────────────────────────
HUGE = "1" + "0" * 400                                   # valid JSON, a Python int, and no float can hold it
DEEP = "[" * 2000                                        # under the size cap; plain bad JSON on today's Python (see VERY_DEEP)
good_window = {"schema": 1, "budget_at_boot": LENGTH, "remaining": LENGTH - 60, "boot_id": "boot-1", "opened_mono": 100.0,
               "opened_at": None, "wall_deadline": None, "boots": 1}


def raw(obj, **text_fields):
    """JSON text of obj, with some fields replaced by raw JSON text (so a huge int stays an int)."""
    t = json.dumps(dict(obj, **{k: "@@%s@@" % k for k in text_fields}))
    for k, v in text_fields.items():
        t = t.replace('"@@%s@@"' % k, v)
    return t


hostile_windows = [("opened_mono too large for a float", raw(good_window, opened_mono=HUGE)),
                   ("budget_at_boot too large", raw(good_window, budget_at_boot=HUGE)),
                   ("remaining too large", raw(good_window, remaining=HUGE)),
                   ("wall_deadline too large", raw(good_window, wall_deadline=HUGE)),
                   ("opened_at too large", raw(good_window, opened_at=HUGE)),
                   ("boots too large", raw(good_window, boots=HUGE)),
                   ("remaining NaN", raw(good_window, remaining="NaN")),
                   ("opened_mono -Infinity", raw(good_window, opened_mono="-Infinity")),
                   ("nested past the parser", DEEP), ("a list", "[1]"), ("a string", '"x"')]
hostile_markers = [("sealed_at too large for a float", raw(GOOD_SEALED, sealed_at=HUGE)),
                   ("sealed_at NaN", raw(GOOD_SEALED, sealed_at="NaN")), ("sealed_at Infinity", raw(GOOD_SEALED, sealed_at="Infinity")),
                   ("reason a list", raw(GOOD_SEALED, reason="[1]")), ("reason a dict", raw(GOOD_SEALED, reason="{}")),
                   ("boot_id a number", raw(GOOD_SEALED, boot_id=HUGE)), ("toplevel a list", raw(GOOD_SEALED, toplevel="[]")),
                   ("nested past the parser", DEEP), ("a list", "[1]")]
roles = [("recompute", A.recompute), ("tick", A.tick), ("stop", lambda c: A.tick(c, final=True)), ("wait", A.wait),
         ("seal", lambda c: A.seal(c, "budget")), ("ended", A.ended), ("status", A.status)]
os.environ["SERVICE_RESULT"] = "timeout"
sink = io.StringIO()
for kind, cases in (("window.json", hostile_windows), ("sealed", hostile_markers)):
    for label, text in cases:
        for rname, role in roles:
            rreset(); A.recompute(rcfg())                      # an OPEN window: the uid is in the kernel's set
            check(json.load(open(SETSTATE)) == [61], "V: fixture: the window is not open")
            if kind == "sealed":
                put("sealed", text)
            else:
                put("window.json", text)
            detector([False]); calls(); so, sys.stdout = sys.stdout, sink
            try:
                try:
                    rc = role(rcfg()); raised = None
                except Exception as e:                         # the point of the arm: this must not happen
                    rc, raised = None, e
            finally:
                sys.stdout = so
            check(raised is None, "V: %s raised %r on a %s with %s" % (rname, raised, kind, label))
            check(isinstance(rc, int), "V: %s returned %r" % (rname, rc))
            if rname not in ("status", "stop"):          # (those two only read, or only stamp)
                check(json.load(open(SETSTATE)) == [], "V: %s left the window OPEN on a %s with %s (rc=%r)" % (rname, kind, label, rc))
            if kind == "sealed":
                check(not os.path.exists(TOKEN), "V: %s enabled logins on a %s with %s" % (rname, kind, label))
            check(A.load_window(rcfg())[0] in ("valid", "absent", "malformed") and A.load_sealed(rcfg())[0] in ("valid", "absent", "malformed"),
                  "V: a loader did not return a verdict")
os.environ.pop("SERVICE_RESULT")
# The loaders WITHOUT their safety wrapper: the verdict must come from the checks, not from a caught crash.
for label, text in hostile_windows:
    rreset(); put("window.json", text)
    try:
        got = A.load_window.__wrapped__(cfg())[0]; raised = None
    except Exception as e:
        got, raised = None, e
    check(raised is None and got == "malformed", "V: window.json with %s: the loader's own checks must say malformed (got %r, raised %r)" % (label, got, raised))
for label, text in hostile_markers:
    rreset(); put("sealed", text)
    try:
        got = A.load_sealed.__wrapped__(cfg())[0]; raised = None
    except Exception as e:
        got, raised = None, e
    check(raised is None and got == "malformed", "V: a marker with %s: the loader's own checks must say malformed (got %r, raised %r)" % (label, got, raised))
# books that open the window in the future (the floor would freeze the budget): sealed, invalid state.
rreset(); A.recompute(rcfg()); w = window(); put("window.json", dict(w, opened_mono=Clock.mono + 10 ** 6)); calls()
check(A.tick(rcfg()) == 0 and marker()["reason"] == "invalid state" and json.load(open(SETSTATE)) == [],
      "V: a window opened in the future must be sealed (invalid state), not held open")
rreset(); A.recompute(rcfg()); w = window(); put("window.json", dict(w, opened_mono=Clock.mono + 10 ** 6)); calls()
check(A.recompute(rcfg()) == 0 and marker()["reason"] == "invalid state", "V: ...by the window unit too (an nftables reload must not re-open it)")
# a marker rewritten with a new time, same boot: still ONE record for that boot's seal.
areset(); A.recompute(acfg()); A.seal(acfg(), "complete"); A.tick(acfg()); A.tick(acfg())
for k in range(3):
    put("sealed", dict(marker(), sealed_at=marker()["sealed_at"] + 60.0 * (k + 1)))
    check(A.tick(acfg()) == 0 and len(seals()) == 1, "V: a marker rewritten with a new time got a second record (%d)" % len(seals()))
# the pointer: hostile contents must neither break the tick nor keep the seal's record out of the chain.
for label, text in (("an offset too large to seek to", lambda m: raw({"boot_id": m["boot_id"], "sealed_at": m["sealed_at"], "sha256": "0" * 64}, offset=HUGE)),
                    ("an offset of 2**63", lambda m: raw({"boot_id": m["boot_id"], "sealed_at": m["sealed_at"], "sha256": "0" * 64}, offset=str(2 ** 63))),
                    ("a sha256 that is a number", lambda m: raw({"boot_id": m["boot_id"], "sealed_at": m["sealed_at"], "offset": 0}, sha256=HUGE)),
                    ("nested past the parser", lambda m: DEEP), ("a list", lambda m: "[1]"), ("a sealed_at too large", lambda m: raw({"boot_id": m["boot_id"], "offset": 0, "sha256": "0" * 64}, sealed_at=HUGE))):
    areset(); A.recompute(acfg()); A.seal(acfg(), "complete"); put("recorded.json", text(marker()))
    try:
        rc = A.tick(acfg()); raised = None
    except Exception as e:
        rc, raised = None, e
    check(raised is None and rc == 0 and len(seals()) == 1, "V: a pointer with %s: tick rc=%r raised=%r records=%d (the record must still be written)" % (label, rc, raised, len(seals())))
# a TORN tail is not a record, even if the torn line is, byte for byte, this seal's record without
# its newline: `audit append` wrote the newline with the line, so a line without one never landed whole.
areset(); A.recompute(acfg()); A.seal(acfg(), "complete"); A.tick(acfg())
whole = open(AUDIT_LOG, "rb").read(); sealed_line = [l for l in whole.splitlines(True) if b"window-sealed" in l][0]
open(AUDIT_LOG, "wb").write(whole[:whole.index(sealed_line)] + sealed_line.rstrip(b"\n"))
check(A._find_seal(acfg(), marker()) is False, "V: a torn tail must not count as the seal's record")
# a chain line nested past the parser, naming the event: skipped, not fatal.
areset(); A.recompute(acfg()); A.seal(acfg(), "complete")
open(AUDIT_LOG, "a").write('{"event":"window-sealed","x":' + "[" * 3000 + "\n")
check(A._find_seal(acfg(), marker()) is False, "V: an unparseable chain line must be skipped by the scan")
# Each layer by itself (the layers cover for one another above, so each is also tried alone):
VERY_DEEP = b"[" * 1000000                               # deep enough for RecursionError on every Python this ships with
try:
    json.loads(VERY_DEEP.decode()); depth_error = None
except Exception as e:
    depth_error = e
check(isinstance(depth_error, RecursionError), "V: fixture: this depth no longer raises RecursionError here (%r); find one that does" % depth_error)
check(A._json(VERY_DEEP) is A._BAD and A._json(DEEP.encode()) is A._BAD and A._json(b"\xff") is A._BAD and A._json(b"{") is A._BAD and A._json(b"[1]") == [1],
      "V: the parser wrapper must turn every failure of the parser into _BAD, a depth error included")
areset(); A.recompute(acfg()); A.seal(acfg(), "complete"); mkv = marker()
for off in (HUGE, str(2 ** 63), str(2 ** 62)):
    put("recorded.json", raw({"boot_id": mkv["boot_id"], "sealed_at": mkv["sealed_at"], "sha256": "0" * 64}, offset=off))
    try:
        got = A._pointer_holds.__wrapped__(acfg(), mkv); raised = None
    except Exception as e:
        got, raised = None, e
    check(raised is None and got is False, "V: the pointer's own bound must refuse offset %s... before any seek (raised %r)" % (off[:12], raised))
real_open = A._open_log
A._open_log = lambda c: (_ for _ in ()).throw(RuntimeError("planted in the log reader"))
try:
    put("recorded.json", {"boot_id": mkv["boot_id"], "sealed_at": mkv["sealed_at"], "offset": 0, "sha256": "0" * 64})
    check(A._pointer_holds(acfg(), mkv) is False and A._find_seal(acfg(), mkv) is None and A.reconcile(acfg(), mkv) is False,
          "V: a reader that raises inside the pointer check, the scan or the reconciliation must read as 'cannot say'")
    check(A.tick(acfg()) == 0 and len(seals()) == 0, "V: ...and the tick must survive it, with the record still owed")
finally:
    A._open_log = real_open
check(A.tick(acfg()) == 0 and len(seals()) == 1, "V: ...and pay it on the next tick")
# THE NET. An exception inside a role's own logic: the set is flushed, the role returns 1.
def boom(*a, **k):
    raise RuntimeError("planted")


for rname, role, victim in (("recompute", A.recompute, "_recompute"), ("tick", A.tick, "_tick"), ("wait", A.wait, "_wait"),
                            ("seal", lambda c: A.seal(c, "budget"), "_seal"), ("ended", A.ended, "seal")):
    rreset(); A.recompute(rcfg()); calls(); real = getattr(A, victim); setattr(A, victim, boom)
    os.environ["SERVICE_RESULT"] = "timeout"
    try:
        try:
            rc = role(rcfg()); raised = None
        except Exception as e:
            rc, raised = None, e
    finally:
        setattr(A, victim, real); os.environ.pop("SERVICE_RESULT")
    check(raised is None and rc == 1 and json.load(open(SETSTATE)) == [] and CLOSE in calls(),
          "V: an exception inside %s must end with the set flushed and 1 (rc=%r raised=%r)" % (rname, rc, raised))
pstatus_path = os.path.join(TMP, "p-status-deep"); open(pstatus_path, "w").write("[" * 100000)
check(P.read_status(pstatus_path) == ("", None, ""), "V: the console program on a status nested past the parser")
ok("V no reader raises on hostile contents; a role that raises anyway fails closed")

# ── W. no open next to the agent; the anomaly records ───────────────────────────────────────────
PROC, PASSWD = os.path.join(TMP, "proc"), os.path.join(TMP, "passwd")
AGENT_UID = 1000


def passwd(text="root:x:0:0::/root:/bin/sh\nollama:x:61:61::/var/empty:/nologin\nagent:x:%d:100::/home/agent:/bin/bash\n" % AGENT_UID):
    open(PASSWD, "w").write(text)


def status_of(pid, uids):
    return ("Name:\tx\nUmask:\t0022\nState:\tS (sleeping)\nTgid:\t%d\nNgid:\t0\nPid:\t%d\nPPid:\t1\nTracerPid:\t0\n"
            "Uid:\t%d\t%d\t%d\t%d\nGid:\t100\t100\t100\t100\n" % ((pid, pid) + tuple(uids))).encode()


def procs(*entries):
    """(pid, uids) with uids four numbers, raw bytes for the status file, or None for a process that
    was listed and then exited (the directory is there, the file is not)."""
    shutil.rmtree(PROC, True); os.makedirs(PROC)
    os.makedirs(os.path.join(PROC, "sys")); open(os.path.join(PROC, "cpuinfo"), "w").close()
    for pid, st in entries:
        d = os.path.join(PROC, str(pid)); os.makedirs(d)
        if st is not None:
            open(os.path.join(d, "status"), "wb").write(st if isinstance(st, bytes) else status_of(pid, st))


QUIET = [(1, (0, 0, 0, 0)), (40, (61, 61, 61, 61)), (41, None), (77, (154, 154, 154, 154))]


def wcfg(**over):
    return acfg(**dict({"agent_user": "agent", "fetch_user": "ollama", "proc_path": PROC, "passwd_path": PASSWD}, **over))


def anomalies(kind=None):
    return [r for r in mine() if r["event"] == "window-anomaly" and kind in (None, r["kind"])]


def unseal():
    """Take the box back to "no marker, no token" without a new boot (what removing the state and
    the token by hand does)."""
    for f in (os.path.join(STATE, "sealed"), TOKEN):
        if os.path.exists(f):
            os.unlink(f)


passwd()
# nobody of the account: it opens. An exited process (41) and other accounts change nothing.
areset(); procs(*QUIET)
check(A.foreign_processes(wcfg()) == 0 and A.recompute(wcfg()) == 0 and json.load(open(SETSTATE)) == [61]
      and [r["event"] for r in mine()] == ["window-opened"],
      "W: with no process of the agent's account the window must open: %r" % [r["event"] for r in mine()])
# a name that only LOOKS like a pid entry, and an empty status (a task being torn down), are not processes
procs(*QUIET); os.makedirs(os.path.join(PROC, "self")); open(os.path.join(PROC, "self", "status"), "wb").write(status_of(9, (AGENT_UID,) * 4))
os.makedirs(os.path.join(PROC, "55")); open(os.path.join(PROC, "55", "status"), "wb").close()
check(A.foreign_processes(wcfg()) == 0, "W: only numeric entries are processes, and an empty status is one that is gone")

# WHO COUNTS. Not "the agent's uid": the account can hold processes under other uids (its
# subordinate range via newuidmap, a Nix build it asked for). Everything that is not a system
# account's uid or one systemd allocated for a unit counts.
for uid, counts, what in ((0, 0, "root"), (61, 0, "the fetcher"), (999, 0, "the last system uid"), (1000, 1, "the first normal uid"),
                          (1001, 1, "another normal account"), (30001, 1, "a Nix build user"), (100000, 1, "a subordinate uid"),
                          (165535, 1, "the end of a subordinate range"), (61183, 1, "just below the DynamicUser range"),
                          (61184, 0, "the first DynamicUser uid"), (65519, 0, "the last DynamicUser uid"), (65520, 1, "just above it"),
                          (65534, 1, "nobody"), (4294967294, 1, "the largest uid")):
    procs(*(QUIET + [(900, (uid,) * 4)]))
    got = A.foreign_processes(wcfg(agent_user=None))        # the ranges alone: the named account (uid 1000 here) must not mask a boundary
    check(got == counts and A.foreign_processes(wcfg()) == counts, "W: %s (uid %d) must count as %d: %r" % (what, uid, counts, got))
# with no account named in the configuration the ranges still decide: there is no switch
procs(*(QUIET + [(900, (100000,) * 4)]))
check(A.foreign_processes(wcfg(agent_user=None)) == 1 and A.foreign_processes(cfg(proc_path=PROC)) == 1,
      "W: a configuration that names no account must still see a foreign process")
# an agent's account below 1000 still counts, by its number
passwd("root:x:0:0::/root:/bin/sh\nollama:x:61:61::/:/n\nagent:x:500:100::/:/n\n"); procs(*(QUIET + [(900, (500,) * 4)]))
check(A.foreign_processes(wcfg()) == 1 and A.foreign_processes(wcfg(agent_user=None)) == 0, "W: the named account counts by its number, wherever it is")
passwd()
# a uid field that is not a number is a process we cannot attribute
procs(*(QUIET + [(900, b"Name:\tx\nUid:\t0\t0\tx\t0\n")]))
check(A.foreign_processes(wcfg()) is None, "W: a Uid line with a non-number")

# the account in ANY of the four slots, before the open
for slot in range(4):
    uids = [0, 0, 0, 0]; uids[slot] = AGENT_UID
    areset(); procs(*(QUIET + [(900, tuple(uids))])); calls()
    check(A.foreign_processes(wcfg()) == 1, "W: uid slot %d must count" % slot)
    rc = A.recompute(wcfg()); did = calls()
    check(rc == 0 and OPEN not in did and CLOSE in did and json.load(open(SETSTATE)) == []
          and "window.json" not in state_files() and marker()["reason"] == "refused" and os.path.exists(TOKEN),
          "W: slot %d: with the agent's account alive nothing may open, no books, and the box is sealed `refused`: rc=%r %r %r"
          % (slot, rc, did, state_files()))
    recs = mine()
    check([r["event"] for r in recs] == ["window-anomaly"] and recs[0]["kind"] == "foreign-process" and recs[0]["processes"] == 1
          and recs[0]["boot_id"] == "boot-1" and recs[0]["toplevel"] == TOP and recs[0]["ruleset_sha256"] == RULESET_SHA
          and set(recs[0]) >= {"src", "schema", "event", "fetch_uid"},
          "W: slot %d: one foreign-process record, nothing else, and no window-opened: %r" % (slot, recs))
# the tick then records that seal; the chain reads: anomaly, sealed(refused)
check(A.tick(wcfg()) == 0 and [r["event"] for r in mine()] == ["window-anomaly", "window-sealed"]
      and seals()[0]["marker"]["reason"] == "refused", "W: the tick must record the refusal's seal: %r" % [r["event"] for r in mine()])

# once per boot, whatever is done to the pointer; again in a new boot
PTR = os.path.join(STATE, "anomaly-foreign-process.json")
unseal(); check(A.recompute(wcfg()) == 0 and len(anomalies("foreign-process")) == 1 and os.path.exists(PTR),
                "W: a second refusal in the same boot must find the record and write the pointer, not a second record")
p0 = json.load(open(PTR))
line = open(AUDIT_LOG, "rb").read()[p0["offset"]:].split(b"\n")[0] + b"\n"
check(set(p0) == {"boot_id", "kind", "offset", "sha256"} and hashlib.sha256(line).hexdigest() == p0["sha256"]
      and json.loads(line)["kind"] == "foreign-process", "W: the pointer must name the anomaly's line: %r" % p0)
for label, bad in (("another boot", dict(p0, boot_id="boot-9")), ("another kind", dict(p0, kind="token-without-marker")),
                   ("an offset past the file", dict(p0, offset=10 ** 12)), ("an offset no file has", dict(p0, offset=10 ** 400)),
                   ("a hash that is not the line's", dict(p0, sha256="0" * 64)), ("not an object", [1]), ("nested past the parser", "[" * 3000),
                   ("another line", dict(p0, offset=0)), ("absent", None)):
    unseal()
    if bad is None:
        os.unlink(PTR)
    else:
        put("anomaly-foreign-process.json", bad)
    try:
        rc = A.recompute(wcfg()); raised = None
    except Exception as e:
        rc, raised = None, e
    check(raised is None and rc == 0 and len(anomalies("foreign-process")) == 1 and marker()["reason"] == "refused",
          "W: pointer %s: still sealed, still ONE record (rc=%r raised=%r n=%d)" % (label, rc, raised, len(anomalies("foreign-process"))))
# the pointer is a pointer: a chain that LOST the record gets it again, whatever the file says
put("anomaly-foreign-process.json", p0); shutil.rmtree(AUDIT_DIR); unseal()
check(A.recompute(wcfg()) == 0 and [r["event"] for r in mine()] == ["window-anomaly"],
      "W: a pointer into a chain that no longer has the record must not be believed: %r" % [r["event"] for r in mine()])
# ...and so does a chain that never had it: a pointer at a REAL line, with that line's own hash,
# which is some other record (here the window's own `window-opened`).
areset(); procs(*QUIET); check(A.recompute(wcfg()) == 0 and [r["event"] for r in mine()] == ["window-opened"], "W: fixture: one other record")
line0 = open(AUDIT_LOG, "rb").read().split(b"\n")[0] + b"\n"
put("anomaly-foreign-process.json", {"boot_id": "boot-1", "kind": "foreign-process", "offset": 0, "sha256": hashlib.sha256(line0).hexdigest()})
procs(*(QUIET + [(900, (AGENT_UID,) * 4)]))
check(A.recompute(wcfg()) == 0 and [r["event"] for r in mine()] == ["window-opened", "window-anomaly"] and marker()["reason"] == "refused",
      "W: a pointer at a line that is not the anomaly's must not be believed: %r" % [r["event"] for r in mine()])
# the pointer's own bound, tested on the reader itself (the net around it would hide a hole here)
for off in (HUGE, str(2 ** 63), str(2 ** 62)):
    put("anomaly-foreign-process.json", raw({"boot_id": "boot-1", "kind": "foreign-process", "sha256": "0" * 64}, offset=off))
    try:
        got = A._anomaly_pointer_holds.__wrapped__(wcfg(), "boot-1", "foreign-process"); raised = None
    except Exception as e:
        got, raised = None, e
    check(raised is None and got is False, "W: the anomaly pointer's own bound must refuse offset %s... before any seek (raised %r)" % (off[:12], raised))
# the other kind in the same boot is its own record (the key is the boot AND the kind)
unseal(); open(TOKEN, "w").close()
check(A.tick(wcfg()) == 0 and sorted(r["kind"] for r in anomalies()) == ["foreign-process", "token-without-marker"],
      "W: one record per kind per boot, not one per boot: %r" % [r["kind"] for r in anomalies()])
unseal(); boot(2)
check(A.recompute(wcfg()) == 0 and [r["boot_id"] for r in anomalies("foreign-process")] == ["boot-1", "boot-2"],
      "W: a new boot's refusal is a new record")
# a chain that refuses the record changes nothing about the refusal
areset(); procs(*(QUIET + [(900, (AGENT_UID,) * 4)])); os.makedirs(AUDIT_DIR); open(AUDIT_LOG, "w").write('{"torn')
check(A.recompute(wcfg()) == 0 and json.load(open(SETSTATE)) == [] and marker()["reason"] == "refused" and "window.json" not in state_files(),
      "W: a refusal must not wait for its record")

# what cannot be said counts as present; nothing raises
for label, setup, over in (
        ("no /proc", lambda: shutil.rmtree(PROC), {}),
        ("a status with no Uid line", lambda: procs(*(QUIET + [(900, b"Name:\tx\nState:\tS\n")])), {}),
        ("a Uid line with three numbers", lambda: procs(*(QUIET + [(900, b"Name:\tx\nUid:\t1\t2\t3\n")])), {}),
        ("a status that is noise", lambda: procs(*(QUIET + [(900, bytes(range(256)) * 8)])), {}),
        ("an account that does not exist", lambda: (procs(*QUIET), passwd("root:x:0:0::/root:/bin/sh\nollama:x:61:61::/:/n\n")), {}),
        ("an account that is root", lambda: (procs(*QUIET), passwd("agent:x:0:0::/root:/bin/sh\nollama:x:61:61::/:/n\n")), {}),
        ("an account with no number", lambda: (procs(*QUIET), passwd("agent:x:abc:0::/:/n\nollama:x:61:61::/:/n\n")), {}),
        ("a uid too long to be one", lambda: (procs(*QUIET), passwd("agent:x:%s:0::/:/n\nollama:x:61:61::/:/n\n" % ("9" * 400))), {}),
        ("no passwd", lambda: (procs(*QUIET), os.unlink(PASSWD)), {}),
        ("an account name that is not ASCII", lambda: procs(*QUIET), {"agent_user": "agént"}),
        ("the walk past its limit", lambda: procs(*QUIET), None)):
    areset(); passwd(); setup(); calls()
    limit0 = A.PROC_LIMIT
    if over is None:
        A.PROC_LIMIT, over = -1, {}
    try:
        try:
            n = A.foreign_processes(wcfg(**over)); rc = A.recompute(wcfg(**over)); raised = None
        except Exception as e:
            n = rc = None; raised = e
    finally:
        A.PROC_LIMIT = limit0
    did = calls()
    check(raised is None and n is None and rc == 0 and OPEN not in did and json.load(open(SETSTATE)) == []
          and marker()["reason"] == "refused" and "window.json" not in state_files()
          and [(r["kind"], r["processes"]) for r in anomalies()] == [("foreign-process", None)],
          "W: %s must count as present: n=%r rc=%r raised=%r %r" % (label, n, rc, raised, did))
passwd(); check(limit0 == 5, "W: PROC_LIMIT")

# the second look: a process of the account appears while the set is being opened
areset(); procs(*QUIET); calls(); order = []
real_open, real_record, real_tool2 = A.open_set, A._append_record, A._tool


def open_then_appear(c):
    r = real_open(c); procs(*(QUIET + [(901, (AGENT_UID,) * 4)])); return r


A.open_set = open_then_appear
A._append_record = lambda c, event, **f: (order.append(("record " + event + " " + f.get("kind", ""),
                                                        json.load(open(SETSTATE)) if os.path.exists(SETSTATE) else None)), real_record(c, event, **f))[1]
try:
    rc = A.recompute(wcfg())
finally:
    A.open_set, A._append_record = real_open, real_record
did = calls()
check(rc == 0 and OPEN in did and did.index(CLOSE, did.index(OPEN)) > did.index(OPEN) and json.load(open(SETSTATE)) == []
      and marker()["reason"] == "refused" and [r["event"] for r in mine()] == ["window-opened", "window-anomaly"]
      and order[-1] == ("record window-anomaly foreign-process", []),
      "W: a process that appears during the open: flushed first, sealed `refused`, then recorded: rc=%r %r %r" % (rc, did, order))

# the tick asks too, once a minute, while the window is open: here the refusal's flush is NOT
# confirmed (nft opens but will not close), so the set still holds the uid and the books say open.
areset(); procs(*QUIET); tool(NFT, rc=1, measure_rc=0, open_rc=0); calls()
A.open_set = open_then_appear
try:
    rc = A.recompute(wcfg())
finally:
    A.open_set = real_open
check(rc == 1 and json.load(open(SETSTATE)) == [61] and "sealed" not in state_files() and window()["boot_id"] == "boot-1",
      "W: fixture: a refusal whose flush failed leaves the set full and no marker (rc=%r %r)" % (rc, state_files()))
check(A.tick(wcfg()) == 1 and "sealed" not in state_files(), "W: while nft still refuses, the tick cannot seal either (and says so by failing)")
tool(NFT); calls()
rc = A.tick(wcfg()); did = calls()
check(rc == 0 and did[0] == CLOSE and json.load(open(SETSTATE)) == [] and marker()["reason"] == "refused",
      "W: the tick must close a window that is open next to the agent's account, whatever is left of the budget: rc=%r %r" % (rc, did))
# ...and it does not disturb a healthy window: nobody of the account, nothing closes
areset(); procs(*QUIET); A.recompute(wcfg()); calls()
check(A.tick(wcfg()) == 0 and CLOSE not in calls() and json.load(open(SETSTATE)) == [61] and "sealed" not in state_files(),
      "W: a tick on a healthy open window closed it")
# what the tick cannot say counts as present there too
shutil.rmtree(PROC)
check(A.tick(wcfg()) == 0 and json.load(open(SETSTATE)) == [] and marker()["reason"] == "refused", "W: a tick that cannot read /proc must close the window")

# THE FLUSH COMES BEFORE THE RECORD, in the first look too: the unit restarted with the set still
# full (a restart does not flush nft) and a foreign process now there.
areset(); procs(*QUIET); check(A.recompute(wcfg()) == 0 and json.load(open(SETSTATE)) == [61], "W: fixture: open")
procs(*(QUIET + [(900, (100000,) * 4)])); calls(); order = []; del SYS.lines[:]
real_log = A.log
A._append_record = lambda c, event, **f: (order.append(("record " + event + " " + f.get("kind", ""), json.load(open(SETSTATE)))), real_record(c, event, **f))[1]
A.log = lambda line: (order.append(("log", json.load(open(SETSTATE)))), real_log(line))[1]
try:
    rc = A.recompute(wcfg())
finally:
    A._append_record, A.log = real_record, real_log
check(rc == 0 and marker()["reason"] == "refused" and order and all(state == [] for _, state in order)
      and ("record window-anomaly foreign-process", []) in order,
      "W: a refusal with the set already full must flush before it logs or records anything: rc=%r %r" % (rc, order))

# a configuration that does not name the fetcher's account is refused, not waved through
areset(); procs(*QUIET); calls()
rc = A.recompute(wcfg(fetch_user=None)); did = calls()
check(rc == 0 and OPEN not in did and marker()["reason"] == "refused" and "window.json" not in state_files(),
      "W: no fetcher account in the configuration must seal, not open: rc=%r %r" % (rc, did))

# the fetcher's account does not have the uid the set names (a box upgraded in place)
areset(); procs(*QUIET); passwd("root:x:0:0::/root:/bin/sh\nollama:x:992:992::/:/n\nagent:x:1000:100::/:/n\n"); calls(); del SYS.lines[:]
rc = A.recompute(wcfg()); did = calls()
check(rc == 0 and OPEN not in did and marker()["reason"] == "refused" and "window.json" not in state_files() and mine() == []
      and any("reinstall" in l for l in SYS.lines),
      "W: a fetcher account with another uid: sealed, nothing opened, and the log says a reinstall is needed: %r %r" % (did, SYS.lines))
passwd()
check(A._account_uid(wcfg(), "ollama") == 61 and A._account_uid(wcfg(), "agent") == AGENT_UID and A._account_uid(wcfg(), "nobody-here") is None,
      "W: account lookup")

# the tick: the login token with no marker, once per boot, with what the books look like
scans = []
real_find_line = A._find_line                     # every scan of the chain goes through this one function
A._find_line = lambda c, is_it: (scans.append("scan"), real_find_line(c, is_it))[1]
appended = []
A._append_record = lambda c, event, **f: (appended.append(event), real_record(c, event, **f))[1]
try:
    def one_tick(c):
        del scans[:]; del appended[:]
        rc = A.tick(c)
        check(len(scans) <= 1 and len(appended) <= 1, "W: a tick did more than one job on the chain: %r %r" % (scans, appended))
        return rc
    # (a) books of this boot: the window is open and the token appears
    areset(); procs(*QUIET); check(A.recompute(wcfg()) == 0 and json.load(open(SETSTATE)) == [61], "W: fixture: open")
    open(TOKEN, "w").close(); calls()
    rc = one_tick(wcfg()); did = calls()
    a = anomalies("token-without-marker")
    check(rc == 0 and CLOSE in did and json.load(open(SETSTATE)) == [] and "sealed" not in state_files()
          and len(a) == 1 and a[0]["books"] == "this-boot" and a[0]["boot_id"] == "boot-1" and "processes" not in a[0],
          "W: the token while the window is open: closed, no marker, one record with books=this-boot: rc=%r %r %r" % (rc, did, a))
    check(one_tick(wcfg()) == 0 and one_tick(wcfg()) == 0 and len(anomalies("token-without-marker")) == 1
          and os.path.exists(os.path.join(STATE, "anomaly-token-without-marker.json")), "W: once per boot, and the pointer is written")
    # (b) the state removed in a sealed boot: no books at all
    areset(); procs(*QUIET); A.recompute(wcfg()); check(A.seal(wcfg(), "complete") == 0 and os.path.exists(TOKEN), "W: fixture: sealed")
    rc = one_tick(wcfg())
    check(rc == 0 and anomalies() == [] and len(seals()) == 1, "W: a sealed box with its marker is no anomaly: rc=%r %r %r" % (rc, [r["event"] for r in mine()], SYS.lines[-3:]))
    for f in state_files():
        os.unlink(os.path.join(STATE, f))
    check(one_tick(wcfg()) == 0 and [x["books"] for x in anomalies("token-without-marker")] == ["absent"]
          and "sealed" not in state_files() and json.load(open(SETSTATE)) == [], "W: the state removed in a sealed boot: books=absent, and no new marker")
    # (c) books of another boot, (d) books that are not books: one record per BOOT, so each in its own
    for n, (label, books, want) in enumerate((("another boot's", None, "other-boot"), ("malformed", "{not json", "malformed")), 3):
        areset(); procs(*QUIET); A.recompute(wcfg()); A.tick(wcfg()); boot(n)
        if books is not None:
            put("window.json", books)
        open(TOKEN, "w").close()
        check(one_tick(wcfg()) in (0, 1) and [x["books"] for x in anomalies("token-without-marker")] == [want]
              and json.load(open(SETSTATE)) == [], "W: %s books beside a token: %r" % (label, anomalies()))
    # no boot id: nothing to count "once per boot" in, so nothing is written (and nothing raises)
    areset(); procs(*QUIET); open(BOOTID, "w").close()
    check(A.note_anomaly(wcfg(), "token-without-marker", books="absent") is False and mine() == [], "W: no boot id, no anomaly record")
    boot(1)
    check(A.note_anomaly(wcfg(), "made-up", x=1) is False and mine() == [], "W: only the two kinds exist")
finally:
    A._find_line, A._append_record = real_find_line, real_record
check(A.ANOMALIES == ("foreign-process", "token-without-marker") and A.BOOKS == ("absent", "malformed", "this-boot", "other-boot"), "W: the kinds")
ok("W no open next to the agent's account; the two anomalies, once per boot each")


print("agos-window-battery: PASS (%d criteria)" % len(passed))
