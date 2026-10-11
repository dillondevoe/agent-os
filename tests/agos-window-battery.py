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
    check(st["sealed"] == "valid" and st["reason"] == "complete", "N: status after the seal: %r" % st)
finally:
    os.environ.pop("AGOS_WINDOW_CONFIG")
ok("N the command line accepts exactly its roles; status reports the state")

print("agos-window-battery: PASS (%d criteria)" % len(passed))
