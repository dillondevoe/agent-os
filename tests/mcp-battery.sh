#!/usr/bin/env bash
# tests/mcp-battery.sh — Phase 2 · Step 4 conformance + hostile-input battery for bin/mcp.
#
# Proves the front-door invariants of docs/phase2-threat-model.md §9: one pinned protocol
# rev, three methods only, and fail-closed on everything unrecognized/malformed. The
# hostile-input battery Rabbot mandated is here in full — oversized, deeply-nested,
# duplicate-id, duplicate-key, wrong-type, trailing-bytes, and unicode/control tricks —
# each with exactly ONE expected verdict (the battery IS the parser-differential test).
# A regression fails `nix flake check`. Driven by the flake's mcp-conformance check as
#   bash tests/mcp-battery.sh <path-to-bin/mcp> <workdir>
set -uo pipefail

MCP="${1:?usage: mcp-battery.sh <path-to-bin/mcp> <workdir>}"
WORK="${2:?usage: mcp-battery.sh <path-to-bin/mcp> <workdir>}"
PY="${PYTHON:-python3}"
PASS=0

fail() { echo "MCP-BATTERY FAIL: $*" >&2; exit 1; }
pass() { PASS=$((PASS + 1)); }

run() { printf '%s\n' "$1" | "$PY" "$MCP" parse; }

expect_ok() {  # <json> <must-contain> <label>
  local out; out=$(run "$1") || fail "$3: parse exited nonzero"
  case "$out" in *'"ok":true'*) ;; *) fail "$3: expected ok=true, got: $out" ;; esac
  case "$out" in *"$2"*) ;; *) fail "$3: missing substring [$2], got: $out" ;; esac
  pass
}
expect_deny() {  # <json> <code> <label>
  local out; out=$(run "$1") || fail "$3: parse exited nonzero"
  case "$out" in *'"ok":false'*) ;; *) fail "$3: expected ok=false, got: $out" ;; esac
  case "$out" in *"\"code\":$2"*) ;; *) fail "$3: expected code $2, got: $out" ;; esac
  pass
}
expect_deny_env() {  # <ENV=v> <json> <code> <label>
  local out; out=$(printf '%s\n' "$2" | env "$1" "$PY" "$MCP" parse) || fail "$4: nonzero"
  case "$out" in *'"ok":false'*) ;; *) fail "$4: expected ok=false, got: $out" ;; esac
  case "$out" in *"\"code\":$3"*) ;; *) fail "$4: expected code $3, got: $out" ;; esac
  pass
}
expect_deny_raw() {  # <printf-format-with-bytes> <code> <label>
  local out; out=$(printf "$1" | "$PY" "$MCP" parse) || fail "$3: parse exited nonzero"
  case "$out" in *'"ok":false'*) ;; *) fail "$3: expected ok=false, got: $out" ;; esac
  case "$out" in *"\"code\":$2"*) ;; *) fail "$3: expected code $2, got: $out" ;; esac
  pass
}

# ── 1. Accept path — the three pinned methods, well-formed ────────────────────────────
expect_ok '{"jsonrpc":"2.0","method":"initialize","id":1,"params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"x","version":"1"}}}' '"method":"initialize"' "initialize/pinned-rev"
expect_ok '{"jsonrpc":"2.0","method":"tools/list","id":2}' '"cursor":null' "tools-list/no-params"
expect_ok '{"jsonrpc":"2.0","method":"tools/list","id":3,"params":{"cursor":"abc"}}' '"cursor":"abc"' "tools-list/cursor"
expect_ok '{"jsonrpc":"2.0","method":"tools/call","id":4,"params":{"name":"message.send","arguments":{"to":"x","body":"hi"}}}' '"name":"message.send"' "tools-call/basic"
# arguments round-trip faithfully (sorted, no coercion or field loss)
expect_ok '{"jsonrpc":"2.0","method":"tools/call","id":5,"params":{"name":"t","arguments":{"to":"x","body":"hi"}}}' '"arguments":{"body":"hi","to":"x"}' "tools-call/args-roundtrip"
# arguments omitted -> normalized to {} (never a coerced null)
expect_ok '{"jsonrpc":"2.0","method":"tools/call","id":6,"params":{"name":"t"}}' '"arguments":{}' "tools-call/no-args"
# a bare-string id is a valid, distinct id (not coerced to/from the integer form)
expect_ok '{"jsonrpc":"2.0","method":"tools/list","id":"req-1"}' '"id":"req-1"' "tools-list/string-id"

# ── 2. Method allow-list + envelope ───────────────────────────────────────────────────
expect_deny '{"jsonrpc":"2.0","method":"tools/delete","id":1}' -32601 "unknown-method"
expect_deny '{"jsonrpc":"2.0","method":"notifications/initialized"}' -32600 "notification-no-id"
expect_deny '{"jsonrpc":"1.0","method":"tools/list","id":1}' -32600 "wrong-jsonrpc-version"
expect_deny '{"jsonrpc":2.0,"method":"tools/list","id":1}' -32600 "jsonrpc-number-not-string"
expect_deny '{"jsonrpc":"2.0","method":"tools/list"}' -32600 "missing-id"
expect_deny '{"jsonrpc":"2.0","method":123,"id":1}' -32600 "method-not-string"
expect_deny '{"jsonrpc":"2.0","method":"tools/list","id":1,"extra":9}' -32600 "unexpected-top-level-field"

# ── 3. Type coercion / ambiguity (no coercion; deny) ──────────────────────────────────
expect_deny '{"jsonrpc":"2.0","method":"tools/list","id":1.5}' -32600 "float-id"
expect_deny '{"jsonrpc":"2.0","method":"tools/list","id":true}' -32600 "bool-id"
expect_deny '{"jsonrpc":"2.0","method":"tools/list","id":null}' -32600 "null-id"
expect_deny '{"jsonrpc":"2.0","method":"tools/list","id":[1]}' -32600 "container-id"
expect_deny '[{"jsonrpc":"2.0","method":"tools/list","id":1}]' -32600 "batch-array-denied"
expect_deny '{"jsonrpc":"2.0","method":"tools/list","method":"initialize","id":1}' -32600 "duplicate-json-key"
# a string id outside _ID_STR_RE's conservative charset (space/brace/unicode) is denied,
# not coerced — deliberate narrowing so no exotic id smuggles past Step-5's id-space.
expect_deny '{"jsonrpc":"2.0","method":"tools/list","id":"a b"}' -32600 "string-id-bad-charset"

# ── 4. Parse-level hostile framing ────────────────────────────────────────────────────
expect_deny '{"jsonrpc":"2.0","method":"tools/list","id":1} trailing' -32700 "trailing-bytes"
expect_deny 'not json at all' -32700 "not-json"
expect_deny '{"jsonrpc":"2.0","method":"tools/list","id":1' -32700 "truncated-json"
expect_deny_raw '\377\n' -32700 "non-utf8-byte"
expect_deny_raw '\357\273\277{"jsonrpc":"2.0","method":"tools/list","id":1}\n' -32700 "leading-BOM"
# bareword NaN/Infinity/-Infinity are NOT JSON (RFC 8259); parse_constant rejects them ->
# they can neither reach ok:true (enforce_caps doesn't bound floats) nor poison emit()'s
# own verdict line. This is the one differential Fable found on PR #7 — its regression test.
expect_deny '{"jsonrpc":"2.0","method":"tools/call","id":1,"params":{"name":"t","arguments":{"x":NaN}}}' -32700 "bareword-NaN"
expect_deny '{"jsonrpc":"2.0","method":"tools/call","id":1,"params":{"name":"t","arguments":{"x":Infinity}}}' -32700 "bareword-Infinity"
expect_deny '{"jsonrpc":"2.0","method":"tools/call","id":1,"params":{"name":"t","arguments":{"x":-Infinity}}}' -32700 "bareword-neg-Infinity"

# ── 5. Per-method params schema ───────────────────────────────────────────────────────
expect_deny '{"jsonrpc":"2.0","method":"initialize","id":1,"params":{"protocolVersion":"1999-01-01","capabilities":{},"clientInfo":{}}}' -32602 "initialize-wrong-rev"
expect_deny '{"jsonrpc":"2.0","method":"initialize","id":1,"params":{"protocolVersion":"2025-06-18","capabilities":[]}}' -32602 "capabilities-not-object"
expect_deny '{"jsonrpc":"2.0","method":"initialize","id":1,"params":{"protocolVersion":"2025-06-18","weird":1}}' -32602 "initialize-unexpected-param"
expect_deny '{"jsonrpc":"2.0","method":"tools/call","id":1,"params":{"arguments":{}}}' -32602 "tools-call-missing-name"
expect_deny '{"jsonrpc":"2.0","method":"tools/call","id":1,"params":{"name":"bad name","arguments":{}}}' -32602 "tools-call-unsafe-name"
expect_deny '{"jsonrpc":"2.0","method":"tools/call","id":1,"params":{"name":"t","arguments":"x"}}' -32602 "tools-call-args-not-object"
expect_deny '{"jsonrpc":"2.0","method":"tools/call","id":1,"params":{"name":"t","arguments":{},"sneak":1}}' -32602 "tools-call-unexpected-param"
expect_deny '{"jsonrpc":"2.0","method":"tools/list","id":1,"params":{"cursor":9}}' -32602 "cursor-not-string"

# ── 6. Caps (policy denials, -32000) — driven with tiny test-only env caps ────────────
expect_deny_env 'AGENT_OS_MCP_MAX_BYTES=64' '{"jsonrpc":"2.0","method":"tools/call","id":1,"params":{"name":"t","arguments":{}}}' -32000 "oversized-message"
expect_deny_env 'AGENT_OS_MCP_MAX_DEPTH=4' '{"jsonrpc":"2.0","method":"tools/call","id":1,"params":{"name":"t","arguments":{"a":{"b":{"c":1}}}}}' -32000 "depth-cap"
expect_deny_env 'AGENT_OS_MCP_MAX_ITEMS=6' '{"jsonrpc":"2.0","method":"tools/call","id":1,"params":{"name":"t","arguments":{"a":1,"b":2,"c":3,"d":4,"e":5,"f":6,"g":7}}}' -32000 "items-cap"
expect_deny_env 'AGENT_OS_MCP_MAX_STRING=20' '{"jsonrpc":"2.0","method":"tools/call","id":1,"params":{"name":"t","arguments":{"x":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}}}' -32000 "string-cap"
# escaped \u0000 decodes to a raw NUL byte -> INV-1 control scrub denies it (-32000)
expect_deny '{"jsonrpc":"2.0","method":"tools/call","id":1,"params":{"name":"t","arguments":{"x":"\u0000"}}}' -32000 "control-char-in-string"
# a lone surrogate (\ud800, no low half) is un-encodable UTF-8 -> enforce_caps byte-count
# encode fails closed at policy; it cannot survive to a downstream arg-check as a valid str.
expect_deny '{"jsonrpc":"2.0","method":"tools/call","id":1,"params":{"name":"t","arguments":{"x":"\ud800"}}}' -32000 "lone-surrogate"

# ── 7. Stateful within-stream duplicate-id (first accepts, reuse denies) ──────────────
dup=$(printf '%s\n%s\n' \
  '{"jsonrpc":"2.0","method":"tools/list","id":7}' \
  '{"jsonrpc":"2.0","method":"tools/list","id":7}' | "$PY" "$MCP" parse) || fail "dup-id: nonzero"
l1=$(printf '%s\n' "$dup" | sed -n 1p)
l2=$(printf '%s\n' "$dup" | sed -n 2p)
case "$l1" in *'"ok":true'*) ;; *) fail "dup-id: first message should accept, got: $l1" ;; esac
case "$l2" in *'"code":-32000'*) ;; *) fail "dup-id: reused id should deny -32000, got: $l2" ;; esac
pass

# ── 8. Stream framing: one verdict per line, blanks skipped ───────────────────────────
strm=$(printf '%s\n%s\n%s\n' \
  '{"jsonrpc":"2.0","method":"tools/list","id":10}' \
  'garbage' \
  '{"jsonrpc":"2.0","method":"tools/call","id":11,"params":{"name":"t"}}' | "$PY" "$MCP" parse)
n=$(printf '%s\n' "$strm" | grep -c '"ok":')
[ "$n" -eq 3 ] || fail "stream: expected 3 verdicts (one per line), got $n"
pass
blanks=$(printf '\n%s\n\n' '{"jsonrpc":"2.0","method":"tools/list","id":12}' | "$PY" "$MCP" parse)
nb=$(printf '%s\n' "$blanks" | grep -c '"ok":')
[ "$nb" -eq 1 ] || fail "blank-lines: expected 1 verdict, got $nb"
pass

# ── 9. Classifier-not-gate: `parse` exits 0 even when it denies (Step 5 gates) ────────
printf '%s\n' 'garbage' | "$PY" "$MCP" parse >/dev/null; rc=$?
[ "$rc" -eq 0 ] || fail "classifier: parse must exit 0 even when denying (got $rc)"
pass

echo "mcp-battery: OK — $PASS checks passed (conformance + hostile-input battery)"
