#!/usr/bin/env python3
# brain-dispatch-battery.py — the RED ARM for agent-brain.py's desktop hands (item 4, PR #228).
#
# WHY THIS EXISTS. Hyprland 0.56's Lua config changes what `hyprctl dispatch` MEANS: under a
# hyprland.lua it EVALUATES its argument as Lua (`hl.dispatch(<arg>)`) rather than parsing a
# hyprlang command word. `open_url` used to build `hyprctl dispatch exec "firefox --new-window
# <url>"` from a MODEL-SUPPLIED url. Translated mechanically, that url would have landed inside a
# Lua expression the compositor evaluates — a crafted url closes the string literal and runs
# arbitrary Lua in the process that owns the display. The ruling (Rabbot, 2026-08-30) was to
# REMOVE the sink rather than escape it, because an escaper is "wrong once and wrong forever".
#
# So this battery does not test an escaper. It asserts the sink is UNREACHABLE:
#   A. open_url with a Lua-escape payload in the url invokes NO hyprctl at all, and hands the url
#      to firefox as ONE literal argv element (not concatenated, not shell-parsed).
#   B. open_url uses Popen with a LIST — never a string, never shell=True.
#   C. arrange_windows on each of the four enum keys dispatches exactly one hyprctl with the
#      TABLE's value, and the caller's string never reaches the command line except as a dict key.
#   D. arrange_windows on an UNKNOWN key dispatches NOTHING (the control arm — without it, a
#      function that dispatched nothing ever would pass C's "no injection" half vacuously).
#   E. the HYPR table is closed and contains no interpolation placeholder ('{' / '%s' / '+').
#   F. 'tidy' is absent from the table AND from the tool description the model reads — an enum the
#      model is told about but that the dispatcher rejects is a worse failure than no enum.
#
# Zero compositor required: subprocess.Popen/run are stubbed and every invocation recorded.
# Run: python3 tests/brain-dispatch-battery.py   (from the repo root)
import importlib.util, os, py_compile, sys

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
spec = importlib.util.spec_from_file_location("agent_brain_dispatch_test", MOD)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

# ── stub the process layer; record every invocation verbatim ──
CALLS = []
class _Done:
    returncode = 0; stdout = ""; stderr = ""
def _popen(argv, *a, **kw):
    CALLS.append(("Popen", argv, kw)); return _Done()
def _run(argv, *a, **kw):
    CALLS.append(("run", argv, kw)); return _Done()
brain.subprocess.Popen = _popen
brain.subprocess.run = _run

def hyprctl_calls():
    out = []
    for _, argv, _kw in CALLS:
        if isinstance(argv, (list, tuple)) and argv and "hyprctl" in str(argv[0]):
            out.append(list(argv))
        elif isinstance(argv, str) and "hyprctl" in argv:
            out.append([argv])
        # a `bash -c "...hyprctl..."` shape is a hyprctl call too, and one with a shell in it
        elif isinstance(argv, (list, tuple)) and any("hyprctl" in str(x) for x in argv):
            out.append(list(argv))
    return out

# ── A + B: the eval sink is unreachable from open_url ──
PAYLOAD = 'https://example.com/") ; os.execute("touch /tmp/pwned") --'
CALLS.clear()
brain.do_tool("open_url", {"url": PAYLOAD})
check("A open_url issues ZERO hyprctl invocations", hyprctl_calls() == [],
      "saw: " + repr(hyprctl_calls()))
argvs = [argv for kind, argv, _ in CALLS if kind == "Popen"]
check("B open_url calls Popen with a LIST (no shell string)",
      len(argvs) == 1 and isinstance(argvs[0], list), "saw: " + repr(argvs))
if argvs and isinstance(argvs[0], list):
    av = argvs[0]
    check("A2 payload url is ONE literal argv element, unmodified", PAYLOAD in av,
          "argv: " + repr(av))
    check("A3 argv[0] is the browser, not hyprctl", "firefox" in av[0], "argv0: " + repr(av[0]))
    check("A4 no element concatenates the url into a larger string",
          all(x == PAYLOAD or PAYLOAD not in x for x in av), "argv: " + repr(av))
kwargs = [kw for kind, _, kw in CALLS if kind == "Popen"]
check("B2 open_url never passes shell=True", all(not kw.get("shell") for kw in kwargs))

# ── C: the closed enum dispatches the TABLE's value ──
for act, expected in brain.HYPR.items():
    CALLS.clear()
    brain.do_tool("arrange_windows", {"action": act})
    hc = hyprctl_calls()
    check("C " + act + " -> exactly one hyprctl dispatch", len(hc) == 1, "saw: " + repr(hc))
    if len(hc) == 1:
        check("C " + act + " -> dispatches the table value verbatim", expected in hc[0],
              "saw: " + repr(hc[0]))
        check("C " + act + " -> no shell interpreter in the argv",
              not any(x in ("bash", "sh", "-c") for x in hc[0]), "saw: " + repr(hc[0]))

# ── D: CONTROL ARM. Without this, a do_tool that dispatched nothing at all would satisfy A ──
for bogus in ("tidy", "definitely-not-a-real-action", 'x"); os.execute("id'):
    CALLS.clear()
    r = brain.do_tool("arrange_windows", {"action": bogus})
    check("D unknown key " + repr(bogus) + " dispatches NOTHING", hyprctl_calls() == [],
          "saw: " + repr(hyprctl_calls()))
    check("D unknown key " + repr(bogus) + " reports the error as data",
          isinstance(r, str) and "unknown" in r.lower(), "returned: " + repr(r))

# ── E: the table itself carries no interpolation seam ──
check("E HYPR values contain no format placeholder",
      all(("{" not in v) and ("%" not in v) for v in brain.HYPR.values()),
      "table: " + repr(brain.HYPR))
check("E HYPR is a plain dict of str->str",
      isinstance(brain.HYPR, dict) and all(isinstance(k, str) and isinstance(v, str)
                                           for k, v in brain.HYPR.items()))

# ── F: 'tidy' is gone from BOTH the dispatcher and the description the model reads ──
check("F 'tidy' absent from HYPR", "tidy" not in brain.HYPR)
src = open(MOD, encoding="utf-8").read()
desc_line = [l for l in src.splitlines() if '"name":"arrange_windows"' in l]
check("F arrange_windows tool schema found", len(desc_line) == 1)
if len(desc_line) == 1:
    check("F 'tidy' absent from the arrange_windows description the model is shown",
          "'tidy'" not in desc_line[0], desc_line[0][:200])
# CODE only, not prose: the comment above HYPR deliberately NAMES the old hyprlang verb it
# replaced, and a naive grep over the whole file would flag that explanation forever — which
# would push the next author to delete the explanation to get the check green. Strip comments
# with tokenize so the check reads what actually executes. (This arm caught its own battery on
# first run, which is the only reason the distinction is written down here.)
import tokenize, io as _io
_code = "".join(
    tok.string + " " for tok in tokenize.generate_tokens(_io.StringIO(src).readline)
    if tok.type not in (tokenize.COMMENT, tokenize.NL))
check("F no residual hyprlang dispatch verbs in the executing code",
      not any(v in _code for v in ("killactive", "cyclenext", "layoutmsg orientationcycle")),
      "a hyprlang verb survives; under a .lua config it is evaluated, not parsed")

print(("  brain-dispatch: FAIL" if EX else "  brain-dispatch: all checks passed"))
sys.exit(EX)
