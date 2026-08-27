#!/usr/bin/env bash
# Battery for scripts/flake-retry-decide.sh — THE MARKER EMITTER THE CENSUS DEPENDS ON.
#
# WHY THIS EXISTS. The vm-tests retry wrapper emits `FLAKE-A-RETRY test= run= attempt=`, and
# docs/ci-flake-ledger.md's census counts those markers to measure the shape-A flake rate. As of
# 2026-08-27 the census reads ZERO across 5 runs / 45 job executions — and every one of those was
# a first-attempt green, so the emit path has never once executed in CI. It sits behind a failed
# build plus two `exit 1` gates.
#
# A SILENTLY-DEAD EMITTER AND A FIXED HARNESS READ IDENTICALLY: both are zero. That sentence is
# already in the ledger about the census grep, where it was guarded. It is just as true one layer
# down about the thing being grepped, where it was not. Saturation was guarded twice (Geist's
# `run=[0-9]+`, my `sort -u`); the emitter's live path was guarded never.
#
# So the decision logic moved out of the workflow YAML and into a script that can be RUN. Not
# duplicated here — duplicating it would be the reader/writer-in-two-languages scar this repo has
# already paid for four times. The workflow calls the same file this battery calls.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
DECIDE="$HERE/../scripts/flake-retry-decide.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
fails=0
SIG='Shell did not start in time'

run() {  # run <logfile> ; sets rc/out
  out="$($DECIDE "$1" test-identity-boot 33049576961 1 2>&1)"; rc=$?
}
bad() { fails=$((fails+1)); echo "FAIL: $1"; }

# --- ARM 1: shape A alone -> RETRY, and THE MARKER IS ACTUALLY EMITTED --------------------
# This is the arm the corpus cannot produce. It is the whole point of the file.
printf 'building...\n%s\n' "$SIG" > "$TMP/a.log"
run "$TMP/a.log"
[ "$rc" -eq 0 ] || bad "shape-A alone did not decide RETRY (rc=$rc)"
echo "$out" | grep -q '^::warning::FLAKE-A-RETRY test=test-identity-boot run=33049576961 attempt=1$' \
  || bad "shape-A alone emitted no census-shaped marker. THE CENSUS READS ZERO EITHER WAY."

# --- ARM 2: the emitted marker matches THE CENSUS GREP, byte for byte ---------------------
# Not a restatement of ARM 1: ARM 1 asserts a string I wrote, this asserts the ledger's
# instrument accepts it. The two drifting apart is precisely how a census silently reads zero.
CENSUS='FLAKE-A-RETRY test=[a-z0-9-]+ run=[0-9]+ attempt=[0-9]+'
[ "$(echo "$out" | grep -coE "$CENSUS")" -eq 1 ] \
  || bad "the census grep from docs/ci-flake-ledger.md does not match the emitted marker"

# --- ARM 3: no signature -> NO RETRY ------------------------------------------------------
printf 'error: builder failed with exit code 1\n' > "$TMP/b.log"
run "$TMP/b.log"
[ "$rc" -ne 0 ] || bad "a failure with NO shape-A signature was retried"
echo "$out" | grep -q '::error::' || bad "no-signature path emitted no ::error::"
echo "$out" | grep -q 'FLAKE-A-RETRY' && bad "no-signature path emitted a census marker anyway"

# --- ARM 4: signature AND assertion -> NO RETRY -------------------------------------------
# MANUFACTURED, because the real corpus cannot make it: the one genuine assertion log
# (32630978544) has zero shape-A lines, so it exits at ARM 3's gate and leaves this one
# untested. Concatenating the two shapes is the only way this arm is ever exercised.
{ printf '%s\n' "$SIG"; printf 'RequestedAssertionFailed: identity seal absent\n'; } > "$TMP/c.log"
run "$TMP/c.log"
[ "$rc" -ne 0 ] || bad "a REAL assertion failure was retried because shape A was also present"
echo "$out" | grep -q 'FLAKE-A-RETRY' && bad "assertion path emitted a census marker — a genuine red would be counted as a flake"

# --- ARM 5 / CONTROL: the assertion gate is reachable at all ------------------------------
# Without this, ARM 4 passes on a decider that refuses EVERYTHING, and so does ARM 3.
# ARM 1 is the accept-side control, this is the statement of why it is load-bearing.
printf 'AssertionError: nope\n' > "$TMP/d.log"
run "$TMP/d.log"
[ "$rc" -ne 0 ] || bad "assertion without shape A was retried"

# --- ARM 6: missing log file is NOT a retry ----------------------------------------------
# Fail CLOSED here, unlike the debounce guard. A retry decision made on evidence that does not
# exist is a retry that could mask any red at all, so absence must never reach the emit path.
run "$TMP/does-not-exist.log"
[ "$rc" -ne 0 ] || bad "a MISSING log decided RETRY — absence of evidence read as shape A"

if [ "$fails" -eq 0 ]; then echo "flake-retry-decide battery: all 6 arms pass"; else
  echo "flake-retry-decide battery: $fails FAILING"; fi
exit "$fails"
