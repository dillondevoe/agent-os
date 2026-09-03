#!/usr/bin/env bash
# What an open PR's green does NOT tell you — three axes, reported side by side.
#
# WHY THIS EXISTS (2026-09-03, #232). #232 sat mergeable on eleven green checks. Every visible
# signal said fine: mergeStateStatus CLEAN, no conflict, and the path intersection between the
# 37 commits it was behind and the six files it changed was EMPTY. What none of those could see
# is that `3c6ea20` had landed inside that gap and extended the self-swallowing-arms lint to scan
# `.sh` for the first time — and #232's whole test surface is `.sh`. Its green was made by a run
# that could not ask the question. Nothing was wrong with the PR; the green just meant less than
# it looked like it meant, and no display anywhere showed that.
#
# THE PREDICATE, AND THE ONE THAT LOOKS RIGHT AND ISN'T. The obvious test is ancestry — is the
# gate commit an ancestor of the PR head. That is WRONG and it over-reports. These workflows all
# trigger on `pull_request`, and a `pull_request` run checks out the merge of the head into main
# AS OF THE RUN. So a run carries main's criteria at run time: a branch forked months ago whose
# checks re-ran ten minutes ago has every current gate, and ancestry flags it anyway. The correct
# question is temporal — DID ANY CRITERIA-CHANGING COMMIT LAND AFTER THIS PR'S LATEST RUN? The two
# agreed on #232 by coincidence, which is exactly how a wrong predicate survives.
#
# WHAT IT CANNOT TELL YOU, and this is not a footnote — it is the axis that bit the author of this
# file the day it was written. A currency lens answers "was this asked today" and is COMPLETELY
# BLIND to "did it pass." Having built the lens, I read #168 through it, reported it as a currency
# exposure, and did not notice it was RED — a real defect, which no re-run would have fixed. So the
# conclusion column below reports the check outcome alongside the currency, because a report that
# answers only its own question trains its reader to ask only that question.
#
#   "no overlap"      answers CONTENT
#   "N commits behind" answers DISTANCE
#   "asked today"     answers CURRENCY   <- this file
#   "pass/fail"       answers OUTCOME
#
# None of the first three answers the fourth. All four are printed.
set -uo pipefail

# Paths whose change alters what CI ASKS, rather than what it builds. A commit touching these
# devalues every outstanding green without moving any visible number.
CRITERIA_RE='^(\.github/workflows/|flake\.nix$|tests/|tools/)'
BASE="${BASE:-origin/main}"

command -v gh >/dev/null 2>&1 || { echo "CANNOT-ASSESS: gh not on PATH; currency needs run metadata."; exit 2; }

prs="$(gh pr list --state open --json number -q '.[].number' 2>/dev/null)"
if [ -z "$prs" ]; then echo "no open PRs"; exit 0; fi

rc=0
for pr in $prs; do
  head="$(gh pr view "$pr" --json headRefOid -q .headRefOid 2>/dev/null)"
  run="$(gh pr checks "$pr" 2>/dev/null | grep -oE 'runs/[0-9]+' | head -1 | cut -d/ -f2)"
  if [ -z "$run" ]; then
    echo "PR $pr: NO RUN FOUND — no green to weigh. Not 'clean'."
    rc=1; continue
  fi
  when="$(gh run view "$run" --json createdAt -q .createdAt 2>/dev/null)"
  # OUTCOME first, deliberately: the axis this report does not measure goes at the front so it
  # cannot be crowded out by the one it does.
  fails="$(gh pr checks "$pr" 2>/dev/null | awk '$2=="fail"{print $1}' | tr '\n' ' ')"
  outcome="pass"; [ -n "$fails" ] && { outcome="FAIL: $fails"; rc=1; }

  mb="$(git merge-base "$BASE" "$head" 2>/dev/null)"
  dist="$(git rev-list --count "${mb}..${BASE}" 2>/dev/null || echo '?')"

  n=0; which=""
  for c in $(git rev-list "${mb}..${BASE}" 2>/dev/null); do
    git show --name-only --format='' "$c" 2>/dev/null | grep -qE "$CRITERIA_RE" || continue
    [ "$(git log -1 --format=%cI "$c")" \> "$when" ] && { n=$((n+1)); which="$which $(git log -1 --format=%h "$c")"; }
  done

  echo "PR $pr"
  echo "    outcome  : $outcome"
  echo "    distance : $dist commits behind $BASE"
  echo "    currency : $n criteria commit(s) landed after its run ($when)"
  [ "$n" -gt 0 ] && echo "               ->$which"
  [ "$n" -gt 0 ] && echo "               a re-run repairs this and moves NO other number here."
done

# Deliberately NOT a merge gate. Currency is context for a human deciding whether to re-run; a
# stale-criteria PR is not thereby wrong, and promoting this to blocking would need a labelled
# false-positive measurement first. rc reflects OUTCOME failures only.
exit "$rc"
