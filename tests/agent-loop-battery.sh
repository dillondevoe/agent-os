#!/usr/bin/env bash
# agent-loop-battery — property battery for bin/agent-loop (v0.2 A2: route through the wall).
#
# Drives the REAL loop against a scripted fake Ollama (tests/ollama-stub.py) over loopback
# AND the REAL bin/mcp piped into a deterministic stub broker (tests/broker-stub.py). The
# loop makes ZERO security decisions, so this proves loop MECHANICS — not wall policy (mcp
# and broker each carry their own battery). The stub sits exactly where the real broker sits
# (downstream of mcp's verdict stream) so the loop's marshalled request still passes the REAL
# structural front door; only the tier/taint/registry decision is scripted. Properties:
#   1  --check passes iff Ollama is up (crash-loop guard contract)
#   2  a plain answer (no tool_calls) prints and never traces a tool
#   3  a valid capability call ROUND-TRIPS through the wall: dispatch -> broker data_result
#      fed back to the model as a role:"tool" message that preserves the content_type:"data"
#      envelope -> final answer. The tool SURFACE itself is discovered through the wall via a
#      capabilities.list data_result (no hardcoded tool list).
#   4  three broker denials in ONE user turn stop tool-calling; the final turn offers NO tools
#   5  tool_calls emitted on that withheld final turn are NEVER dispatched (structural stop)
#   6  model-supplied terminal control bytes are scrubbed before reaching the tty
#
# A missing/garbled wall as a fail-closed deny (the other arms of HARD REQ 4) is proven by
# the standalone wall-smoke; here property 4 proves the broker-deny arm end-to-end.
# Args: <path-to-agent-loop> <path-to-ollama-stub.py> <path-to-mcp> <path-to-broker-stub.py> <workdir>
# Invoked as `python3 "$LOOP"` / `python3 "$STUB"` (not via shebang) to dodge the nix
# sandbox's missing /usr/bin/env (ENOEXEC); the loop execs mcp/broker under sys.executable
# for the same reason, so no store path here needs an executable bit or a resolvable shebang.
set -uo pipefail

PY="${PYTHON:-python3}"
LOOP="$1"; STUB="$2"; MCP="$3"; BROKER_STUB="$4"; WORK="$5"
PASS=0
fail() { echo "agent-loop-battery: FAIL -- $1" >&2; exit 1; }
pass() { PASS=$((PASS + 1)); }
has()   { grep -qF -- "$2" "$1" || fail "$3"; }
# hasnt REQUIRES the substrate before asserting an absence. Without the -s guard, a missing or
# empty file makes grep fail, `&& fail` never fires, and the arm passes — "the bad string is not
# in the output" is vacuously true of a run that produced no output at all. The mirror-image
# helper `has` is fail-SAFE for free (missing file -> grep fails -> fail fires), which is exactly
# why the asymmetry is easy to miss when reading the two lines together.
#
# NOT a live defect when this was written: all three call sites happen to sit beside a `has` or a
# trace-count arm on the same file, so the substrate is established by a NEIGHBOUR. That makes the
# guarantee a property of call-site ordering rather than of the helper — a fourth hasnt added
# without such a neighbour would be silently vacuous and nothing would object. Control-armed
# below, so the helper now carries its own guarantee.
hasnt() {
  [ -s "$1" ] || fail "$3 (substrate absent or empty: $1 — an absence assertion against no output is vacuous)"
  grep -qF -- "$2" "$1" && fail "$3"
  return 0
}

# ── control arm for the helpers themselves. A battery whose harness can only ever say "ok" is
# not measuring anything; these run before any property so a broken helper is named as such
# rather than surfacing later as a mysteriously green suite.
_selfcheck() {
  mkdir -p "$WORK"
  local missing="$WORK/.nosuch" empty="$WORK/.empty" full="$WORK/.full"
  : > "$empty"; printf 'needle\n' > "$full"
  ( hasnt "$missing" needle "x" ) >/dev/null 2>&1 && fail "harness: hasnt PASSED on a missing file"
  ( hasnt "$empty"   needle "x" ) >/dev/null 2>&1 && fail "harness: hasnt PASSED on an empty file"
  ( hasnt "$full"    needle "x" ) >/dev/null 2>&1 && fail "harness: hasnt PASSED on a file that CONTAINS the needle"
  # permitting arm: without it, a hasnt that failed unconditionally would satisfy all three above
  ( hasnt "$full" absent-string "x" ) >/dev/null 2>&1 || fail "harness: hasnt REJECTED a genuine absence"
  ( has "$full" needle "x" ) >/dev/null 2>&1 || fail "harness: has REJECTED a present string"
  ( has "$missing" needle "x" ) >/dev/null 2>&1 && fail "harness: has PASSED on a missing file"
  echo "agent-loop-battery: harness self-check ok (hasnt requires substrate; has fails closed)"
  return 0
}
_selfcheck

# ── wall wiring: the loop resolves mcp+broker from these env pins (never from the model).
# Point mcp at the REAL front door and broker at the deterministic stub. One config serves
# every scenario: it advertises a single capability (mem.recall) so discovery yields a
# non-empty tool surface (property 4/5 need `has_tools` true on the tool-offering turns) and
# answers mem.recall with a data_result (property 3's round-trip); every OTHER tool name the
# model invents (rm_rf_slash) is absent from `responses` -> unknown-capability deny.
export AGENT_OS_MCP="$MCP"
export AGENT_OS_BROKER="$BROKER_STUB"
BROKER_CFG="$WORK/broker.json"
cat > "$BROKER_CFG" <<'EOF'
{ "capabilities": [ {"name":"mem.recall","tier":"pure","summary":"recall a stored memory"} ],
  "responses":    { "mem.recall": {"data":"PINGPONG42","capability_ok":true} } }
EOF
export AGENT_OS_BROKER_STUB="$BROKER_CFG"

STUB_PID=""
stop_stub() { [ -n "$STUB_PID" ] && kill "$STUB_PID" 2>/dev/null; wait "$STUB_PID" 2>/dev/null; STUB_PID=""; }
trap 'stop_stub' EXIT

# start_stub <script.json> <reqlog|/dev/null> : boots a fresh stub, sets $HOST to its base.
start_stub() {
  stop_stub
  local script="$1" reqlog="$2" fifo
  fifo="$WORK/port.fifo"; rm -f "$fifo"; mkfifo "$fifo"
  AGENT_OS_STUB_SCRIPT="$script" AGENT_OS_STUB_LOG="$reqlog" AGENT_OS_STUB_PORTFILE="$fifo" \
    "$PY" "$STUB" &
  STUB_PID=$!
  local port; IFS= read -r port < "$fifo"; rm -f "$fifo"   # blocks until the stub reports its port (no sleep)
  [ -n "$port" ] || fail "stub did not report a port"
  HOST="http://127.0.0.1:$port"
}

OUT="$WORK/out.txt"; ERR="$WORK/err.txt"
run_loop() {  # <input> : pipe input into the loop against $HOST, capture stdout->$OUT
  printf '%b' "$1" | OLLAMA_HOST="$HOST" "$PY" "$LOOP" > "$OUT" 2>"$ERR"
}

# --- 1 : --check honors Ollama reachability (no wall involved — --check never runs main) ---
printf '%s' '[]' > "$WORK/empty.json"
start_stub "$WORK/empty.json" /dev/null
OLLAMA_HOST="$HOST" "$PY" "$LOOP" --check || fail "--check must pass while the stub advertises a model"
stop_stub
OLLAMA_HOST="http://127.0.0.1:1" "$PY" "$LOOP" --check && fail "--check must fail when nothing is listening" || pass

# --- 2 : plain answer, no tools ------------------------------------------------------
# main() still discovers the surface through the wall (banner shows `tools: mem.recall`);
# the plain answer must still never TRACE a tool. Note: the banner substring "tools:" does
# not contain "tool:" (the char after "tool" is "s", not ":"), so the guard below is exact.
cat > "$WORK/s2.json" <<'EOF'
[ {"role":"assistant","content":"Hello there, no tools needed."} ]
EOF
start_stub "$WORK/s2.json" /dev/null
run_loop 'hi\n'
has "$OUT" "Hello there, no tools needed." "plain answer was not printed"
hasnt "$OUT" "tool:" "a plain answer must not trace any tool"
pass

# --- 3 : capability round-trip THROUGH THE WALL: call -> broker data_result -> fed back ----
cat > "$WORK/s3.json" <<'EOF'
[ {"role":"assistant","content":"","tool_calls":[{"function":{"name":"mem.recall","arguments":{"query":"PINGPONG42"}}}]},
  {"role":"assistant","content":"Done, the memory returned PINGPONG42."} ]
EOF
start_stub "$WORK/s3.json" "$WORK/s3.log"
run_loop 'please recall PINGPONG42\n'
has "$OUT" "tool: mem.recall" "a valid capability call should be traced"
hasnt "$OUT" "denied" "the happy capability path must not deny"
has "$OUT" "Done, the memory returned PINGPONG42." "final answer after the tool was not printed"
"$PY" - "$WORK/s3.log" <<'PYEOF' || fail "capability result was not fed back as a data-enveloped tool message"
import json, sys
rows = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
assert len(rows) >= 2, f"expected >=2 chat calls, got {len(rows)}"
tool_msgs = [m for m in rows[1]["messages"] if m.get("role") == "tool"]
assert tool_msgs, "2nd request carried no role:tool message"
payload = json.loads(tool_msgs[0]["content"])   # the tool msg carries the broker envelope as a JSON string
assert payload.get("content_type") == "data", payload   # broker-emitted trusted-side, loop preserves it
assert payload.get("content") == "PINGPONG42", payload
PYEOF
pass

# --- 4 : deny cap: 3 broker denials -> stop tool-calling -> final turn offers NO tools ----
# rm_rf_slash is a structurally-valid tool NAME (mcp accepts it) that the stub broker does
# not register -> unknown-capability deny. Three in one turn spend the deny budget.
cat > "$WORK/s4.json" <<'EOF'
[ {"role":"assistant","content":"","tool_calls":[{"function":{"name":"rm_rf_slash","arguments":{}}}]},
  {"role":"assistant","content":"","tool_calls":[{"function":{"name":"rm_rf_slash","arguments":{}}}]},
  {"role":"assistant","content":"","tool_calls":[{"function":{"name":"rm_rf_slash","arguments":{}}}]},
  {"role":"assistant","content":"I stopped after three failed tool attempts; my tools can't do that."} ]
EOF
start_stub "$WORK/s4.json" "$WORK/s4.log"
run_loop 'delete everything now\n'
denies="$(grep -cF -- 'denied' "$OUT")"
[ "$denies" -eq 3 ] || fail "expected exactly 3 denials, got $denies"
has "$OUT" "I stopped after three failed tool attempts" "final explanation not printed after the deny cap"
"$PY" - "$WORK/s4.log" <<'PYEOF' || fail "final turn must offer NO tools once the deny cap is hit"
import json, sys
rows = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
assert len(rows) == 4, f"expected exactly 4 chat calls, got {len(rows)}"
assert all(rows[i]["has_tools"] for i in (0, 1, 2)), "turns 1-3 should offer tools"
assert rows[3]["has_tools"] is False, "the final (post-cap) turn must NOT offer tools"
PYEOF
pass

# --- 5 : the withheld final turn NEVER dispatches tool_calls (structural stop) --------
cat > "$WORK/s5.json" <<'EOF'
[ {"role":"assistant","content":"","tool_calls":[{"function":{"name":"rm_rf_slash","arguments":{}}}]},
  {"role":"assistant","content":"","tool_calls":[{"function":{"name":"rm_rf_slash","arguments":{}}}]},
  {"role":"assistant","content":"","tool_calls":[{"function":{"name":"rm_rf_slash","arguments":{}}}]},
  {"role":"assistant","content":"stopping","tool_calls":[{"function":{"name":"mem.recall","arguments":{"query":"SHOULD_NOT_EXECUTE"}}}]} ]
EOF
start_stub "$WORK/s5.json" /dev/null
run_loop 'spam tools\n'
traces="$(grep -cF -- 'tool: ' "$OUT")"
[ "$traces" -eq 3 ] || fail "final-turn tool_calls must not dispatch; expected 3 traces, got $traces"
hasnt "$OUT" "tool: mem.recall" "the mem.recall on the withheld final turn must never run (no trace)"
has "$OUT" "stopping" "final content should still print"
pass

# --- 6 : model control bytes scrubbed before the tty ---------------------------------
# The model injects a red-ESC color (ESC[31m), a BEL (0x07), a NUL (0x00), and two 8-bit
# C1 controls -- DCS (U+0090) and OSC (U+009D) -- none of which the loop itself ever emits
# (its own colors are 38;5;{79,179,244}m + 0m), so their absence in the output is
# unambiguous. The C1 pair exercises the widened _CTRL class (C0 + DEL + all C1); a Python
# str carries U+0090/U+009D as codepoints (not UTF-8 continuation bytes), and if they leaked
# stdout would encode them as \xc2\x90 / \xc2\x9d -- the \x90/\x9d asserts below catch both
# bare and UTF-8 forms. Built via python so the shell source stays plain ASCII (no raw
# control bytes) and json.dump writes correctly-escaped JSON.
"$PY" - "$WORK/s6.json" <<'PYEOF'
import json, sys
content = "start\x1b[31mRED\x07\x00\x90\x9dend"
json.dump([{"role": "assistant", "content": content}], open(sys.argv[1], "w"))
PYEOF
start_stub "$WORK/s6.json" /dev/null
run_loop 'hi\n'
"$PY" - "$OUT" <<'PYEOF' || fail "model control bytes were not scrubbed"
import sys
d = open(sys.argv[1], "rb").read()
assert b"\x1b[31m" not in d, "model ESC color leaked to the tty"
assert b"\x07" not in d and b"\x00" not in d, "BEL/NUL leaked to the tty"
assert b"\x90" not in d and b"\x9d" not in d, "C1 DCS/OSC leaked to the tty (widened _CTRL class)"
assert b"RED" in d and b"start[31mREDend" in d, "scrub removed too much (payload/de-ESC'd literal gone)"
PYEOF
pass

echo "agent-loop-battery: OK -- $PASS checks passed"
