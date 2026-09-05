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
#   H. the internal `_usage`/`_route` keys never survive into the message history.
#   I. NEGATIVE CONTROL (Geist gate, PR #141 F1): an in-flight 429 on the escalate provider
#      degrades to the floor AND the audit record says so — the record names the route that
#      SERVED the call, not the one turn() asked for. On the pre-gate code this arm fails:
#      the record claims a cloud spend (role=escalate, consent_source set) that never happened.
#   J. NEGATIVE CONTROL (Geist gate, PR #141 F2): timeout-then-429 on the escalate provider
#      still returns a message. Pre-gate, the degrade consumed a retry attempt, the loop fell
#      off the end, chat_stream_safe returned None and turn() crashed on msg.pop.
#
# Zero network, zero model: _stream_events is exercised through a stub transport table.
# Run: python3 tests/escalate-consent-battery.py   (from the repo root)
#
# Wired into .github/workflows/flake-check.yml as "escalate consent battery". THAT STEP IS THE
# AUTHORITATIVE INVOCATION and this line exists to agree with it, not to compete with it.
#
# The `PYTHONPATH=modules` this carried was inert — MOD is an absolute path built from
# __file__ and loaded via spec_from_file_location, so the environment never mattered. Fixed in
# the wiring commit rather than after it: per PR #189, a Run: header can only diverge from the
# workflow once the file is WIRED, so that is the moment to make them agree.
# SIDE_EFFECTS — Geist's law as amended 2026-09-05T13:05Z: a box-runnable battery declares
# every effect that leaves the machine or outlives the run. Read as DATA by
# tests/vm-matrix-contract.py, not as a comment.
SIDE_EFFECTS = []

import importlib.util, io, json, os, subprocess, sys, tempfile, textwrap, urllib.error

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

def load_brain(consent="", providers_yaml=None, turn_log=None, key=True):
    os.environ["AGENT_OS_PROVIDERS"] = providers_yaml or "/nonexistent/providers.yaml"
    os.environ["AGENT_OS_ESCALATE_CONSENT"] = consent
    os.environ["OLLAMA_MODEL"] = "qwen3.5:9b"
    # The startup preflight (2026-09-01) RESOLVES the escalate provider's api_key_ref at import
    # and marks the provider UNAVAILABLE when it cannot. The fixture above refs
    # env://ANTHROPIC_API_KEY, which is unset in CI, so every arm below silently became a FLOOR
    # arm: eight checks failed and the subject of this battery — consent routing — stopped being
    # exercised at all. `key=` makes the secret's presence an explicit fixture input rather than
    # an accident of the environment, and arm L below tests BOTH settings of it.
    for var in ("ANTHROPIC_API_KEY", "OTHER_KEY"):
        if key: os.environ[var] = "sk-fixture-not-a-real-key"
        else:   os.environ.pop(var, None)
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
      hist and all("_usage" not in m and "_route" not in m for m in hist), repr(hist))
check("H: and the stripped count reaches the audit record",
      json.loads(open(os.path.join(tmp, "h.jsonl")).readline())["tokens"] == 7)

# ── I. NEGATIVE CONTROL: in-flight degrade → the audit record names the SERVED route ─────
# Simulates the escalate provider answering 429 on the first call of a session-consented
# turn. chat_stream_safe must degrade to the floor (never-spill), and turn() must log the
# floor route — logging the escalate route it asked for would be an audit record that claims
# money was spent on a turn the local model answered.
ilog = os.path.join(tmp, "i.jsonl")
bi = load_brain(consent="session", providers_yaml=cfg, turn_log=ilog)
calls = []
def _cs_429_then_floor(msgs, route=None):
    calls.append(route["role"])
    if route["role"] == "escalate":
        raise urllib.error.HTTPError("https://api.example/", 429, "rate limited", {}, None)
    return {"role": "assistant", "content": "ok", "tool_calls": [], "_usage": 3}
bi.chat_stream = _cs_429_then_floor
hist = []
bi.turn(hist, "session")
irec = json.loads(open(ilog).readline())
check("I: in-flight 429 on escalate → audit record says FLOOR, not cloud",
      irec["role"] == "floor" and irec["provider"] == "local-ollama", repr(irec))
check("I: ... and consent_source is null for the floor-served call", irec["consent_source"] is None, repr(irec))
check("I: ... and the floor's token count is what got recorded", irec["tokens"] == 3, repr(irec))
check("I: escalate provider is marked unavailable for the session", "cloud-claude" in bi._ESCALATE_UNAVAILABLE)
check("I: exactly one cloud attempt, then the floor — no hammering", calls == ["escalate", "floor"], repr(calls))
check("I: _route never enters the history", hist and all("_route" not in m for m in hist), repr(hist))

# ── J. NEGATIVE CONTROL: timeout-then-429 must not fall off the retry loop ───────────────
bj = load_brain(consent="session", providers_yaml=cfg)
seq = ["timeout", "429", "ok"]
def _cs_seq(msgs, route=None):
    s = seq.pop(0)
    if s == "timeout": raise TimeoutError("cold prefill")
    if s == "429": raise urllib.error.HTTPError("https://api.example/", 429, "rate limited", {}, None)
    return {"role": "assistant", "content": "ok", "tool_calls": []}
bj.chat_stream = _cs_seq
mj = bj.chat_stream_safe([], route=bj._route_for_turn("session"))
check("J: timeout-then-429 on escalate still returns a message (not None)",
      isinstance(mj, dict) and mj.get("content") == "ok", repr(mj))
check("J: ... served by the floor (degraded, never spilled)",
      isinstance(mj, dict) and (mj.get("_route") or {}).get("role") == "floor"
      and (mj.get("_route") or {}).get("provider") == "local-ollama", repr(mj))

# ── K. REGRESSION CONTROL: a FLOOR-role HTTPError still degrades gracefully, never a traceback ──
# chat_stream_safe's founding contract is "never crash to a raw traceback on tty1" (P1 fix #1).
# HTTPError subclasses URLError, so 5f00bd6's separate `except urllib.error.HTTPError` clause —
# added above the generic handler purely to catch the escalate degrade — silently captured
# FLOOR-role HTTP errors too and re-raised them. An ollama 500 (model missing, server wedged) went
# from a polite "model isn't responding right now" to a traceback, on the path that has nothing to
# do with escalation. Verified in both directions against the real pre-323 module before fixing.
#
# The general shape, worth more than the bug: a new `except` clause placed above an existing one
# does not merely ADD a case — it SUBTRACTS every subclass it shadows from the handler that used
# to serve them. Narrowing by exception type is invisible at the call site, and the arms for the
# NEW behavior (B, D) all passed while the OLD behavior silently stopped.
bk = load_brain(consent="", providers_yaml=cfg, turn_log=os.path.join(tmp, "k.jsonl"))
import urllib.error as _ue

def _raiser(code):
    def boom(*a, **k):
        raise _ue.HTTPError("http://x", code, "boom", {}, None)
    return boom

_stdout = sys.stdout
for code in (404, 500, 503):
    bk.chat_stream = _raiser(code)
    sys.stdout = io.StringIO()
    try:
        msg = bk.chat_stream_safe([{"role": "user", "content": "hi"}], route=bk._floor_route())
        raised = None
    except BaseException as e:
        msg, raised = None, e
    finally:
        sys.stdout = _stdout
    check(f"K: floor-role HTTP {code} returns a message, never raises",
          raised is None and isinstance(msg, dict) and msg.get("role") == "assistant",
          f"raised={raised!r} msg={msg!r}")

# and the escalate degrade this shares a handler with still works (no regression the other way)
bk._ESCALATE_UNAVAILABLE.clear()
_calls = []
def _boom_then_ok(msgs, route=None):
    _calls.append(route["role"])
    if route["role"] == "escalate":
        raise _ue.HTTPError("http://x", 429, "rate limited", {}, None)
    return {"role": "assistant", "content": "floor answered", "tool_calls": [], "_usage": 5}
bk.chat_stream = _boom_then_ok
sys.stdout = io.StringIO()
try:
    m = bk.chat_stream_safe([{"role": "user", "content": "hi"}], route=bk._route_for_turn("turn"))
finally:
    sys.stdout = _stdout
check("K: escalate 429 still degrades to the floor in the shared handler",
      _calls == ["escalate", "floor"], repr(_calls))
check("K: and the message is tagged with the SERVED route, not the asked one",
      (m or {}).get("_route", {}).get("role") == "floor", repr((m or {}).get("_route")))

# ── L. startup secret preflight (2026-09-01) ──────────────────────────────────────────────
# The arm that was missing when the preflight shipped, and whose absence let it silently turn
# this whole battery into a floor-only battery. It is deliberately TWO-SIDED: the key-present
# arm is the permitting one, without which a preflight that marked EVERYTHING unavailable would
# look correct here.
bl_ok = load_brain(consent="session", providers_yaml=cfg, key=True)
check("L: key present → escalate provider is NOT marked unavailable",
      "cloud-claude" not in bl_ok._ESCALATE_UNAVAILABLE, repr(bl_ok._ESCALATE_UNAVAILABLE))
check("L: key present → escalate route still dispatches on the cloud provider",
      bl_ok._route_for_turn("turn")["role"] == "escalate", repr(bl_ok._route_for_turn("turn")))
check("L: key present → status surface reports configured",
      bl_ok.ESCALATE_STATUS.get("configured") is True, repr(bl_ok.ESCALATE_STATUS))

bl_no = load_brain(consent="session", providers_yaml=cfg, key=False)
check("L: key absent → escalate provider marked unavailable at startup",
      "cloud-claude" in bl_no._ESCALATE_UNAVAILABLE, repr(bl_no._ESCALATE_UNAVAILABLE))
check("L: key absent → the turn is served by the FLOOR, never the cloud",
      bl_no._route_for_turn("turn")["role"] == "floor", repr(bl_no._route_for_turn("turn")))
check("L: key absent → status says NOT configured, so the surface agrees with the router",
      bl_no.ESCALATE_STATUS.get("configured") is False, repr(bl_no.ESCALATE_STATUS))
check("L: key absent → never spills to the OTHER metered provider",
      bl_no._route_for_turn("turn").get("provider") != "other-metered",
      repr(bl_no._route_for_turn("turn")))
# The reason must name the REF and never the value — a status string is a log surface.
_reason = (bl_no.ESCALATE_STATUS.get("reason") or "")
check("L: key absent → reason names the ref, not the secret",
      "ANTHROPIC_API_KEY" in _reason and "sk-fixture" not in _reason, repr(_reason))

print("  " + ("ALL PASS" if EX == 0 else "FAILURES"))
sys.exit(EX)
