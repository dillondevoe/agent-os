#!/usr/bin/env python3
"""xml-toolcall-battery.py — a tool call that renders as prose is a CLAIM.

Rabbot's P1 item 2 (2026-08-31, from Dillon's photo of the Dell TUI at 06:44 CDT):
qwen3.5:9b emitted its call as XML/Hermes content —

    <tool_call><function=run_command><parameter=command>uptime</parameter></function></tool_call>

— which `extract_tools()`'s JSON-shaped fallback regex does not match. The block was
printed as text and the brain then NARRATED a system status it never ran. The defect is
not the rendering; it is that the model asked to act, nothing acted, and the next turn
spoke as though it had.

ARMS. A, B and C are the fix. D, E, F and G exist so a green run means something:

  A  the observed live shape yields ONE call, name `run_command`, with the command
     string intact — asserted as a (name, args) pair a caller can DISPATCH, which is
     what "execution, not text" means at this seam.
  B  the XML block is REMOVED from the returned clean text. If it survived, the brain
     would both run the tool and print the raw markup that asked for it.
  C  multiple parameters and multiple calls in one block all come back.
  D  CONTROL — structured `tool_calls` still win outright, and content is not consulted.
     Without this arm the new branch could have shadowed the normal path.
  E  CONTROL — the pre-existing JSON-shaped fallback still parses. The XML branch is
     reached only when JSON finds nothing; this arm is what proves it did not displace it.
  F  PRE-FIX ARM, the one that makes this file honest — the OLD extract_tools (the JSON
     regex ALONE, reconstructed here) is run on arm A's input and MUST return zero calls
     with the markup left in the text. Without it, A could be passing on an input the old
     code already handled, and this battery would be theatre.
  G  a parameter value containing `{` and `}` survives VERBATIM. `<parameter=command>`
     carries a shell string; json.loads'ing it would corrupt every command with a brace
     and would look like a passing parse.

Run standalone (tests/run-local.sh) and in the flake (xml-toolcall-contract).
"""
import importlib.util, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
MOD  = os.path.join(os.path.dirname(HERE), "modules", "agent-brain.py")

spec = importlib.util.spec_from_file_location("brain_xml", MOD)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

FAILS = []
def check(arm, cond, detail):
    print(("  ok   " if cond else "  FAIL ") + arm + " — " + detail)
    if not cond: FAILS.append(arm)

LIVE = ("Let me check the box.\n"
        "<tool_call>\n<function=run_command>\n"
        "<parameter=command>uptime && free -h && df -h / | head -5</parameter>\n"
        "</function>\n</tool_call>\n")

print("A/B — the live shape from the Dell TUI photo")
calls, clean = brain.extract_tools({"content": LIVE})
check("A", len(calls) == 1, "one call parsed, got %d: %r" % (len(calls), calls))
if len(calls) == 1:
    name, args = calls[0]
    check("A", name == "run_command", "name is run_command, got %r" % (name,))
    check("A", args.get("command") == "uptime && free -h && df -h / | head -5",
          "command string intact, got %r" % (args.get("command"),))
check("B", "<tool_call>" not in clean and "<function=" not in clean,
      "markup stripped from clean text, got %r" % (clean,))

print("C — several parameters, several calls")
multi = ("<tool_call><function=calculator><parameter=expression>2+3</parameter></function>"
         "<function=notes><parameter=action>add</parameter><parameter=text>hi</parameter></function></tool_call>")
calls, _ = brain.extract_tools({"content": multi})
check("C", [n for n, _ in calls] == ["calculator", "notes"],
      "both calls in order, got %r" % ([n for n, _ in calls],))
check("C", calls and calls[-1][1] == {"action": "add", "text": "hi"},
      "both parameters of the second call, got %r" % (calls[-1][1] if calls else None,))

print("D — CONTROL: structured tool_calls still win")
calls, clean = brain.extract_tools({
    "tool_calls": [{"function": {"name": "calculator", "arguments": {"expression": "1+1"}}}],
    "content": LIVE})
check("D", calls == [("calculator", {"expression": "1+1"})],
      "structured call returned unchanged, got %r" % (calls,))
check("D", clean == "", "content not consulted when structured calls exist, got %r" % (clean,))

print("E — CONTROL: the JSON-shaped fallback is not displaced")
calls, _ = brain.extract_tools({"content": 'sure: {"name": "calculator", "arguments": {"expression": "7*6"}}'})
check("E", calls == [("calculator", {"expression": "7*6"})],
      "JSON fallback still parses, got %r" % (calls,))

print("F — PRE-FIX ARM: the old JSON-only fallback on arm A's input")
def old_extract(c):
    out = []
    for m in re.finditer(r'\{[^{}]*"name"\s*:\s*"(\w+)"[^{}]*"arguments"\s*:\s*(\{[^{}]*\})[^{}]*\}', c):
        try: out.append((m.group(1), json.loads(m.group(2))))
        except Exception: pass
    return out
old = old_extract(LIVE)
check("F", old == [], "old code found NO call in the live shape (the bug), got %r" % (old,))

print("G — a brace in a parameter value survives verbatim")
braced = ("<tool_call><function=run_command>"
          "<parameter=command>awk '{print $1}' /etc/hostname</parameter>"
          "</function></tool_call>")
calls, _ = brain.extract_tools({"content": braced})
check("G", calls and calls[0][1].get("command") == "awk '{print $1}' /etc/hostname",
      "shell braces intact, got %r" % (calls[0][1].get("command") if calls else None,))

if FAILS:
    print("\nFAILED arms: " + ", ".join(sorted(set(FAILS)))); sys.exit(1)
print("\nall arms green")
