#!/usr/bin/env bash
# Battery for scripts/vm-connect-probe.sh — the time-to-connect probe.
#
# WHY THIS EXISTS. Shape A (docs/ci-flake-ledger.md) partitions 14/5 across the pre-stagger
# corpus, and all 19 tracebacks bottom out in `connect()` waiting on the guest root shell. The
# 14 top-level cases carry NO `(connecting took N seconds)` line at all, because that connect
# never completed. So the datum is not only the timing — it is the ABSENCE of a timing.
#
# That is why the probe must emit on SUCCESS and must emit n=0 rather than staying silent. A
# probe that only speaks when it has numbers cannot distinguish "no connect happened" from
# "probe never ran" — the same zero the census read for the wrong reason at 185a937, and the
# same well-formed-wrong-answer row.
set -u
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
P="$(dirname "$0")/../scripts/vm-connect-probe.sh"
# The driver's real stream prefix. Every `machine:` line the producer emits carries it; a
# fixture without it is a shape no run produces, which is not coverage of the producer (#208).
PFX="vm-test-run-agentos-seal-faildown> "
fails=0; arms=0
bad() { echo "FAIL: $*"; fails=$((fails+1)); }
run() { arms=$((arms+1)); out=$("$P" "$1" test-x 12345 1 2>&1); rc=$?; }

# The exact census grep a consumer would use. Kept here so the marker and its reader are
# checked together — a marker whose reader is written elsewhere is a rule spelled twice.
CENSUS='::notice::VM-CONNECT test=[a-z-]+ run=[0-9]+ attempt=[0-9]+ n=[0-9]+'

# ARM 1 — the real green shape: two connects, as measured on run 33122558616.
{ printf 'building...\n'
  printf '%smachine: (connecting took 12.61 seconds)\n' "$PFX"
  printf '%smachine: (connecting took 14.31 seconds)\n' "$PFX"; } > "$TMP/green.log"
run "$TMP/green.log"
[ "$rc" -eq 0 ] || bad "ARM1: probe exited $rc on a healthy log"
echo "$out" | grep -qE "$CENSUS" || bad "ARM1: marker does not match the census grep: $out"
echo "$out" | grep -q ' n=2' || bad "ARM1: expected n=2, got: $out"
echo "$out" | grep -q 'max=14.31' || bad "ARM1: expected max=14.31, got: $out"

# ARM 2 — THE ARM THIS SCRIPT EXISTS FOR. A shape-A top-level failure carries no connect line.
# The probe must still SPEAK, with n=0. Silence here would be indistinguishable from not running.
{ printf 'building...\n'
  printf '%smachine: Guest root shell did not produce any data yet...\n' "$PFX"
  printf 'RuntimeError: Shell did not start in time\n'; } > "$TMP/none.log"
run "$TMP/none.log"
[ "$rc" -eq 0 ] || bad "ARM2: probe exited $rc — it must not fail the job it is measuring"
echo "$out" | grep -qE "$CENSUS" || bad "ARM2: probe was SILENT on a zero-connect log — this is the defect it exists to prevent"
echo "$out" | grep -q ' n=0' || bad "ARM2: expected n=0, got: $out"

# ARM 3 — single connect.
printf '%smachine: (connecting took 9.04 seconds)\n' "$PFX" > "$TMP/one.log"
run "$TMP/one.log"
echo "$out" | grep -q ' n=1' || bad "ARM3: expected n=1, got: $out"
echo "$out" | grep -q 'max=9.04' || bad "ARM3: expected max=9.04, got: $out"

# ARM 4 / CONTROL — a `took N seconds` line that is NOT a connect must not be counted. Without
# this arm a probe that counted every "took" line would pass ARMs 1-3.
{ printf '%smachine: (connecting took 5.00 seconds)\n' "$PFX"
  printf '%smachine: (booting took 61.20 seconds)\n' "$PFX"
  printf '%smachine: unit start took 3.10 seconds\n' "$PFX"; } > "$TMP/mixed.log"
run "$TMP/mixed.log"
echo "$out" | grep -q ' n=1' || bad "ARM4 CONTROL: non-connect timing lines were counted — got: $out"
echo "$out" | grep -q 'max=5.00' || bad "ARM4 CONTROL: max drawn from a non-connect line — got: $out"

# ARM 7 — THE DUAL RENDERING, and it is the defect the hand-made fixtures above could not show.
# On FAILURE, nix reprints the tail of the build log as an indented summary block, so a single
# connect appears TWICE: once stream-prefixed (`vm-test-run-agentos-NAME> machine: ...`) and once
# indented. Measured on the real corpus: job 98504220231 renders one 12.89s connect on lines 4026
# and 6676, while the green job 98692802259 renders two DISTINCT connects (12.61, 14.31) with no
# summary copy at all. Counting both renderings reports n=2 for one connect and corrupts the very
# distribution this probe exists to build.
#
# The discriminator is the RENDERING, not the value: deduping identical numbers would wrongly
# collapse two genuine connects that happened to take the same time. Only the stream-prefixed
# line is 1:1 with a connect event. Same class as PR #180 — the runner echoes a marker's source
# into the job log, so count the copy that corresponds to the event.
{ printf '2026-08-27T11:43:10.9898335Z vm-test-run-agentos-seal-faildown> machine: (connecting took 12.89 seconds)\n'
  printf '2026-08-27T11:50:38.4685363Z     machine: (connecting took 12.89 seconds)\n'; } > "$TMP/dual.log"
run "$TMP/dual.log"
echo "$out" | grep -q ' n=1' || bad "ARM7: one connect rendered twice was counted twice — got: $out"

# ARM 8 / CONTROL for ARM 7 — two GENUINE connects of the SAME duration must still count as two.
# Without this, a fix that deduped by value would pass ARM 7 and silently under-count.
{ printf '2026-08-27T22:30:03Z vm-test-run-agentos-seal-faildown> machine: (connecting took 12.61 seconds)\n'
  printf '2026-08-27T22:32:41Z vm-test-run-agentos-seal-faildown> machine: (connecting took 12.61 seconds)\n'; } > "$TMP/twinsame.log"
run "$TMP/twinsame.log"
echo "$out" | grep -q ' n=2' || bad "ARM8 CONTROL: two real connects of equal duration were collapsed — got: $out"

# ARM 9 — THE NODE IS NOT ALWAYS `machine`. Real shape from the first live run of the merged probe
# (run 33128842725, job 98713342301, egress-uid-scope): the node is `sealed`, and the probe read
# n=0 on a GREEN job — 5 of the 9 matrix entries did. Only seal-faildown and identity-boot use
# `machine`; the rest are `sealed`, `peer`, `meshpeer`, `box`. Green-n=0 is byte-identical to
# shape-A-n=0, which is the confusion this probe exists to prevent.
{ printf '2026-08-28T00:13:31.7572097Z vm-test-run-agentos-egress-uid-scope> sealed: starting vm\n'
  printf '2026-08-28T00:13:45.1201234Z vm-test-run-agentos-egress-uid-scope> sealed: (connecting took 13.14 seconds)\n'
  printf '2026-08-28T00:13:47.9001234Z vm-test-run-agentos-egress-uid-scope> peer: (connecting took 14.02 seconds)\n'; } > "$TMP/sealed.log"
run "$TMP/sealed.log"
echo "$out" | grep -q ' n=2' || bad "ARM9: non-'machine' node names were not counted (two nodes, two connects) — got: $out"

# ARM 10 / CONTROL for ARM 9 — the indented summary copy of a non-'machine' node must STILL be
# excluded. Widening the node name must not widen past the stream prefix.
{ printf '2026-08-28T00:13:45Z vm-test-run-agentos-egress-uid-scope> sealed: (connecting took 13.14 seconds)\n'
  printf '2026-08-28T00:13:50Z     sealed: (connecting took 13.14 seconds)\n'; } > "$TMP/sealed-dual.log"
run "$TMP/sealed-dual.log"
echo "$out" | grep -q ' n=1' || bad "ARM10 CONTROL: widening the node name also admitted the summary copy — got: $out"

# ARM 11 / CONTROL for ARM 9, the OTHER direction. ARM 10 controls that the widened node name did
# not reach PAST the stream prefix; nothing yet controls that it did not swallow the node FIELD
# itself. A regex that made the `<node>:` part optional would pass ARM 9 (box counted), ARM 10 (the
# summary copy has no prefix and is still excluded) and every other arm, while counting any
# prefixed timing line as a connect. #212 widened `machine` -> `[A-Za-z0-9_-]+` and the next
# widening is the one to fear: the field is REQUIRED, and this is the arm that says so.
printf '2026-08-28T00:10:00Z vm-test-run-agentos-x> (connecting took 18.23 seconds)\n' > "$TMP/nonode.log"
run "$TMP/nonode.log"
echo "$out" | grep -q ' n=0' || bad "ARM11 CONTROL: a line with no <node>: field was counted as a connect — got: $out"

# ARM 5 / CONTROL — a missing log file must be loud, not a quiet n=0. Otherwise ARM 2's n=0
# loses its meaning: "no connect" and "no log" would print the same thing.
run "$TMP/does-not-exist.log"
[ "$rc" -ne 0 ] || bad "ARM5 CONTROL: a missing log produced rc=0 — n=0 can no longer mean 'no connect'"

# ARM 6 / VACUITY — the fixture in ARM 1 must actually contain the pattern the probe looks for.
arms=$((arms+1))   # ARM 6 asserts without calling run(), so it must count itself
grep -qE '\(connecting took [0-9.]+ seconds\)' "$TMP/green.log" \
  || bad "ARM6: the ARM1 fixture does not carry the driver's real shape — ARM1 is vacuous"

# SELF-CHECK — the printed count must equal the number of arms this file DECLARES. `arms` is
# incremented by run(), so an arm that asserts without invoking the probe (ARM 6, vacuity) was
# silently uncounted: the battery reported "all 10 arms pass" with 11 arms present. Correcting
# the number alone would leave the next such arm to drift the same way, so the count is now
# checked against the `# ARM` labels rather than trusted. A battery that miscounts its own arms
# is the instrument-miscounts-itself shape one level down from what this battery exists to catch.
declared=$(grep -cE '^# ARM ' "$0")
[ "$arms" -eq "$declared" ] \
  || bad "SELF-CHECK: $arms arms counted but $declared declared — an arm is not being counted"

if [ "$fails" -eq 0 ]; then echo "vm-connect-probe: all $arms arms pass"; else echo "$fails FAILING of $arms"; exit 1; fi
