#!/usr/bin/env bash
# Battery for tools/personal-data-gate.sh.
#
# THE POINT OF THE ARMED CASES: a denylist that has never blocked anything is a
# claim, not a control. Every BLOCK case below is paired with a PASS control, because
# a gate that refused EVERYTHING would sail through a block-only battery -- and so
# would a gate that refused nothing, through a pass-only one. An empty result is
# informative only once the instrument has been shown capable of a non-empty one.
#
# Case 1 is not a synthetic string. It is the exact literal that sat in
# modules/confirm-pkg.nix:35 of this repo, and it is armed here because it is the
# string that already defeated TWO drafts of the tailnet pattern (100\.1[0-9]{2}\.
# and 100\.10[0-9]\.). The regression this battery guards is one that actually happened.
set -u
GATE="$(CDPATH= cd -- "$(dirname -- "$0")/../tools" && pwd)/personal-data-gate.sh"
pass=0; fail=0

# diff() emits a minimal unified diff adding $2 to file $1.
mkdiff() { printf -- '--- a/%s\n+++ b/%s\n@@ -0,0 +1 @@\n+%s\n' "$1" "$1" "$2"; }

expect() { # expect <BLOCK|PASS> <name> <file> <line>
  want=$1; name=$2
  out=$(mkdiff "$3" "$4" | "$GATE" --stdin 2>&1); rc=$?
  if { [ "$want" = BLOCK ] && [ $rc -eq 1 ]; } || { [ "$want" = PASS ] && [ $rc -eq 0 ]; }; then
    printf 'ok   %-46s (%s, rc=%s)\n' "$name" "$want" "$rc"; pass=$((pass+1))
  else
    printf 'FAIL %-46s want=%s rc=%s\n%s\n' "$name" "$want" "$rc" "$out"; fail=$((fail+1))
  fi
}

echo "== armed: real leaks that must be blocked =="
expect BLOCK "1 relayAddr literal (the real bad commit)" modules/confirm-pkg.nix \
  '    relayAddr = "100.71.238.38";'  # gate-allow
expect BLOCK "2 CGNAT low edge 100.64.x"    a.nix 'x = "100.64.0.1";'  # gate-allow
expect BLOCK "3 CGNAT high edge 100.127.x"  a.nix 'x = "100.127.255.254";'  # gate-allow
expect BLOCK "4 mesh bus path"              m.py  'D = "~/jarvis-sync/brain-comms"'  # gate-allow
expect BLOCK "5 operator account"           d.sh  'ssh dtd@mini "uptime"'  # gate-allow
expect BLOCK "6 brain process name"         r.nix 'ExecStart = "run_rabbot";'  # gate-allow
expect BLOCK "7 bot handle"                 n.md  'Notify via @HeraldTalbot on ship.'  # gate-allow
expect BLOCK "8 telegram id"                c.nix 'telegram_chat_id = 123456789;'  # gate-allow
expect BLOCK "9 case-insensitive form"      z.md  'see ~/Jarvis-Sync/ for the bus'  # gate-allow

echo
echo "== controls: these MUST pass, or the gate is just a wall =="
# Without these, a gate with `exit 1` hard-coded would score 9/9 above.
expect PASS  "C1 public-range IP is not tailnet"  a.nix 'x = "100.200.1.1";'
expect PASS  "C2 100.63 is below the CGNAT block" a.nix 'x = "100.63.0.1";'
expect PASS  "C3 100.128 is above the block"      a.nix 'x = "100.128.0.1";'
expect PASS  "C4 ordinary code"                   b.py  'def ensure_boot_identities(owner, agent):'
expect PASS  "C5 AUTHORSHIP is not a leak"        LICENSE 'Copyright (c) 2026 Dillon DeVoe'
expect PASS  "C6 provenance comment survives"     m.py  '# Dillon ruled 2026-08-30: history stays.'
expect PASS  "C7 nix hash is not a telegram id"   f.lock '"narHash": "sha256-1234567890abcdef";'
expect PASS  "C8 allow-marked self-documentation" tools/x.txt 'match ~/jarvis-sync here  # gate-allow'  # gate-allow

echo
# A removed line must not trip the gate: cleanup commits DELETE these strings, and a
# gate that blocks its own remediation guarantees it gets bypassed on the first cleanup.
out=$(printf -- '--- a/x\n+++ b/x\n@@ -1 +0,0 @@\n-relayAddr = "100.71.238.38";\n' | "$GATE" --stdin 2>&1); rc=$?  # gate-allow
if [ $rc -eq 0 ]; then printf 'ok   %-46s (PASS, rc=0)\n' "C9 REMOVING a leak is allowed"; pass=$((pass+1));
else printf 'FAIL %-46s rc=%s\n%s\n' "C9 REMOVING a leak is allowed" "$rc" "$out"; fail=$((fail+1)); fi

# --- 2026-08-30 widening: armed cases for the patterns added this pass --------
# B10 is the one that matters: it is the EXACT line Rabbot found at HEAD in
# docs/log-console-spec.md, and before this pass the gate returned 0 for it in a
# DIFF as well -- the miss was the pattern set, not the diff/HEAD distinction.
expect BLOCK "B10 RFC1918 host addr (the HEAD leak)" x 'Measured on the Dell (root@192.168.1.253)'  # gate-allow
expect BLOCK "B11 10/8 host addr"                    x 'ssh admin@10.4.2.7'  # gate-allow
expect BLOCK "B12 172.16/12 host addr"               x 'target = 172.20.3.9'  # gate-allow
expect BLOCK "B13 mDNS hostname"                     x 'rsync to the-air.local:/srv'  # gate-allow
expect BLOCK "B14 personal mailbox"                  x 'notify someone@gmail.com on failure'  # gate-allow
expect BLOCK "B15 tailscale auth key"                x 'TS_AUTHKEY=tskey-auth-kQ3vN7bXyz9Lm'  # gate-allow
expect BLOCK "B16 telegram bot token"                x 'BOT=8412345678:AAHxYzPq3nLm7QrStUvWxYz012345678901'  # gate-allow

# Controls. Without these a denylist that blocked EVERY dotted quad would pass
# the arms above while making the gate useless -- an armed zero needs its arm.
expect PASS  "C11 172.15 is NOT private"             x 'peer = 172.15.0.1'
expect PASS  "C12 172.32 is NOT private"             x 'peer = 172.32.0.1'
expect PASS  "C13 public addr untouched"             x 'resolver = 8.8.8.8'
expect PASS  "C14 semver is not an address"          x 'version = "10.4.2"'
expect PASS  "C15 marked range definition"           x 'allow 10.0.0.0/8  # gate-allow'  # gate-allow

# Null-instrument arm: an empty denylist must ERROR (2), never pass silently (0).
out=$(: > /tmp/empty-denylist.$$; mkdiff x 'ssh dtd@mini' | PERSONAL_DATA_DENYLIST=/tmp/empty-denylist.$$ "$GATE" --stdin 2>&1); rc=$?  # gate-allow
rm -f /tmp/empty-denylist.$$
if [ $rc -eq 2 ]; then printf 'ok   %-46s (ERROR, rc=2)\n' "C10 empty denylist refuses to run"; pass=$((pass+1));
else printf 'FAIL %-46s want rc=2 got %s\n' "C10 empty denylist refuses to run" "$rc"; fail=$((fail+1)); fi


# ---- --tree arms -----------------------------------------------------------------------------
# These run in a THROWAWAY git repo, never against the real tree. A battery that plants a leak in
# its own checkout and restores it afterwards is one early `exit` away from leaving the working
# copy dirty, and in CI that is a confusing red on an unrelated job.
tarm() { # tarm <name> <want-rc> <setup-body>
  name=$1; want=$2; body=$3
  d=$(mktemp -d); ( cd "$d" && git init -q . && mkdir -p tools
    printf 'tskey-auth-[A-Za-z0-9-]{6,}\n192\\.168\\.[0-9]{1,3}\\.[0-9]{1,3}\n' > tools/dl.txt
    : > tools/bl.txt
    eval "$body"
    git add -A && git -c user.email=b@x -c user.name=b commit -qm t ) >/dev/null 2>&1
  ( cd "$d" && PERSONAL_DATA_DENYLIST="$d/tools/dl.txt" PERSONAL_DATA_TREE_BASELINE="$d/tools/bl.txt" \
      "$GATE" --tree ) >/dev/null 2>&1
  rc=$?
  rm -rf "$d"
  if [ "$rc" = "$want" ]; then printf 'ok   %-46s (rc=%s)\n' "$name" "$rc"; pass=$((pass+1));
  else printf 'FAIL %-46s want rc=%s got %s\n' "$name" "$want" "$rc"; fail=$((fail+1)); fi
}

# CONTROL: a leak sitting in HEAD -- the case the DIFF gate cannot see by construction, and the
# only reason --tree exists. Without this arm every arm below passes on a detector that finds
# nothing.
tarm "T1 --tree catches a leak already in HEAD" 1 'echo "K=tskey-auth-aBcDeFgHiJ" > f.txt'  # gate-allow
# PERMITTING: a clean tree must pass, or --tree is unusable and gets switched off within a week.
tarm "T2 --tree passes a clean tree"            0 'echo "nothing to see" > f.txt'  # gate-allow
# The baseline works...
tarm "T3 --tree honours a baselined pair"       0 'echo "peer=192.168.1.1" > f.txt; printf "f.txt\t192\\\\.168\\\\.[0-9]{1,3}\\\\.[0-9]{1,3}\n" > tools/bl.txt'  # gate-allow
# ...and is SCOPED. This is the arm that keeps the baseline from becoming the vulnerability: the
# file is baselined for one pattern, and a DIFFERENT class in it must still block.
tarm "T4 baselined file, different class: BLOCK" 1 'printf "peer=192.168.1.1\nK=tskey-auth-aBcDeFgHiJ\n" > f.txt; printf "f.txt\t192\\\\.168\\\\.[0-9]{1,3}\\\\.[0-9]{1,3}\n" > tools/bl.txt'  # gate-allow
# Null-instrument arm for the tree path, matching C10 on the diff path.
tarm "T5 --tree with empty denylist refuses"    2 'echo x > f.txt; : > tools/dl.txt'  # gate-allow

# BOTH-HALVES arm. The exempt-path rule was once spelled twice -- a `case` on the diff side, a
# `grep -v` on the tree side -- and when the baseline file was added to one, the other kept
# blocking on it. This asserts the two halves agree, which is the property, rather than asserting
# either half in isolation.
for exempt in tools/personal-data-denylist.txt tools/personal-data-tree-baseline.txt; do
  out=$(mkdiff "$exempt" 'K=tskey-auth-aBcDeFgHiJ' | "$GATE" --stdin 2>&1); rc=$?  # gate-allow
  if [ $rc -eq 0 ]; then printf 'ok   %-46s (PASS, rc=0)\n' "X-$exempt exempt on the DIFF half"; pass=$((pass+1));
  else printf 'FAIL %-46s want rc=0 got %s\n' "X-$exempt exempt on the DIFF half" "$rc"; fail=$((fail+1)); fi
done

# ---- ARGUMENT-SHAPE arms: the fail-open found 2026-09-03 ------------------------------------
# The gate accepted an argument that was not a revision and `git diff` read it as a PATHSPEC.
# Handed a path to a diff FILE -- the plausible mistake, and the one actually made -- git
# returned the diff of that path in the WORKING TREE, which is empty. Zero added lines were
# scanned and the gate reported CLEAN, exit 0. Note the direction: `nonsense-not-a-range` and
# `totally/bogus.diff` BOTH gave rc=2 already. It failed open ONLY on the plausible mistake,
# which is why no one hit it.
#
# A1 is the arm; A1-control is a PRE-FIX copy asserting the defect was real. Without the
# control, A1 would also pass on a gate that rejected every argument, and this arm would be
# certifying nothing. (Same lesson as leg 9 of the cap-sandbox battery: an arm must
# discriminate between the rejection it claims and every other rejection available.)
gitarm() { # gitarm <name> <want-rc> <gate-path> ; runs $3 inside a scratch repo, arg = a real FILE
  name=$1; want=$2; g=$3
  d=$(mktemp -d)
  ( cd "$d" && git init -q . && mkdir -p tools
    printf 'tskey-auth-[A-Za-z0-9-]{6,}\n' > tools/dl.txt
    echo seed > seed.txt && git add -A && git -c user.email=b@x -c user.name=b -c commit.gpgsign=false commit -qm t
    printf -- '--- a/f\n+++ b/f\n@@ -0,0 +1 @@\n+K=tskey-auth-aBcDeFgHiJ\n' > payload.diff ) >/dev/null 2>&1  # gate-allow
  ( cd "$d" && PERSONAL_DATA_DENYLIST="$d/tools/dl.txt" "$g" payload.diff ) >/dev/null 2>&1
  rc=$?
  rm -rf "$d"
  if [ "$rc" = "$want" ]; then printf 'ok   %-46s (rc=%s)\n' "$name" "$rc"; pass=$((pass+1));
  else printf 'FAIL %-46s want rc=%s got %s\n' "$name" "$want" "$rc"; fail=$((fail+1)); fi
}

# The payload fed in is one the gate MUST block when it actually reads it -- so a pass here can
# never be "there was nothing to find". Via the supported route it is rc=1:
out=$(printf -- '--- a/f\n+++ b/f\n@@ -0,0 +1 @@\n+K=tskey-auth-aBcDeFgHiJ\n' | "$GATE" --stdin 2>&1); rc=$?  # gate-allow
if [ $rc -eq 1 ]; then printf 'ok   %-46s (BLOCK, rc=1)\n' "A0 payload blocks via --stdin"; pass=$((pass+1));
else printf 'FAIL %-46s want rc=1 got %s\n' "A0 payload blocks via --stdin" "$rc"; fail=$((fail+1)); fi

gitarm "A1 existing file path is refused, not scanned" 2 "$GATE"

# A1-control: the same input against the PRE-FIX gate (trailing `--` removed). It must come back
# rc=0 -- CLEAN on a diff containing a live-shaped key. If this arm ever stops reporting 0, the
# defect it reproduces is gone and A1 is no longer attributable to the fix.
PREFIX_GATE="$(mktemp -d)/personal-data-gate.sh"
sed 's/git diff -U0 "\$1" --/git diff -U0 "$1"/' "$GATE" > "$PREFIX_GATE"
chmod +x "$PREFIX_GATE"
if cmp -s "$GATE" "$PREFIX_GATE"; then
  printf 'FAIL %-46s neutering produced a byte-identical copy\n' "A1-control pre-fix gate differs"; fail=$((fail+1))
else
  printf 'ok   %-46s (neutered copy differs from fixed)\n' "A1-control pre-fix gate differs"; pass=$((pass+1))
fi
bash -n "$PREFIX_GATE" \
  && { printf 'ok   %-46s (parses)\n' "A1-control pre-fix gate parses"; pass=$((pass+1)); } \
  || { printf 'FAIL %-46s does not parse -- a crash would masquerade as a refusal\n' "A1-control pre-fix gate parses"; fail=$((fail+1)); }
gitarm "A1-control PRE-FIX gate reports CLEAN (rc=0)" 0 "$PREFIX_GATE"
rm -rf "$(dirname "$PREFIX_GATE")"

# A2: non-empty input the parser does not recognise as a diff. A gate whose PASS does not depend
# on having scanned anything cannot notice that it scanned nothing.
out=$(printf 'hello\nworld\n' | "$GATE" --stdin 2>&1); rc=$?
if [ $rc -eq 2 ]; then printf 'ok   %-46s (rc=2)\n' "A2 non-diff stdin refuses"; pass=$((pass+1));
else printf 'FAIL %-46s want rc=2 got %s\n' "A2 non-diff stdin refuses" "$rc"; fail=$((fail+1)); fi

# A3 is A2's control: a WELL-FORMED diff with headers but zero added lines is a legitimate
# deletions-only change and must still PASS. Without this, A2 could be satisfied by a gate that
# refused every diff carrying no additions.
out=$(printf -- '--- a/f\n+++ b/f\n@@ -1 +0,0 @@\n-gone\n' | "$GATE" --stdin 2>&1); rc=$?
if [ $rc -eq 0 ]; then printf 'ok   %-46s (PASS, rc=0)\n' "A3 deletions-only diff still passes"; pass=$((pass+1));
else printf 'FAIL %-46s want rc=0 got %s\n' "A3 deletions-only diff still passes" "$rc"; fail=$((fail+1)); fi

# A4: an unknown --option must be usage, never silently treated as a range.
out=$("$GATE" --bogus 2>&1); rc=$?
if [ $rc -eq 2 ]; then printf 'ok   %-46s (rc=2)\n' "A4 unknown option refuses"; pass=$((pass+1));
else printf 'FAIL %-46s want rc=2 got %s\n' "A4 unknown option refuses" "$rc"; fail=$((fail+1)); fi

# rangearm <name> <want-rc> <shell that mutates the tree> -- builds a scratch repo, applies the
# mutation, commits it, and runs the gate on the REAL RANGE HEAD~1..HEAD.
#
# WHY REAL RANGES AND NOT CRAFTED STDIN. Geist's hold on the first cut of this fix landed exactly
# here: I had asserted "a deletions-only diff still passes" using a hand-written diff that carried
# a `+++ b/f` header, so it exercised the shape that already worked. The shapes that BROKE --
# whole-file deletion (`+++ /dev/null`), pure rename, binary, mode-only -- are precisely the ones
# git generates and I cannot reliably hand-type. The arm has to make git emit the diff, or it is
# testing my model of git rather than git.
#
# AND THE COMMIT MUST BE ASSERTED. `gitarm` above swallows a failed scratch commit: on a host with
# SSH commit signing configured, the commits never land and the arm still passes, because a
# file-as-revision fails with or without a HEAD. These arms are not so lucky -- no HEAD~1 means no
# range at all -- so `commit.gpgsign=false` is forced and the presence of HEAD~1 is checked before
# the rc is believed. A setup failure must not be able to look like a verdict.
rangearm() {
  name=$1; want=$2; mutate=$3
  d=$(mktemp -d)
  gc="git -c user.email=b@x -c user.name=b -c commit.gpgsign=false"
  (
    cd "$d" && git init -q . && mkdir -p tools
    printf 'tskey-auth-[A-Za-z0-9-]{6,}\n' > tools/dl.txt
    echo seed > keep.txt
    echo doomed > doomed.txt
    printf 'old\n' > renamed-from.txt
    printf '\000\001\002binary-before\n' > blob.bin
    echo '#!/bin/sh' > script.sh
    git add -A && $gc commit -qm base
    eval "$mutate"
    git add -A && $gc commit -qm mutation
  ) >/dev/null 2>&1
  if ! ( cd "$d" && git rev-parse --verify -q HEAD~1 ) >/dev/null 2>&1; then
    printf 'FAIL %-46s setup: no HEAD~1 -- scratch commits did not land\n' "$name"
    fail=$((fail+1)); rm -rf "$d"; return
  fi
  ( cd "$d" && PERSONAL_DATA_DENYLIST="$d/tools/dl.txt" "$GATE" HEAD~1..HEAD ) >/dev/null 2>&1
  rc=$?
  rm -rf "$d"
  if [ "$rc" = "$want" ]; then printf 'ok   %-46s (rc=%s)\n' "$name" "$rc"; pass=$((pass+1));
  else printf 'FAIL %-46s want rc=%s got %s\n' "$name" "$want" "$rc"; fail=$((fail+1)); fi
}

# The four shapes git emits with no `+++ b/` header. Each was rc=2 before this fix; each is a
# range a real cleanup or refactor push actually produces, which is why the old predicate would
# have failed the pre-commit hook and reddened CI on ordinary work.
rangearm "range: whole-file deletion only passes"      0 'git rm -q doomed.txt'
rangearm "range: pure rename only passes"              0 'git mv renamed-from.txt renamed-to.txt'
rangearm "range: binary change only passes"            0 'printf "\000\001\002binary-after-x\n" > blob.bin'
rangearm "range: mode-only change passes"              0 'chmod +x script.sh'

# THE CONTROL ON ALL FOUR. Without it, a gate that had been broken into passing every range would
# score four green arms. This range deletes a file AND adds a live-shaped key: it must still BLOCK.
rangearm "range: deletion + leak still BLOCKs"         1 'git rm -q doomed.txt; printf "K=tskey-auth-aBcDeFgHiJ\n" > leak.txt'  # gate-allow

# ---- arm count -------------------------------------------------------------------------------
# #252's general form, applied here: a verdict that does not depend on HOW MANY arms ran cannot
# notice any of them going missing. An arm deleted in a refactor leaves a byte-identical PASS.
WANT_ARMS=51
ran=$((pass+fail))
if [ "$ran" != "$WANT_ARMS" ]; then
  echo "FAIL arm count: $ran arms ran, expected $WANT_ARMS -- an arm was added or silently lost"
  echo "     (if you deliberately added or removed one, update WANT_ARMS in this file)"
  fail=$((fail+1))
fi

echo
echo "battery: $pass passed, $fail failed ($ran arms)"
[ "$fail" -eq 0 ]
