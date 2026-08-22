#!/usr/bin/env python3
# escalate-consent-battery.py — task 323, Geist ruling 2026-08-22.
#
# Arms the `escalate` role's CONSENT semantics: "escalation to a metered cloud provider is a
# human act, never an inference." What that means operationally is that there must exist NO
# input — however hard, however long, however confidently the floor model fails — that routes a
# turn to the metered provider without an explicit operator act. That is a negative property,
# so most of what follows is negative controls: arms C and D are the ones that would catch a
# regression, not arm E.
#
# What it proves:
#   A. no consent → floor route, even with escalate configured and reachable (the default).
#   B. per-turn consent → ONE escalated turn, then back to floor (the grant is one-shot).
#   C. session consent → escalated on every turn until the session ends.
#   D. escalate provider unavailable → degrades to the LOCAL FLOOR with a visible reason,
#      never to another metered provider (the 2026-08-06 overnight-bleed rule). Armed with a
#      THIRD metered provider present in the config, so a spill has somewhere to go if the
#      never-spill rule is broken — a two-provider fixture cannot fail this test.
#   E. the audit record carries provider, model, role, consent_source and tokens.
#   F. consent: always is refused at import (SystemExit), not silently downgraded.
#   G. NEGATIVE CONTROL: the transport dispatcher honors the route it is handed, not the
#      module-level floor globals — the bug that would make every arm above pass while the
#      escalate role stayed unroutable.
#
# Zero network, zero model: _stream_events is exercised through a stub transport table.
# Run: PYTHONPATH=modules python3 tests/escalate-consent-battery.py
import importlib.util, json, os, subprocess, sys, tempfile, textwrap

MOD = os.path.join(os.path.dirname(__file__), "..", "modules", "agent-brain.py")
EX = 0

def check(name, cond, detail=""):
    global EX
    print(("  PASS " if cond else "  FAIL ") + name + (("\n      " + detail) if detail and not cond else ""))
    if not cond: EX = 1

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "modules"))

YAML = textwrap.dedent("""
    providers:
      local-ollama:  {kind: ollama, cost_tier: free, model: "qwen3.5:9b"}
      cloud-claude:  {kind: claude, cost_tier: metered, model: "claude-opus-5", api_key_ref: "env://ANTHROPIC_API_KEY"}
      other-metered: {kind: claude, cost_tier: metered, model: "some-other-paid-model", api_key_ref: "env://OTHER_KEY"}
    roles:
      floor: local-ollama
      escalate: cloud-claude
""")

def load_brain(consent="", providers_yaml=None, turn_log=None):
    os.environ["AGENT_OS_PROVIDERS"] = providers_yaml or "/nonexistent/providers.yaml"
    os.environ["AGENT_OS_ESCALATE_CONSENT"] = consent
    os.environ["OLLAMA_MODEL"] = "qwen3.5:9b"
    if turn_log: os.environ["AGENT_OS_TURN_LOG"] = turn_log
    spec = importlib.util.spec_from_file_location("agent_brain_consent_test", MOD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

tmp = tempfile.mkdtemp()
cfg = os.path.join(tmp, "providers.yaml")
open(cfg, "w").write(YAML)

# ── A. no consent → floor, unconditionally ────────────────────────────────────────────────
b = load_brain(consent="", providers_yaml=cfg)
check("escalate role is configured and reachable in the fixture",
      b.ESCALATE_STATUS["configured"] is True and b.ESCALATE_STATUS["provider"] == "cloud-claude",
      repr(b.ESCALATE_STATUS))
r = b._route_for_turn(b.CONSENT.consume())
check("A: no consent → floor route", r["role"] == "floor" and r["provider"] == "local-ollama", repr(r))
check("A: no consent → consent_source is null (not fabricated)", r["consent_source"] is None)
check("A: unarmed status line says so", "unarmed" in b.escalate_status_line(), b.escalate_status_line())

# ── B. per-turn consent is ONE turn ───────────────────────────────────────────────────────
b.CONSENT.arm_turn()
r1 = b._route_for_turn(b.CONSENT.consume())
r2 = b._route_for_turn(b.CONSENT.consume())
check("B: armed turn routes to escalate", r1["role"] == "escalate" and r1["provider"] == "cloud-claude", repr(r1))
check("B: escalated turn records consent_source='turn'", r1["consent_source"] == "turn")
check("B: escalated turn uses the ESCALATE provider's model, not the floor's",
      r1["model"] == "claude-opus-5", repr(r1))
check("B: the NEXT turn falls back to floor (grant is one-shot)", r2["role"] == "floor", repr(r2))

# ── C. session consent persists ───────────────────────────────────────────────────────────
bs = load_brain(consent="session", providers_yaml=cfg)
rs = [bs._route_for_turn(bs.CONSENT.consume()) for _ in range(3)]
check("C: session consent → every turn escalates", all(x["role"] == "escalate" for x in rs), repr(rs))
check("C: session-consented turns record consent_source='session'",
      all(x["consent_source"] == "session" for x in rs))
check("C: armed status line says ARMED", "ARMED" in bs.escalate_status_line(), bs.escalate_status_line())

# ── D. never-spill: unavailable escalate → LOCAL FLOOR, never the other metered provider ──
bs._ESCALATE_UNAVAILABLE.add("cloud-claude")
rd = bs._route_for_turn("session")
check("D: unavailable escalate degrades to the local floor", rd["provider"] == "local-ollama", repr(rd))
check("D: never spills onto the other metered provider", rd["provider"] != "other-metered", repr(rd))
check("D: the degrade is visible, not silent", bool(rd["degraded"]), repr(rd))
check("D: degraded turn is a floor turn, cost-wise", rd["role"] == "floor" and rd["consent_source"] is None)

# ── E. audit record shape ─────────────────────────────────────────────────────────────────
log = os.path.join(tmp, "turn-log.jsonl")
be = load_brain(consent="", providers_yaml=cfg, turn_log=log)
be.CONSENT.arm_turn()
be._log_turn_provenance(be._route_for_turn(be.CONSENT.consume()), 1234)
be._log_turn_provenance(be._route_for_turn(be.CONSENT.consume()), None)
recs = [json.loads(l) for l in open(log)]
check("E: two records written", len(recs) == 2, repr(recs))
if len(recs) == 2:
    esc, flr = recs
    check("E: escalated record names the cloud provider + model",
          esc["provider"] == "cloud-claude" and esc["model"] == "claude-opus-5", repr(esc))
    check("E: escalated record names role + consent source",
          esc["role"] == "escalate" and esc["consent_source"] == "turn", repr(esc))
    check("E: escalated record carries tokens", esc["tokens"] == 1234, repr(esc))
    check("E: floor record is honestly consent-free",
          flr["role"] == "floor" and flr["consent_source"] is None, repr(flr))
    check("E: unknown token count is null, never 0 (a 0 would read as a free turn)",
          flr["tokens"] is None, repr(flr))

# ── F. consent: always is refused at import, not downgraded ───────────────────────────────
env = dict(os.environ, AGENT_OS_PROVIDERS=cfg, AGENT_OS_ESCALATE_CONSENT="always")
p = subprocess.run([sys.executable, "-c",
                    "import importlib.util,sys;"
                    f"spec=importlib.util.spec_from_file_location('m',{MOD!r});"
                    "m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)"],
                   env=env, capture_output=True, text=True)
check("F: consent=always exits non-zero (refuse, don't downgrade)", p.returncode != 0, repr(p.returncode))
check("F: and says why", "always" in (p.stderr or ""), repr(p.stderr[-200:]))

# ── G. NEGATIVE CONTROL: the dispatcher honors the ROUTE, not the module globals ──────────
# Pre-323 _stream_events read ACTIVE_PROVIDER_KIND/ACTIVE_PROVIDER. With those globals pinned
# to the floor (they always are — floor is resolved at import), that bug leaves every arm above
# green while no turn can actually reach the cloud. This arm fails on that code.
seen = {}
def _stub(msgs, provider=None, _kind=None):
    seen["provider"] = provider; seen["kind"] = _kind
    yield ("done", {"eval_count": 0, "eval_seconds": 0.0})
bs2 = load_brain(consent="session", providers_yaml=cfg)
bs2._TRANSPORTS = {"ollama": lambda m, p=None: _stub(m, p, "ollama"),
                   "claude": lambda m, p=None: _stub(m, p, "claude")}
list(bs2._stream_events([], bs2._route_for_turn("session")))
check("G: escalate route dispatches on the CLOUD provider", seen.get("provider") == "cloud-claude", repr(seen))
check("G: escalate route dispatches on the CLOUD transport kind", seen.get("kind") == "claude", repr(seen))
seen.clear()
list(bs2._stream_events([], bs2._route_for_turn(None)))
check("G: no-consent route still dispatches on the local floor transport",
      seen.get("provider") == "local-ollama" and seen.get("kind") == "ollama", repr(seen))

# ── H. the internal `_usage` key never survives into the message history ─────────────────
# chat_stream tags its result with `_usage` so turn() can log a token count. turn() pops it
# before appending; if it ever stopped, an unknown key would ride the history straight into a
# provider payload — and would fail on the METERED side first, which is the worst place to
# find out.
bh = load_brain(consent="", providers_yaml=cfg, turn_log=os.path.join(tmp, "h.jsonl"))
bh.chat_stream_safe = lambda msgs, retries=1, route=None: {
    "role": "assistant", "content": "done", "tool_calls": [], "_usage": 7}
hist = []
bh.turn(hist)
check("H: turn() strips _usage before the message enters the history",
      hist and all("_usage" not in m for m in hist), repr(hist))
check("H: and the stripped count reaches the audit record",
      json.loads(open(os.path.join(tmp, "h.jsonl")).readline())["tokens"] == 7)

print("  " + ("ALL PASS" if EX == 0 else "FAILURES"))
sys.exit(EX)
