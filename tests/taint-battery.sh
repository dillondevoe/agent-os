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
LOG="$AGENT_OS_AUDIT_DIR/audit.log"

t()  { "$PY" "$TAINT" "$@"; }            # run taint, inherit exit code
av() { "$PY" "$AUDIT" verify >/dev/null 2>&1; }

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

# 2. HUMAN-ONLY reset (§6). The model has no command that clears the bit; only an explicit
#    human reset does, and it must carry --confirm-human. A bare reset is REFUSED.
if t reset >/dev/null 2>&1; then fail "reset without --confirm-human succeeded (model could clear taint)"; fi
status_is TAINTED                                  # refusal left the bit set
t reset --confirm-human >/dev/null || fail "human reset (--confirm-human) failed"
status_is clean                                    # a blessed session is clean

# 3. MONOTONIC set + PERSISTENCE (§6). set can only RAISE the bit; a fresh process still
#    sees it (on-disk, survives truncation/summarization). Nothing but reset clears it.
t set "ingested untrusted web content" >/dev/null || fail "set failed"
status_is TAINTED
t set "second untrusted source" >/dev/null || fail "second set failed"
status_is TAINTED                                  # still tainted — monotone, no toggle

# 4. STAMP records a mem entry's origin FROM the session taint (§6). Clean session -> the
#    entry is TRUSTED; tainted session -> UNTRUSTED, and that tag lives in the taint dir,
#    a Step-1 protected path the model cannot write.
t reset --confirm-human >/dev/null || fail "reset before trusted stamp failed"
out="$(t stamp trusted/key1)" || fail "stamp trusted/key1 failed"
case "$out" in *"origin=TRUSTED"*) : ;; *) fail "clean-session stamp not TRUSTED: $out";; esac
t set "poisoned page" >/dev/null || fail "set before untrusted stamp failed"
out="$(t stamp untrusted/key2)" || fail "stamp untrusted/key2 failed"
case "$out" in *"origin=UNTRUSTED"*) : ;; *) fail "tainted-session stamp not UNTRUSTED: $out";; esac

# 5. UNTRUSTED is ABSORBING (§6 "permanently"). Re-stamping an UNTRUSTED entry from a
#    CLEAN session must NOT downgrade it. Provenance only ratchets toward untrusted.
t reset --confirm-human >/dev/null || fail "reset before re-stamp failed"
out="$(t stamp untrusted/key2)" || fail "re-stamp untrusted/key2 failed"
case "$out" in *"origin=UNTRUSTED"*) : ;; *) fail "UNTRUSTED downgraded to TRUSTED (laundering!): $out";; esac

# 6. RECALL of an UNTRUSTED entry RE-TAINTS — the headline cross-session anti-laundering
#    property (§6). key2 was written UNTRUSTED in an earlier (tainted) session; recalling
#    it in a fresh CLEAN session must re-taint. Poison cannot be washed by a reset.
t reset --confirm-human >/dev/null || fail "reset before untrusted recall failed"
status_is clean
out="$(t recall untrusted/key2)" || fail "recall untrusted/key2 failed"
case "$out" in *"RE-TAINTED"*) : ;; *) fail "recall of UNTRUSTED did not re-taint: $out";; esac
status_is TAINTED

# 7. RECALL of a TRUSTED entry does NOT taint (no false positives on clean provenance).
t reset --confirm-human >/dev/null || fail "reset before trusted recall failed"
out="$(t recall trusted/key1)" || fail "recall trusted/key1 failed"
case "$out" in *"no change"*) : ;; *) fail "recall of TRUSTED wrongly re-tainted: $out";; esac
status_is clean

# 8. RECALL of an UNKNOWN-origin key FAILS CLOSED (§8). No origin record == unknown
#    provenance == untrusted; recalling it must re-taint, not silently pass.
t reset --confirm-human >/dev/null || fail "reset before unknown recall failed"
out="$(t recall never/stamped)" || fail "recall of unknown key errored"
case "$out" in *"RE-TAINTED"*) : ;; *) fail "recall of UNKNOWN origin did not fail-closed: $out";; esac
status_is TAINTED

# 9. BOOT-TAINT on untrusted-origin mem load (§6). (a) an UNTRUSTED entry in the ledger
#    taints the boot; (b) an unstamped mem file (unknown provenance) taints the boot;
#    (c) an empty ledger with no mem loaded boots CLEAN. (b)/(c) use an isolated taint dir
#    so the shared ledger's UNTRUSTED key2 doesn't mask the mem-root path.
t reset --confirm-human >/dev/null || fail "reset before boot(a) failed"
out="$(t boot)" || fail "boot(a) errored"
case "$out" in *"BOOT TAINTED"*) : ;; *) fail "boot did not taint on untrusted-origin ledger: $out";; esac

MEMROOT="$SCRATCH/memroot"; mkdir -p "$MEMROOT/domain"
printf 'body\n' > "$MEMROOT/domain/note.md"
( export AGENT_OS_TAINT_DIR="$SCRATCH/taint-iso"; mkdir -p "$AGENT_OS_TAINT_DIR"
  "$PY" "$TAINT" reset --confirm-human >/dev/null || exit 21
  # (c) clean ledger, nothing loaded -> clean boot
  o="$("$PY" "$TAINT" boot)" || exit 22
  case "$o" in *"BOOT clean"*) : ;; *) exit 23;; esac
  "$PY" "$TAINT" reset --confirm-human >/dev/null || exit 24
  # (b) an unknown-origin mem file present at boot -> tainted
  o="$("$PY" "$TAINT" boot --mem-root "$MEMROOT")" || exit 25
  case "$o" in *"BOOT TAINTED"*) : ;; *) exit 26;; esac
) || fail "boot-taint isolated legs failed (rc via subshell — clean/unknown-mem paths)"

# 10. SHADOW gate — computes a verdict and LOGS it, but GATES NOTHING: every computed
#     verdict exits 0, would-block and would-allow alike. Only bad usage is non-zero.
t reset --confirm-human >/dev/null || fail "reset before gate(clean) failed"
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
grep -q taint "$LOG" || fail "no taint records in the audit log (decisions were not logged)"
for ev in set reset stamp recall boot gate; do
  grep -q "$ev" "$LOG" || fail "audit log missing a '$ev' taint decision"
done

# 12. NO-LOG -> NO-EXECUTE for taint (§8, matches the audit contract). If the audit binary
#     is unreachable, a mutating taint op is unauditable and must fail closed (non-zero),
#     WITHOUT mutating state. Point AUDIT_BIN at nothing and confirm set refuses.
( export AUDIT_BIN="$SCRATCH/nonexistent-audit"; export AGENT_OS_TAINT_DIR="$SCRATCH/taint-nolog"
  mkdir -p "$AGENT_OS_TAINT_DIR"
  "$PY" "$TAINT" reset --confirm-human >/dev/null 2>&1 && exit 31   # reset also logs -> must fail
  exit 0
) || fail "taint op SUCCEEDED with an unreachable audit log (no-log->no-execute violated)"

echo "taint-battery: ALL PROPERTIES HOLD"
