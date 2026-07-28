#!/usr/bin/env bash
# taint-battery.sh — property tests for bin/taint (Phase 2 · Step 3, SHADOW mode).
#
# Usage: taint-battery.sh <path-to-taint-bin> <path-to-audit-bin> <scratch-dir>
# Exits 0 iff EVERY property holds. Run by the flake check (checks.<sys>.taint-shadow)
# and standalone. Each property maps to a threat-model §6/§8 clause; a failure names it.
#
# taint drives the REAL audit primitive to record its shadow decisions, so this battery
# is also an integration test: after the run, the audit chain must still verify and must
# contain the taint events. Because every `taint` call is a fresh process, the persistence
# properties (a set bit survives context truncation) are exercised implicitly — each
# `status` reads the on-disk bit, never an in-memory one.
set -u

TAINT="${1:?path to bin/taint required}"
AUDIT="${2:?path to bin/audit required}"
SCRATCH="${3:?scratch dir required}"
PY="${PYTHON:-python3}"

fail() { echo "taint-battery FAIL: $*" >&2; exit 1; }

export AGENT_OS_TAINT_DIR="$SCRATCH/taint"
export AGENT_OS_AUDIT_DIR="$SCRATCH/audit"
export AUDIT_BIN="$AUDIT"                 # taint shells out to this to log every decision
mkdir -p "$AGENT_OS_TAINT_DIR" "$AGENT_OS_AUDIT_DIR"
# CF-1c: run channel-less by DEFAULT — a leaked operator AGENT_OS_CONFIRM_SEAM must NOT silently
# wire the confirm path and mask the fail-closed behaviour test 2(a) relies on. Test 2 opts INTO a
# scripted approve/deny seam explicitly, per-subshell; break-glass (reset_clean) never reads it.
unset AGENT_OS_CONFIRM_SEAM 2>/dev/null || true
LOG="$AGENT_OS_AUDIT_DIR/audit.log"

t()  { "$PY" "$TAINT" "$@"; }            # run taint, inherit exit code
av() { "$PY" "$AUDIT" verify >/dev/null 2>&1; }

# CF-1c: the DEFAULT human reset clears taint via a nonce-bound APPROVAL over the confirm channel
# (test 2 exercises that + deny + flag-gating directly). For routine test SETUP — "get to a clean
# session" between phases — use the channel-free audited BREAK-GLASS (two-token operator gate), so
# setup never needs a live confirm seam. It still emits an audited "event":"reset" (via:break-glass).
reset_clean() { "$PY" "$TAINT" reset --confirm-human --break-glass; }

# Assert the leading status token. "taint: TAINTED" vs "taint: clean" — matched with the
# "taint: " prefix so a reason string containing the word "clean" can't false-pass.
status_is() {  # $1 = TAINTED|clean
  local out; out="$(t status)" || fail "status exited non-zero"
  case "$out" in
    *"taint: $1"*) : ;;
    *) fail "expected status '$1', got: $out" ;;
  esac
}

# 1. FAIL-CLOSED uninitialized (§8 "uncertain -> untrusted"). With no session state on
#    disk, the box is TAINTED until a human blesses it. Absence of proof != clean.
status_is TAINTED

# 2. HUMAN + CONFIRM-CHANNEL reset, hardened (CF-1c §6). The model has no command that clears the
#    bit (reset is not a registry key — CF-1a). CF-1c further hardens the HUMAN path: the DEFAULT
#    reset clears taint ONLY on a nonce-bound APPROVAL routed through the confirm client — a bare
#    self-asserted --confirm-human no longer suffices. A separately-gated, audited BREAK-GLASS
#    (--confirm-human --break-glass) clears WITHOUT the channel so a dead channel can never strand
#    taint (spec §6 bootstrap-inversion guard). Every path is human-only + audited.
SEAMDIR="$SCRATCH/reset-seams"; mkdir -p "$SEAMDIR"
# approve seam: CAPTURES the request frame it was handed (so we can assert its shape), then approves.
# deny seam: refuses. Both mimic the confirm client's terminal {approved,reason} contract.
cat > "$SEAMDIR/approve" <<SEAM
#!/bin/sh
cat > "$SEAMDIR/req.json"
printf '{"approved":true,"reason":"operator approved"}'
SEAM
cat > "$SEAMDIR/deny" <<'SEAM'
#!/bin/sh
cat > /dev/null
printf '{"approved":false,"reason":"operator denied"}'
SEAM
chmod +x "$SEAMDIR/approve" "$SEAMDIR/deny"
# (a) bare reset with NO confirm channel wired -> fail-closed DENY (exit 5), bit stays TAINTED.
#     Neither the model nor a channel-less caller can clear taint by merely asking.
if t reset >/dev/null 2>&1; then fail "bare reset with no confirm channel cleared taint"; fi
status_is TAINTED
# (b) --confirm-human ALONE no longer clears taint (CF-1c: self-assertion is not sufficient; exit 4).
if t reset --confirm-human >/dev/null 2>&1; then fail "--confirm-human alone cleared taint (CF-1c gate missing)"; fi
status_is TAINTED
# (c) confirm channel DENIES -> NOT cleared (fail-closed on an explicit refusal; exit 5).
if ( export AGENT_OS_CONFIRM_SEAM="$SEAMDIR/deny"; t reset >/dev/null 2>&1 ); then fail "reset cleared taint despite a channel DENY"; fi
status_is TAINTED
# (d) confirm channel APPROVES -> cleared; the client was handed a taint.reset frame bound to a nonce.
( export AGENT_OS_CONFIRM_SEAM="$SEAMDIR/approve"; t reset >/dev/null ) || fail "confirm-approved reset failed"
status_is clean
grep -F '"capability": "taint.reset"' "$SEAMDIR/req.json" >/dev/null || fail "confirm client was not sent a taint.reset frame"
grep -F '"nonce"' "$SEAMDIR/req.json" >/dev/null || fail "reset confirm frame carried no nonce"
# (e) BREAK-GLASS is a deliberate TWO-token operator act. Re-taint, then: --break-glass ALONE is
#     refused (exit 4, still TAINTED); only the --confirm-human --break-glass pair clears (dead-
#     channel recovery), and it does so WITHOUT consulting the confirm channel.
t set "re-taint for break-glass leg" >/dev/null || fail "set before break-glass leg failed"
status_is TAINTED
if t reset --break-glass >/dev/null 2>&1; then fail "--break-glass without --confirm-human cleared taint"; fi
status_is TAINTED
reset_clean >/dev/null || fail "break-glass (--confirm-human --break-glass) reset failed"
status_is clean                                    # a blessed session is clean
# (f) STRICT-APPROVAL regression (Fable ruling, CF-1c a522af9). _confirm_reset accepts ONLY a JSON
#     `true`. A seam emitting a TRUTHY-NON-TRUE approval ("false" string, 1, [1], NaN) must fail-
#     closed DENY (exit 5) with the bit still TAINTED. `bool()`-coercion (the pre-a522af9 bug) would
#     launder EVERY one of these into a CLEAR on the single field that clears the monotonic taint bit
#     — same class as the MCP-NaN bug — leaving taint strictly weaker than the broker (approved is
#     not True -> deny) on the identical seam. This pins the fix against a silent refactor to bool().
t set "re-taint for strict-approval regression" >/dev/null || fail "set before strict-approval leg failed"
status_is TAINTED
for bad in '"false"' '1' '[1]' 'NaN'; do
  cat > "$SEAMDIR/truthy" <<SEAM
#!/bin/sh
cat > /dev/null
printf '{"approved":$bad,"reason":"truthy-non-true must NOT clear taint"}'
SEAM
  chmod +x "$SEAMDIR/truthy"
  ( export AGENT_OS_CONFIRM_SEAM="$SEAMDIR/truthy"; t reset >/dev/null 2>&1 ); rc=$?
  [ "$rc" -eq 5 ] || fail "truthy-non-true approval $bad: expected fail-closed DENY exit 5, got $rc (bool()-coercion fail-open regressed)"
  status_is TAINTED
done
reset_clean >/dev/null || fail "reset after strict-approval regression failed"
status_is clean                                    # restored: block is state-neutral for later phases

# 3. MONOTONIC set + PERSISTENCE (§6). set can only RAISE the bit; a fresh process still
#    sees it (on-disk, survives truncation/summarization). Nothing but reset clears it.
t set "ingested untrusted web content" >/dev/null || fail "set failed"
status_is TAINTED
t set "second untrusted source" >/dev/null || fail "second set failed"
status_is TAINTED                                  # still tainted — monotone, no toggle

# 4. STAMP records a mem entry's origin FROM the session taint (§6). Clean session -> the
#    entry is TRUSTED; tainted session -> UNTRUSTED, and that tag lives in the taint dir,
#    a Step-1 protected path the model cannot write.
reset_clean >/dev/null || fail "reset before trusted stamp failed"
out="$(t stamp trusted/key1)" || fail "stamp trusted/key1 failed"
case "$out" in *"origin=TRUSTED"*) : ;; *) fail "clean-session stamp not TRUSTED: $out";; esac
t set "poisoned page" >/dev/null || fail "set before untrusted stamp failed"
out="$(t stamp untrusted/key2)" || fail "stamp untrusted/key2 failed"
case "$out" in *"origin=UNTRUSTED"*) : ;; *) fail "tainted-session stamp not UNTRUSTED: $out";; esac

# 5. UNTRUSTED is ABSORBING (§6 "permanently"). Re-stamping an UNTRUSTED entry from a
#    CLEAN session must NOT downgrade it. Provenance only ratchets toward untrusted.
reset_clean >/dev/null || fail "reset before re-stamp failed"
out="$(t stamp untrusted/key2)" || fail "re-stamp untrusted/key2 failed"
case "$out" in *"origin=UNTRUSTED"*) : ;; *) fail "UNTRUSTED downgraded to TRUSTED (laundering!): $out";; esac

# 6. RECALL of an UNTRUSTED entry RE-TAINTS — the headline cross-session anti-laundering
#    property (§6). key2 was written UNTRUSTED in an earlier (tainted) session; recalling
#    it in a fresh CLEAN session must re-taint. Poison cannot be washed by a reset.
reset_clean >/dev/null || fail "reset before untrusted recall failed"
status_is clean
out="$(t recall untrusted/key2)" || fail "recall untrusted/key2 failed"
case "$out" in *"RE-TAINTED"*) : ;; *) fail "recall of UNTRUSTED did not re-taint: $out";; esac
status_is TAINTED

# 7. RECALL of a TRUSTED entry does NOT taint (no false positives on clean provenance).
reset_clean >/dev/null || fail "reset before trusted recall failed"
out="$(t recall trusted/key1)" || fail "recall trusted/key1 failed"
case "$out" in *"no change"*) : ;; *) fail "recall of TRUSTED wrongly re-tainted: $out";; esac
status_is clean

# 8. RECALL of an UNKNOWN-origin key FAILS CLOSED (§8). No origin record == unknown
#    provenance == untrusted; recalling it must re-taint, not silently pass.
reset_clean >/dev/null || fail "reset before unknown recall failed"
out="$(t recall never/stamped)" || fail "recall of unknown key errored"
case "$out" in *"RE-TAINTED"*) : ;; *) fail "recall of UNKNOWN origin did not fail-closed: $out";; esac
status_is TAINTED

# 9. BOOT-TAINT on untrusted-origin mem load (§6). (a) an UNTRUSTED entry in the ledger
#    taints the boot; (b) an unstamped mem file (unknown provenance) taints the boot;
#    (c) an empty ledger with no mem loaded boots CLEAN. (b)/(c) use an isolated taint dir
#    so the shared ledger's UNTRUSTED key2 doesn't mask the mem-root path.
reset_clean >/dev/null || fail "reset before boot(a) failed"
out="$(t boot)" || fail "boot(a) errored"
case "$out" in *"BOOT TAINTED"*) : ;; *) fail "boot did not taint on untrusted-origin ledger: $out";; esac

MEMROOT="$SCRATCH/memroot"; mkdir -p "$MEMROOT/domain"
printf 'body\n' > "$MEMROOT/domain/note.md"
( export AGENT_OS_TAINT_DIR="$SCRATCH/taint-iso"; mkdir -p "$AGENT_OS_TAINT_DIR"
  reset_clean >/dev/null || exit 21
  # (c) clean ledger, nothing loaded -> clean boot
  o="$("$PY" "$TAINT" boot)" || exit 22
  case "$o" in *"BOOT clean"*) : ;; *) exit 23;; esac
  reset_clean >/dev/null || exit 24
  # (b) an unknown-origin mem file present at boot -> tainted
  o="$("$PY" "$TAINT" boot --mem-root "$MEMROOT")" || exit 25
  case "$o" in *"BOOT TAINTED"*) : ;; *) exit 26;; esac
) || fail "boot-taint isolated legs failed (rc via subshell — clean/unknown-mem paths)"

# 10. SHADOW gate — computes a verdict and LOGS it, but GATES NOTHING: every computed
#     verdict exits 0, would-block and would-allow alike. Only bad usage is non-zero.
reset_clean >/dev/null || fail "reset before gate(clean) failed"
out="$(t gate T2)" || fail "shadow gate on clean session must exit 0 (gates nothing)"
case "$out" in *"WOULD-ALLOW-AUTO"*) : ;; *) fail "clean gate T2 verdict wrong: $out";; esac
t set "untrusted for gate" >/dev/null || fail "set before gate(tainted) failed"
out="$(t gate T2)" || fail "shadow gate on TAINTED session must STILL exit 0 (shadow gates nothing)"
case "$out" in *"WOULD-REQUIRE-CONFIRM"*) : ;; *) fail "tainted gate T2 verdict wrong: $out";; esac
out="$(t gate T0)" || fail "gate T0 must exit 0"
case "$out" in *"WOULD-ALLOW-AUTO"*) : ;; *) fail "tainted gate T0 should allow (T0 runs under taint): $out";; esac
if t gate NOPE >/dev/null 2>&1; then fail "gate accepted an invalid tier"; fi

# 11. AUDIT INTEGRATION — every decision above was recorded via the Step-2 audit primitive,
#     and the chain still verifies. This is what "computes + logs via the audit log" means.
av || fail "audit chain does not verify after taint decisions (taint corrupted the log)"
# EXACT field greps — the audit record serializes compact (separators=(",", ":")), so an
# event is '"event":"set"' with no spaces. Substring greps false-pass when a reason string
# happens to contain "reset"/"boot"/etc.
grep -q '"src":"taint"' "$LOG" || fail "no taint records in the audit log (decisions were not logged)"
for ev in set reset stamp recall boot gate; do
  grep -q "\"event\":\"$ev\"" "$LOG" || fail "audit log missing a '$ev' taint decision"
done

# 12. NO-LOG -> NO-EXECUTE for taint (§8, matches the audit contract). If the audit binary
#     is unreachable, EVERY mutating taint op is unauditable and must fail closed (non-zero)
#     WITHOUT mutating state. Establish a known state with a LIVE audit, snapshot it, then
#     prove each op (set/reset/stamp/recall/boot) fails AND leaves session.json + origins.json
#     byte-identical under a DEAD audit. (The old battery only exercised `reset`, and never
#     checked the "WITHOUT mutating state" half.)
NL="$SCRATCH/taint-nolog"; mkdir -p "$NL"
( export AGENT_OS_TAINT_DIR="$NL"                   # setup under the REAL audit
  reset_clean >/dev/null || exit 41
  "$PY" "$TAINT" stamp seed/key >/dev/null || exit 42
) || fail "test-12 setup (live audit) failed"
SNAP_S="$(cat "$NL/session.json")"; SNAP_O="$(cat "$NL/origins.json")"
# Every mutating op under a DEAD audit: must fail closed AND leave state byte-identical.
while IFS= read -r op; do
  [ -n "$op" ] || continue
  ( export AUDIT_BIN="$SCRATCH/nonexistent-audit"; export AGENT_OS_TAINT_DIR="$NL"
    "$PY" "$TAINT" $op >/dev/null 2>&1 ) \
    && fail "taint '$op' SUCCEEDED under unreachable audit (no-log->no-execute violated)"
  [ "$(cat "$NL/session.json")" = "$SNAP_S" ] || fail "taint '$op' MUTATED session.json under dead audit"
  [ "$(cat "$NL/origins.json")" = "$SNAP_O" ] || fail "taint '$op' MUTATED origins.json under dead audit"
done <<'OPS'
set poison
reset --confirm-human --break-glass
stamp new/key
recall seed/key
boot
OPS

# 13. CORRUPT STATE fails closed (§8) — the tests that would have caught FIX-2. Garbage in
#     session.json must read TAINTED. Garbage in origins.json must make `stamp` REFUSE
#     (never rebuild the ledger / launder a formerly-UNTRUSTED key to TRUSTED) and make
#     `recall`/`boot` fail-closed to tainted.
CS="$SCRATCH/taint-corrupt"; mkdir -p "$CS"
# (a) corrupt session.json -> TAINTED (not a crash, not clean).
printf 'not json at all }{' > "$CS/session.json"
o="$(AGENT_OS_TAINT_DIR="$CS" "$PY" "$TAINT" status)" || fail "status errored on corrupt session.json"
case "$o" in *"taint: TAINTED"*) : ;; *) fail "corrupt session.json did not read TAINTED: $o";; esac
# (b) corrupt origins.json + CLEAN session -> stamp REFUSES and does NOT rewrite the ledger.
#     Without FIX-2 this stamps launder/key TRUSTED (clean session) and drops all tags.
( export AGENT_OS_TAINT_DIR="$CS"
  reset_clean >/dev/null || exit 51
  printf '{ broken origins ' > "$CS/origins.json"; BEFORE="$(cat "$CS/origins.json")"
  if "$PY" "$TAINT" stamp launder/key >/dev/null 2>&1; then exit 52; fi  # must FAIL, not stamp TRUSTED
  [ "$(cat "$CS/origins.json")" = "$BEFORE" ] || exit 53                 # must NOT rebuild the ledger
) || fail "corrupt origins.json: stamp did not fail-closed without rebuilding (FIX-2 laundering hole)"
# (c) valid JSON but structurally wrong (tags not a dict) is ALSO corrupt -> stamp refuses.
( export AGENT_OS_TAINT_DIR="$CS"
  reset_clean >/dev/null || exit 54
  printf '{"tags":"not-a-dict"}' > "$CS/origins.json"
  if "$PY" "$TAINT" stamp x/y >/dev/null 2>&1; then exit 55; fi
) || fail "structurally-wrong origins.json: stamp did not fail-closed"
# (d) corrupt origins.json -> recall fails closed (re-taints) and boot taints.
( export AGENT_OS_TAINT_DIR="$CS"
  reset_clean >/dev/null || exit 56
  printf '{ broken origins ' > "$CS/origins.json"
  r="$("$PY" "$TAINT" recall anything)" || exit 57
  case "$r" in *"RE-TAINTED"*) : ;; *) exit 58;; esac
  reset_clean >/dev/null || exit 59
  printf '{ broken origins ' > "$CS/origins.json"
  b="$("$PY" "$TAINT" boot)" || exit 60
  case "$b" in *"BOOT TAINTED"*) : ;; *) exit 61;; esac
) || fail "corrupt origins.json: recall/boot did not fail-closed to tainted"

# 14. GAP-3: `status --json` structured consult (the Step-5 broker's ONLY hard prerequisite).
#     Additive read-only mode on the SAME load_session() fail-closed owner. It must (a) emit a
#     single canonical JSON line with EXACTLY the four keys the broker reads, (b) its `tainted`
#     bit + `session_id` AGREE with the human `status` line in every state, and (c) fail-closed
#     identically: uninitialized/corrupt session -> tainted=true, session_id=-1. The broker
#     derives its LIVE decision from this bit, never from `gate`'s (always-0 shadow) exit code
#     — a lie or a disagreement here is a wall breach. status is read-only (no audit emission),
#     so it neither needs the audit binary nor perturbs the chain the final `av` re-verifies.

# Extract one field from the --json line using the same json the tool serializes with. Prints
# a shell-comparable token (true/false for bools, str() otherwise); non-zero if not valid JSON
# or the key is absent (strict — the happy shape must actually ship, not just fail-close).
json_field() {  # $1 = json string, $2 = key
  printf '%s' "$1" | "$PY" -c 'import sys,json
o=json.load(sys.stdin); k=sys.argv[1]
if k not in o: sys.exit(7)
v=o[k]; sys.stdout.write("true" if v is True else "false" if v is False else str(v))' "$2"
}

# (a) clean session: exit 0, valid JSON, EXACTLY the four broker keys, tainted=false, session>=0.
reset_clean >/dev/null || fail "reset before json(clean) failed"
J="$(t status --json)" || fail "status --json exited non-zero on clean session"
keys="$(printf '%s' "$J" | "$PY" -c 'import sys,json; print(",".join(sorted(json.load(sys.stdin))))')" \
  || fail "status --json did not emit valid JSON on clean session: $J"
[ "$keys" = "note,reasons,session_id,tainted" ] \
  || fail "status --json keys wrong (want note,reasons,session_id,tainted): $keys"
[ "$(json_field "$J" tainted)" = "false" ] || fail "clean session json tainted!=false: $J"
sid_clean="$(json_field "$J" session_id)" || fail "clean session json missing session_id: $J"
case "$sid_clean" in -*|"") fail "clean session json session_id negative/empty: $J";; esac

# (b) tainted session: tainted=true; a set only raises the bit, it must NOT bump session_id.
t set "json-mode untrusted source" >/dev/null || fail "set before json(tainted) failed"
J="$(t status --json)" || fail "status --json exited non-zero on tainted session"
[ "$(json_field "$J" tainted)" = "true" ] || fail "tainted session json tainted!=true: $J"
[ "$(json_field "$J" session_id)" = "$sid_clean" ] \
  || fail "set changed session_id in json (only reset bumps it): $J"
status_is TAINTED                                  # (c) --json and human line agree: tainted

# (c cont.) after a human reset both read clean again — the two views never disagree on the bit.
reset_clean >/dev/null || fail "reset before json(agreement) failed"
J="$(t status --json)" || fail "status --json exited non-zero after reset"
[ "$(json_field "$J" tainted)" = "false" ] || fail "post-reset json tainted!=false: $J"
status_is clean

# (d) FAIL-CLOSED uninitialized/corrupt: an isolated empty dir and a garbage session.json must
#     BOTH read tainted=true, session_id=-1 — the load_session sentinel the broker keys on. The
#     human line already asserts this (tests 1 + 13a); --json must carry the same bit + sentinel.
( export AGENT_OS_TAINT_DIR="$SCRATCH/taint-json-uninit"; mkdir -p "$AGENT_OS_TAINT_DIR"
  J="$("$PY" "$TAINT" status --json)" || exit 71                       # no state on disk
  ts=$(printf '%s' "$J" | "$PY" -c 'import sys,json; o=json.load(sys.stdin); print(o["tainted"],o["session_id"])')
  [ "$ts" = "True -1" ] || exit 72
) || fail "status --json on uninitialized dir did not fail-closed to tainted/-1"
( export AGENT_OS_TAINT_DIR="$SCRATCH/taint-json-corrupt"; mkdir -p "$AGENT_OS_TAINT_DIR"
  printf 'not json }{' > "$AGENT_OS_TAINT_DIR/session.json"
  J="$("$PY" "$TAINT" status --json)" || exit 73
  ts=$(printf '%s' "$J" | "$PY" -c 'import sys,json; o=json.load(sys.stdin); print(o["tainted"],o["session_id"])')
  [ "$ts" = "True -1" ] || exit 74
) || fail "status --json on corrupt session.json did not fail-closed to tainted/-1"

# 6. CF-5 / GAP-5: EPOCH HIGH-WATER. A reset over a LOST or CORRUPT session.json must NOT roll the
#    epoch back to a colliding low session_id. The broker binds every confirm-nonce to session_id
#    and denies on a changed epoch, so a reused id is a nonce/epoch replay surface (spec §Nonce).
#    Pre-fix `new_id = old+1 if old>=0 else 0` minted 0 whenever load_session() returned the -1
#    sentinel (missing OR corrupt) — colliding with the very first epoch. The fix mints
#    max(old, high_water)+1 from a DURABLE mark in the protected taint dir, and REFUSES (exit 6) if
#    that mark is itself corrupt (won't mint an id it can't prove collision-free). Isolated dir so
#    the epoch walk here can't perturb the shared session; audit still lands in the shared chain.
( export AGENT_OS_TAINT_DIR="$SCRATCH/taint-cf5-hwm"; mkdir -p "$AGENT_OS_TAINT_DIR"
  sid() { "$PY" "$TAINT" status --json | "$PY" -c 'import sys,json; print(json.load(sys.stdin)["session_id"])'; }
  reset_clean >/dev/null || exit 61                    # advance the epoch well above 0 so a
  reset_clean >/dev/null || exit 62                    # rollback-to-0 is unmistakable:
  reset_clean >/dev/null || exit 63                    # fresh dir walks 0 -> 1 -> 2
  before="$(sid)"; [ "$before" -ge 2 ] || exit 64
  rm -f "$AGENT_OS_TAINT_DIR/session.json"             # LOSE the session -> the -1 rollback trigger
  reset_clean >/dev/null || exit 65
  after="$(sid)"
  [ "$after" -gt "$before" ] || exit 66                # bumped from the high-water, NOT reset to 0
  printf 'not json{' > "$AGENT_OS_TAINT_DIR/epoch-hwm.json"   # CORRUPT the high-water mark
  "$PY" "$TAINT" reset --confirm-human --break-glass >/dev/null 2>&1; rc=$?
  [ "$rc" -eq 6 ] || exit 67                            # corrupt hwm must REFUSE, not mint a low id
) || fail "CF-5 epoch high-water: reset rolled the epoch back to a colliding id, or a corrupt high-water did not fail closed (exit 6)"

# Final: the corrupt/no-log sections logged only via the real audit (or not at all); the
# real chain must STILL verify end-to-end.
av || fail "audit chain broken after the no-log / corrupt-state sections"

echo "taint-battery: ALL PROPERTIES HOLD"
