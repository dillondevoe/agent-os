#!/usr/bin/env bash
# Emit a countable time-to-connect measurement from a vm-test build log. Runs on EVERY vm-test
# job, green or red.
#
# WHY IT EMITS ON SUCCESS. Shape A's 19-specimen partition (docs/ci-flake-ledger.md) shows all
# 19 tracebacks terminating in `connect()` on the guest root shell — 14 reached through a
# top-level `wait_for_unit` before any reboot, 5 through a reboot subtest. Geist measured the
# cheap contention hypothesis and it came back NEGATIVE: intra-repo run crowding does not
# predict SIG (13.5% at 0 neighbours vs 12.5% at 1-2), and hour-of-day is flat. What remains
# unmeasurable from run metadata is the hosted runner's own speed, and the only way to see it
# is to record connect duration on runs that DID connect. A failure-only probe has no baseline
# to compare against, which is the whole reason this one is not conditioned on failure.
#
# WHY n=0 IS EMITTED RATHER THAN SKIPPED. The 14 top-level specimens carry no
# `(connecting took N seconds)` line at all — the connect never completed, so the driver never
# printed a duration. The ABSENCE is the datum. If this script stayed silent in that case, a
# zero-connect job and a job where the probe never ran would produce byte-identical evidence:
# nothing. That is the census-reads-zero-for-the-wrong-reason shape (185a937) and the
# well-formed-wrong-answer row. A missing LOG is loud (rc=1) precisely so that n=0 keeps
# meaning "no connect happened" and nothing else.
set -u

log="${1:?usage: vm-connect-probe.sh <log> <test_name> <run_id> <attempt>}"
test_name="${2:?}"; run_id="${3:?}"; attempt="${4:?}"

if [ ! -f "$log" ]; then
  echo "::error::vm-connect-probe: log '$log' does not exist. Refusing to emit n=0, which would" \
       "be indistinguishable from a job whose guest shell never connected."
  exit 1
fi

# The driver's own wording, matched exactly. Anchored on "connecting" so that other `took N
# seconds` lines — booting, unit starts — cannot be mistaken for a connect.
# COUNT THE STREAM RENDERING ONLY. On failure, nix reprints the tail of the build log as an
# indented summary block, so one connect appears twice: once as
# `vm-test-run-agentos-NAME> machine: (connecting took N seconds)` and once indented with no
# stream prefix. Measured: job 98504220231 renders a single 12.89s connect on lines 4026 and
# 6676; the green job 98692802259 renders two DISTINCT connects and no summary copy. Counting
# both renderings reports n=2 for one connect — an over-count concentrated in exactly the
# failing runs this probe is meant to characterise.
#
# The discriminator is the RENDERING, not the value. Deduplicating identical numbers would
# wrongly collapse two genuine connects that happened to take the same time; only the
# stream-prefixed line is 1:1 with a connect event. Same class as PR #180, where the runner
# echoed a marker's source into the job log.
# THE NODE NAME IS NOT `machine`. Found on the FIRST live run of the merged probe (fabb4da, run
# 33128842725): 5 of 9 GREEN jobs reported n=0. Only seal-faildown and identity-boot name their
# VM node `machine`; the others are `sealed`, `peer`, `meshpeer`, `box` (tests/*.nix `nodes.*`).
# A green job reading n=0 is byte-identical to shape A's n=0 — the exact confusion this probe
# exists to prevent, reintroduced by a fixture that carried the one node name the author had
# seen. Match ANY node name after the stream prefix; the stream prefix is still the discriminator
# against the indented summary copy. Multi-node tests (egress-uid-scope: sealed + peer) count
# one connect per node, so n>1 on a single boot is expected there.
secs=$(grep -oE '[a-z0-9-]+> *[A-Za-z0-9_-]+: \(connecting took [0-9.]+ seconds\)' "$log" \
       | grep -oE '[0-9.]+ seconds' | grep -oE '^[0-9.]+' || true)

n=$(printf '%s' "$secs" | grep -c . || true)
if [ "$n" -eq 0 ]; then
  csv=""; max=""
else
  csv=$(printf '%s' "$secs" | paste -sd, -)
  max=$(printf '%s\n' "$secs" | sort -g | tail -1)
fi

marker="VM-CONNECT test=${test_name} run=${run_id} attempt=${attempt} n=${n} secs=${csv} max=${max}"
echo "::notice::${marker}"
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  echo "- \`${marker}\`" >> "$GITHUB_STEP_SUMMARY"
fi
exit 0
