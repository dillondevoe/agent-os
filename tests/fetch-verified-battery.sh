#!/usr/bin/env bash
# fetch-verified-battery.sh — arms for bin/fetch-verified.sh.
#
#   bash tests/fetch-verified-battery.sh <path-to-fetch-verified.sh> [repo-root]
#
# Arm A is the one that earns the rest: it plants BYTES THAT DIFFER FROM THE PIN and
# asserts the helper refuses. Without it, a helper reduced to `exit 0` passes every
# other arm here forever — the same reason agos-key-drift was shown failing on a
# planted key before its clean run was trusted.
#
# The seam is `AGOS_PIN_MANIFEST` plus `file://` URLs: the fixtures are real fetches
# through the real curl path, not a mocked one. Arm B's control matters for the
# mirror-image failure — a helper that refused EVERYTHING would pass arm A.
set -uo pipefail

# ROOT is REQUIRED, and that is a change. It used to default to empty, which made arm I --
# the ONLY arm that checks the shipped `bin/setup-brain.sh` does not pipe a fetch into a shell --
# evaporate in silence: the whole block is wrapped in `[ -n "$ROOT" ] && [ -f ... ]`, so an unset
# ROOT or a dropped `cp` line in flake.nix removes two arms and the battery still prints the
# byte-identical verdict `PASS`. Demonstrated before this change: with ROOT the run ends
# "ok I setup-brain.sh has no live curl|bash"; without it that line is simply not there, and
# nothing counts the difference. An existence guard is legitimate for "not applicable here" and
# illegitimate for "the file we are asserting about should always exist" -- this one was the
# second wearing the clothes of the first.
FV="${1:?usage: fetch-verified-battery.sh <fetch-verified.sh> <repo-root>}"
ROOT="${2:?usage: fetch-verified-battery.sh <fetch-verified.sh> <repo-root> (repo-root is required: it is what arms arm I)}"
W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT
fail=0
ran=0
ok()  { echo "  ok   $1"; ran=$((ran+1)); }
bad() { echo "  FAIL $1"; fail=1; ran=$((ran+1)); }

# The count below is the general form of the same fix. Arm I is the instance I found; a verdict
# that does not depend on HOW MANY arms ran cannot notice ANY of them going missing. Two sibling
# batteries already print "all N arms pass"; this one printed a bare PASS.
WANT_ARMS=13

GOOD="$W/upstream.sh"; printf '#!/bin/sh\necho hello\n' > "$GOOD"
GOODSHA="$(sha256sum "$GOOD" | awk '{print $1}')"
BAD="$W/hostile.sh";   printf '#!/bin/sh\nrm -rf /\n' > "$BAD"

mkman() { printf '# pins\n%s\n' "$1" > "$W/man.txt"; }
run()   { AGOS_PIN_MANIFEST="$W/man.txt" bash "$FV" "$@" >"$W/out" 2>&1; echo $?; }

# A — pin records the good script; upstream now serves the hostile one. MUST refuse.
mkman "art  $GOODSHA  file://$BAD"
rc=$(run art "$W/dst")
[ "$rc" = 1 ] && ok "A tampered payload -> rc 1" || bad "A tampered payload -> rc $rc (want 1)"
grep -q MISMATCH "$W/out" && ok "A names MISMATCH" || bad "A did not say MISMATCH"
# H — and it leaves NOTHING behind. Partial execution is the pipe's failure mode; a
# partial FILE would just relocate it to the caller.
[ ! -e "$W/dst" ] && ok "H mismatch writes no outfile" || bad "H left a partial outfile"

# B — control: matching digest is accepted and the bytes are the bytes.
mkman "art  $GOODSHA  file://$GOOD"
rc=$(run art "$W/dst")
[ "$rc" = 0 ] && ok "B verified payload -> rc 0" || bad "B verified payload -> rc $rc (want 0)"
cmp -s "$GOOD" "$W/dst" && ok "B outfile is byte-identical" || bad "B outfile differs from source"

# C — an artifact with no pin is not a pass. Fetching it unpinned is the defect.
mkman "other  $GOODSHA  file://$GOOD"
rc=$(run art "$W/dst2")
[ "$rc" = 2 ] && ok "C unpinned name -> rc 2" || bad "C unpinned name -> rc $rc (want 2)"

# D — upstream unreachable. Not clean, not drift: cannot assess.
mkman "art  $GOODSHA  file://$W/absent.sh"
rc=$(run art "$W/dst3")
[ "$rc" = 2 ] && ok "D fetch failure -> rc 2" || bad "D fetch failure -> rc $rc (want 2)"

# E — manifest missing entirely. The instrument cannot read its input.
rc=$(AGOS_PIN_MANIFEST="$W/nope.txt" bash "$FV" art "$W/dst4" >/dev/null 2>&1; echo $?)
[ "$rc" = 2 ] && ok "E absent manifest -> rc 2" || bad "E absent manifest -> rc $rc (want 2)"

# F — a commented-out pin is not a pin. (Catches a lookup that greps instead of parsing.)
printf '#art  %s  file://%s\n' "$GOODSHA" "$GOOD" > "$W/man.txt"
rc=$(AGOS_PIN_MANIFEST="$W/man.txt" bash "$FV" art "$W/dst5" >/dev/null 2>&1; echo $?)
[ "$rc" = 2 ] && ok "F commented pin is not honoured" || bad "F used a commented pin (rc $rc)"

# G — --record reports the CURRENT digest and writes no artifact.
mkman "art  0000  file://$GOOD"
AGOS_PIN_MANIFEST="$W/man.txt" bash "$FV" --record art >"$W/rec" 2>"$W/rec.err"
grep -q "$GOODSHA" "$W/rec" && ok "G --record prints the live digest" || bad "G --record digest missing"
grep -q CHANGED "$W/rec.err" && ok "G --record flags the change" || bad "G --record did not flag CHANGED"

# I — the CALL SITE stayed converted. A verified-fetch helper sitting unused beside a
# live `curl | bash` is the same shape as a scanner installed with no timer.
# The substrate is asserted, not assumed: a missing setup-brain.sh is a FAILURE of this battery,
# not a reason to quietly not run its arms.
[ -f "$ROOT/bin/setup-brain.sh" ] || bad "I substrate absent: $ROOT/bin/setup-brain.sh (arm I cannot run -- this is a failure, not a skip)"
if [ -f "$ROOT/bin/setup-brain.sh" ]; then
  if grep -nE 'curl[^|]*\|[[:space:]]*(sudo )?(ba)?sh' "$ROOT/bin/setup-brain.sh" | grep -vE '^[0-9]+:[[:space:]]*#'; then
    bad "I setup-brain.sh still pipes a fetch into a shell"
  else
    ok "I setup-brain.sh has no live curl|bash"
  fi
  grep -q 'fetch-verified' "$ROOT/bin/setup-brain.sh" && ok "I setup-brain.sh uses fetch-verified" \
    || bad "I setup-brain.sh does not call fetch-verified"
fi

# An arm that stops running is a silent loss of coverage, so the count is part of the verdict.
if [ "$ran" != "$WANT_ARMS" ]; then
  echo "  FAIL arm count: $ran arms ran, expected $WANT_ARMS -- an arm was added or silently lost"
  echo "       (if you deliberately added or removed one, update WANT_ARMS in this file)"
  fail=1
fi

[ "$fail" = 0 ] && { echo "fetch-verified battery: PASS ($ran arms)"; exit 0; }
echo "fetch-verified battery: FAIL"; exit 1
