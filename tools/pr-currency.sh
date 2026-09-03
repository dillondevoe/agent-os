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
#
# AND THE OUTCOME COLUMN GOT IT WRONG ON ITS FIRST DAY, which is why it now has a control arm.
# v1 read the state with awk's DEFAULT field separator. `gh pr checks` emits TAB-separated rows, and
# every matrix job name contains a space -- "vm-test (test-identity-boot)" -- so default splitting put
# `(test-identity-boot)` in $2 and the state in $3. The fail test never matched ANY vm-test row: nine
# of twelve on #232, and the vm-test matrix is the most load-bearing gate in the repo. A real failure
# there would have printed `pass`. v1 also treated every non-`fail` state as a pass, so `pending`,
# `cancelled`, `timed_out` and `skipped` all read green -- "has not run yet" rendered identically to
# "ran and passed", which is the same shape as the merely-weaker green this file was written about.
# Both were found by Augur reading the file, not by running it, because nothing ran the parser against
# a row it could fail on. Hence --selftest below: a detector that finds nothing is indistinguishable
# from a broken detector, and this one WAS the broken kind while reporting clean.
set -uo pipefail

# Paths whose change alters what CI ASKS, rather than what it builds. A commit touching these
# devalues every outstanding green without moving any visible number.
CRITERIA_RE='^(\.github/workflows/|flake\.nix$|tests/|tools/)'
BASE="${BASE:-origin/main}"

# Reads `gh pr checks` rows on stdin, prints ONE outcome verdict. THREE states, not two.
#
# FS='\t' is load-bearing, not style: matrix job names contain spaces, so default splitting puts the
# state in a field that shifts per row. And the default is UNKNOWN -- a row whose state this function
# does not recognise must not be absorbed into `pass`, because an unrecognised state and a green one
# are exactly the pair this file exists to keep apart.
classify_checks() {
  awk -F'\t' '
    NF < 2 { next }
    { rows++; st=$2
      if (st=="fail" || st=="failure" || st=="timed_out" || st=="cancelled" || st=="startup_failure" || st=="action_required") { bad = bad " " $1 }
      else if (st=="pending" || st=="queued" || st=="in_progress" || st=="waiting" || st=="expected") { inc = inc " " $1 }
      else if (st=="pass" || st=="success" || st=="skipping" || st=="skipped" || st=="neutral") { ok++ }
      else { unk = unk " " $1 "=" st }
    }
    END {
      if (rows == 0)      { print "NO ROWS -- gh returned nothing; this is not a green"; exit }
      if (bad != "")      { print "FAIL:" bad; exit }
      if (unk != "")      { print "UNKNOWN state:" unk; exit }
      if (inc != "")      { print "INCOMPLETE (still running):" inc; exit }
      print "pass (" ok " checks)"
    }'
}

# The control arm. v1 shipped a parser that could not report a failure on 75% of this repo\'s rows and
# reported clean the whole time; nothing here exercised it against a row it should have caught. These
# fixtures are tab-separated on purpose -- SP1 is the exact shape that defeated v1.
if [ "${1:-}" = "--selftest" ]; then
  fail=0
  arm() { got="$(printf '%b' "$2" | classify_checks)"
          case "$got" in $3) echo "  ok   $1" ;; *) echo "  FAIL $1: got [$got] want [$3]"; fail=1 ;; esac; }
  echo "pr-currency selftest"
  arm "SP1 space-in-name FAIL is seen (v1 could not)" 'vm-test (test-identity-boot)\tfail\t7m\turl\n' 'FAIL:*'
  arm "SP2 space-in-name pass is not misread"         'vm-test (test-identity-boot)\tpass\t7m\turl\n' 'pass*'
  arm "P1  pending is NOT a pass"                     'gate\tpending\t0s\turl\n'                      'INCOMPLETE*'
  arm "P2  fail outranks pending"                     'a\tpending\t0s\tu\nb\tfail\t1s\tu\n'        'FAIL:*'
  arm "P3  cancelled is not a pass"                   'gate\tcancelled\t0s\turl\n'                    'FAIL:*'
  arm "P4  an unrecognised state is UNKNOWN"          'gate\tbanana\t0s\turl\n'                       'UNKNOWN*'
  arm "P5  empty input is not a pass"                 ''                                                'NO ROWS*'
  arm "C1  CONTROL: all-green really does say pass"   'a\tpass\t1s\tu\nb\tpass\t1s\tu\n'          'pass*'
  # C1 is why the seven arms above mean anything: without it a classifier that answered FAIL to
  # everything would pass all of them.
  echo "TZ arm: a commit at 02:00-05:00 vs a run at 05:00Z"
  cz="$(TZ=UTC date -u -d '2026-09-03T02:00:00-05:00' +%Y-%m-%dT%H:%M:%SZ)"
  if [ "$cz" \> "2026-09-03T05:00:00Z" ]; then echo "  ok   TZ1 normalised compare counts it ($cz)"
  else echo "  FAIL TZ1: $cz did not sort after 05:00Z"; fail=1; fi
  [ "$fail" = 0 ] && echo "ALL GREEN" || echo "SELFTEST FAILED"
  exit "$fail"
fi

command -v gh >/dev/null 2>&1 || { echo "CANNOT-ASSESS: gh not on PATH; currency needs run metadata."; exit 2; }

prs="$(gh pr list --state open --json number -q '.[].number' 2>/dev/null)"
if [ -z "$prs" ]; then echo "no open PRs"; exit 0; fi

rc=0
for pr in $prs; do
  head="$(gh pr view "$pr" --json headRefOid -q .headRefOid 2>/dev/null)"
  # OLDEST run of the set, not an arbitrary one. A PR's checks span several workflow runs; `head -1`
  # took whichever gh listed first. Today they share a timestamp (one push triggers all three), so it
  # cannot be wrong yet -- it breaks the first time someone re-runs one workflow alone, after which a
  # fresh sibling masks the stale gates. Currency is a property of the STALEST run, so take the min.
  when="$(for r in $(gh pr checks "$pr" 2>/dev/null | grep -oE 'runs/[0-9]+' | cut -d/ -f2 | sort -u); do
            gh run view "$r" --json createdAt -q .createdAt 2>/dev/null; done | sort | head -1)"
  run="$when"
  if [ -z "$run" ]; then
    echo "PR $pr: NO RUN FOUND — no green to weigh. Not 'clean'."
    rc=1; continue
  fi
  # OUTCOME first, deliberately: the axis this report does not measure goes at the front so it
  # cannot be crowded out by the one it does.
  outcome="$(gh pr checks "$pr" 2>/dev/null | classify_checks)"
  # rc counts "could not determine the outcome" as an outcome failure, not as a pass. Augur's finding:
  # rc used to mean only "no FAIL string was produced", which is satisfied equally by twelve greens and
  # by a script that could not reach GitHub -- and the same real event (auth expiry, rate limit, network
  # drop) exited 1 through the NO RUN branch and 0 through this one. The file's own rule is that an
  # unrecognised state must not be absorbed into green; rc was the last place it still was.
  # INCOMPLETE stays 0 deliberately: "still running" really is context, not a verdict.
  case "$outcome" in FAIL*|"NO ROWS"*|UNKNOWN*) rc=1 ;; esac

  mb="$(git merge-base "$BASE" "$head" 2>/dev/null)"
  dist="$(git rev-list --count "${mb}..${BASE}" 2>/dev/null || echo '?')"

  n=0; which=""
  for c in $(git rev-list "${mb}..${BASE}" 2>/dev/null); do
    git show --name-only --format='' "$c" 2>/dev/null | grep -qE "$CRITERIA_RE" || continue
    # BOTH SIDES MUST BE Z. `[ a \> b ]` is a STRING compare, and the two clocks disagree by default:
    # git prints the committer's own offset (-05:00 throughout this repo) while gh prints UTC. A commit
    # made 00:00-05:00 local then sorts BEFORE a run in that window and is silently dropped -- an
    # UNDER-report, i.e. it makes a stale PR look current, the wrong direction for this tool to fail.
    cz="$(TZ=UTC git log -1 --format=%cd --date=format-local:%Y-%m-%dT%H:%M:%SZ "$c" 2>/dev/null)"
    [ "$cz" \> "$when" ] && { n=$((n+1)); which="$which $(git log -1 --format=%h "$c")"; }
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
