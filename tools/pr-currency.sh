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

# GATE STRENGTH (--gate-strength, OFF by default because it costs one API call per criteria commit).
#
# Augur, 2026-09-03, from the first run of this tool against real PRs rather than fixtures: a commit
# under CRITERIA_RE is reviewed by a SMALLER check set than the commits whose greens it devalues.
# #272 changes .github/workflows/personal-data-gate.yml and is gated by 3 checks, because vm-tests.yml
# is paths-filtered and does not fire on it -- while #168 and #232, which it devalues, carry 12.
# The axis this file measures is moved most cheaply by the commits it is least able to see reviewed.
#
# Augur's literal proposal was "print the check count next to the currency count -- you already print
# it in outcome". That number is THIS PR's, and printing it twice says nothing new; the sentence his
# prose actually makes is about the INCREMENTING commits' own gate. So this resolves each counted
# commit to its merged PR and reports THAT check count. It is the more expensive reading and it is
# the one that carries the finding.
#
# NOT a gate and not a default: a small gate set is frequently correct scoping (a docs commit should
# not pay for the vm-test matrix), so this reports a shape for a human, and a low number here is a
# question, not a defect.
GATE_STRENGTH=0
case "${1:-}" in --gate-strength) GATE_STRENGTH=1 ;; esac
_GS_CACHE="$(mktemp -d)"; trap 'rm -rf "$_GS_CACHE"' EXIT

# sha -> "<pr>:<nchecks>", cached, because one criteria commit devalues many PRs and would otherwise
# be looked up once per PR. Prints "?" on any failure -- an unresolvable commit must not render as a
# small gate, which would invent the very finding this is here to measure.
gate_of() {
  _f="$_GS_CACHE/$1"
  [ -f "$_f" ] && { cat "$_f"; return 0; }
  _p="$(gh pr list --search "$1" --state merged --json number --jq '.[0].number' 2>/dev/null)"
  case "$_p" in ''|*[!0-9]*) echo '?' > "$_f"; cat "$_f"; return 0 ;; esac
  _n="$(gh pr checks "$_p" 2>/dev/null | grep -c .)"
  case "$_n" in ''|0) echo '?' > "$_f" ;; *) echo "$_n" > "$_f" ;; esac
  cat "$_f"
}

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
  # ABSOLUTE, because the fixture arms below re-run this script from a DIFFERENT cwd and `$0` is
  # whatever the caller typed. A relative `$0` would make those arms silently run nothing.
  SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"

  # The fixture repo. Added 2026-09-05 to close the gap disclosed in f01ebba's commit message: the
  # gate-strength and paths arms read the REAL repo via `git rev-parse HEAD~6`, so they could not run
  # inside a nix check derivation (no history there), and this whole battery therefore ran by hand
  # only -- an arm nothing runs is prose with a shell prompt. A synthetic repo makes the arms depend
  # on commits THEY create rather than on whatever this branch happens to be sitting on, which also
  # removes a second, quieter defect: those arms' inputs changed every time anyone pushed here, so a
  # green was never reproducible twice.
  #
  # Layout: base -> W (a workflow) -> T (a tool). "The PR" forked at `base`, so W and T are exactly
  # the criteria commits in its gap, and the arms can assert on paths they wrote themselves.
  mk_fixture() {
    f="$(mktemp -d)"
    ( cd "$f" || exit 1
      git init -q -b main .
      git config user.email t@t; git config user.name t; git config commit.gpgsign false
      mkdir -p .github/workflows tools
      echo base > README; git add -A; git commit -qm base
      echo "on: [pull_request]" > .github/workflows/fixture.yml; git add -A; git commit -qm "ci: fixture workflow"
      echo "# tool"          > tools/fixture.sh;                 git add -A; git commit -qm "tools: fixture tool"
    ) >/dev/null 2>&1 || return 1
    echo "$f"
  }
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
  # ORD arms: the ordering defect is CONTROL FLOW, not parsing, so these run the whole script against a
  # stub `gh` on PATH. The unit arms above could not have caught it -- classify_checks was always
  # correct; it was simply never called. An arm has to exercise the layer the defect lives in.
  echo "ORD arms: outcome must survive a PR whose currency cannot be computed"
  ord_run() {  # $1 = check rows gh should emit; echoes the script's stdout
    d="$(mktemp -d)"
    { echo '#!/bin/sh'
      echo 'case "$1 $2" in'
      echo '  "pr list") echo 999 ;;'
      echo '  "pr checks") printf %b "$STUB_ROWS" ;;'
      echo '  "pr view") echo "${STUB_HEAD:-deadbeef}" ;;'
      echo '  "run view") echo "${STUB_DATE:-2026-09-03T00:00:00Z}" ;;'
      echo 'esac'; } > "$d/gh"
    chmod +x "$d/gh"
    ( [ -n "${FIXTURE:-}" ] && cd "$FIXTURE"
      STUB_ROWS="$1" BASE="${BASE_OVERRIDE:-$BASE}" PATH="$d:$PATH" bash "$SELF" ${2:-} 2>&1 )
    rm -rf "$d"
  }
  o="$(ord_run 'codecov/patch\tfail\t3s\thttps://codecov.io/x\n')"
  case "$o" in *"FAIL: codecov/patch"*) echo "  ok   ORD1 a failing EXTERNAL status is still named" ;;
    *) echo "  FAIL ORD1: outcome suppressed by the no-run path; got [$o]"; fail=1 ;; esac
  case "$o" in *"currency : unknown"*) echo "  ok   ORD2 currency degrades instead of eating the row" ;;
    *) echo "  FAIL ORD2: no degraded currency line; got [$o]"; fail=1 ;; esac
  # ORD3 is the control arm. Without it, a script that printed "currency : unknown" unconditionally --
  # i.e. one that had lost currency entirely -- would satisfy ORD1 and ORD2 both.
  o3="$(ord_run 'gate\tpass\t3s\thttps://github.com/o/r/actions/runs/1/job/2\n')"
  case "$o3" in *"currency : unknown"*) echo "  FAIL ORD3 CONTROL: a real Actions run still read as unknown"; fail=1 ;;
    *"criteria commit"*) echo "  ok   ORD3 CONTROL: a real run gets a computed currency line, not 'unknown'" ;;
    *) echo "  FAIL ORD3 CONTROL: no currency line at all; got [$o3]"; fail=1 ;; esac
  # GS arms: --gate-strength annotates each counted commit with the gate its OWN PR passed.
  # The stub answers `pr checks` with one row, so an annotated commit must read "(1ck)".
  echo "GS arms: gate strength of the commits that devalue the green"
  FIX="$(mk_fixture)" || { echo "  FAIL FX0: could not build the fixture repo"; fail=1; FIX=""; }
  if [ -n "$FIX" ]; then FIXBASE="$(git -C "$FIX" rev-parse main)"; FIXFORK="$(git -C "$FIX" rev-parse main~2)"; fi
  gs="$(FIXTURE="$FIX" BASE_OVERRIDE=main STUB_DATE=2026-01-01T00:00:00Z STUB_HEAD="$FIXFORK" ord_run 'gate\tpass\t3s\thttps://github.com/o/r/actions/runs/1/job/2\n' --gate-strength)"
  case "$gs" in *"ck)"*) echo "  ok   GS1 counted commits carry their own PR's check count" ;;
    *) echo "  FAIL GS1: no gate annotation under --gate-strength; got [$gs]"; fail=1 ;; esac
  # GS2 is the control arm and it is the one that matters: without it, a script that annotated
  # UNCONDITIONALLY would pass GS1, and the flag's whole justification is that the lookup is opt-in
  # because it costs an API call per commit. A default that silently pays that cost is the defect.
  case "$o3" in *"ck)"*) echo "  FAIL GS2 CONTROL: annotated without the flag -- lookup is not opt-in"; fail=1 ;;
    *) echo "  ok   GS2 CONTROL: default run does no gate lookup" ;; esac
  # PD arms: the paths themselves. Added 2026-09-05, one tick after the paths shipped WITHOUT an
  # arm and I said so in the state line -- a disclosed gap is still a gap, and the disclosure is not
  # the control. The detail block is the whole point of that change: a COUNT cannot say what is in
  # the gap, so an arm that only checks the count would have gone green over its removal.
  echo "PD arms: the counted commits' PATHS, not just their count"
  pd="$(FIXTURE="$FIX" BASE_OVERRIDE=main STUB_DATE=2026-01-01T00:00:00Z STUB_HEAD="$FIXFORK" ord_run 'gate\tpass\t3s\thttps://github.com/o/r/actions/runs/1/job/2\n')"
  if printf '%s\n' "$pd" | grep -qE "^ +(\.github/workflows/|flake\.nix|tests/|tools/)"; then
    echo "  ok   PD1 a counted commit prints the criteria path it touched"
  else echo "  FAIL PD1: counted commits but printed no paths; got [$pd]"; fail=1; fi
  # PD2 is the control arm and it carries PD1. Without it, a script that dumped paths on every PR --
  # including ones with nothing in the gap -- would satisfy PD1 while destroying the signal, because
  # a report that prints paths unconditionally cannot distinguish a stale PR from a current one.
  if printf '%s\n' "$o3" | grep -qE "^ +(\.github/workflows/|flake\.nix|tests/|tools/)"; then
    echo "  FAIL PD2 CONTROL: printed paths for a PR with 0 criteria commits"; fail=1
  else echo "  ok   PD2 CONTROL: no counted commits, no path block"; fi
  # FX1 is the fixture's own control arm, and without it every arm above that uses $FIX is
  # vacuous: a fixture that produced ZERO criteria commits would make PD2's "no path block"
  # assertion pass for the wrong reason and PD1 fail for a reason that has nothing to do with
  # the code under test. Assert the fixture really does put two criteria commits in the gap.
  case "$pd" in *"currency : 2 criteria commit"*) echo "  ok   FX1 CONTROL: the fixture puts exactly 2 criteria commits in the gap" ;;
    *) echo "  FAIL FX1 CONTROL: fixture gap is not 2 commits -- arms above are not measuring what they say; got [$pd]"; fail=1 ;; esac
  # FX2: the arms must no longer depend on THIS repo. Run the fixture arm from a directory that is
  # not a git repo at all -- which is what a nix check derivation looks like -- and require the same
  # answer. This is the arm that licenses wiring the battery into CI.
  nog="$(mktemp -d)"
  pdx="$(cd "$nog" && FIXTURE="$FIX" BASE_OVERRIDE=main STUB_DATE=2026-01-01T00:00:00Z STUB_HEAD="$FIXFORK" ord_run 'gate\tpass\t3s\thttps://github.com/o/r/actions/runs/1/job/2\n')"
  case "$pdx" in *"currency : 2 criteria commit"*) echo "  ok   FX2 no ambient repo needed -- runs from a non-repo cwd" ;;
    *) echo "  FAIL FX2: depends on the ambient checkout; got [$pdx]"; fail=1 ;; esac
  # BD arms: the board must DATE ITSELF. Augur's law, 2026-09-05: a currency (or inertness) verdict is
  # a dated measurement of what CI DOES, not a property of a path -- valid for one PR, against one CI
  # configuration, at one timestamp. He proved the retroactive half on #168: his own "a662b0d is inert"
  # reading was correct when written and wrong eight minutes later, because `flake.nix` started building
  # the path. Nothing in the reading said WHEN or AGAINST WHAT, so the natural reuse -- paste the board
  # excerpt into the next comm -- silently converts a measurement into a property. And it converts in
  # the permissive direction: "inert" reads as "no re-run needed".
  echo "BD arms: the board dates itself, so a pasted excerpt cannot be reused as a property"
  case "$pd" in *"board    : computed "*"Z against "*) echo "  ok   BD1 the board prints a UTC stamp and a base" ;;
    *) echo "  FAIL BD1: no self-dating header; got [$pd]"; fail=1 ;; esac
  # BD2 is the arm that makes BD1 mean anything: a header carrying a HARD-CODED or AMBIENT sha would
  # satisfy BD1 while being exactly the lie the header exists to prevent. Require the sha printed to be
  # the sha of the base this run actually measured against -- the fixture's, not this checkout's.
  if [ -n "$FIX" ]; then
    fixshort="$(git -C "$FIX" rev-parse --short main)"
    case "$pd" in *"against main $fixshort"*) echo "  ok   BD2 CONTROL: the stamped sha is the base actually measured ($fixshort)" ;;
      *) echo "  FAIL BD2 CONTROL: header sha is not the measured base $fixshort; got [$pd]"; fail=1 ;; esac
  fi
  # BD3 asserts a FORMAT, and Augur's review is right that this is its ceiling: the fixture's base is
  # committed during the run, so its date is always ~now and always parseable. There is no run here in
  # which a stale base makes BD3 go red. It proves the field is PRINTED; it cannot prove the field
  # DISCRIMINATES -- and the incident that motivated the header was eight minutes wide, where a date is
  # visibly fresh and still wrong. That limit belongs in the arm, not in a comment above it. BD4/BD5 are
  # what carry the discrimination, by asserting the FETCH STATE is reported and is not a blanket claim.
  case "$pd" in *"(base dated "*)  echo "  ok   BD3 the stamped base carries its own commit date (format only -- see above)" ;;
    *) echo "  FAIL BD3: base named but not dated -- a stale ref is undetectable; got [$pd]"; fail=1 ;; esac
  case "$pd" in *"never fetched"*|*"fetched just now"*|*"FETCH FAILED"*)
      echo "  ok   BD4 the header states the base's FETCH state, not just its date" ;;
    *) echo "  FAIL BD4: no fetch state -- staleness is the default and goes unreported; got [$pd]"; fail=1 ;; esac
  # BD6: the FETCH FAILED state, which Augur flagged as the one state no arm produced. It is the state
  # most likely to appear in the wild (CI with no credentials for the remote, a laptop offline) and the
  # only one whose text carries real information. Exercised by giving the fixture a remote named `origin`
  # that points nowhere, with the remote-tracking ref planted by hand so the BASE still resolves -- so
  # the fetch fails while everything downstream of it stays measurable, which is exactly the wild case.
  if [ -n "$FIX" ]; then
    git -C "$FIX" remote add origin /nonexistent/definitely-not-a-repo.git >/dev/null 2>&1
    git -C "$FIX" update-ref refs/remotes/origin/main "$(git -C "$FIX" rev-parse main)" >/dev/null 2>&1
    ff="$(FIXTURE="$FIX" BASE_OVERRIDE=origin/main STUB_DATE=2026-01-01T00:00:00Z STUB_HEAD="$FIXFORK" ord_run 'gate\tpass\t3s\thttps://github.com/o/r/actions/runs/1/job/2\n')"
    case "$ff" in *"FETCH FAILED"*) echo "  ok   BD6 an unreachable remote is reported, not silently treated as fresh" ;;
      *) echo "  FAIL BD6: fetch failed but the board did not say so; got [$ff]"; fail=1 ;; esac
    # BD6b CONTROL: the run must still PRODUCE a board -- a fetch failure that aborted the report would
    # satisfy BD6's sibling concerns while destroying the tool. Degrade, do not die.
    case "$ff" in *"currency : "*) echo "  ok   BD6b CONTROL: the board still reports after a failed fetch" ;;
      *) echo "  FAIL BD6b CONTROL: a failed fetch killed the report; got [$ff]"; fail=1 ;; esac
    git -C "$FIX" remote remove origin >/dev/null 2>&1
  fi
  # BD5 CONTROL, and it is the one that stops BD4 being satisfied by a lie: the fixture measures against a
  # LOCAL ref, so a header that claimed "fetched just now" unconditionally -- the flattering answer, and
  # the one that reproduces the defect -- must fail here.
  case "$pd" in *"fetched just now"*) echo "  FAIL BD5 CONTROL: claimed a fetch for a purely local base"; fail=1 ;;
    *"never fetched"*) echo "  ok   BD5 CONTROL: a local base is reported as never fetched, not as fresh" ;;
    *) echo "  FAIL BD5 CONTROL: no fetch state at all; got [$pd]"; fail=1 ;; esac
  rm -rf "$nog" "$FIX"
  [ "$fail" = 0 ] && echo "ALL GREEN" || echo "SELFTEST FAILED"
  exit "$fail"
fi

command -v gh >/dev/null 2>&1 || { echo "CANNOT-ASSESS: gh not on PATH; currency needs run metadata."; exit 2; }

prs="$(gh pr list --state open --json number -q '.[].number' 2>/dev/null)"
if [ -z "$prs" ]; then echo "no open PRs"; exit 0; fi

# THE BOARD DATES ITSELF (Augur, 2026-09-05). Every line below is a measurement against ONE base at
# ONE instant, and the natural thing to do with a board is paste an excerpt of it into a comm hours
# later. Without a stamp there is nothing in the text that says the reading has an expiry, so it gets
# reused as a PROPERTY of the PR ("still current") or of a path ("inert, no re-run needed") -- both of
# which fail permissive. The base moves under this report constantly, and so does what counts as
# criteria-bearing: `tools/pr-currency.sh` was correctly inert at a662b0d and criteria-bearing at
# 703d930 with no change to the regex that matches it, because flake-check builds `checks` unfiltered.
# The stamp does not stop the reuse. It makes the reuse checkable, which is the most a printed line can do.
board_at="$(TZ=UTC date -u +%Y-%m-%dT%H:%M:%SZ)"
# STALE IS THE DEFAULT, NOT THE EDGE CASE (Augur's #275 review, 2026-09-05). `origin/main` is a LOCAL
# ref that moves only when someone runs `git fetch`; nothing in this file ever did. So the board did not
# *risk* measuring against a stale base -- absent an unrelated fetch it always did, by an amount that is
# a property of the operator's shell history. Augur hit it in this repo the same morning: his origin/main
# sat at 5d4e475 while 703d930 was already merged. Fetch, and SAY whether the fetch worked -- a board that
# could not reach the network and says so is honest; one that silently reports a week-old base is the
# exact thing this header exists to prevent.
# CONTRACT NOTE (Augur, 2026-09-05): this fetch means THE BOARD MUTATES WHAT IT MEASURES. Two
# consecutive runs can disagree for a reason the first run itself caused, and running it moves
# origin/* under whatever else the operator has going in that checkout. Only remote-tracking refs
# move -- no worktree, no local branch, nothing lost -- but the file is no longer a pure read-only
# probe, and the next person to read its NAME will otherwise be right about the name and wrong about
# the file. Recorded here rather than in a reviewer's memory.
case "$BASE" in
  origin/*|upstream/*)
    if git fetch -q "${BASE%%/*}" 2>/dev/null; then board_fresh="fetched just now"
    else board_fresh="FETCH FAILED -- base is as of this checkout's last fetch, age unknown"; fi ;;
  *) board_fresh="local ref, never fetched -- age is this checkout's" ;;
esac
board_base="$(git rev-parse --short "$BASE" 2>/dev/null || echo '?')"
# ...AND THE STAMP MUST DATE THE BASE, NOT JUST NAME IT: a sha looks equally authoritative fresh or stale.
# %cI carries a REAL OFFSET rather than a hand-appended `Z`. The earlier form put the Z literal in the
# format string with `format-local`, the one directive that reads the environment -- so its correctness
# lived entirely in a `TZ=UTC` prefix, and Augur reproduced the failure by dropping it while reviewing:
# 703d930 printed as 01:11:31Z for a commit made at 06:11:31Z. CDT wearing a Z, no diagnostic. A format
# that cannot lie about its offset beats a prefix that has to be remembered.
board_bdate="$(git log -1 --format=%cI "$BASE" 2>/dev/null || echo '?')"
echo "board    : computed $board_at against $BASE $board_base (base dated $board_bdate; $board_fresh)"

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
  # OUTCOME IS COMPUTED BEFORE THE NO-RUN BRANCH, AND THAT ORDERING IS THE POINT (Augur, 2026-09-03).
  # It used to sit below a `rc=1; continue` that fired whenever `when` was empty -- so on any PR where
  # currency could not be computed, the outcome column was never printed AT ALL, including when it would
  # have read `FAIL: codecov/patch`. `when` comes from the same `gh pr checks` output, and it is empty
  # for a whole real class: PRs whose checks are EXTERNAL statuses (Codecov, Dependabot, any non-Actions
  # check), whose URLs do not match `runs/[0-9]+`. A failing check on such a PR was reported only as
  # "NO RUN FOUND". That is this file's founding sin at the control-flow level rather than the parser
  # level: the axis the report DOES measure was crowding out the axis its own header says goes first.
  # It also made `"NO ROWS"*` in the case below unreachable by any input -- the branches did not
  # disagree, one ate the other. Currency now degrades to `unknown` on its own line instead of taking
  # the row with it.
  outcome="$(gh pr checks "$pr" 2>/dev/null | classify_checks)"
  # rc counts "could not determine the outcome" as an outcome failure, not as a pass. Augur's finding:
  # rc used to mean only "no FAIL string was produced", which is satisfied equally by twelve greens and
  # by a script that could not reach GitHub -- and the same real event (auth expiry, rate limit, network
  # drop) exited 1 through the NO RUN branch and 0 through this one. The file's own rule is that an
  # unrecognised state must not be absorbed into green; rc was the last place it still was.
  # INCOMPLETE stays 0 deliberately: "still running" really is context, not a verdict.
  case "$outcome" in FAIL*|"NO ROWS"*|UNKNOWN*) rc=1 ;; esac

  if [ -z "$run" ]; then
    echo "PR $pr"
    echo "    outcome  : $outcome"
    echo "    currency : unknown — no Actions run found for this PR's checks"
    echo "               (external-status-only PR, or gh returned nothing; the outcome line above still holds)"
    rc=1; continue
  fi

  mb="$(git merge-base "$BASE" "$head" 2>/dev/null)"
  dist="$(git rev-list --count "${mb}..${BASE}" 2>/dev/null || echo '?')"

  n=0; which=""; detail=""
  for c in $(git rev-list "${mb}..${BASE}" 2>/dev/null); do
    # Keep the matching PATHS, not just the fact that one matched. A COUNT cannot say what is in
    # the gap, and the reader who cannot see the paths supplies a guess -- in this lane the guess
    # has been "cosmetic" three times running. Measured 2026-09-05 on this repo's own board: of the
    # 3 commits devaluing every open PR, one was a comment-only block in vm-tests.yml and one was a
    # tool file no workflow invokes; only the actions/checkout bump changes what a run executes.
    # Same count, three very different answers to "should I re-run this."
    # Deliberately NOT classified inert/material here: a classifier that mislabels fails toward
    # "no re-run needed", which is the direction this tool must never fail in. Show, do not judge.
    cp="$(git show --name-only --format='' "$c" 2>/dev/null | grep -E "$CRITERIA_RE")"
    [ -n "$cp" ] || continue
    # BOTH SIDES MUST BE Z. `[ a \> b ]` is a STRING compare, and the two clocks disagree by default:
    # git prints the committer's own offset (-05:00 throughout this repo) while gh prints UTC. A commit
    # made 00:00-05:00 local then sorts BEFORE a run in that window and is silently dropped -- an
    # UNDER-report, i.e. it makes a stale PR look current, the wrong direction for this tool to fail.
    cz="$(TZ=UTC git log -1 --format=%cd --date=format-local:%Y-%m-%dT%H:%M:%SZ "$c" 2>/dev/null)"
    if [ "$cz" \> "$when" ]; then
      n=$((n+1)); sh="$(git log -1 --format=%h "$c")"
      if [ "$GATE_STRENGTH" = 1 ]; then which="$which $sh($(gate_of "$c")ck)"; else which="$which $sh"; fi
      detail="$detail
               $sh $(git log -1 --format=%s "$c" | cut -c1-60)
$(echo "$cp" | sed 's/^/                    /')"
    fi
  done

  echo "PR $pr"
  echo "    outcome  : $outcome"
  echo "    distance : $dist commits behind $BASE"
  echo "    currency : $n criteria commit(s) landed after its run ($when)"
  [ "$n" -gt 0 ] && echo "               ->$which"
  [ "$n" -gt 0 ] && printf '%s\n' "${detail#?}"
  [ "$n" -gt 0 ] && echo "               a re-run repairs this and moves NO other number here."
  # The comparison Augur's finding is made of: this PR's gate against the gate its devaluers passed.
  [ "$n" -gt 0 ] && [ "$GATE_STRENGTH" = 1 ] && \
    echo "               (Nck = checks on the commit's OWN merged PR; compare against this PR's outcome count)"
done

# Deliberately NOT a merge gate. Currency is context for a human deciding whether to re-run; a
# stale-criteria PR is not thereby wrong, and promoting this to blocking would need a labelled
# false-positive measurement first. rc reflects OUTCOME failures only.
exit "$rc"
