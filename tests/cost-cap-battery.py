#!/usr/bin/env python3
# cost-cap-battery.py — HARNESS-MAP guardrail 3: the cost-cap breaker in the 7B turn() loop.
#
# Proves the three layers of the breaker:
#   config  — providers.yaml `limits:` validation (fail-loud on garbage, absent → defaults
#             that preserve prior behavior exactly: 6 hops, no token ceiling);
#   trip    — a token-ceiling breach REFUSES the pending tool calls (do_tool never fires),
#             stubs one role:"tool" message per refused call so both transports' transcripts
#             stay well-formed, and logs a cost_cap_breaker event to the provenance log;
#   loud    — hop exhaustion (which always ended the turn, silently) now logs the same event.
#
# No ollama needed: chat_stream_safe and do_tool are monkeypatched. Same import pattern as
# frontdoor-kick-battery.py / wiring-battery.py. Needs pyyaml (fail loud if absent — the
# same K6 rule as wiring-battery: a SKIP would hide the silent-degrade regression).
#
# Run: PYTHONPATH=modules python3 tests/cost-cap-battery.py
# MUTATES_SHARED_STATE — Geist's law, 2026-09-05: a box-runnable battery never mutates
# human-shared state. Read as DATA by tests/vm-matrix-contract.py, not as a comment.
MUTATES_SHARED_STATE = False

import importlib.util, json, os, sys, tempfile, textwrap, py_compile

try:
    import yaml  # noqa: F401
except ImportError:
    print("  FAIL pyyaml present — providers.py needs it (see wiring-battery.py header)")
    sys.exit(1)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
BRAIN = os.path.join(ROOT, "modules", "agent-brain.py")
sys.path.insert(0, os.path.join(ROOT, "modules"))
import providers as P

EX = 0
def check(name, cond):
    global EX
    print(("  PASS " if cond else "  FAIL ") + name)
    if not cond: EX = 1

for f in (BRAIN, os.path.join(ROOT, "modules", "providers.py")):
    try:
        py_compile.compile(f, doraise=True); print("  PASS compile " + os.path.relpath(f, ROOT))
    except py_compile.PyCompileError as e:
        print("  FAIL compile " + f + ": " + str(e)); EX = 1

# ── layer 1: providers.py limits validation ─────────────────────────────────────
BASE = textwrap.dedent("""
    providers:
      local:
        kind: ollama
        cost_tier: free
    roles:
      floor: local
""")

def load_yaml(extra=""):
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write(BASE + extra)
    return f.name, P.load_providers(f.name)

_, cfg = load_yaml()
check("absent limits block → {} (defaults live in the brain)", cfg["limits"] == {})

_, cfg = load_yaml("limits:\n  max_hops_per_turn: 4\n  max_output_tokens_per_turn: 2000\n")
check("valid limits round-trip", cfg["limits"] == {"max_hops_per_turn": 4,
                                                   "max_output_tokens_per_turn": 2000})

for bad, why in [("limits:\n  max_hops_per_tern: 4\n", "unknown key rejected (typo-proof)"),
                 ("limits:\n  max_hops_per_turn: 0\n", "zero rejected"),
                 ("limits:\n  max_hops_per_turn: -3\n", "negative rejected"),
                 ("limits:\n  max_hops_per_turn: true\n", "bool rejected (int subclass trap)"),
                 ("limits:\n  max_output_tokens_per_turn: lots\n", "string rejected"),
                 ("limits: 5\n", "non-mapping limits rejected")]:
    try:
        load_yaml(bad); check(why, False)
    except P.ProviderConfigError:
        check(why, True)

# ── brain loading helper ────────────────────────────────────────────────────────
def load_brain(providers_yaml=None, env=None):
    os.environ.pop("AGENT_OS_MAX_TURN_HOPS", None)
    os.environ.pop("AGENT_OS_MAX_TURN_TOKENS", None)
    os.environ["AGENT_OS_PROVIDERS"] = providers_yaml or "/nonexistent/providers.yaml"
    os.environ["AGENT_OS_TURN_LOG"] = os.environ.get("_CCB_TURN_LOG", "/tmp/ccb-turn-log.jsonl")
    for k, v in (env or {}).items():
        os.environ[k] = v
    spec = importlib.util.spec_from_file_location("agent_brain_ccb", BRAIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

turn_log = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False).name
os.environ["_CCB_TURN_LOG"] = turn_log

# ── layer 1b: precedence yaml > env > default ───────────────────────────────────
brain = load_brain()
check("no config → hop default 6 (prior literal)", brain.MAX_TURN_HOPS == 6)
check("no config → no token ceiling (prior behavior)", brain.MAX_TURN_TOKENS is None)

brain = load_brain(env={"AGENT_OS_MAX_TURN_HOPS": "3", "AGENT_OS_MAX_TURN_TOKENS": "150"})
check("env path sets both ceilings", brain.MAX_TURN_HOPS == 3 and brain.MAX_TURN_TOKENS == 150)

lim_yaml, _ = load_yaml("limits:\n  max_hops_per_turn: 2\n  max_output_tokens_per_turn: 100\n")
brain = load_brain(lim_yaml, env={"AGENT_OS_MAX_TURN_TOKENS": "999999"})
check("yaml limits win over env", brain.MAX_TURN_HOPS == 2 and brain.MAX_TURN_TOKENS == 100)

try:
    load_brain(env={"AGENT_OS_MAX_TURN_TOKENS": "banana"})
    check("garbled env ceiling refuses to start", False)
except SystemExit as e:
    check("garbled env ceiling refuses to start", e.code == 1)

def breaker_events():
    with open(turn_log) as f:
        return [json.loads(l) for l in f if l.strip() and "cost_cap_breaker" in l]

def wants_tools(n_calls=1, tokens=10):
    return {"role": "assistant", "content": "", "_usage": tokens,
            "tool_calls": [{"type": "function", "function":
                            {"name": "run_command", "arguments": json.dumps({"command": "id"})}}
                           for _ in range(n_calls)]}

# ── layer 2: token trip refuses pending calls, stubs transcript, logs ───────────
brain = load_brain(lim_yaml)  # hops 2, tokens 100
fired = []
brain.do_tool = lambda name, args: fired.append(name) or "ok"
brain.chat_stream_safe = lambda msgs, route=None: wants_tools(n_calls=2, tokens=250)
msgs = [{"role": "user", "content": "go"}]
brain.turn(msgs)
check("token trip: pending tool calls NOT executed", fired == [])
stubs = [m for m in msgs if m.get("role") == "tool"]
check("token trip: one role:tool stub per refused call (transcript stays well-formed)",
      len(stubs) == 2 and all("COST-CAP BREAKER" in m["content"] for m in stubs))
check("token trip: _usage popped before transcript (shared key with provenance log)",
      all("_usage" not in m for m in msgs))
ev = breaker_events()
check("token trip: cost_cap_breaker event logged with kind=token",
      len(ev) == 1 and ev[0]["kind"] == "token" and ev[0]["output_tokens"] == 250)
# Interaction with the escalate-consent layer (#141/#142, same function neighbourhood):
# the breaker's hop counter rides on the SAME popped `_usage` value the per-hop provenance
# line consumes, and the breaker event must name the route that actually served the turn.
def prov_events():
    with open(turn_log) as f:
        return [json.loads(l) for l in f if l.strip() and "cost_cap_breaker" not in l]
pv = prov_events()
check("interaction: one provenance line per served hop, carrying the popped _usage",
      len(pv) == 1 and pv[0]["tokens"] == 250 and pv[0]["role"] == "floor")
check("interaction: breaker event carries the served route (provider/model/role/consent)",
      ev[0]["provider"] == pv[0]["provider"] and ev[0]["model"] == pv[0]["model"]
      and ev[0]["role"] == "floor" and ev[0]["consent_source"] is None)
# A transport that reports no usage (None) cannot trip the token ceiling (None is 0 spend,
# logged as null) — the breaker still ends the turn on the hop ceiling.
open(turn_log, "w").close()
brain = load_brain(lim_yaml)
brain.do_tool = lambda name, args: "ok"
brain.chat_stream_safe = lambda msgs, route=None: wants_tools(n_calls=1, tokens=None)
brain.turn([{"role": "user", "content": "go"}])
ev = breaker_events()
check("interaction: None usage never trips token cap (hop trip, output_tokens 0)",
      len(ev) == 1 and ev[0]["kind"] == "hop" and ev[0]["output_tokens"] == 0)
check("interaction: None usage logged as null in provenance, never 0",
      all(e["tokens"] is None for e in prov_events()))

# ── layer 3: hop exhaustion executes its tools but ends LOUD ────────────────────
open(turn_log, "w").close()
brain = load_brain(lim_yaml)  # hops 2, tokens 100
fired = []
brain.do_tool = lambda name, args: fired.append(name) or "ok"
brain.chat_stream_safe = lambda msgs, route=None: wants_tools(n_calls=1, tokens=10)  # never over token cap
msgs = [{"role": "user", "content": "go"}]
brain.turn(msgs)
check("hop trip: tools ran on every allowed hop (behavior preserved)", len(fired) == 2)
ev = breaker_events()
check("hop trip: cost_cap_breaker event logged with kind=hop",
      len(ev) == 1 and ev[0]["kind"] == "hop" and ev[0]["hops"] == 2)

# ── clean turn under both ceilings: breaker never fires ─────────────────────────
open(turn_log, "w").close()
brain = load_brain(lim_yaml)
brain.do_tool = lambda name, args: "ok"
answers = iter([wants_tools(n_calls=1, tokens=10),
                {"role": "assistant", "content": "done", "_usage": 10, "tool_calls": []}])
brain.chat_stream_safe = lambda msgs, route=None: next(answers)
msgs = [{"role": "user", "content": "go"}]
brain.turn(msgs)
check("clean turn: no breaker event", breaker_events() == [])
check("clean turn: final answer in transcript", msgs[-1]["content"] == "done")

print(("cost-cap-battery: ALL PASS" if EX == 0 else "cost-cap-battery: FAILURES"), file=sys.stderr)
sys.exit(EX)
