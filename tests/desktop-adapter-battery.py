#!/usr/bin/env python3
# tests/desktop-adapter-battery.py — the CONTRACT BATTERY for modules/agos_desktop.py, the desktop
# adapter (docs/design/surfaces-and-first-login.md §6). Fake hyprctl / notify-send on PATH record
# their argv; no compositor needed.
#
#   A. Hyprland windows(): parses `hyprctl clients -j` into {class, title, workspace}, via direct argv.
#   B. hyprctl absent -> Unsupported("hyprctl not on this unit's PATH"); falsy, and NOT a list
#      (the third state never reads as "saw nothing").
#   C. hyprctl answers garbage -> Unsupported("hyprctl gave no readable answer").
#   D. arrange(known) dispatches exactly ["dispatch", <table value>]; arrange(unknown) dispatches
#      NOTHING and says so; a Lua-escape string as the action dispatches nothing.
#   E. The ARRANGE table is closed: no interpolation placeholder in any value.
#   F. NoDesktop: windows() and arrange() are Unsupported; its table is empty.
#   G. notify(): argv is ["--app-name=Agent OS", "--", msg] with msg capped at 500 (so a message that
#      starts with "-" is never read as a flag); notify-send absent -> Unsupported.
#
# stdlib only.

SIDE_EFFECTS = []  # temp dir, removed at exit

import atexit, importlib.util, json, os, shutil, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MOD = os.path.abspath(os.path.join(HERE, "..", "modules", "agos_desktop.py"))
spec = importlib.util.spec_from_file_location("agos_desktop", MOD)
D = importlib.util.module_from_spec(spec); spec.loader.exec_module(D)
TMP = tempfile.mkdtemp(prefix="desktop-adapter-battery.")
atexit.register(shutil.rmtree, TMP, True)
BIN = os.path.join(TMP, "bin"); os.makedirs(BIN)
LOG = os.path.join(TMP, "argv.log")
REAL_PATH = os.environ.get("PATH", "")


def check(c, msg):
    if not c:
        raise AssertionError(msg)


def fake(name, stdout=""):
    p = os.path.join(BIN, name)
    with open(p, "w") as fh:
        fh.write("#!%s\nimport json, sys\nopen(%r, 'a').write(json.dumps([%r] + sys.argv[1:]) + '\\n')\n"
                 "sys.stdout.write(%r)\n" % (sys.executable, LOG, name, stdout))
    os.chmod(p, 0o755)


def reset(*tools):
    shutil.rmtree(BIN); os.makedirs(BIN)
    if os.path.exists(LOG):
        os.unlink(LOG)
    for t in tools:
        fake(*t) if isinstance(t, tuple) else fake(t)
    os.environ["PATH"] = BIN       # only the fakes: an absent tool is genuinely absent


def calls():
    return [json.loads(l) for l in open(LOG)] if os.path.exists(LOG) else []


passed = []
def ok(tag):
    passed.append(tag); print("  ok  " + tag)


H = D.HyprlandAdapter()

# A
reset(("hyprctl", json.dumps([{"class": "kitty", "title": "a terminal", "workspace": {"name": "1"}},
                              {"class": "firefox", "title": "page"}])))
w = H.windows()
check(w == [{"class": "kitty", "title": "a terminal", "workspace": "1"}, {"class": "firefox", "title": "page", "workspace": ""}],
      "A: parsed windows: %r" % w)
check(calls() == [["hyprctl", "clients", "-j"]], "A: direct argv: %r" % calls())
ok("A windows() parses hyprctl clients -j via direct argv")

# B
reset()
w = H.windows()
check(isinstance(w, D.Unsupported) and not w and not isinstance(w, list) and "not on this unit's PATH" in w.reason, "B: %r" % w)
ok("B hyprctl absent -> Unsupported (falsy, not a list)")

# C
reset(("hyprctl", "not json"))
w = H.windows()
check(isinstance(w, D.Unsupported) and "no readable answer" in w.reason, "C: %r" % w)
ok("C garbage -> Unsupported")

# D
for act, disp in H.ARRANGE.items():
    reset("hyprctl")
    r = H.arrange(act)
    check(calls() == [["hyprctl", "dispatch", disp]] and r == "desktop: %s done" % act, "D %s: %r %r" % (act, calls(), r))
for bad in ("tidy", '") os.execute("id") --', ""):
    reset("hyprctl")
    r = H.arrange(bad)
    check(calls() == [] and "unknown window action" in r, "D unknown %r must dispatch nothing: %r" % (bad, calls()))
ok("D known keys dispatch exactly their table value; unknown/escape strings dispatch nothing")

# E
check(all(not any(t in v for t in ("{", "%s", "+")) for v in H.ARRANGE.values()), "E: table must have no placeholders")
ok("E ARRANGE table closed, no placeholders")

# F
N = D.NoDesktop()
check(isinstance(N.windows(), D.Unsupported) and isinstance(N.arrange("close"), D.Unsupported) and N.ARRANGE == {}, "F")
ok("F NoDesktop: optional verbs Unsupported, empty table")

# G
reset("notify-send")
r = H.notify("-x looks like a flag " + "y" * 600)
c = calls()
check(r == "notified" and len(c) == 1 and c[0][:3] == ["notify-send", "--app-name=Agent OS", "--"]
      and c[0][3].startswith("-x looks like a flag") and len(c[0][3]) == 500, "G: %r" % c)
reset()
check(isinstance(N.notify("hi"), D.Unsupported), "G: absent notify-send -> Unsupported")
ok("G notify: message after --, capped at 500; absent tool -> Unsupported")

os.environ["PATH"] = REAL_PATH
print("desktop-adapter-battery: PASS (%d criteria)" % len(passed))
