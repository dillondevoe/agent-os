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

# ---- arm count -------------------------------------------------------------------------------
# #252's general form, applied here: a verdict that does not depend on HOW MANY arms ran cannot
# notice any of them going missing. An arm deleted in a refactor leaves a byte-identical PASS.
WANT_ARMS=38
ran=$((pass+fail))
if [ "$ran" != "$WANT_ARMS" ]; then
  echo "FAIL arm count: $ran arms ran, expected $WANT_ARMS -- an arm was added or silently lost"
  echo "     (if you deliberately added or removed one, update WANT_ARMS in this file)"
  fail=$((fail+1))
fi

echo
echo "battery: $pass passed, $fail failed ($ran arms)"
[ "$fail" -eq 0 ]
