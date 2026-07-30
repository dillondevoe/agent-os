#!/usr/bin/env bash
# agent-loop-battery — property battery for bin/agent-loop (v0.2 A1 tool-call loop).
#
# Drives the REAL loop against a scripted fake Ollama (tests/ollama-stub.py) over loopback
# and proves the loop MECHANICS, deterministically, with no model:
#   1  --check passes iff Ollama is up (crash-loop guard contract)
#   2  a plain answer (no tool_calls) prints and never traces a tool
#   3  a valid echo call ROUND-TRIPS: dispatch ok -> result fed back to the model as a
#      role:"tool" message that preserves the content_type:"data" envelope -> final answer
#   4  three denials in ONE user turn stop tool-calling; the final turn offers NO tools
#   5  tool_calls emitted on that withheld final turn are NEVER dispatched (structural stop)
#   6  model-supplied terminal control bytes are scrubbed before reaching the tty
#
# agent-loop makes ZERO security decisions, so this is loop correctness -- not a wall test.
# Args: <path-to-agent-loop> <path-to-ollama-stub.py> <workdir>
# Invoked as `python3 "$LOOP"` / `python3 "$STUB"` (not via shebang) to dodge the nix
# sandbox's missing /usr/bin/env (ENOEXEC).
set -uo pipefail

PY="${PYTHON:-python3}"
LOOP="$1"; STUB="$2"; WORK="$3"
PASS=0
fail() { echo "agent-loop-battery: FAIL -- $1" >&2; exit 1; }
pass() { PASS=$((PASS + 1)); }
has()   { grep -qF -- "$2" "$1" || fail "$3"; }
hasnt() { grep -qF -- "$2" "$1" && fail "$3"; return 0; }

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

# --- 1 : --check honors Ollama reachability ------------------------------------------
printf '%s' '[]' > "$WORK/empty.json"
start_stub "$WORK/empty.json" /dev/null
OLLAMA_HOST="$HOST" "$PY" "$LOOP" --check || fail "--check must pass while the stub advertises a model"
stop_stub
OLLAMA_HOST="http://127.0.0.1:1" "$PY" "$LOOP" --check && fail "--check must fail when nothing is listening" || pass

# --- 2 : plain answer, no tools ------------------------------------------------------
cat > "$WORK/s2.json" <<'EOF'
[ {"role":"assistant","content":"Hello there, no tools needed."} ]
EOF
start_stub "$WORK/s2.json" /dev/null
run_loop 'hi\n'
has "$OUT" "Hello there, no tools needed." "plain answer was not printed"
hasnt "$OUT" "tool:" "a plain answer must not trace any tool"
pass

# --- 3 : echo round-trip: call -> dispatch -> result fed back -> final answer ---------
cat > "$WORK/s3.json" <<'EOF'
[ {"role":"assistant","content":"","tool_calls":[{"function":{"name":"echo","arguments":{"text":"PINGPONG42"}}}]},
  {"role":"assistant","content":"Done, the echo returned PINGPONG42."} ]
EOF
start_stub "$WORK/s3.json" "$WORK/s3.log"
run_loop 'please echo PINGPONG42\n'
has "$OUT" "tool: echo" "a valid echo call should be traced"
hasnt "$OUT" "denied" "the happy echo path must not deny"
has "$OUT" "Done, the echo returned PINGPONG42." "final answer after the tool was not printed"
"$PY" - "$WORK/s3.log" <<'PYEOF' || fail "echo result was not fed back as a data-enveloped tool message"
import json, sys
rows = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
assert len(rows) >= 2, f"expected >=2 chat calls, got {len(rows)}"
tool_msgs = [m for m in rows[1]["messages"] if m.get("role") == "tool"]
assert tool_msgs, "2nd request carried no role:tool message"
payload = json.loads(tool_msgs[0]["content"])   # the tool msg carries the envelope as a JSON string
assert payload.get("content_type") == "data", payload
assert payload.get("content") == "PINGPONG42", payload
PYEOF
pass

# --- 4 : deny cap: 3 denials -> stop tool-calling -> final turn offers NO tools -------
cat > "$WORK/s4.json" <<'EOF'
[ {"role":"assistant","content":"","tool_calls":[{"function":{"name":"rm_rf_slash","arguments":{}}}]},
  {"role":"assistant","content":"","tool_calls":[{"function":{"name":"rm_rf_slash","arguments":{}}}]},
  {"role":"assistant","content":"","tool_calls":[{"function":{"name":"rm_rf_slash","arguments":{}}}]},
  {"role":"assistant","content":"I stopped after three failed tool attempts, I only have an inert echo tool."} ]
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
  {"role":"assistant","content":"stopping","tool_calls":[{"function":{"name":"echo","arguments":{"text":"SHOULD_NOT_EXECUTE"}}}]} ]
EOF
start_stub "$WORK/s5.json" /dev/null
run_loop 'spam tools\n'
traces="$(grep -cF -- 'tool: ' "$OUT")"
[ "$traces" -eq 3 ] || fail "final-turn tool_calls must not dispatch; expected 3 traces, got $traces"
hasnt "$OUT" "tool: echo" "the echo on the withheld final turn must never run (no echo trace)"
has "$OUT" "stopping" "final content should still print"
pass

# --- 6 : model control bytes scrubbed before the tty ---------------------------------
# The model injects a red-ESC color (ESC[31m), a BEL (0x07), and a NUL (0x00) -- none of
# which the loop itself ever emits (its own colors are 38;5;{79,179,244}m + 0m), so their
# absence in the output is unambiguous. Built via python so the shell source stays plain
# ASCII (no raw control bytes) and json.dump writes correctly-escaped JSON.
"$PY" - "$WORK/s6.json" <<'PYEOF'
import json, sys
content = "start\x1b[31mRED\x07\x00end"
json.dump([{"role": "assistant", "content": content}], open(sys.argv[1], "w"))
PYEOF
start_stub "$WORK/s6.json" /dev/null
run_loop 'hi\n'
"$PY" - "$OUT" <<'PYEOF' || fail "model control bytes were not scrubbed"
import sys
d = open(sys.argv[1], "rb").read()
assert b"\x1b[31m" not in d, "model ESC color leaked to the tty"
assert b"\x07" not in d and b"\x00" not in d, "BEL/NUL leaked to the tty"
assert b"RED" in d and b"start[31mREDend" in d, "scrub removed too much (payload/de-ESC'd literal gone)"
PYEOF
pass

echo "agent-loop-battery: OK -- $PASS checks passed"
