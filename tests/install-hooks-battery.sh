#!/usr/bin/env bash
# Battery for tools/install-hooks.sh -- specifically the pre-push hook it WRITES.
#
# WHY THIS FILE EXISTS. install-hooks.sh had no battery at all, while the gate it installs has
# 57 arms. That asymmetry is the whole defect class: the gate was tested, and the thing that
# decides WHICH COMMITS the gate ever sees was not. A scanner pointed at the wrong range is
# indistinguishable from a clean tree by anything it prints.
#
# code-qa run 1 (2026-10-31), install-hooks.sh:24: on a NEW ref, when `origin/HEAD` cannot be
# resolved, the base fell back to `$lsha~1` -- so only the TIP commit was scanned and every
# earlier commit on the branch was published unscanned. Reachable after `git init` + `remote add`
# with no clone (no origin/HEAD is created), or pushing to a non-origin remote.
#
# The hook is driven through its REAL contract -- the four fields git feeds on stdin, and the
# remote name/URL in $1/$2 -- rather than through `git push`, so the arms are deterministic and
# need no reachable remote. What they do NOT prove is that git invokes the hook at all; that is
# git's contract, not this repo's, and asserting it here would be testing git.
set -u
HERE="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
pass=0; fail=0
ZERO=0000000000000000000000000000000000000000

ok(){ if [ "$2" = "$3" ]; then pass=$((pass+1)); printf 'ok   %-52s (%s)\n' "$1" "$2";
      else fail=$((fail+1)); printf 'FAIL %-52s want=%s got=%s\n' "$1" "$3" "$2"; fi }

# scratch <leak-in>  ->  prints the repo dir. Three commits; the leak lands in the commit named
# by $1 (first|tip) and nowhere else, so an arm can never pass because the payload was everywhere.
scratch(){
  d=$(mktemp -d)
  mkdir -p "$d/tools"
  cp "$HERE/tools/personal-data-gate.sh" "$d/tools/"
  cp "$HERE/tools/install-hooks.sh" "$d/tools/"
  printf 'tskey-auth-[A-Za-z0-9-]{6,}\n' > "$d/tools/personal-data-denylist.txt"
  : > "$d/tools/personal-data-tree-baseline.txt"
  (
    cd "$d" && git init -q .
    git config user.email b@x; git config user.name b; git config commit.gpgsign false
    case "$1" in
      first) printf 'K=tskey-auth-aBcDeFgHiJ\n' > a.txt ;;   # gate-allow
      *)     printf 'clean\n' > a.txt ;;
    esac
    git add -A && git commit -qm c1
    printf 'middle\n' > b.txt && git add -A && git commit -qm c2
    case "$1" in
      tip) printf 'K=tskey-auth-aBcDeFgHiJ\n' > c.txt ;;     # gate-allow
      *)   printf 'clean tip\n' > c.txt ;;
    esac
    git add -A && git commit -qm c3
    bash tools/install-hooks.sh
  ) >/dev/null 2>&1
  echo "$d"
}

# run_hook <repo> <remote-ref-state> -> rc of the installed pre-push hook on a NEW-REF push.
# "no-origin-head" is the defect's precondition: a repo created with `git init` + `remote add`
# has no refs/remotes/origin/HEAD, which is the ordinary state and not an exotic one.
run_hook(){
  d=$1
  ( cd "$d" && lsha=$(git rev-parse HEAD) &&
    printf 'refs/heads/main %s refs/heads/main %s\n' "$lsha" "$ZERO" \
      | .git/hooks/pre-push origin https://example.invalid/x.git ) >/dev/null 2>&1
  echo $?
}

# H1 -- THE ARM THAT CARRIES THE FILE. The leak is in the FIRST commit of a new ref with no
# origin/HEAD. Publishing that ref publishes that commit, so the hook must block.
d=$(scratch first); ok "H1 new ref, leak in the FIRST commit: blocks" "$(run_hook "$d")" 1; rm -rf "$d"

# H2 -- permitting twin. Without it H1 passes on a hook that refuses every push, which would be
# uninstalled within a day and is therefore worse than no hook.
d=$(scratch clean); ok "H2 new ref, no leak anywhere: passes"         "$(run_hook "$d")" 0; rm -rf "$d"

# H3 -- control on the other end of the range: a leak in the TIP was caught even BEFORE the fix
# (that is the one commit `$lsha~1` covered). Keeping it means a regression that breaks the tip
# case cannot hide behind H1 going green.
d=$(scratch tip);   ok "H3 new ref, leak in the TIP commit: blocks"   "$(run_hook "$d")" 1; rm -rf "$d"

# H4 -- PRE-FIX arm. The same input against the hook as it was written, with the `$lsha~1`
# fallback restored, must come back 0: CLEAN over a ref whose first commit carries a live-shaped
# key. Without this, H1 is not attributable to the fix -- it would also pass on a hook that
# happened to block for some unrelated reason.
d=$(scratch first)
# The neutering replaces the WHOLE fallback assignment, continuation lines and all, and REFUSES
# rather than silently doing nothing if it cannot find it -- a neutering that no-ops leaves H4
# running the fixed hook and reporting a defect that is not there.
#
# My first version of this matched up to the first `)` on one line and quietly did not apply,
# because the fixed fallback spans two lines. H4b passed anyway: it grepped for `lsha~1`, which
# occurs in the fix's own COMMENT explaining what the old fallback was. An arm that passes on the
# prose it is meant to be checking the absence of is this repo's most common defect shape, so H4b
# now asserts on code -- the marker gone, the old assignment present on a non-comment line.
python3 - "$d/.git/hooks/pre-push" <<'PY'
import re, sys, pathlib
p = pathlib.Path(sys.argv[1]); t = p.read_text()
pat = re.compile(r'^[ \t]*base=\$\(git merge-base "\$lsha" origin/HEAD.*?\)[ \t]*$',
                 re.S | re.M)
t2, n = pat.subn('    base="$lsha~1"', t)
if n != 1:
    sys.exit(f"neutering did not apply ({n} matches) -- the fallback's shape changed")
p.write_text(t2)
PY
ok "H4 PRE-FIX arm: the \$lsha~1 fallback reports CLEAN" "$(run_hook "$d")" 0
if grep -q 'hash-object' "$d/.git/hooks/pre-push"; then
  fail=$((fail+1)); printf 'FAIL %-52s the fixed fallback is still present\n' "H4b the pre-fix hook really carries lsha~1"
elif grep -vE '^[[:space:]]*#' "$d/.git/hooks/pre-push" | grep -q 'base="\$lsha~1"'; then
  pass=$((pass+1)); printf 'ok   %-52s (neutered: code, not a comment)\n' "H4b the pre-fix hook really carries lsha~1"
else
  fail=$((fail+1)); printf 'FAIL %-52s neutering left neither form in place\n' "H4b the pre-fix hook really carries lsha~1"
fi
rm -rf "$d"

# H5 -- the fix must not break the ordinary case. An UPDATE to an existing ref passes the old
# remote sha as the base, which is exact; the full-content fallback must not displace it.
d=$(scratch first)
rc=$( cd "$d" && base=$(git rev-parse HEAD~2) && lsha=$(git rev-parse HEAD) &&
      printf 'refs/heads/main %s refs/heads/main %s\n' "$lsha" "$base" \
        | .git/hooks/pre-push origin https://example.invalid/x.git >/dev/null 2>&1; echo $? )
ok "H5 ref UPDATE still scans only the pushed range" "$rc" 0
rm -rf "$d"

echo
echo "install-hooks battery: $pass passed, $fail failed ($((pass+fail)) arms)"
[ "$fail" -eq 0 ]
