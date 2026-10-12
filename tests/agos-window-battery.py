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


def cfg(**over):
    c = {"state_dir": STATE, "length": LENGTH, "build_epoch": BUILD, "fetch_uid": 61, "nft": NFT, "systemctl": SYSTEMCTL,
         "open_file": OPEN_F, "close_file": CLOSE_F, "ruleset_file": RULESET, "boot_id_path": BOOTID, "toplevel": TOP,
         "restart_units": ["ollama.service"], "stop_units": ["agent-os-pull-model.timer", "agent-os-pull-model.service"],
         "store_prefix": TMP + "/"}                       # the battery's "store"; the installed copy has no such key
    c.update(over)
    return c


def tool(path, rc=0):
    with open(path, "w") as f:
        f.write("#!%s\nimport json,sys\nopen(%r,'a').write(json.dumps([%r]+sys.argv[1:])+'\\n')\nsys.exit(%d)\n"
                % (sys.executable, LOG, os.path.basename(path), rc))
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
               "sealed_at": 1.0, "reason": "complete"}


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
reset(); tool(NFT, rc=1); calls()
check(A.recompute(cfg()) == 1 and calls() == [OPEN, CLOSE], "H: an nft that refuses the open must end in a flush")
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
import re
REAL_LOCK_WAIT = int(re.search(r"^LOCK_WAIT = (\d+)", open(MOD).read(), re.M).group(1))      # the battery shortens A.LOCK_WAIT
check(sum(t for _, _, t in spent) < REAL_LOCK_WAIT and A.NFT_LIMIT + REAL_LOCK_WAIT < 180,
      "J: a seal (%d s of tools at most) must finish inside the lock wait (%d s), and a stamp that waited for the lock must still fit the unit's 180 s stop timeout"
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

print("agos-window-battery: PASS (%d criteria)" % len(passed))
