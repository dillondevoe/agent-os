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
_REAL_RUN = brain.subprocess.run       # captured BEFORE the stubs below; G1 needs the genuine ones
_REAL_POPEN = brain.subprocess.Popen   # subprocess.run() is implemented ON Popen, so restoring
                                       # run alone leaves the real one driving a stubbed Popen
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

# ── G: the SCHEME GATE. argv kills the shell and the Lua eval, but firefox reads a leading
#      `-` as a FLAG, and its flags are not inert (--remote-debugging-port hands localhost the
#      browser; --screenshot writes a file; -P swaps the profile). `url` is model-supplied and
#      the model reads untrusted pages, so this is reachable by prompt injection.
#      (Found by /security-review on the argv fix itself, 2026-08-30.)
for bad in ("--remote-debugging-port=9222", "--screenshot=/tmp/x.png", "-P evil",
            "file:///etc/shadow", "javascript:alert(1)", "", "ftp://example.com/x"):
    CALLS.clear()
    r = brain.do_tool("open_url", {"url": bad})
    check("G " + repr(bad) + " launches NOTHING", CALLS == [], "saw: " + repr(CALLS))
    check("G " + repr(bad) + " reports the refusal as data",
          isinstance(r, str) and "refus" in r.lower(), "returned: " + repr(r))
# and the control arm: an ordinary URL still opens, or the gate above is vacuous
for good in ("https://example.com/a?b=c", "http://example.com", "HTTPS://Example.COM/x"):
    CALLS.clear()
    brain.do_tool("open_url", {"url": good})
    argv_ok = [a for k, a, _ in CALLS if k == "Popen"]
    check("G control: " + repr(good) + " DOES launch firefox with the url",
          len(argv_ok) == 1 and good in argv_ok[0] and "firefox" in argv_ok[0][0],
          "saw: " + repr(CALLS))

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


# ══ G: THE SHELL THE BOX ACTUALLY HAS (P0, 2026-09-05) ═══════════════════════════════════
# rabbot-to-mirror P0: every run_command on the Dell died `[Errno 2] ... 'bash'`. NixOS has
# no /bin/bash and the brain service's PATH is not a login shell's. The ask was explicit —
# "add a battery arm on NixOS that runs `true` through it; a tool that has never run on the
# target is a claim." So G1 is an END-TO-END arm with the process layer UNSTUBBED: it runs a
# real command through the real resolved shell inside the nix build sandbox, which is the
# closest thing to the target this check can reach.
brain.subprocess.run = _REAL_RUN
brain.subprocess.Popen = _REAL_POPEN
check("G0 a shell resolved at import", bool(brain.SHELL) and os.path.exists(brain.SHELL or ""),
      "SHELL=" + repr(brain.SHELL))
_out = brain.do_tool("run_command", {"command": "printf hello-from-the-hand"})
check("G1 run_command executes for real and returns its stdout", _out == "hello-from-the-hand",
      "saw: " + repr(_out))
# G2: a failing command is DATA, not an exception the model has to narrate.
_out = brain.do_tool("run_command", {"command": "echo boom >&2; exit 3"})
check("G2 a failing command returns its stderr as data", "boom" in _out, "saw: " + repr(_out))
# G3 CONTROL/VACUITY ARM. Without it, G1 would also pass on a build that happened to have
# `bash` on PATH while the code still hardcoded the name — which is exactly the Dell's
# failure, invisible from any machine that has one. Force the no-shell world and assert the
# branch REPORTS rather than raising a FileNotFoundError.
_saved_shell = brain.SHELL
brain.SHELL = None
_out = brain.do_tool("run_command", {"command": "true"})
check("G3 no shell on the box -> reported as data, never an errno",
      _out.startswith("error:") and "no shell" in _out, "saw: " + repr(_out))
brain.SHELL = _saved_shell
# G4: the executing code must not name a shell binary literally. Comments may (the block
# above _resolve_shell explains the bug by naming `bash`), so read the stripped token stream.
# The name `bash` DOES legitimately appear in the executing code — inside _resolve_shell's
# candidate list, which is the fix. What must not survive is a shell-OUT that names a binary
# instead of using the resolved one, i.e. the `[<literal>, "-c", ...]` argv shape.
check("G4 no shell-out names its interpreter literally",
      not any(sig in _code for sig in ('"bash" , "-c"', '"sh" , "-c"', '"/bin/bash" , "-c"')),
      "a hardcoded `[<shell>, '-c', ...]` argv survives; it is resolved at import for a reason")
brain.subprocess.run = _run       # re-stub both for everything below
brain.subprocess.Popen = _popen

# ══ H: POWER IS A CAPABILITY (P0 ask b) ══════════════════════════════════════════════════
def agos_calls():
    return [list(a) for _k, a, _kw in CALLS
            if isinstance(a, (list, tuple)) and a and "agos-sys" in str(a[0])]
for verb, expect in (("reboot", "reboot"), ("poweroff", "poweroff")):
    CALLS.clear()
    brain.do_tool("system", {"action": "power", "value": verb})
    ac = agos_calls()
    check("H1 power " + verb + " -> exactly one agos-sys invocation", len(ac) == 1, repr(ac))
    if len(ac) == 1:
        check("H1 power " + verb + " -> argv is the fixed enum word", ac[0][1:] == ["power", expect],
              repr(ac[0]))
# H2 CONTROL ARM. Without it, a dispatcher that shelled out on ANY value would pass H1.
for bogus in ("halt", "", "reboot; id", "REBOOT --now"):
    CALLS.clear()
    r = brain.do_tool("system", {"action": "power", "value": bogus})
    check("H2 power " + repr(bogus) + " dispatches NOTHING", agos_calls() == [], repr(agos_calls()))
    check("H2 power " + repr(bogus) + " returns the refusal as data", "power takes" in r, repr(r))
# H3: the model must be TOLD the hand exists. An enum the dispatcher accepts but the schema
# hides is how "reboot is not a capability" was true while the code could do it.
_sys_line = [l for l in src.splitlines() if '"name":"system"' in l]
check("H3 system tool schema found", len(_sys_line) == 1)
if len(_sys_line) == 1:
    check("H3 'power' is in the description the model reads", "power" in _sys_line[0], _sys_line[0][:240])

# ══ I: LITERAL VERBS DO NOT REACH A MODEL (P1 ask 2) ═════════════════════════════════════
for text, want in (("reboot", "reboot"), ("Reboot.", "reboot"), ("  RESTART  ", "reboot"),
                   ("shut down", "poweroff"), ("poweroff!", "poweroff")):
    lv = brain.literal_verb(text)
    check("I1 " + repr(text) + " -> power " + want,
          lv is not None and lv[1].get("value") == want, repr(lv))
# I2 CONTROL ARM, and it is the load-bearing one: without it a matcher that returned `reboot`
# for everything passes every arm in I1. Exact-whole-utterance is the whole safety property.
for text in ("reboot the router when you get a chance", "rebooting", "why did it reboot",
             "", "reboot now", "shut down the vm"):
    check("I2 " + repr(text) + " is NOT a literal verb", brain.literal_verb(text) is None,
          repr(brain.literal_verb(text)))
# I3/I4: end to end through frontdoor_turn, with the HTTP layer recorded. I3 asserts a literal
# makes ZERO model calls; I4 is its control — a non-literal must still reach one, or I3 would
# pass on a front door that had stopped calling models at all.
HTTP = []
def _urlopen(req, *a, **kw):
    HTTP.append(getattr(req, "full_url", str(req)))
    raise OSError("no ollama in the battery")
brain.urllib.request.urlopen = _urlopen
CALLS.clear(); HTTP.clear()
_msgs = [{"role": "user", "content": "reboot"}]
brain.frontdoor_turn(_msgs)
check("I3 a literal verb makes NO model call", HTTP == [], repr(HTTP))
check("I3 a literal verb dispatches the capability", len(agos_calls()) == 1, repr(agos_calls()))
CALLS.clear(); HTTP.clear()
try:
    brain.frontdoor_turn([{"role": "user", "content": "reboot the router when you get a chance"}])
except Exception:
    pass
check("I4 CONTROL: a non-literal turn DOES reach a model", len(HTTP) >= 1, repr(HTTP))
check("I4 CONTROL: a non-literal turn dispatches no power", agos_calls() == [], repr(agos_calls()))

# I5 — THE TRANSPORT ASSUMPTION IS STATED AT THE TABLE (Augur, #277 review §3).
# The table's safety argument is "typing the bare word IS the act" — a claim about how the
# utterance ARRIVED, which nothing in this file can see or check. It is true today (one
# call site, the typed REPL; no ASR path anywhere in the module). If a voice/transcription
# front end is ever wired in, a mis-transcribed one-word utterance reboots the box with no
# model in the path, and every line of the table still reads as correct.
#
# ADJACENCY IS THE ARM, not the presence of the words. A rule with no trigger point does
# not fire at an unfamiliar surface, and the surface it must fire at is this table — so the
# note is required to sit in the contiguous comment block immediately above the definition,
# where an editor reaching for _LITERAL_VERBS cannot miss it. A file-wide grep would pass
# with the note stranded 900 lines away, which is the failure mode being guarded.
_lines = src.split("\n")
_tbl = [i for i, l in enumerate(_lines) if l.startswith("_LITERAL_VERBS = {")]
check("I5 the literal-verb table is defined exactly once", len(_tbl) == 1, repr(_tbl))
_i = _tbl[0] - 1
_block = []
while _i >= 0 and _lines[_i].lstrip().startswith("#"):
    _block.append(_lines[_i]); _i -= 1
# Comment markers stripped and whitespace collapsed BEFORE matching. A line-oriented match
# over hard-wrapped prose measures the wrapping as much as the content: "revisit here" is
# split across a line break in the shipped note, and the first version of this arm went red
# on a note that says exactly what it demands. That is this tree's own scar (a phrase
# grep that returned 0 for a heading its author had merged) applied before it could bite.
# REVERSED back into reading order: the block is collected walking UPWARD from the table,
# so joining it as-collected yields the note line-reversed and any multi-line phrase match
# fails on prose that plainly contains it. Caught by this arm going red against a note that
# says exactly what it asks for.
_block = " ".join(l.lstrip().lstrip("#") for l in reversed(_block))
_block = " ".join(_block.split()).lower()
check("I5 the transport assumption is named in the comment block AT the table",
      "typed transport" in _block, "not in the %d contiguous comment lines above" % len(_block.split("\n")))
check("I5 and it names the trigger that must send a reader here",
      ("voice" in _block or "asr" in _block) and "revisit here" in _block,
      "the note states an assumption but no moment at which to revisit it")

# I5b — CONTROL/VACUITY ARM. Without it, I5 would pass on a build whose comment scanner
# silently swept the WHOLE file (or the whole module docstring) rather than the block, and
# the adjacency property — the entire point — would go unmeasured.
check("I5b the scanned block is genuinely bounded, not the whole file",
      0 < len(_block) < len(src) / 4,
      "scanned %d chars of a %d-char file" % (len(_block), len(src)))

print(("  brain-dispatch: FAIL" if EX else "  brain-dispatch: all checks passed"))
sys.exit(EX)
