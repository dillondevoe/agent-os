#!/usr/bin/env bash
# Decide whether a failed vm-test build is a retryable shape-A flake, and EMIT THE CENSUS MARKER.
#
#   flake-retry-decide.sh <log> <test> <run_id> <attempt>
#     rc 0  -> retry; the `FLAKE-A-RETRY test= run= attempt=` marker is written to stdout
#              (as a ::warning::) and appended to $GITHUB_STEP_SUMMARY when that is set
#     rc 1  -> do not retry: the failure carries no shape-A signature
#     rc 2  -> do not retry: a genuine assertion failure, even though shape A is also present
#     rc 3  -> do not retry: the log does not exist
#
# THIS LOGIC USED TO LIVE INLINE IN .github/workflows/vm-tests.yml, WHERE IT COULD NOT BE RUN.
# It sits behind a failed build plus two gates, so across 5 runs and 45 job executions since the
# retry shipped it has never once executed. The census in docs/ci-flake-ledger.md counts the
# markers it emits — and reads zero. A dead emitter and a fixed harness are the same zero.
#
# The ledger already guards the census grep against reading zero for the wrong reason, twice
# (`run=[0-9]+` against the runner echoing the script body; `sort -u` against a doubled logs zip).
# Both guard the READER. Nothing guarded the thing being read. Moving the decision here does not
# make it more correct — it makes it EXERCISABLE, which is the property the census's meaning rests
# on. `tests/flake-retry-decide-battery.sh` is that exercise; it asserts the emitted marker
# matches the ledger's own grep, so the instrument and its input cannot drift apart silently.
set -u

log=${1:?usage: flake-retry-decide.sh <log> <test> <run_id> <attempt>}
test_name=${2:?}
run_id=${3:?}
attempt=${4:?}

SIG='Shell did not start in time'
# Failure-only markers. NOT `subtest`, which appears in healthy output too.
ASSERT='RequestedAssertionFailed|AssertionError|Test .* failed'

# FAIL CLOSED on a missing log, unlike the debounce guard which fails OPEN. The asymmetry is
# deliberate and the directions are not interchangeable: a debounce that engages on a broken
# clock silences the loop, while a RETRY granted on evidence that does not exist can mask any
# red at all. Absence must never reach the emit path.
if [ ! -f "$log" ]; then
  echo "::error::${test_name}: retry decision asked for on a log that does not exist (${log})."
  exit 3
fi

if ! grep -q "$SIG" "$log"; then
  echo "::error::${test_name} failed WITHOUT the shape-A signature. Not retrying."
  exit 1
fi

# ASSERT IS EVALUATED ONLY ON LINES THAT DO NOT THEMSELVES CARRY THE SIGNATURE.
#
# `Test .* failed` was written to catch a genuine red. But the nix vm-test driver reports a
# shell timeout inside a named subtest AS A FAILED TEST, quoting the signature as its error:
#
#   !!! Test "2. token re-asserts ..." failed with error: "Shell did not start in time"
#
# That is shape A wearing an assert-shaped sentence, and it is the ONLY shape shape-A takes in
# production — the bare SIG line the battery fixtured is one the driver never emits. So the
# whole-file `grep -qE` classified every real shape-A flake as a genuine red: rc=2, no retry,
# no marker, and a census that reads near-zero for exactly the wrong reason. Measured on job
# 98504220231 of run 33068427490 (test-seal-faildown, 2026-08-27T11:40Z): 2 ASSERT matches in
# 8159 lines, ZERO of them free of the signature.
#
# The predicate wanted is `SIG AND (an assert that is NOT this signature)`. Geist filed the
# two-predicate row for the CENSUS side on 2026-08-27 — retry wants `SIG AND NOT ASSERT`,
# census wants `SIG AND (NOT ASSERT OR ASSERT-carries-SIG)` — and recorded that the gate does
# not move. It does. The same second term belongs on both sides; only the polarity differs.
#
# The line-scoped form is what makes the co-occurrence case still work: a genuine assertion on
# ITS OWN line survives the filter and blocks the retry (battery ARM 8), so this narrows the
# gate rather than removing it.
if grep -E "$ASSERT" "$log" | grep -qv "$SIG"; then
  echo "::error::${test_name} carries a real assertion failure. Not retrying, even though the" \
       "shape-A signature is also present — a retry here would hide a genuine red."
  exit 2
fi

# The countable trace. Both destinations on purpose: the job log is what the census greps, the
# step summary is what a human sees without opening logs.
#
# `attempt=` is load-bearing, not decoration. GITHUB_RUN_ID is IDENTICAL across attempts of the
# same run, so a `test=X run=Y` pair alone is not unique within a run: dedupe the census across a
# whole run rather than within one attempt and two real instances silently merge into one. The
# triple closes that by construction, so the census's correctness stops depending on a prose
# caveat nobody re-reads.
marker="FLAKE-A-RETRY test=${test_name} run=${run_id} attempt=${attempt}"
echo "::warning::${marker}"
[ -n "${GITHUB_STEP_SUMMARY:-}" ] && echo "$marker" >> "$GITHUB_STEP_SUMMARY"
exit 0
