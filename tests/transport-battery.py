#!/usr/bin/env python3
# transport-battery.py — Phase 1.5 slice 5 (task 287): the provider transport seam.
#
# The seam splits chat_stream into (a) a per-provider generator that speaks a wire protocol
# and yields normalized ("kind", data) events, and (b) the provider-agnostic terminal
# renderer. This battery pins the CONTRACT between the two halves, so the Anthropic transport
# (next slice) can be written against a spec rather than against chat_stream's internals.
#
# Run: python3 tests/transport-battery.py   (from the repo root)
#
# Wired into .github/workflows/flake-check.yml as "transport seam battery" (PR #188, the second
# entry taken off tests/known-unwired-debt.txt). THAT STEP IS THE AUTHORITATIVE INVOCATION and
# this line exists to agree with it, not to compete with it.
#
# It used to read `PYTHONPATH=modules python3 ...`. The variable was inert — this battery resolves
# modules/agent-brain.py by PATH (spec_from_file_location, below), so the environment never
# mattered. Harmless while the file ran nowhere; a second, divergent spelling of "how to run this"
# the moment it got wired, which is the scar this repo keeps re-cutting.
#
# Note the direction that makes it worth fixing rather than annotating. The header is what a
# HUMAN copies; the workflow is what actually runs. A reader who copied this line got a passing
# run and no reason to doubt it — the two spellings agreed on the verdict while disagreeing on
# the command, which is exactly the condition under which nobody looks again.
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
# SIDE_EFFECTS — Geist's law as amended 2026-09-05T13:05Z: a box-runnable battery declares
# every effect that leaves the machine or outlives the run. Read as DATA by
# tests/vm-matrix-contract.py, not as a comment.
SIDE_EFFECTS = []

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

# ── J. A MUTE TURN IS VISIBLE ──
# The scar: 2026-08-31 the Dell returned an EMPTY message after burning all 2048 output
# tokens on reasoning, and chat_stream printed the timing line and nothing else — the box
# looked like it had answered with silence, for 457 seconds, twice. OLLAMA_THINK=off fixed
# that CAUSE; this arm covers the CLASS, which outlives it (any ceiling, stop sequence or
# transport hiccup lands in the same place).
# Arm I above leaves ACTIVE_PROVIDER_KIND pointing at its spy transport and does not put it
# back, so anything added after it inherits a module that cannot route to ollama. Restored
# here explicitly rather than by reordering: a battery whose arms depend on running in a
# particular order is one edit away from a false green.
b.ACTIVE_PROVIDER_KIND, b.ACTIVE_PROVIDER = "ollama", None

def _run_stream(ndjson):
    """Drive chat_stream against a canned wire body; return what the operator SAW on stderr."""
    err = io.StringIO()
    _o_urlopen, _o_stderr = b.urllib.request.urlopen, sys.stderr
    b.urllib.request.urlopen = lambda *a, **k: _FakeResp(ndjson)
    sys.stderr = err
    try:
        return b.chat_stream([{"role": "user", "content": "hi"}]), err.getvalue()
    finally:
        b.urllib.request.urlopen, sys.stderr = _o_urlopen, _o_stderr

MUTE = b"".join([
    b'{"message":{"thinking":"reasoning forever"}}\n',
    b'{"done":true,"eval_count":2048,"eval_duration":450000000000}\n',
])
res, err = _run_stream(MUTE)
check("J. a mute turn (no content, no tool_calls) is reported to the operator",
      "no answer" in err)
check("J. the report names eval_count — a large count and a ~0 count want opposite fixes",
      "2048" in err)
check("J. the mute turn still returns a well-formed empty message",
      res["content"] == "" and res["tool_calls"] == [])

# CONTROL, not a defect: a turn that DID answer must stay quiet. Without this arm a
# renderer that warned on every turn would pass J above and the check would be noise.
res2, err2 = _run_stream(b'{"message":{"content":"hi"}}\n{"done":true,"eval_count":3}\n')
check("J. CONTROL, not a defect: a turn WITH content emits no no-answer report",
      "no answer" not in err2 and res2["content"] == "hi")

# CONTROL, not a defect: tool_calls alone is a legitimate silent turn — the model acted
# instead of speaking, and warning there would train the operator to ignore the warning.
res3, err3 = _run_stream(
    b'{"message":{"tool_calls":[{"function":{"name":"echo","arguments":{"s":"hi"}}}]}}\n'
    b'{"done":true,"eval_count":9}\n')
check("J. CONTROL, not a defect: tool_calls with no prose emits no no-answer report",
      "no answer" not in err3 and res3["tool_calls"] != [])

print("transport-battery: " + ("PASS" if EX == 0 else "FAIL"))
sys.exit(EX)
