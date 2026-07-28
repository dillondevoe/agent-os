#!/usr/bin/env bash
# broker-battery.sh — property tests for bin/broker (Phase 2 · Step 5, "the wall").
#
# Usage: broker-battery.sh <broker> <taint> <audit> <mcp> <real-registry.json> <scratch-dir>
# Exits 0 iff EVERY property holds. Run by the flake check (checks.<sys>.broker-core) and
# standalone. Each property maps to a spec §6 junction / §7 test-plan clause; a failure names it.
#
# The broker drives the REAL taint (Step 3) and REAL audit (Step 2) primitives as children,
# so this is also an integration test: after the run the audit chain must still verify and
# must contain the broker's routing records. The two effect SEAMS (confirm, invoke) default
# to fail-closed stubs; where a leg needs the fuller path it injects a tiny scripted seam via
# AGENT_OS_CONFIRM_SEAM / AGENT_OS_INVOKE_SEAM (the only way to reach past the stubs — exactly
# how production Step 6/7 will replace them).
#
# TWO registries are used on purpose:
#   * the REAL materialized registry (arg $5) — routing matrix, T3 non-expressibility,
#     enum membership (GAP-1), real arg types. This is the production contract.
#   * a TEST registry ($SCRATCH/testreg.json) written below — a fixture that makes net.fetch
#     reachable to the invoke stage (net.fetch is T2/confirm-gated, so the UNTRUSTED-origin
#     return path is exercised against a fixture wired past the confirm seam). The broker's
#     origin policy is keyed on the capability NAME, so net.fetch is still UNTRUSTED here —
#     the code path under test is identical.
set -u

BROKER="${1:?path to bin/broker required}"
TAINT="${2:?path to bin/taint required}"
AUDIT="${3:?path to bin/audit required}"
MCP="${4:?path to bin/mcp required}"
REGREAL="${5:?path to the materialized real registry json required}"
SCRATCH="${6:?scratch dir required}"
PY="${PYTHON:-python3}"

fail() { echo "broker-battery FAIL: $*" >&2; exit 1; }

export AGENT_OS_TAINT_DIR="$SCRATCH/taint"
export AGENT_OS_AUDIT_DIR="$SCRATCH/audit"
export AUDIT_BIN="$AUDIT"
export TAINT_BIN="$TAINT"
export AGENT_OS_REGISTRY="$REGREAL"       # default; TESTREG legs override in a subshell
mkdir -p "$AGENT_OS_TAINT_DIR" "$AGENT_OS_AUDIT_DIR" "$SCRATCH/mark"
LOG="$AGENT_OS_AUDIT_DIR/audit.log"

"$PY" "$TAINT" reset --confirm-human --break-glass >/dev/null 2>&1 || fail "could not bless a clean start session"

# ── helpers ──────────────────────────────────────────────────────────────────
# Feed ONE verdict line to `broker run`; echo its single result line. (Broker's exit code is
# not captured here — `one` runs inside a command-substitution subshell, so a $? set here would
# not survive; the few legs that assert an exit code capture it directly at the pipe.)
one() { printf '%s\n' "$1" | "$PY" "$BROKER" run; }

# Extract a value from a JSON line by python expression over `o`. Prints a shell token
# (true/false/None/str); non-zero if invalid JSON or the path is absent (strict).
jf() { printf '%s' "$1" | "$PY" -c 'import sys,json
o=json.load(sys.stdin); v=eval(sys.argv[1])
sys.stdout.write("true" if v is True else "false" if v is False else "None" if v is None else str(v))' "$2"; }

taint_is() { local o; o="$("$PY" "$TAINT" status)" || fail "taint status errored"
  case "$o" in *"taint: $1"*) : ;; *) fail "taint expected '$1', got: $o";; esac; }

# The two injectable seams (env-driven; each leg sets the CONFIRM_*/INVOKE_* knobs it needs).
SEAM_CONFIRM="$SCRATCH/seam_confirm.py"
SEAM_INVOKE="$SCRATCH/seam_invoke.py"
cat > "$SEAM_CONFIRM" <<'PYEOF'
import sys, json, os, subprocess
try: json.load(sys.stdin)
except Exception: pass
if os.environ.get("CONFIRM_MARKER"): open(os.environ["CONFIRM_MARKER"], "w").close()
if os.environ.get("CONFIRM_RESET") == "1":
    # CF-1c: bump the epoch via the audited break-glass (channel-free) — this stub IS the confirm
    # channel, so it cannot drive the confirm-channel reset path without recursing. --confirm-human
    # ALONE no longer clears taint, so it would leave the epoch unbumped and void the race sim.
    subprocess.run([sys.executable, os.environ["TAINT_BIN"], "reset", "--confirm-human", "--break-glass"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
sys.stdout.write(json.dumps({"approved": os.environ.get("CONFIRM_APPROVE", "0") == "1",
                             "reason": "test"}))
PYEOF
cat > "$SEAM_INVOKE" <<'PYEOF'
import sys, json, os
try: req = json.load(sys.stdin)
except Exception: req = {}
if os.environ.get("INVOKE_MARKER"): open(os.environ["INVOKE_MARKER"], "w").close()
out = {"ok": os.environ.get("INVOKE_OK", "1") == "1",
       "content": os.environ.get("INVOKE_CONTENT",
                                 "CONTENT-" + json.dumps(req.get("arguments", {}), sort_keys=True))}
if "INVOKE_KEY" in os.environ: out["meta"] = {"key": os.environ["INVOKE_KEY"]}
if os.environ.get("INVOKE_LIE_ORIGIN") == "1": out["origin"] = "TRUSTED"   # broker MUST ignore this
sys.stdout.write(json.dumps(out))
PYEOF

# A minimal TEST registry: net.fetch WITHOUT the enum arg so it can reach the invoke stage.
TESTREG="$SCRATCH/testreg.json"
cat > "$TESTREG" <<'JSONEOF'
{
 "net.fetch":    {"tier":"T2","impl":"t","summary":"","args":{"url":"url"},
                  "sandbox":{"readWritePaths":[],"readOnlyPaths":[],"network":true}},
 "file.read":    {"tier":"T0","impl":"t","summary":"","args":{"path":"path"},
                  "sandbox":{"readWritePaths":[],"readOnlyPaths":["/var/lib/agent-os/safe-read"]}},
 "mem.recall":   {"tier":"T0","impl":"t","summary":"","args":{"namespace":"namespace","query":"string"},
                  "sandbox":{"readWritePaths":[],"readOnlyPaths":["/var/lib/agent-os/mem"]}},
 "mem.remember": {"tier":"T1","impl":"t","summary":"","args":{"namespace":"namespace","content":"string"},
                  "sandbox":{"readWritePaths":["/var/lib/agent-os/mem/session"],"readOnlyPaths":[]}}
}
JSONEOF

# reusable verdict fragments (real registry cap shapes)
V_FILEREAD='{"ok":true,"method":"tools/call","id":1,"name":"file.read","arguments":{"path":"/var/lib/agent-os/safe-read/x"}}'
V_REMEMBER='{"ok":true,"method":"tools/call","id":2,"name":"mem.remember","arguments":{"namespace":"session","content":"hi"}}'

# ── 1. FAIL-CLOSED STUB SEAMS (§6, §1) ───────────────────────────────────────
# invoke unset -> a T0 that authorizes ALLOW-AUTO still returns not-wired, NEVER a fabricated
# success. confirm unset -> a T1 denies with confirm-channel-not-wired.
OUT="$(one "$V_FILEREAD")"
case "$OUT" in *impl-not-wired*) : ;; *) fail "T0 with invoke stub not impl-not-wired: $OUT";; esac
OUT="$(one "$V_REMEMBER")"
case "$OUT" in *confirm-channel-not-wired*) : ;; *) fail "T1 with confirm stub not fail-closed: $OUT";; esac

# ── 2. ROUTING — T0 is ALLOW-AUTO and NEVER consults confirm (§4.5) ──────────
rm -f "$SCRATCH/mark/cA"
OUT="$( CONFIRM_MARKER="$SCRATCH/mark/cA" CONFIRM_APPROVE=1 \
        AGENT_OS_CONFIRM_SEAM="$SEAM_CONFIRM" AGENT_OS_INVOKE_SEAM="$SEAM_INVOKE" one "$V_FILEREAD" )"
[ -e "$SCRATCH/mark/cA" ] && fail "T0 consulted the confirm seam (must be ALLOW-AUTO)"
[ "$(jf "$OUT" 'o["result"]["content_type"]')" = "data" ] || fail "T0 invoke result not DATA-fenced: $OUT"

# ── 3. T1/T2 REQUIRE-CONFIRM: deny under stub, invoke only after APPROVE (§4.5) ──
OUT="$( CONFIRM_APPROVE=0 AGENT_OS_CONFIRM_SEAM="$SEAM_CONFIRM" AGENT_OS_INVOKE_SEAM="$SEAM_INVOKE" one "$V_REMEMBER" )"
case "$OUT" in *"confirm: test"*) : ;; *) fail "T1 explicit-deny confirm not surfaced: $OUT";; esac
rm -f "$SCRATCH/mark/iA"
OUT="$( CONFIRM_APPROVE=1 AGENT_OS_CONFIRM_SEAM="$SEAM_CONFIRM" \
        INVOKE_MARKER="$SCRATCH/mark/iA" INVOKE_KEY="session/x" AGENT_OS_INVOKE_SEAM="$SEAM_INVOKE" one "$V_REMEMBER" )"
[ -e "$SCRATCH/mark/iA" ] || fail "T1 APPROVE did not reach invoke: $OUT"
[ "$(jf "$OUT" 'o["result"]["content_type"]')" = "data" ] || fail "T1 approved result not DATA: $OUT"

# ── 4. T3 NON-EXPRESSIBILITY (§6 CF-1a): forbidden ops are not registry keys -> deny ──
for n in taint.modify broker.modify registry.modify reset credentials.read; do
  V="{\"ok\":true,\"method\":\"tools/call\",\"id\":3,\"name\":\"$n\",\"arguments\":{}}"
  OUT="$(one "$V")"
  case "$OUT" in *unknown-capability*) : ;; *) fail "T3 op '$n' not denied unknown-capability: $OUT";; esac
done

# ── 5. ARG-SCHEMA (§4.3) — end-to-end legs on the real registry ──────────────
# (a) unknown arg key
OUT="$(one '{"ok":true,"method":"tools/call","id":4,"name":"file.read","arguments":{"path":"/var/lib/agent-os/safe-read/x","evil":1}}')"
case "$OUT" in *"unknown arg"*) : ;; *) fail "unknown arg not denied: $OUT";; esac
# (b) missing required arg
OUT="$(one '{"ok":true,"method":"tools/call","id":4,"name":"file.read","arguments":{}}')"
case "$OUT" in *"missing required arg"*) : ;; *) fail "missing arg not denied: $OUT";; esac
# (c) enum membership (GAP-1) — real net.fetch.method now carries a member set. A valid member
#     passes arg-schema (then T2 meets the unwired confirm stub); an out-of-set value denies.
OUT="$(one '{"ok":true,"method":"tools/call","id":4,"name":"net.fetch","arguments":{"url":"https://example.com/","method":"GET"}}')"
case "$OUT" in *"confirm-channel-not-wired"*) : ;; *) fail "net.fetch valid enum did not reach confirm (GAP-1): $OUT";; esac
OUT="$(one '{"ok":true,"method":"tools/call","id":4,"name":"net.fetch","arguments":{"url":"https://example.com/","method":"TRACE"}}')"
case "$OUT" in *"enum-not-in-set"*) : ;; *) fail "net.fetch out-of-set enum not denied: $OUT";; esac
# (d) path confinement — canonical but outside the cap root
OUT="$(one '{"ok":true,"method":"tools/call","id":4,"name":"file.read","arguments":{"path":"/etc/passwd"}}')"
case "$OUT" in *"arg-confinement"*) : ;; *) fail "path outside cap root not confined: $OUT";; esac
# (e) non-canonical path (dotdot escape) rejected before confinement
OUT="$(one '{"ok":true,"method":"tools/call","id":4,"name":"file.read","arguments":{"path":"/var/lib/agent-os/safe-read/../../etc/passwd"}}')"
case "$OUT" in *"path-not-canonical"*) : ;; *) fail "non-canonical path not rejected: $OUT";; esac
# (f) recipient charset (message.send is T2; arg-schema runs BEFORE tier/confirm)
OUT="$(one '{"ok":true,"method":"tools/call","id":4,"name":"message.send","arguments":{"recipient":"a b","body":"x"}}')"
case "$OUT" in *"recipient-not-printable-ascii"*) : ;; *) fail "recipient with space not denied: $OUT";; esac
# (g) namespace charset
OUT="$(one '{"ok":true,"method":"tools/call","id":4,"name":"mem.recall","arguments":{"namespace":"../trusted","query":"q"}}')"
case "$OUT" in *"namespace-charset"*) : ;; *) fail "namespace with slash not denied: $OUT";; esac

# ── 6. VERDICT PASSTHROUGH (§4.1 / CF-6): ok:false is returned verbatim, never re-parsed ──
OUT="$(one '{"ok":false,"id":5,"error":{"code":-32601,"message":"method not found"}}')"
[ "$(jf "$OUT" 'o["ok"]')" = "false" ] || fail "passthrough flipped ok: $OUT"
[ "$(jf "$OUT" 'o["error"]["code"]')" = "-32601" ] || fail "passthrough mangled the error: $OUT"

# ── 7. MALFORMED VERDICT LINE -> DENY + SHUT STREAM (§2): child died/tampered ──
OUT="$(printf '%s\n%s\n' 'this is not json' '{"ok":true,"method":"initialize","id":9}' | "$PY" "$BROKER" run)"; RC=$?
[ "$RC" = 3 ] || fail "malformed line did not shut the stream (rc=$RC)"
[ "$(printf '%s\n' "$OUT" | grep -c .)" = 1 ] || fail "malformed line emitted more than the single deny: $OUT"
case "$OUT" in *'"id":9'*) fail "verdict AFTER the malformed line was still processed (stream not shut): $OUT";; esac

# ── 8. SINGLE-FLIGHT SERIAL (§2): three verdicts -> three ordered result lines ──
THREE="$(printf '%s\n%s\n%s\n' \
  '{"ok":true,"method":"tools/call","id":11,"name":"no.such.a","arguments":{}}' \
  '{"ok":true,"method":"tools/call","id":12,"name":"no.such.b","arguments":{}}' \
  '{"ok":true,"method":"tools/call","id":13,"name":"no.such.c","arguments":{}}' | "$PY" "$BROKER" run)"; RC=$?
[ "$RC" = 0 ] || fail "single-flight stream exit"
[ "$(printf '%s\n' "$THREE" | grep -c .)" = 3 ] || fail "single-flight did not emit 3 lines: $THREE"
[ "$(printf '%s\n' "$THREE" | sed -n 1p | { read -r l; jf "$l" 'o["id"]'; })" = 11 ] || fail "line1 id!=11"
[ "$(printf '%s\n' "$THREE" | sed -n 3p | { read -r l; jf "$l" 'o["id"]'; })" = 13 ] || fail "line3 id!=13"

# ── 9. NO-LOG -> NO-EXECUTE (§4.8): dead audit -> route unauditable -> DENY, invoke NOT reached ──
rm -f "$SCRATCH/mark/nolog"
OUT="$( AUDIT_BIN="$SCRATCH/no-such-audit" INVOKE_MARKER="$SCRATCH/mark/nolog" \
        AGENT_OS_INVOKE_SEAM="$SEAM_INVOKE" one "$V_FILEREAD" )"
case "$OUT" in *"audit-failed"*) : ;; *) fail "dead audit did not deny audit-failed: $OUT";; esac
[ -e "$SCRATCH/mark/nolog" ] && fail "invoke ran despite an unloggable decision (no-log->no-execute broken)"

# ── 10. RETURN PATH — origin is BROKER-derived, taint commits BEFORE content (§4.5a, §4.9) ──
# net.fetch (TESTREG, enum removed) is UNTRUSTED by broker policy. Approve it; the invoke seam
# even LIES origin=TRUSTED — the broker must ignore that and STILL taint the session, and the
# taint must be committed by the time the (DATA-fenced) content is returned.
"$PY" "$TAINT" reset --confirm-human --break-glass >/dev/null || fail "reset before untrusted-return leg"
VNET='{"ok":true,"method":"tools/call","id":21,"name":"net.fetch","arguments":{"url":"https://pub.example/"}}'
OUT="$( AGENT_OS_REGISTRY="$TESTREG" CONFIRM_APPROVE=1 AGENT_OS_CONFIRM_SEAM="$SEAM_CONFIRM" \
        INVOKE_CONTENT="FETCHBODY" INVOKE_LIE_ORIGIN=1 AGENT_OS_INVOKE_SEAM="$SEAM_INVOKE" one "$VNET" )"
[ "$(jf "$OUT" 'o["result"]["content_type"]')" = "data" ] || fail "untrusted fetch result not DATA-fenced: $OUT"
case "$OUT" in *FETCHBODY*) : ;; *) fail "fetch content not returned: $OUT";; esac
taint_is TAINTED   # the broker tainted despite the impl claiming TRUSTED — origin is policy

# error BODY also carries attacker bytes: ok=false must STILL flow through taint + DATA fence,
# and the bytes must NOT land in an instruction-carrying error.message.
"$PY" "$TAINT" reset --confirm-human --break-glass >/dev/null || fail "reset before error-body leg"
OUT="$( AGENT_OS_REGISTRY="$TESTREG" CONFIRM_APPROVE=1 AGENT_OS_CONFIRM_SEAM="$SEAM_CONFIRM" \
        INVOKE_OK=0 INVOKE_CONTENT="ATTACKERBYTES" AGENT_OS_INVOKE_SEAM="$SEAM_INVOKE" one "$VNET" )"
[ "$(jf "$OUT" 'o["result"]["capability_ok"]')" = "false" ] || fail "error body not marked capability_ok=false: $OUT"
case "$OUT" in *'"error"'*) fail "error body leaked into the instruction stream (error.message): $OUT";; esac
case "$OUT" in *ATTACKERBYTES*) : ;; *) fail "error body not DATA-fenced back to caller: $OUT";; esac
taint_is TAINTED

# ── 11. WITHHOLD ON TAINT-FAIL (§4.9): if the covering taint effect can't commit, content is
#        withheld — the emit is never reached. Force it by pointing TAINT_BIN at nothing (route
#        audit stays live, so the deny is a WITHHOLD, not an audit-failure).
OUT="$( AGENT_OS_REGISTRY="$TESTREG" TAINT_BIN="$SCRATCH/no-such-taint" \
        CONFIRM_APPROVE=1 AGENT_OS_CONFIRM_SEAM="$SEAM_CONFIRM" \
        INVOKE_CONTENT="LEAKME" AGENT_OS_INVOKE_SEAM="$SEAM_INVOKE" one "$VNET" )"
case "$OUT" in *"content-withheld"*) : ;; *) fail "taint-set failure did not withhold: $OUT";; esac
case "$OUT" in *LEAKME*) fail "content LEAKED despite the taint effect failing to commit: $OUT";; esac

# ── 12. mem.recall RE-TAINT ACROSS SESSIONS (§4.9): recall of an UNTRUSTED-stored key re-taints;
#        recall of a TRUSTED key does not. The broker calls `taint recall <meta.key>`; taint owns
#        the tag (the impl cannot forge it).
"$PY" "$TAINT" reset --confirm-human --break-glass >/dev/null || fail "reset before recall seed"
"$PY" "$TAINT" set "seed untrusted" >/dev/null || fail "set before untrusted stamp"
"$PY" "$TAINT" stamp untrusted/k >/dev/null || fail "stamp untrusted/k"      # tagged UNTRUSTED
"$PY" "$TAINT" reset --confirm-human --break-glass >/dev/null || fail "reset after seeding"
"$PY" "$TAINT" stamp trusted/k >/dev/null || fail "stamp trusted/k"          # clean session -> TRUSTED
VREC='{"ok":true,"method":"tools/call","id":31,"name":"mem.recall","arguments":{"namespace":"session","query":"q"}}'
OUT="$( INVOKE_KEY="untrusted/k" INVOKE_CONTENT="recalled" AGENT_OS_INVOKE_SEAM="$SEAM_INVOKE" one "$VREC" )"
[ "$(jf "$OUT" 'o["result"]["content_type"]')" = "data" ] || fail "recall result not DATA: $OUT"
taint_is TAINTED   # recalling the UNTRUSTED-origin entry re-tainted the fresh session
"$PY" "$TAINT" reset --confirm-human --break-glass >/dev/null || fail "reset before trusted recall"
OUT="$( INVOKE_KEY="trusted/k" INVOKE_CONTENT="recalled" AGENT_OS_INVOKE_SEAM="$SEAM_INVOKE" one "$VREC" )"
taint_is clean     # recalling a TRUSTED entry does NOT taint

# ── 13. mem.remember — the BROKER owns the stamp (§4.9). A stamped key recalls as "no change"
#        (proof it was tagged); a missing key withholds; a stamp that can't commit withholds.
"$PY" "$TAINT" reset --confirm-human --break-glass >/dev/null || fail "reset before stamp leg"
OUT="$( CONFIRM_APPROVE=1 AGENT_OS_CONFIRM_SEAM="$SEAM_CONFIRM" \
        INVOKE_KEY="session/n1" AGENT_OS_INVOKE_SEAM="$SEAM_INVOKE" one "$V_REMEMBER" )"
[ "$(jf "$OUT" 'o["result"]["content_type"]')" = "data" ] || fail "remember result not DATA: $OUT"
R="$("$PY" "$TAINT" recall session/n1)" || fail "recall of the stamped key errored"
case "$R" in *"no change"*) : ;; *) fail "broker did not stamp session/n1 (recall re-tainted it): $R";; esac
# no key to stamp -> withhold (a write whose provenance can't be pinned is a laundering hole)
OUT="$( CONFIRM_APPROVE=1 AGENT_OS_CONFIRM_SEAM="$SEAM_CONFIRM" AGENT_OS_INVOKE_SEAM="$SEAM_INVOKE" one "$V_REMEMBER" )"
case "$OUT" in *"content-withheld"*) : ;; *) fail "remember with no stamp key did not withhold: $OUT";; esac
# stamp can't commit (TAINT_BIN missing) -> withhold
OUT="$( TAINT_BIN="$SCRATCH/no-such-taint" CONFIRM_APPROVE=1 AGENT_OS_CONFIRM_SEAM="$SEAM_CONFIRM" \
        INVOKE_KEY="session/n2" AGENT_OS_INVOKE_SEAM="$SEAM_INVOKE" one "$V_REMEMBER" )"
case "$OUT" in *"content-withheld"*) : ;; *) fail "remember stamp-fail did not withhold: $OUT";; esac

# ── 14. CONFIRM EPOCH BINDING (§4.6): an approval that arrives under a DIFFERENT session_id
#        (a taint reset raced in during confirm) is rejected.
"$PY" "$TAINT" reset --confirm-human --break-glass >/dev/null || fail "reset before epoch leg"
OUT="$( CONFIRM_APPROVE=1 CONFIRM_RESET=1 AGENT_OS_CONFIRM_SEAM="$SEAM_CONFIRM" \
        AGENT_OS_INVOKE_SEAM="$SEAM_INVOKE" one "$V_REMEMBER" )"
case "$OUT" in *"session-id-mismatch"*) : ;; *) fail "approval under a bumped session_id was not rejected: $OUT";; esac

# ── 15. HANDSHAKE ONCE (§4.1): a second initialize is denied ─────────────────
TWO="$(printf '%s\n%s\n' \
  '{"ok":true,"method":"initialize","id":41,"protocolVersion":"2025-06-18"}' \
  '{"ok":true,"method":"initialize","id":42,"protocolVersion":"2025-06-18"}' | "$PY" "$BROKER" run)"
L2="$(printf '%s\n' "$TWO" | sed -n 2p)"
case "$L2" in *"already initialized"*) : ;; *) fail "second initialize not denied: $L2";; esac

# ── 16. MCP INPUT CONTRACT (§2): `mcp parse | broker run` — the broker consumes the REAL
#        parser's emit format line-by-line without shutting the stream (single-flight over it).
MSGS="$(printf '%s\n%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{}}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"file.read","arguments":{"path":"/var/lib/agent-os/safe-read/x"}}}')"
PIPED="$(printf '%s\n' "$MSGS" | "$PY" "$MCP" parse | "$PY" "$BROKER" run)"; RC=$?
[ "$RC" = 0 ] || fail "broker shut the stream on real mcp output (rc=$RC): $PIPED"
[ "$(printf '%s\n' "$PIPED" | grep -c .)" = 2 ] || fail "mcp->broker did not yield 2 result lines: $PIPED"
printf '%s\n' "$PIPED" | while IFS= read -r l; do
  printf '%s' "$l" | "$PY" -c 'import sys,json; json.load(sys.stdin)' || exit 9
done || fail "mcp->broker produced a non-JSON result line: $PIPED"

# ── 17. PURE VALIDATOR UNIT BATTERY — url evasion + accept, path golden vectors (shared with
#        the Nix pathIsCanonical), namespace, recipient. Imported directly (net.fetch's enum
#        blocks the url validator end-to-end in v1, so it is unit-tested here).
"$PY" - "$BROKER" <<'PYEOF' || fail "validator unit battery failed"
import sys
from importlib.machinery import SourceFileLoader   # bin/broker has no .py suffix
m = SourceFileLoader("broker", sys.argv[1]).load_module()
errs = []

# url — DENY (INV-2 + obfuscated-IP forms). 100.64/10 (CGNAT) is intentionally NOT here: the
# §4.3 validator checks the six standard flags; CGNAT is caught by the Step-7 systemd egress
# deny-list, not this textual pass.
url_deny = ["http://127.0.0.1/", "http://10.0.0.1/", "http://192.168.1.1/",
            "http://172.16.0.1/", "http://169.254.169.254/", "http://2130706433/",
            "http://0x7f000001/", "http://0177.0.0.1/", "http://[::1]/",
            "http://[::ffff:127.0.0.1]/", "http://0.0.0.0/", "http://224.0.0.1/",
            "http://127.1/", "http://10.1/", "http://192.168.257/",
            "http://127.0x1/", "http://10.0x0.0.1/",              # per-octet hex (Fable PR#12 fix)
            "http://１２７.0.0.1/",                   # fullwidth-digit 127 (NFKC-caught)
            "ftp://example.com/", "http:///nohost", "file:///etc/passwd"]
for u in url_deny:
    ok, _ = m.validate_url(u)
    if ok: errs.append("url should DENY but allowed: %r" % u)
# url — ACCEPT (public host / public IP over http(s))
for u in ["http://example.com/", "https://example.com:8443/p?q=1", "http://8.8.8.8/"]:
    ok, why = m.validate_url(u)
    if not ok: errs.append("url should ALLOW but denied: %r (%s)" % (u, why))

# path canonical golden vectors (MUST match modules/capability-registry.nix pathIsCanonical)
for p in ["/a", "/a/b", "/var/lib/agent-os/safe-read/x"]:
    if not m.path_is_canonical(p): errs.append("path should be canonical: %r" % p)
for p in ["a/b", "/", "/a/", "/a//b", "/a/./b", "/a/../b", "", "/..", "//", "/a/.."]:
    if m.path_is_canonical(p): errs.append("path should be NON-canonical: %r" % p)

# namespace
NS = {"sandbox": {"readOnlyPaths": ["/var/lib/agent-os/mem"], "readWritePaths": []}}
for good in ["session", "a.b-c_d", "A1"]:
    ok, _ = m.validate_namespace(good, NS)
    if not ok: errs.append("namespace should ALLOW: %r" % good)
for bad in ["", "../x", "a/b", "." + "x", "-x", "x" * 65]:
    ok, _ = m.validate_namespace(bad, NS)
    if ok: errs.append("namespace should DENY: %r" % bad)

# recipient — printable ASCII, no control/whitespace/non-ASCII
for good in ["user@example.com", "peer-1", "A.B_c"]:
    ok, _ = m.validate_recipient(good)
    if not ok: errs.append("recipient should ALLOW: %r" % good)
for bad in ["", "a b", "a\tb", "a\nb", "café", "x" * 300]:
    ok, _ = m.validate_recipient(bad)
    if ok: errs.append("recipient should DENY: %r" % bad)

if errs:
    sys.stderr.write("\n".join(errs) + "\n"); sys.exit(1)
print("validator unit battery: OK")
PYEOF

# ── 18. CONFIRM-SEAM TIMEOUT (§5, Fable follow-up #2): a confirm seam that runs past
#        AGENT_OS_CONFIRM_TIMEOUT_S is KILLED+reaped and the request DENIES 'confirm-timeout' —
#        a hung/absent human never wedges the single-flight broker, and the killed child's late
#        output is never read (fail-closed, never OPEN). This is the broker half of Step 6 §8.4.
SEAM_SLEEP="$SCRATCH/seam_sleep.py"
cat > "$SEAM_SLEEP" <<'PYEOF'
import sys, json, time
try: json.load(sys.stdin)
except Exception: pass
time.sleep(30)                                   # far past the 1s backstop below
sys.stdout.write('{"approved": true, "reason": "should-never-be-read"}')
PYEOF
OUT="$( AGENT_OS_CONFIRM_TIMEOUT_S=1 AGENT_OS_CONFIRM_SEAM="$SEAM_SLEEP" \
        AGENT_OS_INVOKE_SEAM="$SEAM_INVOKE" one "$V_REMEMBER" )"
case "$OUT" in *confirm-timeout*) : ;; *) fail "sleeping confirm seam did not deny confirm-timeout: $OUT";; esac
case "$OUT" in *should-never-be-read*) fail "broker read a killed seam's late stdout (not reaped): $OUT";; esac

# ── 19. AUDIT INTEGRATION — the broker's decisions were logged via the Step-2 primitive and
#        the chain still verifies (single-flight -> no interleaved appends corrupt it).
"$PY" "$AUDIT" verify >/dev/null 2>&1 || fail "audit chain does not verify after broker decisions"
grep -q '"src":"broker"' "$LOG" || fail "no broker records in the audit log (decisions unlogged)"
grep -q '"event":"route"' "$LOG" || fail "audit log missing a broker route decision"

echo "broker-battery: ALL PROPERTIES HOLD"
