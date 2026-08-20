#!/usr/bin/env python3
"""TTSR battery — Think-Twice Stream Rules (HARNESS-MAP guardrail 4).

Drives the REAL seam: providers.load_providers → agent-brain boot → chat_stream_safe →
_chat_stream_locked → chat_stream → _stream_events. Only the transport generator is
scripted (it would otherwise need a live ollama); everything between the yaml and the
returned assistant msg is the shipped code. Mirror's lesson (three green batteries, no
loop): a composition test that fakes the composition re-proves the halves.

Cases (from ~/dvo-cache/authored/ttsr-scope-2026-08-20.md):
  1 no rules → byte-identical passthrough, transport called once with the caller's list
  2 rule fires → retry with the rule injected as role:system on the WIRE copy only
  3 retry cap: per-rule max_retries AND per-call total — no infinite loop, partial returned, no tools
  4 aborted attempts' tokens reach _out_tokens → the #114 cost cap trips on them
  5 rule never in the caller's msgs (compaction-proof by construction)
  6 invalid rule fails boot LOUD (providers.py + brain)
  7 anthropic path: the translator hoists the injected system msg into `system`
  8 regex straddling chunks fires (buffer search, not piece search)
"""
import contextlib, importlib.util, io, json, os, py_compile, sys, tempfile, textwrap, threading

HERE = os.path.dirname(os.path.abspath(__file__))
MODULES = os.path.join(HERE, "..", "modules")
BRAIN = os.path.join(MODULES, "agent-brain.py")
PROV = os.path.join(MODULES, "providers.py")
sys.path.insert(0, MODULES)

EX = 0
def check(name, cond):
    global EX
    print(("  ✓ " if cond else "  ✗ ") + name)
    if not cond: EX = 1

py_compile.compile(PROV, doraise=True)
py_compile.compile(BRAIN, doraise=True)
import providers as P

BASE = textwrap.dedent("""
    providers:
      local:
        kind: ollama
        cost_tier: free
    roles:
      floor: local
""")
RULES = textwrap.dedent("""
    stream_rules:
      - id: no-bad
        pattern: "BAD"
        rule: "Do not say BAD. Rephrase."
""")

def load_yaml(extra=""):
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write(BASE + extra)
    return f.name

# ── layer 1: providers.py validation ─────────────────────────────────────────────
print("providers.py stream_rules validation")
cfg = P.load_providers(load_yaml())
check("absent stream_rules → []", cfg["stream_rules"] == [])
cfg = P.load_providers(load_yaml(RULES))
check("valid rule round-trips", cfg["stream_rules"] == [{"id": "no-bad", "pattern": "BAD",
                                                         "rule": "Do not say BAD. Rephrase."}])
for bad, why in [
    ("stream_rules:\n  id: x\n", "non-list rejected"),
    ("stream_rules:\n  - id: x\n    pattern: a\n", "missing rule rejected"),
    ("stream_rules:\n  - id: x\n    pattern: a\n    rule: r\n    patern: b\n", "unknown key rejected (typo-proof)"),
    ("stream_rules:\n  - id: x\n    pattern: a\n    rule: r\n  - id: x\n    pattern: b\n    rule: r\n", "duplicate id rejected"),
    ("stream_rules:\n  - id: x\n    pattern: '('\n    rule: r\n", "invalid regex rejected at LOAD, not mid-stream"),
    ("stream_rules:\n  - id: x\n    pattern: a\n    rule: r\n    max_retries: true\n", "max_retries bool trap rejected"),
    ("stream_rules:\n  - id: x\n    pattern: a\n    rule: r\n    max_retries: 0\n", "max_retries 0 rejected"),
    ("stream_rules:\n  - id: ''\n    pattern: a\n    rule: r\n", "empty id rejected"),
]:
    try:
        P.load_providers(load_yaml(bad)); check(why, False)
    except P.ProviderConfigError:
        check(why, True)

# ── brain harness ───────────────────────────────────────────────────────────────
turn_log = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False).name

def load_brain(providers_yaml=None):
    os.environ.pop("AGENT_OS_MAX_TURN_HOPS", None); os.environ.pop("AGENT_OS_MAX_TURN_TOKENS", None)
    os.environ["AGENT_OS_PROVIDERS"] = providers_yaml or "/nonexistent/providers.yaml"
    os.environ["AGENT_OS_TURN_LOG"] = turn_log
    spec = importlib.util.spec_from_file_location("agent_brain_ttsr", BRAIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # spinner threads write to the tty; neuter them so the battery is headless-clean
    class _T:
        def join(self, timeout=None): pass
    mod._spin = lambda render, interval=0.35: (threading.Event(), _T())
    return mod

def script(brain, attempts):
    """Replace ONLY the transport. `attempts` is a list of event-lists (one per call) or a
    callable(n)->event-list for unbounded scripts. Records the msgs each call received."""
    calls = []
    def fake(msgs):
        calls.append(json.loads(json.dumps(msgs)))  # deep copy: what the wire saw
        ev = attempts(len(calls)-1) if callable(attempts) else attempts[len(calls)-1]
        for e in ev:
            yield e
    brain._stream_events = fake
    return calls

def run(brain, msgs):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        msg = brain.chat_stream_safe(msgs)
    return msg, out.getvalue()

def events(kind="ttsr_abort"):
    with open(turn_log) as f:
        return [json.loads(l) for l in f if l.strip() and json.loads(l).get("event") == kind]

def content(text, eval_count=None, tool_calls=None, pieces=None):
    ev = [("content", p) for p in (pieces or [text])]
    if tool_calls: ev.append(("tool_calls", tool_calls))
    if eval_count is not None: ev.append(("done", {"eval_count": eval_count, "eval_seconds": 1.0}))
    return ev

# case 1 — no rules
print("case 1: no rules → passthrough")
brain = load_brain(load_yaml())
check("boot with no stream_rules → STREAM_RULES == []", brain.STREAM_RULES == [])
calls = script(brain, [content("hello BAD world", 9)])
msgs = [{"role": "user", "content": "hi"}]
msg, _ = run(brain, msgs)
check("content passes through byte-identical", msg["content"] == "hello BAD world")
check("transport called exactly once", len(calls) == 1)
check("wire == caller's msgs (no copy, no injection)", calls[0] == msgs and len(msgs) == 1)
check("_out_tokens is the transport's eval_count", msg["_out_tokens"] == 9)
check("no ttsr_abort event logged", events() == [])

# case 2 — fire → retry with ephemeral rule; case 5 — never in caller's msgs
print("case 2/5: rule fires → retry with injected rule; caller's msgs untouched")
brain = load_brain(load_yaml(RULES))
check("boot compiles the rule", [r["id"] for r in brain.STREAM_RULES] == ["no-bad"])
calls = script(brain, [content("hello BAD more text here"), content("hello good", 7)])
msgs = [{"role": "system", "content": "you are X"}, {"role": "user", "content": "hi"}]
before = json.loads(json.dumps(msgs))
msg, out = run(brain, msgs)
check("final content is the retry's", msg["content"] == "hello good")
check("transport called twice", len(calls) == 2)
check("attempt 1 saw the caller's msgs unmodified", calls[0] == before)
check("attempt 2 saw msgs + ONE trailing role:system rule",
      calls[1][:-1] == before and calls[1][-1] == {"role": "system", "content": "Do not say BAD. Rephrase."})
check("[5] caller's msgs NOT appended to (rule is ephemeral / compaction-proof)", msgs == before)
check("[5] rule text appears nowhere in caller's msgs", all("Rephrase" not in m["content"] for m in msgs))
ev = events()
check("one ttsr_abort event, rule id, fire_n 1, gave_up False",
      len(ev) == 1 and ev[0]["rule"] == "no-bad" and ev[0]["fire_n"] == 1 and ev[0]["gave_up"] is False)
est = max(1, len("hello BAD more text here") // 4)   # one piece → the whole buffer is on the wire at fire time
check("_out_tokens = retry eval_count + aborted estimate", msg["_out_tokens"] == 7 + est)
check("tty got the think-twice line", "think-twice" in out)

# case 3 — retry cap
print("case 3: bounded retries")
brain = load_brain(load_yaml(RULES))   # max_retries default 1
calls = script(brain, lambda n: content("BAD"))   # unbounded script: always fires
msgs = [{"role": "user", "content": "hi"}]
msg, out = run(brain, msgs)
check("per-rule cap: 1 retry → exactly 2 transport calls, no infinite loop", len(calls) == 2)
check("gave up → partial returned, NO tool calls", msg["content"] == "BAD" and msg["tool_calls"] == [])
check("gave-up event logged with gave_up True", any(e["gave_up"] is True and e["fire_n"] == 2 for e in events()))
check("tty got the loud give-up banner", "THINK-TWICE" in out and "giving up" in out)
check("caller's msgs still untouched after give-up", msgs == [{"role": "user", "content": "hi"}])
# per-call total cap across rules (none individually exhausted)
three = textwrap.dedent("""
    stream_rules:
      - {id: a, pattern: "AAA", rule: "no a", max_retries: 5}
      - {id: b, pattern: "BBB", rule: "no b", max_retries: 5}
      - {id: c, pattern: "CCC", rule: "no c", max_retries: 5}
""")
brain = load_brain(load_yaml(three))
calls = script(brain, lambda n: content(["AAA", "BBB", "CCC"][n % 3]))
msg, _ = run(brain, [{"role": "user", "content": "hi"}])
check("per-call total cap: MAX_TTSR_ABORTS_PER_CALL+1 calls then stop",
      len(calls) == brain.MAX_TTSR_ABORTS_PER_CALL + 1)
check("each fired rule injected once, in first-fire order",
      [m["content"] for m in calls[-1] if m["role"] == "system"] == ["no a", "no b", "no c"])

# case 4 — aborted tokens reach the cost cap (#114 seam)
print("case 4: aborted tokens feed the cost-cap breaker")
brain = load_brain(load_yaml(RULES + "limits:\n  max_output_tokens_per_turn: 12\n"))
check("token ceiling loaded", brain.MAX_TURN_TOKENS == 12)
long_bad = "x" * 39 + " BAD"           # 43 chars → est 10 tokens
tc = [{"type": "function", "function": {"name": "run_command", "arguments": json.dumps({"command": "id"})}}]
script(brain, [content(long_bad), content("ok", 5, tool_calls=tc)])   # 10 + 5 = 15 ≥ 12
ran = []
brain.do_tool = lambda name, args: ran.append(name) or "unused"
msgs = [{"role": "user", "content": "hi"}]
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    brain.turn(msgs)
check("breaker tripped on aborted+final tokens → pending tool NOT executed", ran == [])
check("cost_cap_breaker event logged with output_tokens 15",
      any(e["output_tokens"] == 15 for e in events("cost_cap_breaker")))
check("transcript well-formed: tool stub appended for the refused call",
      msgs[-1]["role"] == "tool" and "refused" in msgs[-1]["content"])
# control arm: same stream, no rule → 5 tokens < 12 → tool runs
brain = load_brain(load_yaml("limits:\n  max_output_tokens_per_turn: 12\n"))
script(brain, [content(long_bad, 3, tool_calls=tc), content("done", 1)])
ran = []
brain.do_tool = lambda name, args: ran.append(name) or "unused"
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    brain.turn([{"role": "user", "content": "hi"}])
check("control: without the rule the same budget lets the tool run", ran == ["run_command"])

# case 6 — invalid rule fails boot LOUD
print("case 6: invalid rule refuses to start")
try:
    with contextlib.redirect_stderr(io.StringIO()):
        load_brain(load_yaml("stream_rules:\n  - id: x\n    pattern: '('\n    rule: r\n"))
    check("brain boot with invalid regex exits", False)
except SystemExit as e:
    check("brain boot with invalid regex exits non-zero", e.code == 1)

# case 7 — anthropic path hoists the injected rule into `system`
print("case 7: anthropic translator hoists the ephemeral system msg")
brain = load_brain(load_yaml(RULES))
calls = script(brain, [content("BAD"), content("fine", 2)])
run(brain, [{"role": "system", "content": "base prompt"}, {"role": "user", "content": "hi"}])
system, amsgs = brain._anthropic_translate_messages(calls[1])
check("rule lands in `system` after the base prompt", system.endswith("Do not say BAD. Rephrase.") and system.startswith("base prompt"))
check("no stray message for the rule", [m["role"] for m in amsgs] == ["user"])

# case 8 — regex straddling chunks
print("case 8: pattern split across chunks still fires")
brain = load_brain(load_yaml(RULES))
calls = script(brain, [content(None, pieces=["hel", "lo B", "A", "D!"]), content("ok", 1)])
msg, _ = run(brain, [{"role": "user", "content": "hi"}])
check("fired across 'B'/'A'/'D' pieces → retried", len(calls) == 2 and msg["content"] == "ok")
check("event est reflects buffer at fire time", events()[-1]["output_tokens_est"] == max(1, len("hello BAD") // 4))

print("ALL GREEN" if EX == 0 else "FAILURES")
sys.exit(EX)
