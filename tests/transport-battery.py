#!/usr/bin/env python3
# transport-battery.py — Phase 1.5 slice 5 (task 287): the provider transport seam.
#
# The seam splits chat_stream into (a) a per-provider generator that speaks a wire protocol
# and yields normalized ("kind", data) events, and (b) the provider-agnostic terminal
# renderer. This battery pins the CONTRACT between the two halves, so the Anthropic transport
# (next slice) can be written against a spec rather than against chat_stream's internals.
#
# Run: PYTHONPATH=modules python3 tests/transport-battery.py
#
# Checks:
#   A. modules compile clean
#   B. ACTIVE_PROVIDER_KIND resolves: no-config → "ollama"; config → the floor provider's kind
#   C. the ollama transport translates NDJSON → normalized events, in wire order
#   D. `done` carries eval_seconds (SECONDS, not ollama's nanoseconds) — unit normalization
#      lives in the transport, never in the renderer
#   E. blank lines / contentless chunks yield nothing (no empty "content" events)
#   F. an unknown provider kind FAILS LOUD — never a silent fallback to ollama, which would
#      answer from a different provider than the config names (metered-bucket crossing, the
#      exact class providers.py exists to forbid — 2026-08-06 overnight-bleed scar)
#   G. tool_calls pass through in Ollama shape (extract_tools' input contract is unchanged,
#      so the agent loop downstream never learns which provider answered)
#   I. EVERY registered transport takes `provider` as a parameter, and the dispatcher passes
#      the resolved provider name to it. Slice 6 fixed one transport that read the module-level
#      ACTIVE_PROVIDER (the FLOOR provider by construction — correct only while claude IS the
#      floor); nothing prevented the next transport from doing the same. This pins the CLASS,
#      not the instance: a new transport that configures itself from a global fails here at
#      registration time rather than on the first escalate-role turn.
import importlib.util, inspect, io, os, py_compile, sys, tempfile

MOD = "modules/agent-brain.py"
EX = 0

def check(name, cond):
    global EX
    print(("  PASS " if cond else "  FAIL ") + name)
    if not cond: EX = 1

# ── A. compile ──
for f in (MOD, "modules/providers.py"):
    try:
        py_compile.compile(f, doraise=True); print("  PASS compile " + f)
    except py_compile.PyCompileError as e:
        print("  FAIL compile " + f + ": " + str(e)); EX = 1

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "modules"))

def load_brain(providers_yaml=None):
    os.environ["AGENT_OS_PROVIDERS"] = providers_yaml or "/nonexistent/providers.yaml"
    os.environ["OLLAMA_MODEL"] = "qwen3.5:9b"
    spec = importlib.util.spec_from_file_location("agent_brain_transport_test", MOD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

# ── B. kind resolution ──
b = load_brain()
check("no-config ACTIVE_PROVIDER_KIND == ollama", b.ACTIVE_PROVIDER_KIND == "ollama")

with tempfile.TemporaryDirectory() as d:
    p = os.path.join(d, "providers.yaml")
    open(p, "w").write(
        "providers:\n"
        "  local-ollama: {kind: ollama, cost_tier: free, model: qwen3.5:9b}\n"
        "roles:\n"
        "  floor: local-ollama\n"
    )
    bc = load_brain(p)
    check("config ACTIVE_PROVIDER_KIND == floor provider's kind", bc.ACTIVE_PROVIDER_KIND == "ollama")

# ── C/D/E/G. ollama transport translation, against a canned NDJSON body ──
# Feed the transport a fake HTTP response instead of a live ollama: the generator's only
# dependency on the network is urlopen, so patching that is the whole seam boundary.
NDJSON = b"".join([
    b'{"message":{"thinking":"hmm "}}\n',
    b"\n",                                                    # blank line — must be skipped
    b'{"message":{"thinking":"still hmm"}}\n',
    b'{"message":{"content":"hello "}}\n',
    b'{"message":{"role":"assistant","content":""}}\n',       # empty content — no event
    b'{"message":{"content":"world"}}\n',
    b'{"message":{"tool_calls":[{"function":{"name":"echo","arguments":{"s":"hi"}}}]}}\n',
    b'{"done":true,"eval_count":42,"eval_duration":2500000000}\n',
])

class _FakeResp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): self.close(); return False

_orig_urlopen = b.urllib.request.urlopen
b.urllib.request.urlopen = lambda *a, **k: _FakeResp(NDJSON)
try:
    events = list(b._ollama_stream_events([{"role": "user", "content": "hi"}]))
finally:
    b.urllib.request.urlopen = _orig_urlopen

kinds = [k for k, _ in events]
check("C. wire order preserved (thinking→content→tool_calls→done)",
      kinds == ["thinking", "thinking", "content", "content", "tool_calls", "done"])
check("C. thinking pieces verbatim",
      [d for k, d in events if k == "thinking"] == ["hmm ", "still hmm"])
check("C. content pieces verbatim",
      [d for k, d in events if k == "content"] == ["hello ", "world"])
check("E. blank line + empty content yield no events", len(events) == 6)

done = dict(events[-1][1])
check("D. done.eval_count passed through", done.get("eval_count") == 42)
check("D. done.eval_seconds normalized ns→s (2.5e9ns == 2.5s)", abs(done.get("eval_seconds", 0) - 2.5) < 1e-9)
check("D. renderer never sees raw eval_duration", "eval_duration" not in done)

tc = [d for k, d in events if k == "tool_calls"][0]
check("G. tool_calls keep Ollama shape for extract_tools",
      isinstance(tc, list) and tc[0]["function"]["name"] == "echo"
      and tc[0]["function"]["arguments"] == {"s": "hi"})

# ── F. unknown kind fails loud ──
with tempfile.TemporaryDirectory() as d:
    p = os.path.join(d, "providers.yaml")
    open(p, "w").write(
        "providers:\n"
        "  weird-floor: {kind: not-a-real-transport, cost_tier: free}\n"
        "roles:\n"
        "  floor: weird-floor\n"
    )
    bu = load_brain(p)
    try:
        list(bu._stream_events([]))
        check("F. unknown provider kind raises (no silent ollama fallback)", False)
    except RuntimeError as e:
        check("F. unknown provider kind raises (no silent ollama fallback)",
              "not-a-real-transport" in str(e))
    except Exception as e:
        check("F. unknown provider kind raises RuntimeError, got %r" % (e,), False)

# ── seam sanity: the renderer no longer speaks the wire ──
src = open(MOD).read()
cs = src[src.index("\ndef chat_stream(msgs, route=None):"):src.index("\ndef chat(msgs):")]
check("seam: chat_stream contains no urlopen (wire protocol fully in transport)",
      "urlopen" not in cs)
check("seam: chat_stream contains no json.loads of wire chunks",
      "json.loads" not in cs)
check("seam: chat_stream drives the transport dispatcher", "_stream_events(msgs, route)" in cs)

# ── H. end-to-end equivalence: chat_stream over the seam still returns the same message ──
# The refactor's real risk is the renderer half, which no other battery drives (ollama-stub
# serves stream=false, i.e. chat(), not chat_stream()). Run the full renderer against the same
# canned NDJSON and assert the assistant message it returns is byte-identical to what the
# pre-seam code produced: content concatenated in order, tool_calls in Ollama shape.
b.urllib.request.urlopen = lambda *a, **k: _FakeResp(NDJSON)
_out, _err = sys.stdout, sys.stderr
sys.stdout = io.StringIO(); sys.stderr = io.StringIO()
try:
    msg = b.chat_stream([{"role": "user", "content": "hi"}])
    rendered = sys.stdout.getvalue()
    stats = sys.stderr.getvalue()
finally:
    sys.stdout, sys.stderr = _out, _err
    b.urllib.request.urlopen = _orig_urlopen

check("H. chat_stream returns role=assistant", msg.get("role") == "assistant")
check("H. chat_stream concatenates content in order", msg.get("content") == "hello world")
check("H. chat_stream returns tool_calls unchanged",
      msg.get("tool_calls") and msg["tool_calls"][0]["function"]["name"] == "echo")
check("H. renderer still prints the answer text", "hello" in rendered and "world" in rendered)
check("H. renderer still prints the thinking stream", "hmm" in rendered)
check("H. renderer still prints the thought-for separator", "thought for" in rendered)
check("H. tok/s stats computed from normalized seconds (42 tok / 2.5s == 17 tok/s)",
      "17 tok/s" in stats)


# ── I. class-level: a transport configures itself from an ARGUMENT, never a global ──
# Geist's PR-#98 verdict follow-up. The slice-6 bug was a transport reading module-level
# ACTIVE_PROVIDER; the fix threaded the resolved name through the dispatcher, but corrected
# only that instance. These two checks make the contract structural.
for _kind, _fn in sorted(b._TRANSPORTS.items()):
    _params = inspect.signature(_fn).parameters
    check("I. transport %r accepts a `provider` parameter" % _kind, "provider" in _params)

_seen = {}
def _spy(msgs, provider=None):
    _seen["provider"] = provider
    if False: yield None
    return
_saved = dict(b._TRANSPORTS)
try:
    b._TRANSPORTS["spy"] = _spy
    b.ACTIVE_PROVIDER_KIND = "spy"
    b.ACTIVE_PROVIDER = "some-escalate-provider"
    list(b._stream_events([{"role": "user", "content": "hi"}]))
finally:
    b._TRANSPORTS = _saved
check("I. dispatcher passes the RESOLVED provider name to the transport",
      _seen.get("provider") == "some-escalate-provider")

print("transport-battery: " + ("PASS" if EX == 0 else "FAIL"))
sys.exit(EX)
