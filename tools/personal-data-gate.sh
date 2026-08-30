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
# EXIT: 0 clean, 1 hit (with file:line and the pattern), 2 usage/internal error.
#
# ALLOWLIST: a line ending in the marker below is exempt. It exists because this
# file and its denylist must both CONTAIN the patterns they match in order to
# document them, and a gate that cannot describe its own rules grows a second,
# undocumented copy of them somewhere else.
set -u

MARK='gate-allow'
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
LIST="${PERSONAL_DATA_DENYLIST:-$HERE/personal-data-denylist.txt}"

[ -r "$LIST" ] || { echo "gate: cannot read denylist: $LIST" >&2; exit 2; }

# Load patterns. A denylist that silently loads ZERO patterns passes everything
# while exiting 0 -- the null-instrument failure. Refuse to run empty.
pats=$(grep -v '^[[:space:]]*#' "$LIST" | grep -v '^[[:space:]]*$') || true
[ -n "$pats" ] || { echo "gate: denylist has no patterns -- refusing to run" >&2; exit 2; }

case "${1:-}" in
  --staged) diff=$(git diff --cached -U0) ;;
  --stdin)  diff=$(cat) ;;
  "" )      echo "usage: $0 <base>..<head> | --staged | --stdin" >&2; exit 2 ;;
  *)        diff=$(git diff -U0 "$1") || exit 2 ;;
esac

hits=0
file=""
lineno=0

while IFS= read -r ln; do
  case "$ln" in
    '+++ b/'*) file=${ln#+++ b/}; continue ;;
    '--- '*|'+++ '*) continue ;;
    '@@'*)
      # @@ -a,b +c,d @@ -- take c as the next added line number
      h=${ln#*+}; h=${h%% *}; lineno=${h%%,*}
      continue ;;
    '+'*) ;;
    *) continue ;;
  esac

  body=${ln#+}
  case "$body" in *"$MARK") lineno=$((lineno+1)); continue ;; esac

  # The denylist is BY DEFINITION a file of these patterns; it cannot carry an
  # inline allow marker because the marker would become part of the regex. So it
  # is exempt by path. The tradeoff, stated rather than hidden: a leak smuggled
  # into that one file is invisible to this gate. It is a 40-line file whose every
  # line is a regex, so a literal address in it is conspicuous on review -- and
  # GitHub push protection (layer 2) still sees it. Widen this exemption to no
  # other path.
  case "$file" in tools/personal-data-denylist.txt) lineno=$((lineno+1)); continue ;; esac

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
