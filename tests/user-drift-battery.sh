#!/usr/bin/env bash
# user-drift-battery.sh — arms for modules/agos-user-drift/agos-user-drift.py
#
#   bash tests/user-drift-battery.sh <path-to-agos-user-drift.py>
#
# The fixtures are the DELL'S REAL SHAPE, read off the box 2026-08-31: agent (uid 1000,
# primary group `users`), operator (uid 1001, the sole declared member of `wheel`), root,
# and sshd as a nologin system account. Arm B is that box, clean. Every other arm is that
# box with ONE hand edit — which is exactly the threat: mutableUsers=true means a console
# edit persists, and no build gate can see it.
#
# Arm D is the one that earns the scanner. A members-only check reads a box where someone
# set a user's PRIMARY GID to wheel's gid as perfectly clean, because that user never
# appears in /etc/group. Same lesson as key-drift's fingerprint-not-line: compare the
# property, not the one spelling of it you happened to look at.
set -uo pipefail

S="${1:?usage: user-drift-battery.sh <agos-user-drift.py>}"
W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT
fail=0
ok()  { echo "  ok   $1"; }
bad() { echo "  FAIL $1"; fail=1; }

NOLOGIN=/run/current-system/sw/bin/nologin
BASH=/run/current-system/sw/bin/bash

mkroot() {                     # $1 = root dir; writes the CLEAN Dell shape
  local r="$1"
  mkdir -p "$r/etc" "$r/run/current-system" "$r/nix/store"
  cat > "$r/etc/passwd" <<EOF
root:x:0:0:System administrator:/root:$BASH
sshd:x:997:997:SSH privilege separation user:/var/empty:$NOLOGIN
agent:x:1000:100:Agent OS:/home/agent:$BASH
operator:x:1001:100:Human operator:/home/operator:$BASH
EOF
  cat > "$r/etc/group" <<'EOF'
root:x:0:
wheel:x:1:operator
users:x:100:
sshd:x:997:
EOF
  cat > "$r/nix/store/aaaa-users-groups.json" <<EOF
{"mutableUsers": true,
 "users": [
   {"name":"root","group":"root","shell":"$BASH"},
   {"name":"sshd","group":"sshd","shell":"$NOLOGIN"},
   {"name":"agent","group":"users","shell":"$BASH"},
   {"name":"operator","group":"users","shell":"$BASH"}],
 "groups": [
   {"name":"root","gid":0,"members":[]},
   {"name":"wheel","gid":1,"members":["operator"]},
   {"name":"users","gid":100,"members":[]},
   {"name":"sshd","gid":997,"members":[]}]}
EOF
  printf 'echo activating\n/nix/store/aaaa-users-groups.json\n' > "$r/run/current-system/activate"
}

run() {                        # $1 = root; prints rc, output in $W/out
  AGOS_USER_DRIFT_ROOT="" AGOS_USER_DRIFT_REPORT="$W/report.txt" \
  python3 - "$1" <<'PY' >"$W/out" 2>&1
import os, sys, runpy
r = sys.argv[1]
os.environ["AGOS_USER_DRIFT_ROOT"] = r
os.environ.pop("AGOS_USER_DRIFT_SPEC", None)
sys.argv = [os.environ["AGOS_UD_SCRIPT"]]
runpy.run_path(os.environ["AGOS_UD_SCRIPT"], run_name="__main__")
PY
  echo $?
}
export AGOS_UD_SCRIPT="$(cd "$(dirname "$S")" && pwd)/$(basename "$S")"

# B (control FIRST, so a scanner that flags everything cannot pass the rest)
mkroot "$W/b"
rc=$(run "$W/b")
[ "$rc" = 0 ] && ok "B clean box -> rc 0" || { bad "B clean box -> rc $rc (want 0)"; cat "$W/out"; }

# A — a user typed in at a console. mutableUsers=true means it persists.
mkroot "$W/a"; echo "intruder:x:1002:100::/home/intruder:$BASH" >> "$W/a/etc/passwd"
rc=$(run "$W/a")
[ "$rc" = 1 ] && ok "A undeclared user -> rc 1" || bad "A undeclared user -> rc $rc (want 1)"
grep -q 'UNDECLARED-USER intruder' "$W/out" && ok "A names the account" || bad "A did not name intruder"

# C — the escalation everyone reaches for first: a name appended to wheel.
mkroot "$W/c"; sed -i 's/^wheel:x:1:operator/wheel:x:1:operator,agent/' "$W/c/etc/group"
rc=$(run "$W/c")
[ "$rc" = 1 ] && ok "C undeclared wheel member -> rc 1" || bad "C wheel member -> rc $rc (want 1)"
grep -q 'UNDECLARED-MEMBER agent in wheel' "$W/out" && ok "C names agent in wheel" || bad "C did not name agent in wheel"

# D — the SAME escalation, reached without touching /etc/group at all.
mkroot "$W/d"; sed -i "s|^agent:x:1000:100:|agent:x:1000:1:|" "$W/d/etc/passwd"
rc=$(run "$W/d")
[ "$rc" = 1 ] && ok "D primary-gid wheel membership -> rc 1" || bad "D primary-gid -> rc $rc (want 1)"
grep -q 'PRIMARY-GID-MEMBER agent has wheel' "$W/out" && ok "D names the primary-gid path" || bad "D missed the primary-gid path"

# E — a system account given a real shell is a login declared state does not grant.
mkroot "$W/e"; sed -i "s|^sshd:x:997:997:\(.*\):$NOLOGIN|sshd:x:997:997:\1:$BASH|" "$W/e/etc/passwd"
rc=$(run "$W/e")
[ "$rc" = 1 ] && ok "E shell drift -> rc 1" || bad "E shell drift -> rc $rc (want 1)"
grep -q 'SHELL-DRIFT sshd' "$W/out" && ok "E names sshd" || bad "E did not name sshd"

# F — a group nothing declares.
mkroot "$W/f"; echo "backdoor:x:1337:agent" >> "$W/f/etc/group"
rc=$(run "$W/f")
[ "$rc" = 1 ] && ok "F undeclared group -> rc 1" || bad "F undeclared group -> rc $rc (want 1)"

# G — nothing names a spec. Not clean: the scan could not be performed.
mkroot "$W/g"; : > "$W/g/run/current-system/activate"
rc=$(run "$W/g")
[ "$rc" = 2 ] && ok "G no spec named -> rc 2" || bad "G no spec -> rc $rc (want 2)"

# H — unreadable input. Also 2, for the same reason.
mkroot "$W/h"; echo "broken-line-no-colons" >> "$W/h/etc/passwd"
rc=$(run "$W/h")
[ "$rc" = 2 ] && ok "H malformed passwd -> rc 2" || bad "H malformed passwd -> rc $rc (want 2)"

# I — control for D: a user whose primary group IS its declared group must stay silent.
#     Without this, a scanner that flagged every primary-gid match would pass D.
mkroot "$W/i"
rc=$(run "$W/i")
grep -q 'PRIMARY-GID-MEMBER' "$W/out" && bad "I flagged a DECLARED primary group" || ok "I declared primary group is silent"

[ "$fail" = 0 ] && { echo "user-drift battery: PASS"; exit 0; }
echo "user-drift battery: FAIL"; exit 1
