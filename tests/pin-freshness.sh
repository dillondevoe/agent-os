#!/usr/bin/env bash
# install.sh's FLAKE_REV must not silently rot behind main.
#
# WHY THIS EXISTS. The pin (install.sh) makes a fresh install build a KNOWN rev instead of
# whatever main happens to be — staleness is preferred to first-boot breakage. But a pin has
# its own failure mode, and it is the quiet one: main advances, nobody bumps, and months later
# a fresh install lays down a system that predates every fix since. Nothing errors. The
# installer prints a rev and builds it perfectly. Rabbot's ruling 2026-08-31: "consider a CI
# check that fails when main HEAD is >N commits past the pin, so aging is LOUD, not just
# visible-if-someone-looks." A pin whose freshness depends on someone remembering is the same
# class as a scanner with no timer.
#
# WHAT IT ASSERTS, and the asymmetry is deliberate:
#   1. The pin PARSES and is a full 40-char sha (an abbreviated rev resolves differently as
#      the object store grows, and `github:owner/repo/<short>` is not guaranteed stable).
#   2. The pin is an ANCESTOR of main. A pin naming a rev that is not on main — a deleted
#      branch, a force-pushed commit, a typo that happens to be a valid sha — fetches
#      something nobody reviewed, or nothing at all.
#   3. main is at most MAX_DRIFT commits past the pin.
#
# WHAT IT CANNOT TELL YOU: that the pinned rev BOOTS. This is a distance measure, not a
# verification. A pin bumped to a green-but-never-installed HEAD passes this check and is
# exactly the thing the pin exists to prevent, which is why install.sh says bump it as part
# of a verified release rather than as a habit. Green here means "not rotting", never "known good".
set -uo pipefail

MAX_DRIFT="${MAX_DRIFT:-25}"
BASE="${BASE:-origin/main}"
rc=0
fail() { echo "FAIL: $*"; rc=1; }
pass() { echo "PASS: $*"; }

pin="$(sed -n 's/^FLAKE_REV="\${FLAKE_REV:-\([^}]*\)}"$/\1/p' install.sh)"

if [ -z "$pin" ]; then
  echo "CANNOT-ASSESS: no FLAKE_REV default found in install.sh."
  echo "Either the pin was removed (install.sh is back on a moving ref -- that is the"
  echo "regression this check exists for) or its spelling changed and this extractor is now"
  echo "reading a line that no longer exists. Both are failures; neither is 'clean'."
  exit 2
fi
pass "extracted pin $pin"

if ! printf '%s' "$pin" | grep -qE '^[0-9a-f]{40}$'; then
  fail "FLAKE_REV is not a full 40-char sha: '$pin'. Short revs and branch names are moving targets."
else
  pass "pin is a full 40-char sha"
fi

if ! git cat-file -e "${pin}^{commit}" 2>/dev/null; then
  fail "pin $pin is not a commit in this repository."
elif ! git merge-base --is-ancestor "$pin" "$BASE" 2>/dev/null; then
  fail "pin $pin is NOT an ancestor of $BASE -- it names a rev that is not on the mainline."
else
  pass "pin is an ancestor of $BASE"
  drift="$(git rev-list --count "${pin}..${BASE}")"
  if [ "$drift" -gt "$MAX_DRIFT" ]; then
    fail "pin is $drift commits behind $BASE (max $MAX_DRIFT). Bump FLAKE_REV as part of a VERIFIED release -- a fresh install currently lays down a system $drift commits old."
  else
    pass "pin is $drift commits behind $BASE (max $MAX_DRIFT)"
  fi
fi

[ "$rc" = 0 ] && echo "RESULT clean" || echo "RESULT drift"
exit "$rc"
