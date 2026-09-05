#!/usr/bin/env python3
# brain-stderr-journal-battery.py — arms for the IN-LOOP stderr->journal copy (#285 follow-up).
#
# WHY THIS EXISTS. #285 teed the brain's PRE-loop stderr into brain-home's journal. Its own
# firing table recorded the half it could not reach: A2, a write to sys.stderr INSIDE
# patch_stdout(raw=True), reached the screen and NOT the journal — because the guard replaces
# sys.stderr with a StdoutProxy onto stdout, so fd 2 never sees it and the unit's tee has
# nothing to copy. The router-leg latency lines ride that stream.
#
# The fix is one site at the guard, so these arms test the guard-side object, not the eleven
# sys.stderr.write call sites:
#   A1  installed over a proxy: the screen gets EVERY byte verbatim, the journal gets the
#       cooked line (ANSI stripped, one \n-terminated line).
#   A2  PERMITTING TWIN / control — when sys.stderr IS sys.__stderr__ (the nullcontext branch,
#       and the shape #285's tee already covers) NOTHING is installed and the write happens
#       exactly ONCE. Without this arm a copy that installed unconditionally would pass A1 and
#       silently double-write every pre-loop line into the journal.
#   A3  a partial line is HELD (a half-written line is not a line) and flushed on unwind.
#   A4  \r-redraw frames: the journal keeps the final frame only, never the repaints.
#   A5  a journal that raises does not break the screen write — an observer must not be able to
#       kill the turn it observes.
#   A6  isatty()/attribute pass-through: anything keying on the proxy sees the proxy.
#   A7  unwind restores sys.stderr to the proxy itself (not to __stderr__, which would leave the
#       guard's exit restoring the wrong object).
#   A8  PRE-FIX ARM — the same writes with NO copy installed reach the screen and produce ZERO
#       journal lines. Without it every arm above could pass on a journal that was never empty
#       to begin with, and the gap #285 measured would be unproven here.
#   A9  CALL-SITE ARM — main() actually enters journal_stderr_copy() on the same ExitStack, AFTER
#       the guard. A perfect tee that nothing installs is the failure this whole file is about
#       one level up; and entering it BEFORE the guard would unwind in the wrong order.
#
# Run: python3 tests/brain-stderr-journal-battery.py   (from the repo root)
# SIDE_EFFECTS — read as DATA by tests/vm-matrix-contract.py, not as a comment.
SIDE_EFFECTS = []

import importlib.util, io, os, py_compile, re, sys

MOD = "modules/agent-brain.py"
EX = 0
def check(name, cond, extra=""):
    global EX
    print(("  PASS " if cond else "  FAIL ") + name)
    if not cond:
        EX = 1
        if extra: print("    " + extra)

try:
    py_compile.compile(MOD, doraise=True); print("  PASS compile " + MOD)
except py_compile.PyCompileError as e:
    print("  FAIL compile " + MOD + ": " + str(e)); sys.exit(1)

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "modules"))
os.environ.setdefault("OLLAMA_MODEL", "qwen3.5:9b")
os.environ["AGENT_OS_PROVIDERS"] = "/nonexistent/providers.yaml"
spec = importlib.util.spec_from_file_location("agent_brain_stderr_test", MOD)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

class Proxy(io.StringIO):
    """Stands in for prompt_toolkit's StdoutProxy: a writable that is NOT sys.__stderr__."""
    def isatty(self): return True
    encoding = "utf-8"

class Journal(io.StringIO):
    pass

class Angry(io.StringIO):
    def write(self, d): raise OSError("journal gone")

REAL_ERR, REAL_UNDER = sys.stderr, sys.__stderr__

def run_with_proxy(writes, journal=None, close=True):
    """Install the copy over a fake proxy and replay `writes`. Returns (screen, journal, installed)."""
    proxy, jrn = Proxy(), (journal if journal is not None else Journal())
    sys.stderr = proxy
    sys.__stderr__ = jrn
    try:
        cm = brain.journal_stderr_copy()
        installed = cm.__enter__()
        try:
            for w in writes: sys.stderr.write(w)
            if not close:
                return proxy.getvalue(), jrn, installed, sys.stderr
        finally:
            if close: cm.__exit__(None, None, None)
        return proxy.getvalue(), jrn, installed, sys.stderr
    finally:
        if close:
            sys.stderr, sys.__stderr__ = REAL_ERR, REAL_UNDER

LINE = "\x1b[2m[front-door 26.3s]\x1b[0m\n"

# ── A1 ──
screen, jrn, installed, _ = run_with_proxy([LINE])
check("A1 installed over a proxy", installed is True)
check("A1 screen keeps every byte verbatim", screen == LINE, repr(screen))
check("A1 journal gets the cooked line", jrn.getvalue() == "[front-door 26.3s]\n", repr(jrn.getvalue()))

# ── A2 (permitting twin / control) ──
jrn = Journal()
sys.stderr = jrn; sys.__stderr__ = jrn
try:
    cm = brain.journal_stderr_copy(); installed = cm.__enter__()
    sys.stderr.write(LINE)
    cm.__exit__(None, None, None)
    once = jrn.getvalue()
finally:
    sys.stderr, sys.__stderr__ = REAL_ERR, REAL_UNDER
check("A2 no copy when stderr IS __stderr__", installed is False)
check("A2 the write happens exactly once", once == LINE, repr(once))

# ── A3 ──  (inline: the cm must stay referenced, or GC unwinds it early)
proxy, jrn = Proxy(), Journal()
sys.stderr = proxy; sys.__stderr__ = jrn
try:
    cm = brain.journal_stderr_copy(); cm.__enter__()
    sys.stderr.write("partial, no newline")
    held = jrn.getvalue()
    cm.__exit__(None, None, None)
    flushed = jrn.getvalue()
finally:
    sys.stderr, sys.__stderr__ = REAL_ERR, REAL_UNDER
check("A3 a partial line is held", held == "", repr(held))
check("A3 flushed on unwind", flushed == "partial, no newline\n", repr(flushed))

# ── A4 ──
screen, jrn, _, _ = run_with_proxy(["  frame1\r  frame2\r  final\n"])
check("A4 journal keeps the final frame only", jrn.getvalue() == "  final\n", repr(jrn.getvalue()))
check("A4 screen keeps every repaint", "frame1" in screen and "frame2" in screen)

# ── A5 ──
try:
    screen, _, _, _ = run_with_proxy([LINE], journal=Angry())
    survived = True
except Exception as e:
    survived = False; screen = ""
    sys.stderr, sys.__stderr__ = REAL_ERR, REAL_UNDER
check("A5 a raising journal does not break the write", survived)
check("A5 the screen still got the line", screen == LINE, repr(screen))

# ── A6 ──
proxy, jrn = Proxy(), Journal()
sys.stderr = proxy; sys.__stderr__ = jrn
try:
    cm = brain.journal_stderr_copy(); cm.__enter__()
    tee = sys.stderr
    passthru = (tee.isatty() is True and tee.encoding == "utf-8")
    cm.__exit__(None, None, None)
    restored = sys.stderr is proxy
finally:
    sys.stderr, sys.__stderr__ = REAL_ERR, REAL_UNDER
check("A6 isatty/attrs forward to the proxy", passthru)
# ── A7 ──
check("A7 unwind restores sys.stderr to the proxy", restored)

# ── A8 (pre-fix arm) ──
proxy, jrn = Proxy(), Journal()
sys.stderr = proxy; sys.__stderr__ = jrn
try:
    sys.stderr.write(LINE)        # the pre-fix world: guard installed, no copy
    prefix_screen, prefix_journal = proxy.getvalue(), jrn.getvalue()
finally:
    sys.stderr, sys.__stderr__ = REAL_ERR, REAL_UNDER
check("A8 pre-fix: screen YES", prefix_screen == LINE)
check("A8 pre-fix: journal NO (the gap #285 measured)", prefix_journal == "", repr(prefix_journal))

# ── A9 (call-site arm) ──
src = open(MOD, encoding="utf-8").read()
guard_at = src.find("_stack.enter_context(_guard)")
copy_at = src.find("_stack.enter_context(journal_stderr_copy())")
check("A9 main() enters the copy on the same stack", copy_at != -1,
      "nothing installs the tee — a perfect observer nobody wires up")
check("A9 entered AFTER the guard (so it unwinds first)", guard_at != -1 and copy_at > guard_at)

sys.exit(EX)
