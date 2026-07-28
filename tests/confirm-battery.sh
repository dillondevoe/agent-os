#!/usr/bin/env bash
# confirm-battery.sh — property tests for bin/confirm (Phase 2 · Step 6, "the wall's mouth").
#
# Usage: confirm-battery.sh <confirm> <broker> <taint> <audit> <real-registry.json> <scratch-dir>
# Exits 0 iff EVERY property holds. Run by the flake check (checks.<sys>.confirm-channel) and
# standalone. Each group maps to a spec §8 clause; a failure names it.
#
# bin/confirm is a PURE renderer/relayer/collector (§2): it never re-decides, re-audits, or
# touches taint. So most groups drive the client DIRECTLY (stdin request -> stdout verdict) with
# the two backends exercised via test affordances:
#   * telegram  — a one-shot scripted RELAY on 127.0.0.1 (below). The relay speaks the exact
#                 domain-separated-HMAC wire protocol the client expects, and its env knobs
#                 (RELAY_DECISION/NONCE/SESSION/FROMID/REFLECT/BADMAC/EOF/HANG/DUMP) let each leg
#                 forge one specific hop so the client's fail-closed check for it can be asserted.
#   * getty     — file-backed AGENT_OS_CONFIRM_GETTY_IN/OUT stand in for the second console.
# Group 9 is the ONLY leg that starts the REAL broker: route->confirm(seam=bin/confirm)->approve/
# deny->invoke(stub)/withhold, then the audit chain must still verify and carry the nonce.
#
# EVERY negative outcome the client can produce is a fail-closed DENY (approved:false). The client
# can never fail OPEN — only fail to approve (§2). The relay's compromise is out-of-scope (it is
# TCB, §3.1); these tests assert the client's behavior against an HONEST relay and against a relay
# that forges exactly one field, which the domain-separated MAC / nonce / from-id checks must catch.
set -u

CONFIRM="${1:?path to bin/confirm required}"
BROKER="${2:?path to bin/broker required}"
TAINT="${3:?path to bin/taint required}"
AUDIT="${4:?path to bin/audit required}"
REGREAL="${5:?path to the materialized real registry json required}"
SCRATCH="${6:?scratch dir required}"
PY="${PYTHON:-python3}"

fail() { echo "confirm-battery FAIL: $*" >&2; exit 1; }

mkdir -p "$SCRATCH/state"
DILLON="424242"                                   # the pinned Telegram user-id for these tests
SECRET="$SCRATCH/relay.secret"; printf 'confirm-relay-test-key' > "$SECRET"
NONCE="00112233445566778899aabbccddeeff"          # a fixed 128-bit nonce for the direct legs
REQ='{"capability":"message.send","tier":"T2","provenance":"TAINTED",'
REQ="$REQ"'"destination":"user@peer.example","typed_args":{"recipient":"user@peer.example","body":"hello"},'
REQ="$REQ"'"nonce":"'"$NONCE"'","session_id":7}'

# ── verdict helpers ──────────────────────────────────────────────────────────
japproved() { printf '%s' "$1" | "$PY" -c 'import sys,json;print(str(json.load(sys.stdin)["approved"]).lower())'; }
approved_is() { local g; g="$(japproved "$1")" || fail "confirm output not JSON: $1"
  [ "$g" = "$2" ] || fail "approved expected $2 got $g: $1"; }
reason_has() { case "$1" in *"$2"*) : ;; *) fail "reason lacks '$2': $1";; esac; }
wait_file() { "$PY" -c 'import sys,os,time
p=sys.argv[1]
for _ in range(500):
    if os.path.exists(p): sys.exit(0)
    time.sleep(0.01)
sys.exit(1)' "$1"; }

# ── the one-shot scripted relay (the telegram TCB stand-in) ──────────────────
RELAY="$SCRATCH/relay.py"
cat > "$RELAY" <<'PYEOF'
import sys, os, json, socket, hmac, hashlib, time
port = int(sys.argv[1]); secret = open(sys.argv[2], "rb").read().strip()
def mac(dir_tag, obj):
    p = json.dumps({"dir": dir_tag, "body": obj}, sort_keys=True,
                   separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hmac.new(secret, p, hashlib.sha256).hexdigest()
srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("127.0.0.1", port)); srv.listen(1)          # callers pass 0 -> OS-assigned free port
actual_port = srv.getsockname()[1]                    # the concrete port the client must dial
_rp = os.environ["RELAY_READY"]
with open(_rp + ".tmp", "w") as _fh: _fh.write(str(actual_port))
os.replace(_rp + ".tmp", _rp)                         # atomic: ready file appears only fully-written
srv.settimeout(15)
try:
    conn, _ = srv.accept()
except socket.timeout:
    sys.exit(0)
buf = b""
try:
    while b"\n" not in buf:
        c = conn.recv(65536)
        if not c: break
        buf += c
except OSError:
    conn.close(); sys.exit(0)
if os.environ.get("RELAY_EOF") == "1":            # accept+read then hang up -> client sees EOF
    conn.close(); sys.exit(0)
if os.environ.get("RELAY_HANG") == "1":           # never answer -> client's read-timeout fires
    time.sleep(3); conn.close(); sys.exit(0)
try:
    req = json.loads(buf.split(b"\n", 1)[0].decode("utf-8")); frame = req["frame"]
except Exception:
    conn.close(); sys.exit(0)
if os.environ.get("RELAY_DUMP"):                  # capture the transmitted bytes for assertions
    with open(os.environ["RELAY_DUMP"], "w") as fh: json.dump(req, fh)
nonce = frame.get("nonce"); session = frame.get("session_id")
decision = os.environ.get("RELAY_DECISION", "approve")
resp_nonce = os.environ.get("RELAY_NONCE", nonce)
resp_session = json.loads(os.environ["RELAY_SESSION"]) if "RELAY_SESSION" in os.environ else session
from_id = os.environ.get("RELAY_FROMID", frame.get("expect_from_id"))
body = {"nonce": resp_nonce, "session_id": resp_session, "decision": decision}
if os.environ.get("RELAY_REFLECT") == "1":
    m = req.get("mac")                            # replay the dir:"req" MAC as the response MAC
elif os.environ.get("RELAY_BADMAC") == "1":
    m = "0" * 64
else:
    m = mac("resp", body)
resp = {"nonce": resp_nonce, "session_id": resp_session, "decision": decision,
        "from_id": from_id, "mac": m}
try:
    conn.sendall(json.dumps(resp).encode("utf-8") + b"\n")
except OSError:
    pass
conn.close()
PYEOF

# Each relay binds an OS-ephemeral port (arg 0) and writes the chosen port into its own unique
# ready file; the caller reads it back. There is NO shared port counter: legs run inside $(...)
# command substitution (a subshell), so a counter mutation would not persist to the parent anyway
# and every leg would silently reuse one port. A per-leg mktemp ready file + ephemeral port makes
# each leg fully isolated and race-free.
# tg <request-json>: caller exports any RELAY_* knobs + WINDOW; returns the client's verdict line.
tg() {
  local ready; ready="$(mktemp "$SCRATCH/ready.XXXXXX")"; rm -f "$ready"
  RELAY_READY="$ready" "$PY" "$RELAY" 0 "$SECRET" & local rpid=$!
  wait_file "$ready" || { kill "$rpid" 2>/dev/null; wait "$rpid" 2>/dev/null; fail "relay never became ready"; }
  local port; port="$(cat "$ready")"
  local out
  out="$( AGENT_OS_CONFIRM_CHANNELS=telegram \
          AGENT_OS_CONFIRM_RELAY_ADDR=127.0.0.1 AGENT_OS_CONFIRM_RELAY_PORT="$port" \
          AGENT_OS_CONFIRM_RELAY_SECRET_FILE="$SECRET" AGENT_OS_CONFIRM_DILLON_USER_ID="$DILLON" \
          AGENT_OS_CONFIRM_HUMAN_WINDOW_S="${WINDOW:-5}" AGENT_OS_CONFIRM_DIR="$SCRATCH/state" \
          "$PY" "$CONFIRM" <<<"$1" )"
  wait "$rpid" 2>/dev/null
  printf '%s' "$out"
}
tg_reset() { unset RELAY_DECISION RELAY_NONCE RELAY_SESSION RELAY_FROMID RELAY_REFLECT \
                   RELAY_BADMAC RELAY_EOF RELAY_HANG RELAY_DUMP WINDOW 2>/dev/null || true; }

# ── 1. SEAM CONTRACT (§8.1): valid request -> well-formed verdict on BOTH backends; malformed
#       stdin -> deny, no crash. ──────────────────────────────────────────────
CODE="$("$PY" -c 'import base64,sys;print(base64.b32encode(bytes.fromhex(sys.argv[1])).decode()[:8])' "$NONCE")"
GIN="$SCRATCH/gin"; GOUT="$SCRATCH/gout"; printf 'approve %s\n' "$CODE" > "$GIN"
OUT="$( AGENT_OS_CONFIRM_CHANNELS=getty AGENT_OS_CONFIRM_GETTY_IN="$GIN" AGENT_OS_CONFIRM_GETTY_OUT="$GOUT" \
        AGENT_OS_CONFIRM_DIR="$SCRATCH/state" AGENT_OS_CONFIRM_HUMAN_WINDOW_S=5 "$PY" "$CONFIRM" <<<"$REQ" )"
approved_is "$OUT" true; reason_has "$OUT" getty-approved
grep -q "approve $CODE" "$GOUT" || fail "getty frame missing the approve-code prompt"
# getty explicit deny
printf 'deny\n' > "$GIN"
OUT="$( AGENT_OS_CONFIRM_CHANNELS=getty AGENT_OS_CONFIRM_GETTY_IN="$GIN" AGENT_OS_CONFIRM_GETTY_OUT="$GOUT" \
        AGENT_OS_CONFIRM_DIR="$SCRATCH/state" AGENT_OS_CONFIRM_HUMAN_WINDOW_S=5 "$PY" "$CONFIRM" <<<"$REQ" )"
approved_is "$OUT" false; reason_has "$OUT" getty-denied
# telegram approve (honest relay)
export RELAY_DECISION=approve; OUT="$(tg "$REQ")"; tg_reset
approved_is "$OUT" true; reason_has "$OUT" telegram-approved
# malformed stdin -> deny, no crash
OUT="$( AGENT_OS_CONFIRM_CHANNELS=getty "$PY" "$CONFIRM" <<<'this is not json' )"
approved_is "$OUT" false; reason_has "$OUT" confirm-malformed-request
# valid JSON but no nonce -> malformed
OUT="$( AGENT_OS_CONFIRM_CHANNELS=getty "$PY" "$CONFIRM" <<<'{"capability":"message.send","tier":"T2"}' )"
approved_is "$OUT" false; reason_has "$OUT" confirm-malformed-request
# non-dict top-level -> malformed
OUT="$( AGENT_OS_CONFIRM_CHANNELS=getty "$PY" "$CONFIRM" <<<'[1,2,3]' )"
approved_is "$OUT" false; reason_has "$OUT" confirm-malformed-request

# ── 2. FAIL-CLOSED CHANNELS (§8.2): none live -> deny; each channel that can't reach/authn is
#       "unreachable" and falls to the NEXT, never to allow; last-resort is always deny. ───────
OUT="$( AGENT_OS_CONFIRM_CHANNELS= "$PY" "$CONFIRM" <<<"$REQ" )"
approved_is "$OUT" false; reason_has "$OUT" confirm-no-channel-configured
OUT="$( AGENT_OS_CONFIRM_CHANNELS=telegram "$PY" "$CONFIRM" <<<"$REQ" )"   # relay env unset
approved_is "$OUT" false; reason_has "$OUT" confirm-relay-unconfigured
OUT="$( AGENT_OS_CONFIRM_CHANNELS=getty AGENT_OS_CONFIRM_GETTY_TTY=/nonexistent/tty-xyz \
        "$PY" "$CONFIRM" <<<"$REQ" )"
approved_is "$OUT" false; reason_has "$OUT" confirm-console-unavailable
# telegram configured but the port is closed -> unreachable -> deny (never a wedge)
OUT="$( AGENT_OS_CONFIRM_CHANNELS=telegram AGENT_OS_CONFIRM_RELAY_ADDR=127.0.0.1 \
        AGENT_OS_CONFIRM_RELAY_PORT=1 AGENT_OS_CONFIRM_RELAY_SECRET_FILE="$SECRET" \
        AGENT_OS_CONFIRM_DILLON_USER_ID="$DILLON" AGENT_OS_CONFIRM_HUMAN_WINDOW_S=3 \
        "$PY" "$CONFIRM" <<<"$REQ" )"
approved_is "$OUT" false; reason_has "$OUT" confirm-relay-unreachable
# an UNKNOWN channel name is skipped, never allowed
OUT="$( AGENT_OS_CONFIRM_CHANNELS=bogus "$PY" "$CONFIRM" <<<"$REQ" )"
approved_is "$OUT" false; reason_has "$OUT" confirm-no-channel
# fall-through: unreachable telegram THEN absent getty -> still deny (order preserved)
OUT="$( AGENT_OS_CONFIRM_CHANNELS=telegram,getty AGENT_OS_CONFIRM_GETTY_TTY=/nonexistent/tty-xyz \
        "$PY" "$CONFIRM" <<<"$REQ" )"
approved_is "$OUT" false; reason_has "$OUT" confirm-console-unavailable

# ── 3. NONCE ECHO + MAC AUTH (§8.3): honest relay approves; each single forged field is caught
#       fail-closed by the matching check. ─────────────────────────────────────
export RELAY_DECISION=approve RELAY_NONCE="ffffffffffffffffffffffffffffffff"; OUT="$(tg "$REQ")"; tg_reset
approved_is "$OUT" false; reason_has "$OUT" confirm-nonce-mismatch          # replay/stale nonce
export RELAY_DECISION=approve RELAY_REFLECT=1; OUT="$(tg "$REQ")"; tg_reset
approved_is "$OUT" false; reason_has "$OUT" confirm-relay-badmac            # dir:"req" reflection
export RELAY_DECISION=approve RELAY_BADMAC=1; OUT="$(tg "$REQ")"; tg_reset
approved_is "$OUT" false; reason_has "$OUT" confirm-relay-badmac
export RELAY_DECISION=approve RELAY_FROMID="99999"; OUT="$(tg "$REQ")"; tg_reset
approved_is "$OUT" false; reason_has "$OUT" confirm-wrong-user              # callback authenticity
export RELAY_DECISION=approve RELAY_SESSION=999; OUT="$(tg "$REQ")"; tg_reset
approved_is "$OUT" false; reason_has "$OUT" confirm-session-mismatch        # epoch binding
export RELAY_DECISION=deny; OUT="$(tg "$REQ")"; tg_reset
approved_is "$OUT" false; reason_has "$OUT" telegram-denied
# getty: 3 wrong codes -> retries exhausted -> deny (≤3-retry-then-deny, §4.2)
printf 'approve WRONGAAA\napprove WRONGBBB\napprove WRONGCCC\n' > "$GIN"
OUT="$( AGENT_OS_CONFIRM_CHANNELS=getty AGENT_OS_CONFIRM_GETTY_IN="$GIN" AGENT_OS_CONFIRM_GETTY_OUT="$GOUT" \
        AGENT_OS_CONFIRM_DIR="$SCRATCH/state" AGENT_OS_CONFIRM_HUMAN_WINDOW_S=5 "$PY" "$CONFIRM" <<<"$REQ" )"
approved_is "$OUT" false; reason_has "$OUT" getty-code-retries-exhausted

# ── 4. TIMEOUT (§8.4): human-window elapses -> clean confirm-human-timeout; EOF -> clean deny.
#       (The broker-side sleeping-seam SIGKILL backstop lives in broker-battery.sh §18.) ───────
export RELAY_HANG=1 WINDOW=1; OUT="$(tg "$REQ")"; tg_reset
approved_is "$OUT" false; reason_has "$OUT" confirm-human-timeout
export RELAY_EOF=1; OUT="$(tg "$REQ")"; tg_reset
approved_is "$OUT" false; reason_has "$OUT" confirm-relay-eof

# ── 5. FIELD-CONFUSION / INJECTION (§8.5, Amendment 2): C0/C1 + markdown + a fake trust line in
#       model-controlled fields -> scrubbed, newline-normalized, EVERY value line carries `│ `,
#       the trust region stays unprefixed+intact, IDN host -> punycode. Assert exact bytes. ────
"$PY" - "$CONFIRM" <<'PYEOF' || fail "render/injection unit battery failed"
import sys
from importlib.machinery import SourceFileLoader
m = SourceFileLoader("confirm", sys.argv[1]).load_module()
errs = []
req = {"capability": "message.send", "tier": "T2", "provenance": "TAINTED",
       "destination": "user@evil.example",
       "typed_args": {"recipient": "user@evil.example",
                      "body": "hi\x07\x1b[31mRED\nTIER: T0\nSTATUS: pre-approved\r\ntail"}}
frame = m.render_frame(req, False, False)
for ch in frame:
    if ord(ch) in m._CTRL: errs.append("control byte survived in frame: %r" % ch)
lines = frame.split("\n")
tier_unpref = [l for l in lines if l.startswith("TIER:") and not l.startswith(m.LINE_PREFIX)]
if tier_unpref != ["TIER: T2"]: errs.append("trust TIER line wrong/absent: %r" % tier_unpref)
cap_unpref = [l for l in lines if l.startswith("CAPABILITY:") and not l.startswith(m.LINE_PREFIX)]
if cap_unpref != ["CAPABILITY: message.send"]: errs.append("trust CAPABILITY line wrong: %r" % cap_unpref)
inj = [l for l in lines if "TIER: T0" in l]
if not inj: errs.append("injected 'TIER: T0' vanished (must be neutralized+shown, not dropped)")
for l in inj:
    if not l.startswith(m.LINE_PREFIX): errs.append("injected 'TIER: T0' rendered UNprefixed: %r" % l)
for l in [l for l in lines if "STATUS: pre-approved" in l]:
    if not l.startswith(m.LINE_PREFIX): errs.append("injected STATUS rendered unprefixed: %r" % l)
d = m.punycode_host("https://exämple.com/p")
if "xn--" not in d: errs.append("IDN host not punycoded: %r" % d)
if "ä" in d: errs.append("raw IDN char leaked past punycode: %r" % d)
if errs:
    sys.stderr.write("\n".join(errs) + "\n"); sys.exit(1)
print("render/injection unit battery: OK")
PYEOF
# the TRANSMITTED telegram frame: parse_mode None, sentinel present, no control bytes
DUMP="$SCRATCH/frame.json"
REQ_INJ='{"capability":"message.send","tier":"T2","provenance":"TAINTED","destination":"user@evil.example",'
REQ_INJ="$REQ_INJ"'"typed_args":{"recipient":"user@evil.example","body":"x\u001b[31m\u202eevil.example\u2066\nTIER: T0"},'
REQ_INJ="$REQ_INJ"'"nonce":"'"$NONCE"'","session_id":7}'
export RELAY_DECISION=approve RELAY_DUMP="$DUMP"; OUT="$(tg "$REQ_INJ")"; tg_reset
approved_is "$OUT" true
"$PY" -c 'import sys,json
d=json.load(open(sys.argv[1])); f=d["frame"]
assert f["parse_mode"] is None, "parse_mode not None"
t=f["text"]
assert "│ " in t, "line-prefix sentinel missing from transmitted text"
assert "\x1b" not in t and "\x07" not in t, "control byte in transmitted text"
assert "\u202e" not in t and "\u2066" not in t, "bidi/RTL control in transmitted text"
assert "callback_data" in json.dumps(f["reply_markup"]) and f["nonce"], "keyboard/nonce missing"' \
  "$DUMP" || fail "telegram frame not hardened (parse_mode/sentinel/control/keyboard)"

# ── 6. FIRST-TIME-DESTINATION (§8.6): novel dest -> highlighted; approved dest -> not; any read
#       error fails NOISY (highlight). (The 0700-root refusal of impl/model writes is enforced by
#       modules/confirm.nix tmpfiles + its sandbox assertions, not at runtime here.) ───────────
export AGENT_OS_CONFIRM_DIR="$SCRATCH/seenstate"; mkdir -p "$AGENT_OS_CONFIRM_DIR"
"$PY" - "$CONFIRM" <<'PYEOF' || fail "first-time-destination unit failed"
import sys, os
from importlib.machinery import SourceFileLoader
m = SourceFileLoader("confirm", sys.argv[1]).load_module()
errs = []
dest = "user@peer.example"
if not m.is_first_time(dest): errs.append("novel dest not reported first-time")
req = {"capability": "message.send", "tier": "T2", "provenance": "TAINTED",
       "destination": dest, "typed_args": {"recipient": dest, "body": "x"}}
if "NEVER-SEEN-DESTINATION" not in m.render_frame(req, True, False): errs.append("banner missing when first_time")
if "NEVER-SEEN-DESTINATION" in m.render_frame(req, False, False): errs.append("banner shown when not first_time")
m.mark_seen(dest)
if m.is_first_time(dest): errs.append("dest still first-time after mark_seen (approve)")
if not m.is_first_time("other@x.example"): errs.append("an unrelated dest is not first-time")
os.environ["AGENT_OS_CONFIRM_DIR"] = "/proc/nonexistent-xyz/deny"     # force a read error
if not m.is_first_time(dest): errs.append("seen-set read error did not fail-noisy to first-time")
if errs:
    sys.stderr.write("\n".join(errs) + "\n"); sys.exit(1)
print("first-time-destination unit: OK")
PYEOF
unset AGENT_OS_CONFIRM_DIR

# ── 7. PAYLOAD PREVIEW (§8.7): truncation at the cap + mark; control-chars stripped; markup is
#       literal (never executed/rendered). ────────────────────────────────────
"$PY" - "$CONFIRM" <<'PYEOF' || fail "payload-preview unit failed"
import sys
from importlib.machinery import SourceFileLoader
m = SourceFileLoader("confirm", sys.argv[1]).load_module()
errs = []
p = m.payload_preview("message.send", {"recipient": "r", "body": "A" * 1000})
if m.TRUNC not in p: errs.append("no truncation mark on an oversized preview")
if len(p) > m.MAXPREVIEW + len(m.TRUNC) + 64: errs.append("preview not capped near MAXPREVIEW: %d" % len(p))
p2 = m.payload_preview("message.send", {"recipient": "r", "body": "a\x07b\x1bc\x00d\u202ee\u2066f"})
if any(c in p2 for c in "\x07\x1b\x00\u202e\u2066"): errs.append("control/bidi chars survived the preview")
p3 = m.payload_preview("message.send", {"recipient": "r", "body": "*bold* [x](y)"})
if "*bold*" not in p3 or "[x](y)" not in p3: errs.append("markdown not preserved literally (inert)")
if "\x00" in m.scrub({"k": "v\x00x"}): errs.append("scrub left a NUL in a dict value")
if "\u202e" in m.scrub("a\u202eb") or "\u2066" in m.scrub("a\u2066b"): errs.append("scrub left a bidi/RTL control")
if errs:
    sys.stderr.write("\n".join(errs) + "\n"); sys.exit(1)
print("payload-preview unit: OK")
PYEOF

# ── 8. SINGLE-FLIGHT (§8.8): the client handles exactly ONE request; a second request smuggled
#       into the same stdin makes the whole read malformed -> deny (never two approvals). ──────
OUT="$( AGENT_OS_CONFIRM_CHANNELS=getty "$PY" "$CONFIRM" <<<"$REQ$REQ" )"
approved_is "$OUT" false; reason_has "$OUT" confirm-malformed-request

# ── 9. INTEGRATION (§8.9): drive the REAL broker with AGENT_OS_CONFIRM_SEAM=bin/confirm + the
#       scripted relay. A T1 mem.remember and a T2 message.send each round-trip route->confirm->
#       approve->invoke(stub) [DATA-fenced] and route->confirm->deny->withhold; the audit chain
#       still verifies and carries route/approve/deny with the nonce. ─────────────────────────
export AGENT_OS_TAINT_DIR="$SCRATCH/taint" AGENT_OS_AUDIT_DIR="$SCRATCH/audit"
export AUDIT_BIN="$AUDIT" TAINT_BIN="$TAINT" AGENT_OS_REGISTRY="$REGREAL"
mkdir -p "$AGENT_OS_TAINT_DIR" "$AGENT_OS_AUDIT_DIR"
LOG="$AGENT_OS_AUDIT_DIR/audit.log"
"$PY" "$TAINT" reset --confirm-human >/dev/null 2>&1 || fail "could not bless a clean session"

SEAM_INVOKE="$SCRATCH/seam_invoke.py"
cat > "$SEAM_INVOKE" <<'PYEOF'
import sys, json, os
try: req = json.load(sys.stdin)
except Exception: req = {}
out = {"ok": True, "content": "INVOKED-" + json.dumps(req.get("arguments", {}), sort_keys=True)}
if "INVOKE_KEY" in os.environ: out["meta"] = {"key": os.environ["INVOKE_KEY"]}
sys.stdout.write(json.dumps(out))
PYEOF

jf() { printf '%s' "$1" | "$PY" -c 'import sys,json
o=json.load(sys.stdin); v=eval(sys.argv[1])
sys.stdout.write("true" if v is True else "false" if v is False else "None" if v is None else str(v))' "$2"; }

# broker_confirm <verdict-json> <RELAY_DECISION> [INVOKE_KEY]: start a one-shot relay, run the
# REAL broker with bin/confirm as the confirm seam, echo the broker's result line.
broker_confirm() {
  local ready; ready="$(mktemp "$SCRATCH/ready.XXXXXX")"; rm -f "$ready"
  RELAY_READY="$ready" RELAY_DECISION="$2" "$PY" "$RELAY" 0 "$SECRET" & local rpid=$!
  wait_file "$ready" || { kill "$rpid" 2>/dev/null; wait "$rpid" 2>/dev/null; fail "broker relay not ready"; }
  local port; port="$(cat "$ready")"
  local out
  out="$( printf '%s\n' "$1" | env \
      AGENT_OS_CONFIRM_SEAM="$CONFIRM" AGENT_OS_INVOKE_SEAM="$SEAM_INVOKE" \
      ${3:+INVOKE_KEY="$3"} \
      AGENT_OS_CONFIRM_CHANNELS=telegram AGENT_OS_CONFIRM_RELAY_ADDR=127.0.0.1 \
      AGENT_OS_CONFIRM_RELAY_PORT="$port" AGENT_OS_CONFIRM_RELAY_SECRET_FILE="$SECRET" \
      AGENT_OS_CONFIRM_DILLON_USER_ID="$DILLON" AGENT_OS_CONFIRM_HUMAN_WINDOW_S=5 \
      AGENT_OS_CONFIRM_TIMEOUT_S=30 AGENT_OS_CONFIRM_DIR="$SCRATCH/state" \
      "$PY" "$BROKER" run )"
  wait "$rpid" 2>/dev/null
  printf '%s' "$out"
}

# T1 mem.remember, approved -> stamped -> DATA-fenced content
V_REMEMBER='{"ok":true,"method":"tools/call","id":91,"name":"mem.remember","arguments":{"namespace":"session","content":"note"}}'
OUT="$(broker_confirm "$V_REMEMBER" approve "session/i1")"
[ "$(jf "$OUT" 'o["result"]["content_type"]')" = "data" ] || fail "T1 remember approved not DATA-fenced: $OUT"
# T2 message.send, approved -> invoke stub -> DATA-fenced content
V_SEND='{"ok":true,"method":"tools/call","id":92,"name":"message.send","arguments":{"recipient":"peer@ex.example","body":"hi"}}'
OUT="$(broker_confirm "$V_SEND" approve)"
[ "$(jf "$OUT" 'o["result"]["content_type"]')" = "data" ] || fail "T2 send approved not DATA-fenced: $OUT"
# T2 message.send, DENIED at the confirm channel -> withheld (surfaced reason, invoke NOT reached)
OUT="$(broker_confirm "$V_SEND" deny)"
[ "$(jf "$OUT" 'o["ok"]')" = "false" ] || fail "denied confirm did not deny the call: $OUT"
reason_has "$OUT" telegram-denied
case "$OUT" in *INVOKED-*) fail "invoke ran despite a confirm DENY (fail-open): $OUT";; esac

# the audit chain still verifies and carries the broker's confirm records with the nonce
"$PY" "$AUDIT" verify >/dev/null 2>&1 || fail "audit chain does not verify after confirm round-trips"
grep -q '"event":"route"' "$LOG"   || fail "audit log missing a broker route decision"
grep -q '"event":"approve"' "$LOG" || fail "audit log missing a broker approve decision"
grep -q '"event":"deny"' "$LOG"    || fail "audit log missing a broker deny decision"
grep -q '"nonce":"[0-9a-f]' "$LOG" || fail "audit route/approve/deny records carry no nonce"

echo "confirm-battery: ALL PROPERTIES HOLD"
