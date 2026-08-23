#!/usr/bin/env python3
# anthropic-transport-battery.py — Phase 1.5 slice 6 (task 287): the Anthropic transport.
#
# The slice-5 seam defined a transport as a generator yielding normalized
# ("thinking"|"content"|"tool_calls"|"done", data) events. This battery proves the Anthropic
# implementation satisfies that same contract from a completely different wire protocol (SSE,
# not NDJSON) — which is the whole point of having built the seam first.
#
# Run: PYTHONPATH=modules python3 tests/anthropic-transport-battery.py
#
# The two genuinely tricky parts, and why each gets its own checks:
#   * Tool calls stream as content_block_start(tool_use) → input_json_delta fragments →
#     content_block_stop. The input is NOT parseable JSON until the block closes, so the
#     transport must buffer and only emit at stop — and must emit it in OLLAMA shape, since
#     extract_tools and the whole agent loop downstream speak that shape.
#   * Anthropic REQUIRES tool_use_id on every tool_result, but this codebase appends bare
#     {"role":"tool","content":res} with no id. Pairing is positional and has to be exactly
#     right or the API 400s — mismatched ids are the single most likely way this breaks.
import importlib.util, io, json, os, py_compile, sys, tempfile

MOD = "modules/agent-brain.py"
EX = 0

def check(name, cond):
    global EX
    print(("  PASS " if cond else "  FAIL ") + name)
    if not cond: EX = 1

try:
    py_compile.compile(MOD, doraise=True); print("  PASS compile " + MOD)
except py_compile.PyCompileError as e:
    print("  FAIL compile: " + str(e)); sys.exit(1)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "modules"))

_tmp = tempfile.mkdtemp()
_providers = os.path.join(_tmp, "providers.yaml")
open(_providers, "w").write(
    "providers:\n"
    "  local-ollama: {kind: ollama, cost_tier: free, model: qwen3.5:9b}\n"
    "  cloud-claude: {kind: claude, cost_tier: metered, model: claude-opus-5,\n"
    "                 api_key_ref: 'env://AGENT_OS_TEST_KEY'}\n"
    "roles:\n"
    "  floor: cloud-claude\n"      # claude as ACTIVE provider, so the dispatcher routes to it
    "  escalate: cloud-claude\n"
)
os.environ["AGENT_OS_PROVIDERS"] = _providers
os.environ["AGENT_OS_TEST_KEY"] = "sk-ant-test"
spec = importlib.util.spec_from_file_location("agent_brain_anthropic_test", MOD)
b = importlib.util.module_from_spec(spec); spec.loader.exec_module(b)

# ── A. registry ──
check("A. claude kind registered in _TRANSPORTS", "claude" in b._TRANSPORTS)
check("A. ollama transport still registered", "ollama" in b._TRANSPORTS)

# ── B. secret resolution — refuse, don't degrade ──
check("B. env:// resolves", b._resolve_secret("env://AGENT_OS_TEST_KEY") == "sk-ant-test")
for bad, why in [("op://vault/item/field", "no secret CLI in the sealed image"),
                 ("sk-ant-literal", "literal key has no scheme"),
                 ("", "missing ref"),
                 ("env://DEFINITELY_UNSET_VAR_XYZ", "unset env var")]:
    try:
        b._resolve_secret(bad); check("B. %s rejected (%s)" % (bad[:24] or "<empty>", why), False)
    except RuntimeError:
        check("B. %s rejected (%s)" % (bad[:24] or "<empty>", why), True)

# ── C. tool translation: Ollama function-wrapper → Anthropic flat input_schema ──
at = b._anthropic_translate_tools([
    {"type": "function", "function": {"name": "run_command", "description": "Run a shell command",
                                      "parameters": {"type": "object",
                                                     "properties": {"command": {"type": "string"}},
                                                     "required": ["command"]}}}])
check("C. tool name preserved", at[0]["name"] == "run_command")
check("C. parameters → input_schema", at[0]["input_schema"]["required"] == ["command"])
check("C. no leftover function wrapper", "function" not in at[0] and "parameters" not in at[0])

# ── D. message translation ──
sys_str, msgs = b._anthropic_translate_messages([
    {"role": "system", "content": "SYS-A"},
    {"role": "system", "content": "SYS-B"},
    {"role": "user", "content": "hello"},
])
check("D. system hoisted out of messages", sys_str == "SYS-A\n\nSYS-B")
check("D. system not left in the message list", all(m["role"] != "system" for m in msgs))
check("D. user becomes a text block", msgs[0]["content"][0] == {"type": "text", "text": "hello"})

# ── E. tool_use_id pairing — the failure mode most likely to 400 in production ──
_, m = b._anthropic_translate_messages([
    {"role": "user", "content": "do two things"},
    {"role": "assistant", "content": "on it", "tool_calls": [
        {"function": {"name": "a", "arguments": {"x": 1}}},
        {"function": {"name": "b", "arguments": {"y": 2}}}]},
    {"role": "tool", "content": "res-a"},
    {"role": "tool", "content": "res-b"},
])
uses = [blk for blk in m[1]["content"] if blk["type"] == "tool_use"]
results = [blk for blk in m[2]["content"] if blk["type"] == "tool_result"]
check("E. both tool_use blocks emitted", len(uses) == 2 and [u["name"] for u in uses] == ["a", "b"])
check("E. assistant text kept alongside tool_use", m[1]["content"][0]["type"] == "text")
check("E. ids are unique", uses[0]["id"] != uses[1]["id"])
check("E. results pair positionally to the right ids",
      [r["tool_use_id"] for r in results] == [u["id"] for u in uses])
check("E. result CONTENT pairs to the right call (not just the id)",
      [r["content"] for r in results] == ["res-a", "res-b"])
check("E. both results in ONE user message (parallel tool use preserved)",
      len(m) == 3 and len(results) == 2)

_, m2 = b._anthropic_translate_messages([
    {"role": "assistant", "content": "x", "tool_calls": [
        {"function": {"name": "a", "arguments": '{"j": 1}'}}]}])
check("E. string-encoded arguments parsed to dict",
      m2[0]["content"][1]["input"] == {"j": 1})

# ── F. SSE → normalized events, against a canned stream ──
SSE = b"".join([
    b'event: message_start\ndata: {"type":"message_start"}\n\n',
    b'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"thinking_delta","thinking":"pondering"}}\n\n',
    b'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hello "}}\n\n',
    b'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"text_delta","text":"world"}}\n\n',
    b'event: content_block_start\ndata: {"type":"content_block_start","content_block":{"type":"tool_use","id":"toolu_1","name":"run_command"}}\n\n',
    b'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"input_json_delta","partial_json":"{\\"comm"}}\n\n',
    b'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"input_json_delta","partial_json":"and\\": \\"ls\\"}"}}\n\n',
    b'event: content_block_stop\ndata: {"type":"content_block_stop"}\n\n',
    b'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"tool_use"},"usage":{"output_tokens":77}}\n\n',
    b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
])

class _FakeResp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): self.close(); return False

captured = {}
_orig = b.urllib.request.urlopen
def _fake(req, *a, **k):
    captured["headers"] = {k.lower(): v for k, v in req.headers.items()}
    captured["body"] = json.loads(req.data.decode())
    return _FakeResp(SSE)
b.urllib.request.urlopen = _fake
try:
    # Through the DISPATCHER, not the transport directly — that is what a real turn does,
    # and it is what proves the provider name is threaded correctly.
    events = list(b._stream_events([
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "list files"}]))
finally:
    b.urllib.request.urlopen = _orig

kinds = [k for k, _ in events]
check("F. thinking emitted", ("thinking", "pondering") in events)
check("F. text deltas emitted in order",
      [d for k, d in events if k == "content"] == ["Hello ", "world"])
check("F. tool_calls emitted once, after the content", kinds.count("tool_calls") == 1
      and kinds.index("tool_calls") > kinds.index("content"))
check("F. done is last", kinds[-1] == "done")

tc = [d for k, d in events if k == "tool_calls"][0]
check("G. tool call is in OLLAMA shape (extract_tools' contract, not Anthropic's)",
      tc[0]["function"]["name"] == "run_command")
check("G. fragmented input_json reassembled and parsed",
      tc[0]["function"]["arguments"] == {"command": "ls"})

done = dict(events[-1][1])
check("H. done.eval_count from usage.output_tokens", done["eval_count"] == 77)
check("H. done.eval_seconds present and in seconds", 0 <= done["eval_seconds"] < 60)

# ── I. request shape ──
check("I. x-api-key header sent", captured["headers"].get("X-api-key".lower()) == "sk-ant-test")
check("I. anthropic-version header sent",
      captured["headers"].get("Anthropic-version".lower()) == b._ANTHROPIC_VERSION)
check("I. stream enabled", captured["body"]["stream"] is True)
check("I. system hoisted to top-level param", captured["body"]["system"] == "be brief")
check("I. model comes from the resolved provider", captured["body"]["model"] == b.MODEL)
check("I. thinking display=summarized (default 'omitted' would blank the renderer)",
      captured["body"]["thinking"] == {"type": "adaptive", "display": "summarized"})
check("I. no sampling params (removed on current models — would 400)",
      not {"temperature", "top_p", "top_k"} & set(captured["body"]))
check("I. tools translated into the request", captured["body"]["tools"][0]["name"])

# ── I2. the provider-resolution bug this battery caught ──
# The transport originally read the module-level ACTIVE_PROVIDER instead of the provider the
# dispatcher resolved. That is correct only while the claude provider IS the floor; the moment
# a transport serves any other role it would read the wrong entry and find no api_key_ref.
check("I2. dispatcher routes claude kind to the anthropic transport",
      b.ACTIVE_PROVIDER_KIND == "claude" and b._TRANSPORTS["claude"] is b._anthropic_stream_events)
check("I2. transport configures from the provider it was GIVEN, not a global",
      captured["body"]["model"] == "claude-opus-5")
# THE LABEL BELOW MAKES TWO CLAIMS AND THIS ARM USED TO PROVE NEITHER OF THEM.
# It was `except RuntimeError: check(..., True)` — satisfied by ANY RuntimeError from anywhere
# in the call, including one raised for an unrelated reason, and silent about "rather than
# mis-billing", which asserts that no request went out. Structurally a correct two-branch test;
# its sentence was simply wider than its assertion. Found by reading the label against the
# assert after Page did the same on their own arms (2026-08-23) — a structural sweep clears on
# shape and never reads a word.
_before = dict(captured)
try:
    list(b._anthropic_stream_events([{"role": "user", "content": "x"}], provider="local-ollama"))
    check("I2. wrong provider raises rather than proceeding", False)
except RuntimeError as exc:
    check("I2. wrong provider raises rather than proceeding", True)
    check("I2. ...and it raises FOR the missing api_key_ref, not for some other reason",
          "api_key_ref" in str(exc))
    check("I2. ...and no request was issued — the 'rather than mis-billing' half",
          dict(captured) == _before)

# ── J. a stream error must raise, not truncate silently ──
b.urllib.request.urlopen = lambda *a, **k: _FakeResp(
    b'event: error\ndata: {"type":"error","error":{"message":"overloaded"}}\n\n')
try:
    list(b._anthropic_stream_events([{"role": "user", "content": "x"}]))
    check("J. stream error raises", False)
except RuntimeError as e:
    check("J. stream error raises with the API message", "overloaded" in str(e))
finally:
    b.urllib.request.urlopen = _orig

print("anthropic-transport-battery: " + ("PASS" if EX == 0 else "FAIL"))
sys.exit(EX)
