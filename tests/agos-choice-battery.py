#!/usr/bin/env python3
# tests/agos-choice-battery.py — the CONTRACT BATTERY for modules/agos_choice.py, the surface choice
# (docs/design/first-login-and-setup-window.md §3-§4). Everything runs in a temp dir as this user;
# the "root" and "agentos-setup" owners are both this uid, and the wrong-owner arms name another.
#
#   A. read_store(): a valid store gives the choice, and the strings are the module's own.
#   B. Every malformed store gives the defaults and a reason: missing, symlink, directory, FIFO (no
#      hang), oversize, wrong owner, wrong filesystem, not TOML, extra key, missing key, wrong schema,
#      a value not offered, a value of the wrong type, a value with trailing text.
#   C. The typed-line rule: empty / `s` / an in-range number are accepted; ESC, pasted brackets, any
#      control byte, non-ASCII digits, an over-length line, a line with no newline, 0, n+1 are not.
#   D. The front end: picks, skip, a bad line then a good one, too many bad lines (default), EOF
#      (defaults), timeout (defaults); a kind with one option is not asked; the tokens file is 0600.
#   E. The front end never execs or spawns, and nothing typed reaches the tokens file or the log.
#   F. The helper: valid tokens become the store (0644, exact text), and the tokens file is removed.
#   G. The helper refuses and writes NOTHING for: no file, a symlink, the wrong owner, an oversize
#      file, the wrong filesystem, two tokens, four tokens, a value not offered, values in the wrong
#      order, non-ASCII. An existing store is left as it was.
#   H. The helper never reads the keyboard (fd 0).
#   I. The wrappers exec from the compiled-in table, pass arguments through, start the shell as a
#      login shell, fall back to the default on a bad store — and SHELL, PATH, ENV, BASH_ENV, ZDOTDIR,
#      XDG_CONFIG_HOME, TERMINAL in the environment change nothing.
#   J. The installed copy (placeholder substituted) ignores AGOS_CHOICE_CONFIG.
#   K. A compiled-in default that is not offered is refused at start; the placeholder appears once.
#   L. Writes are atomic: no temp file is left, the file and its directory are fsynced.
#
# stdlib only.

SIDE_EFFECTS = []  # a temp dir, removed at exit

import atexit, importlib.util, io, json, os, shutil, sys, tempfile, time, types

try:
    os.fstat(0)
except OSError:
    os.open(os.devnull, os.O_RDONLY)

HERE = os.path.dirname(os.path.abspath(__file__))
MOD = os.path.abspath(os.path.join(HERE, "..", "modules", "agos_choice.py"))
spec = importlib.util.spec_from_file_location("agos_choice", MOD)
A = importlib.util.module_from_spec(spec); spec.loader.exec_module(A)

TMP = tempfile.mkdtemp(prefix="agos-choice-battery.")
atexit.register(shutil.rmtree, TMP, True)
STORE = os.path.join(TMP, "state", "surface.toml"); os.makedirs(os.path.dirname(STORE))
RUN = os.path.join(TMP, "run"); os.makedirs(RUN)
TOKENS = os.path.join(RUN, "choice")
ME = os.getuid()
passed = []


def check(c, msg):
    if not c:
        raise AssertionError(msg)


def ok(tag):
    passed.append(tag); print("  ok  " + tag)


def cfg(**over):
    c = {"offered": {"desktop": ["hyprland", "sway", "none"], "terminal": ["kitty", "foot", "ghostty", "alacritty"],
                     "shell": ["bash", "fish", "zsh"]},
         "defaults": {"desktop": "hyprland", "terminal": "kitty", "shell": "bash"},
         "exec": {"terminal": {"kitty": "/store/kitty/bin/kitty", "foot": "/store/foot/bin/foot",
                               "ghostty": "/store/ghostty/bin/ghostty", "alacritty": "/store/alacritty/bin/alacritty"},
                  "shell": {"bash": "/store/bash/bin/bash", "fish": "/store/fish/bin/fish", "zsh": "/store/zsh/bin/zsh"}},
         "store": STORE, "store_uid": ME, "store_dev": TMP,
         "tokens": TOKENS, "tokens_uid": ME, "tokens_dev": TMP}
    c.update(over)
    return c


def put(path, text, mode=0o644):
    if os.path.lexists(path):
        if os.path.isdir(path) and not os.path.islink(path):
            os.rmdir(path)
        else:
            os.unlink(path)
    with open(path, "w") as f:
        f.write(text)
    os.chmod(path, mode)


def gone(path):
    if os.path.lexists(path):
        if os.path.isdir(path) and not os.path.islink(path):
            os.rmdir(path)
        else:
            os.unlink(path)


class FakeSyslog(types.ModuleType):
    LOG_USER, LOG_INFO = 8, 6

    def __init__(self):
        super().__init__("syslog"); self.lines = []

    def openlog(self, *a):
        pass

    def syslog(self, prio, line):
        self.lines.append(line)


SYS = FakeSyslog(); sys.modules["syslog"] = SYS
GOOD = 'schema = 1\ndesktop = "sway"\nterminal = "foot"\nshell = "fish"\n'
DEFAULTS = {"desktop": "hyprland", "terminal": "kitty", "shell": "bash"}

# A — a valid store
C = cfg(); put(STORE, GOOD)
choice, why = A.read_store(C)
check(choice == {"desktop": "sway", "terminal": "foot", "shell": "fish"} and why is None, "A: %r %r" % (choice, why))
check(all(any(choice[k] is o for o in C["offered"][k]) for k in A.KINDS), "A: a returned string is not the module's own object")
ok("A a valid store gives the choice, as the module's own strings")

# B — every malformed store gives the defaults and a reason
other = os.path.join(TMP, "other.toml"); put(other, GOOD)


def bad_store(tag, setup, c=None):
    gone(STORE); setup()
    choice, why = A.read_store(c or cfg())
    check(choice == DEFAULTS and isinstance(why, str) and why, "B %s: %r %r" % (tag, choice, why))


bad_store("missing", lambda: None)
bad_store("symlink", lambda: os.symlink(other, STORE))
bad_store("directory", lambda: os.mkdir(STORE))
t0 = time.monotonic(); bad_store("fifo", lambda: os.mkfifo(STORE)); check(time.monotonic() - t0 < 5, "B fifo: the reader hung")
bad_store("oversize", lambda: put(STORE, GOOD + "#" * A.STORE_MAX))
bad_store("wrong owner", lambda: put(STORE, GOOD), cfg(store_uid=ME + 1))
bad_store("wrong filesystem", lambda: put(STORE, GOOD), cfg(store_dev="/proc"))
for tag, text in (("not TOML", "schema = \n"), ("binary", "\x00\x01\x02"),
                  ("extra key", GOOD + 'exec = "/bin/sh"\n'), ("missing key", 'schema = 1\ndesktop = "sway"\nterminal = "foot"\n'),
                  ("schema 2", GOOD.replace("schema = 1", "schema = 2")), ("schema true", GOOD.replace("schema = 1", "schema = true")),
                  ("schema string", GOOD.replace("schema = 1", 'schema = "1"')),
                  ("not offered", GOOD.replace('"foot"', '"xterm"')), ("wrong type", GOOD.replace('"foot"', "7")),
                  ("list", GOOD.replace('"foot"', '["foot"]')), ("trailing text", GOOD.replace('"foot"', '"foot; reboot"')),
                  ("path", GOOD.replace('"fish"', '"/bin/sh"')), ("case", GOOD.replace('"fish"', '"Fish"')),
                  ("table", GOOD + "[desktop2]\nx = 1\n")):
    bad_store(tag, lambda text=text: put(STORE, text))
ok("B every malformed store gives the defaults and a reason (and a FIFO does not hang)")

# C — the typed-line rule
N = 4
for raw, want in ((b"\n", ("default",)), (b"s\n", ("skip",)), (b"1\n", ("pick", 0)), (b"4\n", ("pick", 3)),
                  (b" 2 \n", ("pick", 1)), (b"03\n", ("pick", 2)), (b"1" + b" " * 14 + b"\n", ("pick", 0))):   # exactly at the cap
    check(A._valid_line(raw, N) == want, "C: %r -> %r, want %r" % (raw, A._valid_line(raw, N), want))
for raw in (b"0\n", b"5\n", b"12\n", b"-1\n", b"1a\n", b"s s\n", b"S\n", b"1", b"", b"\x1b[A\n", b"\x1b[200~1\x1b[201~\n",
            b"1\x00\n", b"1\r\n", b"\t1\n", b"\xd9\xa1\n", b"\xc2\xb2\n", b"1" * 16 + b"\n", b"1\n2\n", b"1;reboot\n",
            b"001\n", b"hunter2\n",
            b" " * 20 + b"1\n", b"1" + b" " * 15 + b"\n"):      # valid but for their length: only the cap rejects these
    check(A._valid_line(raw, N) is None, "C: %r must be invalid, got %r" % (raw, A._valid_line(raw, N)))
ok("C only empty / s / an in-range number is a valid line")


# D — the front end
def front(script, c=None, timeout=None, close=True):
    """Run firstlogin() with `script` (bytes chunks, one read each) on a pipe. Returns (choice, text)."""
    gone(TOKENS)
    r, w = os.pipe()
    for chunk in script:
        os.write(w, chunk)
    if close:
        os.close(w)
    out = io.StringIO()
    old = A.TIMEOUT
    if timeout is not None:
        A.TIMEOUT = timeout
    try:
        got = A.firstlogin(c or cfg(), fd_in=r, out=out)
    finally:
        A.TIMEOUT = old; os.close(r)
        if not close:
            os.close(w)
    return got, out.getvalue()


# One read returns everything in a pipe, so each question gets its own pipe write via a tiny shim:
# canonical-mode terminals hand over one line per read; emulate that by reading line by line.
_real_read_line = A._read_line


def _line_reader(fd, deadline):
    buf = b""
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            return None
        import select as _s
        r, _, _ = _s.select([fd], [], [], left)
        if not r:
            return None
        ch = os.read(fd, 1)
        if not ch:
            return buf or None
        buf += ch
        if ch == b"\n":
            return buf


A._read_line = _line_reader
got, text = front([b"2\n", b"3\n", b"\n"])
check(got == {"desktop": "sway", "terminal": "ghostty", "shell": "bash"}, "D picks: %r" % got)
check(open(TOKENS).read() == "sway ghostty bash\n" and (os.stat(TOKENS).st_mode & 0o777) == 0o600, "D: tokens file content/mode")
got, text = front([b"3\n", b"s\n"])
check(got == {"desktop": "none", "terminal": "kitty", "shell": "bash"} and "Shell:" not in text, "D skip: %r" % got)
got, text = front([b"9\n", b"\x1b[A\n", b"2\n", b"\n", b"\n"])
check(got["desktop"] == "sway" and text.count("Please type") == 2, "D bad then good: %r" % got)
got, text = front([b"x\n"] * A.ATTEMPTS + [b"2\n", b"\n"])
check(got == {"desktop": "hyprland", "terminal": "foot", "shell": "bash"}, "D too many bad lines must take that default and go on: %r" % got)
got, text = front([b"2\n"])                                          # EOF after one answer
check(got == {"desktop": "sway", "terminal": "kitty", "shell": "bash"}, "D EOF: %r" % got)
t0 = time.monotonic(); got, text = front([b"2\n"], timeout=0.4, close=False)       # the writer stays open: a silent keyboard
check(got == {"desktop": "sway", "terminal": "kitty", "shell": "bash"} and 0.3 < time.monotonic() - t0 < 5, "D timeout: %r" % got)
check(open(TOKENS).read() == "sway kitty bash\n", "D timeout: the defaults must still be written")
one = cfg(); one["offered"] = dict(one["offered"], desktop=["hyprland"])
got, text = front([b"2\n", b"2\n"], c=one)
check(got == {"desktop": "hyprland", "terminal": "foot", "shell": "fish"} and "Desktop:" not in text, "D one option is not asked: %r" % got)
ok("D picks, skip, bad lines, EOF and timeout all end in a 0600 tokens file of offered words")

# E — the front end never execs or spawns; nothing typed is kept
import subprocess
trip = []
saved = {n: getattr(os, n) for n in ("execv", "execve", "execvp", "fork", "system", "posix_spawn") if hasattr(os, n)}
for n in saved:
    setattr(os, n, lambda *a, _n=n, **k: trip.append(_n))
real_popen = subprocess.Popen; subprocess.Popen = lambda *a, **k: trip.append("Popen")
SYS.lines.clear()
try:
    got, text = front([b"hunter2\n", b"2\n", b"PASSWORD-zz\n", b"\n", b"3\n"])
finally:
    for n, f in saved.items():
        setattr(os, n, f)
    subprocess.Popen = real_popen
check(trip == [], "E: the front end called %r" % trip)
blob = open(TOKENS).read() + "".join(SYS.lines)
check("hunter2" not in blob and "PASSWORD" not in blob and "hunter2" not in text, "E: typed text was kept or echoed: %r" % blob)
check(set(open(TOKENS).read().split()) <= {"hyprland", "sway", "none", "kitty", "foot", "ghostty", "alacritty", "bash", "fish", "zsh"},
      "E: the tokens file holds something that is not an offered word")
ok("E the front end never execs or spawns; typed text reaches neither the tokens file nor the log")

# F — the helper writes the store
gone(STORE); put(TOKENS, "sway foot zsh\n", 0o600); SYS.lines.clear()
rc = A.apply(cfg())
check(rc == 0 and open(STORE).read() == 'schema = 1\ndesktop = "sway"\nterminal = "foot"\nshell = "zsh"\n', "F: store text: %r" % open(STORE).read())
check((os.stat(STORE).st_mode & 0o777) == 0o644 and not os.path.lexists(TOKENS), "F: store mode / tokens not removed")
check(A.read_store(cfg()) == ({"desktop": "sway", "terminal": "foot", "shell": "zsh"}, None), "F: the reader does not accept what the helper wrote")
check(SYS.lines == ["store written: desktop=sway terminal=foot shell=zsh"], "F: log: %r" % SYS.lines)
ok("F valid tokens become the store (0644, exact text); the tokens file is read once and removed")

# G — the helper refuses and writes nothing
target = os.path.join(TMP, "elsewhere"); put(target, "sway foot zsh\n", 0o600)


def refuse(tag, setup, c=None, keep=None):
    gone(TOKENS); gone(STORE)
    if keep is not None:
        put(STORE, keep)
    setup()
    rc = A.apply(c or cfg())
    now = open(STORE).read() if os.path.exists(STORE) else None
    check(rc == 1 and now == keep, "G %s: rc=%r store=%r" % (tag, rc, now))
    check(not [f for f in os.listdir(os.path.dirname(STORE)) if f != "surface.toml"], "G %s: a temp file was left" % tag)


refuse("no file", lambda: None)
refuse("symlink", lambda: os.symlink(target, TOKENS))
refuse("directory", lambda: os.mkdir(TOKENS))
refuse("wrong owner", lambda: put(TOKENS, "sway foot zsh\n", 0o600), cfg(tokens_uid=ME + 1))
refuse("wrong filesystem", lambda: put(TOKENS, "sway foot zsh\n", 0o600), cfg(tokens_dev="/proc"))
refuse("oversize", lambda: put(TOKENS, "sway foot zsh" + " " * 60 + "\n", 0o600))
for tag, text in (("two", "sway foot\n"), ("four", "sway foot zsh extra\n"), ("empty", ""), ("not offered", "sway xterm zsh\n"),
                  ("wrong order", "foot sway zsh\n"), ("non-ascii", "sway foot zѕh\n"), ("path", "sway foot /bin/sh\n"),
                  ("toml injection", 'sway foot zsh"\nexec="x\n'), ("case", "Sway foot zsh\n"), ("nul", "sway\x00 foot zsh\n")):
    refuse(tag, lambda text=text: put(TOKENS, text, 0o600))
refuse("existing store untouched", lambda: put(TOKENS, "sway xterm zsh\n", 0o600), keep=GOOD)
user_cfg = cfg(); del user_cfg["tokens_uid"]; user_cfg["tokens_user"] = "no-such-user-agos-battery"
refuse("unknown user", lambda: put(TOKENS, "sway foot zsh\n", 0o600), user_cfg)
ok("G the helper refuses every bad tokens file, writes nothing, and leaves an existing store alone")

# H — the helper never reads fd 0
gone(STORE); put(TOKENS, "none kitty bash\n", 0o600)
r, w = os.pipe(); os.write(w, b"KEYBOARD-BYTES\n"); saved0 = os.dup(0); os.dup2(r, 0)
try:
    rc = A.apply(cfg())
    import select
    still = select.select([0], [], [], 0)[0] and os.read(0, 100)
finally:
    os.dup2(saved0, 0); os.close(saved0); os.close(r); os.close(w)
check(rc == 0 and still == b"KEYBOARD-BYTES\n", "H: the helper consumed the keyboard: %r" % still)
ok("H the helper never reads the keyboard")

# I — the wrappers
execs = []
real_execv = os.execv
os.execv = lambda path, argv: execs.append((path, list(argv)))
POISON = {"SHELL": "/evil/sh", "PATH": "/evil/bin", "ENV": "/evil/env", "BASH_ENV": "/evil/bashenv", "ZDOTDIR": "/evil/z",
          "XDG_CONFIG_HOME": "/evil/xdg", "TERMINAL": "/evil/term", "TERM": "evil"}
keep_env = {k: os.environ.get(k) for k in POISON}
try:
    put(STORE, GOOD)
    A.run("terminal", ["-e", "top"], cfg()); A.run("shell", ["-c", "true"], cfg())
    check(execs == [("/store/foot/bin/foot", ["/store/foot/bin/foot", "-e", "top"]),
                    ("/store/fish/bin/fish", ["-fish", "-c", "true"])], "I: %r" % execs)
    os.environ.update(POISON); execs.clear()
    A.run("terminal", [], cfg()); A.run("shell", [], cfg())
    check([e[0] for e in execs] == ["/store/foot/bin/foot", "/store/fish/bin/fish"], "I: the environment steered the exec: %r" % execs)
    gone(STORE); execs.clear(); SYS.lines.clear()
    A.run("terminal", [], cfg()); A.run("shell", [], cfg())
    check([e[0] for e in execs] == ["/store/kitty/bin/kitty", "/store/bash/bin/bash"] and execs[1][1] == ["-bash"],
          "I: a missing store must mean the defaults: %r" % execs)
    check(len(SYS.lines) == 2 and all("using the default" in l for l in SYS.lines), "I: the fallback is not logged: %r" % SYS.lines)
    put(STORE, GOOD.replace('"foot"', '"/evil/term"')); execs.clear()
    A.run("terminal", [], cfg())
    check(execs[0][0] == "/store/kitty/bin/kitty", "I: a store value outside the list reached exec: %r" % execs)
finally:
    os.execv = real_execv
    for k, v in keep_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
ok("I the wrappers exec only from the compiled-in table; the environment and a bad store change nothing")

# J — the installed copy ignores AGOS_CHOICE_CONFIG
compiled = cfg(); compiled["defaults"] = dict(compiled["defaults"], terminal="alacritty")
src = open(MOD).read()
check(src.count('CONFIG_JSON = "@AGOS_CHOICE_CONFIG@"') == 1, "K: the substitution target must appear exactly once")
inst = src.replace('"@AGOS_CHOICE_CONFIG@"', json.dumps(json.dumps(compiled)))
I = types.ModuleType("agos_choice_installed"); exec(compile(inst, "agos_choice_installed.py", "exec"), I.__dict__)
evil = cfg(); evil["defaults"] = dict(evil["defaults"], terminal="ghostty"); evil["exec"]["terminal"]["ghostty"] = "/evil/term"
os.environ["AGOS_CHOICE_CONFIG"] = json.dumps(evil)
try:
    check(I.config()["defaults"]["terminal"] == "alacritty", "J: the installed copy read AGOS_CHOICE_CONFIG")
    gone(STORE)
    check(I.read_store()[0]["terminal"] == "alacritty", "J: read_store() in the installed copy used the environment")
finally:
    os.environ.pop("AGOS_CHOICE_CONFIG")
ok("J the installed copy ignores AGOS_CHOICE_CONFIG")

# K — a default that is not offered is refused; an unconfigured repo copy says so
bad = cfg(); bad["defaults"] = dict(bad["defaults"], shell="tcsh")
os.environ["AGOS_CHOICE_CONFIG"] = json.dumps(bad)
try:
    try:
        A.config(); raised = False
    except SystemExit:
        raised = True
    check(raised, "K: a default that is not offered was accepted")
    os.environ.pop("AGOS_CHOICE_CONFIG")
    try:
        A.config(); raised = False
    except SystemExit:
        raised = True
    check(raised, "K: an unconfigured copy did not refuse")
finally:
    os.environ.pop("AGOS_CHOICE_CONFIG", None)
ok("K a compiled-in default that is not offered, or no configuration at all, is refused at start")

# L — writes are atomic and synced
synced = []
real_fsync = os.fsync
os.fsync = lambda fd: (synced.append(os.fstat(fd).st_mode), real_fsync(fd))[1]
try:
    gone(STORE); put(TOKENS, "none foot bash\n", 0o600)
    A.apply(cfg())
finally:
    os.fsync = real_fsync
import stat as _stat
check(len(synced) == 2 and _stat.S_ISREG(synced[0]) and _stat.S_ISDIR(synced[1]), "L: fsync of the file then its directory: %r" % synced)
check(os.listdir(os.path.dirname(STORE)) == ["surface.toml"], "L: a temp file was left: %r" % os.listdir(os.path.dirname(STORE)))
ok("L writes are atomic: file and directory fsynced, no temp file left")

print("agos-choice-battery: PASS (%d criteria)" % len(passed))
