#!/usr/bin/env bash
# personal-data-gate.sh -- refuse to publish operator-personal data to this public repo.
#
# WHAT IT READS: added lines (leading '+') of a diff. Not the working tree.
# The repo's HEAD and history legitimately contain author provenance, which
# Dillon ruled stays (2026-08-30). A whole-tree scan would fail on the first run
# and be disabled by the end of the week; a gate you must switch off is not a gate.
# So this gate stops the NEXT leak instead of relitigating the last one -- which is
# also the finding that put it first in the build order: between two inventories of
# this repo taken hours apart, the leak count went 43 -> 44. It accretes while you
# plan the cleanup. Guard first, clean second.
#
# USAGE
#   tools/personal-data-gate.sh <base>..<head>   scan a commit range (CI)
#   tools/personal-data-gate.sh --staged         scan the index (pre-commit)
#   tools/personal-data-gate.sh --stdin          scan a diff on stdin (the battery)
#
# --tree, added 2026-09-02. The diff scope above is right for the STEADY state and wrong for
# one question: what is already IN HEAD. Those lines were never an "added line" under this gate's
# watch, so no amount of diff scanning will ever surface them -- the sweep that found them was a
# one-off, and a one-off finds a leak once and never notices the next one.
#
# It could not have been the default on day one: the tree held 44 hits and a gate that fails on
# every run gets switched off. It can be enforced NOW because the sweep cleaned the real ones,
# and what remains is a bounded, listed set.
#
# THE BASELINE IS THE POINT. tools/personal-data-tree-baseline.txt names, per FILE and per
# PATTERN, the hits that are accepted and why. Scoping by pattern rather than by file is what
# keeps it a gate: a synthetic RFC1918 gateway address in a VM test is accepted, and a key appearing
# in that same file tomorrow is NOT. A file-level allowlist would have retired the file from
# scrutiny entirely, which is how allowlists quietly become the vulnerability.
#
# EXIT: 0 clean, 1 hit (with file:line and the pattern), 2 usage/internal error.
#
# ALLOWLIST: a line ending in the marker below is exempt. It exists because this
# file and its denylist must both CONTAIN the patterns they match in order to
# document them, and a gate that cannot describe its own rules grows a second,
# undocumented copy of them somewhere else.
set -u

MARK='gate-allow'

# THE EXEMPT CONFIG FILES, in ONE place. Both the diff path and the tree path must skip these,
# and when this rule was spelled twice -- a `case` in one half, a `grep -v` in the other -- I
# fixed one and shipped the other still blocking. Two spellings of one rule is how halves drift;
# both callers now ask this function.
is_exempt_path() {
  case "$1" in
    tools/personal-data-denylist.txt|tools/personal-data-tree-baseline.txt) return 0 ;;
    *) return 1 ;;
  esac
}

TREE_HITS=$(mktemp)
trap 'rm -f "$TREE_HITS"' EXIT
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
LIST="${PERSONAL_DATA_DENYLIST:-$HERE/personal-data-denylist.txt}"

[ -r "$LIST" ] || { echo "gate: cannot read denylist: $LIST" >&2; exit 2; }

# Load patterns. A denylist that silently loads ZERO patterns passes everything
# while exiting 0 -- the null-instrument failure. Refuse to run empty.
pats=$(grep -v '^[[:space:]]*#' "$LIST" | grep -v '^[[:space:]]*$') || true
[ -n "$pats" ] || { echo "gate: denylist has no patterns -- refusing to run" >&2; exit 2; }

# ---- --tree: scan tracked files at HEAD against the baseline --------------------------------
if [ "${1:-}" = "--tree" ]; then
  BASE="${PERSONAL_DATA_TREE_BASELINE:-$HERE/personal-data-tree-baseline.txt}"
  [ -r "$BASE" ] || { echo "gate: cannot read tree baseline: $BASE" >&2; exit 2; }

  # Both config files are exempt BY PATH, for the same reason: each must CONTAIN the patterns
  # it governs in order to express them, so scanning them means the gate blocks on itself. The
  # denylist earned this exemption already; the baseline inherits it and the same stated
  # tradeoff -- a leak smuggled into either is invisible here. Both are short files of nothing
  # but regexes and paths, where a literal address is conspicuous on review, and GitHub push
  # protection still sees it. Widen this to no other path.
  files=$(git ls-files) || exit 2
  # Same null-instrument refusal as the denylist: a sweep over zero files exits 0 while
  # proving nothing, and that is indistinguishable from a clean tree.
  nfiles=$(printf '%s\n' "$files" | grep -c .)
  [ "$nfiles" -gt 0 ] || { echo "gate: --tree matched no tracked files -- refusing to run" >&2; exit 2; }

  thits=0
  while IFS= read -r p; do
    # One grep per pattern over the whole tree: same ERE engine and same -i as the diff path,
    # because two spellings of one rule is how the halves drift apart.
    printf '%s\n' "$files" \
      | while IFS= read -r cand; do is_exempt_path "$cand" || printf '%s\n' "$cand"; done \
      | tr '\n' '\0' \
      | xargs -0 grep -EinH -- "$p" 2>/dev/null \
      | while IFS= read -r hit; do
          hf=${hit%%:*}
          rest=${hit#*:}; hl=${rest%%:*}
          body=${rest#*:}
          case "$body" in *"$MARK") continue ;; esac
          # baseline entries are "<path>\t<pattern>"; accept only an exact pair
          if grep -Fqx -- "$hf	$p" "$BASE"; then continue; fi
          printf 'BLOCKED(tree) %s:%s\n  pattern: %s\n' "$hf" "$hl" "$p" >&2
          echo x >> "$TREE_HITS"
        done
  done <<EOF
$pats
EOF

  # NOT `grep -c . || echo 0`: grep -c prints 0 AND exits 1 on an empty file, so the ||
  # branch fires too and the count becomes the two-line string "0\n0" -- which is not "0",
  # so the clean case takes the failure path. Cost me a run; wc -l has no such opinion.
  thits=$(wc -l < "$TREE_HITS" | tr -d ' ')
  echo "gate --tree: scanned $nfiles tracked files against $(printf '%s\n' "$pats" | grep -c .) patterns; $thits unbaselined hit(s)"
  [ "$thits" = 0 ] && exit 0
  cat >&2 <<'MSG'

gate --tree: HEAD contains personal data that is not in the baseline.
Either remove it, or -- if it is genuinely acceptable (a synthetic test address,
documentation of the rule itself) -- add the exact "<path><TAB><pattern>" pair to
tools/personal-data-tree-baseline.txt with a comment saying why.
MSG
  exit 1
fi

# THE TRAILING `--` IS LOAD-BEARING. Without it, `git diff -U0 "$1"` accepts an argument that is
# not a revision at all and reads it as a PATHSPEC: hand this gate a path to a diff FILE -- the
# plausible mistake, and the one I actually made -- and git returns the diff of that path in the
# working tree, which is empty. Zero added lines are then scanned and the gate reports CLEAN,
# exit 0. It failed OPEN on the mistake most likely to be made by someone trying to use it
# correctly, and it did so in silence: `nonsense-not-a-range` and `totally/bogus.diff` both gave
# rc=2, but an EXISTING file path gave rc=0. The `--` makes git resolve the argument as a
# revision or fail (rc=128), so the only paths out of here are a real range or exit 2.
case "${1:-}" in
  --staged) diff=$(git diff --cached -U0) || exit 2 ;;
  --stdin)  diff=$(cat) ;;
  "" )      echo "usage: $0 <base>..<head> | --staged | --stdin | --tree" >&2; exit 2 ;;
  --*)      echo "gate: unknown option '$1'" >&2
            echo "usage: $0 <base>..<head> | --staged | --stdin | --tree" >&2; exit 2 ;;
  *)        diff=$(git diff -U0 "$1" -- 2>/dev/null) || {
              echo "gate: '$1' is not a revision range this repo can resolve." >&2
              echo "gate: if you meant a diff FILE, feed it on stdin: $0 --stdin < '$1'" >&2
              exit 2
            } ;;
esac

hits=0
file=""
lineno=0
# SUBSTRATE COUNTERS. A gate whose PASS does not depend on having scanned anything cannot
# notice that it scanned nothing -- so the count is part of the verdict, not a debug aid.
# `added` is what was actually examined against the denylist; `headers` is how many file
# headers the parser recognised, which is what separates "a diff with no additions"
# (legitimate: a deletions-only change) from "this input is not a unified diff at all".
added=0
headers=0

while IFS= read -r ln; do
  case "$ln" in
    '+++ b/'*) file=${ln#+++ b/}; headers=$((headers+1)); continue ;;
    '--- '*|'+++ '*) continue ;;
    '@@'*)
      # @@ -a,b +c,d @@ -- take c as the next added line number
      h=${ln#*+}; h=${h%% *}; lineno=${h%%,*}
      continue ;;
    '+'*) ;;
    *) continue ;;
  esac

  body=${ln#+}
  added=$((added+1))
  case "$body" in *"$MARK") lineno=$((lineno+1)); continue ;; esac

  # The denylist is BY DEFINITION a file of these patterns; it cannot carry an
  # inline allow marker because the marker would become part of the regex. So it
  # is exempt by path. The tradeoff, stated rather than hidden: a leak smuggled
  # into that one file is invisible to this gate. It is a 40-line file whose every
  # line is a regex, so a literal address in it is conspicuous on review -- and
  # GitHub push protection (layer 2) still sees it. Widen this exemption to no
  # other path.
  if is_exempt_path "$file"; then lineno=$((lineno+1)); continue; fi

  while IFS= read -r p; do
    if printf '%s\n' "$body" | grep -Eiq -- "$p"; then
      printf 'BLOCKED %s:%s\n  pattern: %s\n  line:    %s\n' \
        "$file" "$lineno" "$p" "$body" >&2
      hits=$((hits+1))
      break
    fi
  done <<EOF
$pats
EOF
  lineno=$((lineno+1))
done <<EOF
$diff
EOF

# THE NULL-INSTRUMENT REFUSAL, same rule the denylist and --tree already carry, now applied to
# the diff path -- which was the one place it was missing. Non-empty input that yields ZERO
# recognised file headers means the parser did not understand what it was handed; reporting
# "clean" on that is the fail-open this fix exists to close. Note what is deliberately NOT an
# error: a well-formed diff with headers but no added lines is a real deletions-only change and
# passes, so this refusal cannot be satisfied by the absence of findings alone.
if [ -n "$diff" ] && [ "$headers" = 0 ]; then
  echo "gate: input was non-empty but contained no '+++ b/' file headers -- this does not look" >&2
  echo "gate: like a unified diff, and a scan of zero files is not a clean result." >&2
  exit 2
fi

echo "gate: scanned $added added line(s) across $headers file(s) against $(printf '%s\n' "$pats" | grep -c .) patterns; $hits hit(s)"

if [ "$hits" -gt 0 ]; then
  cat >&2 <<'MSG'

gate: personal data would be published. Nothing was pushed.
Fix: move the value into your private overlay flake (see README: Overlay)
and leave a generic default here. If a hit is a false positive -- documentation
of the rule itself -- append the allow marker to that line.
MSG
  exit 1
fi
exit 0
