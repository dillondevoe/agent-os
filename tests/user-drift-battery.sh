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

# ---------------------------------------------------------------------------
# J/J2 and K/K2 — DUPLICATE NAMES. glibc's name lookup is FIRST-WINS (nss_files
# walks the file and returns the first match), so the entry that decides who can
# log in and who is privileged is the FIRST one. A parser that builds a dict by
# assignment is LAST-WINS, and the two disagree exactly when a name appears twice
# — which is the whole attack: edit the real line, append a clean-looking twin,
# and a last-wins scanner reads the twin and reports clean while glibc honors the
# edit. Found by code-qa run 1 (agos-user-drift.py:88 parse_passwd, :101
# parse_group), both security-tagged.
#
# J2/K2 are the permitting twins and they are load-bearing: without them, a
# scanner that flagged ANY duplicate name would pass J and K while still not
# following the resolver. A duplicate whose FIRST entry is clean is clean BY THIS
# MODULE'S SCOPE — glibc never resolves to the shadowed line, so it grants
# nothing today. Stated non-goal, not an oversight: a shadowed dirty line is a
# LATENT grant (it activates if the first line is ever removed), and reporting
# latent grants is a new report class with its own doctrine, not this fix.

# J — the first sshd line is given a real shell; a clean twin is appended after it.
mkroot "$W/j"
sed -i "s|^sshd:x:997:997:\(.*\):$NOLOGIN|sshd:x:997:997:\1:$BASH|" "$W/j/etc/passwd"
echo "sshd:x:997:997:SSH privilege separation user:/var/empty:$NOLOGIN" >> "$W/j/etc/passwd"
rc=$(run "$W/j")
[ "$rc" = 1 ] && ok "J shadowed shell grant -> rc 1" || bad "J shadowed shell grant -> rc $rc (want 1)"
grep -q 'SHELL-DRIFT sshd' "$W/out" && ok "J names sshd" || bad "J did not name sshd (last-wins read the clean twin)"

# J2 — control: the FIRST entry is the declared one, so glibc grants nothing.
mkroot "$W/j2"
echo "sshd:x:997:997:SSH privilege separation user:/var/empty:$BASH" >> "$W/j2/etc/passwd"
rc=$(run "$W/j2")
[ "$rc" = 0 ] && ok "J2 shadowed-dirty twin is clean -> rc 0" || bad "J2 shadowed-dirty twin -> rc $rc (want 0)"

# K — the first wheel line gains a member; a clean twin is appended after it.
mkroot "$W/k"
sed -i 's/^wheel:x:1:operator/wheel:x:1:operator,agent/' "$W/k/etc/group"
echo "wheel:x:1:operator" >> "$W/k/etc/group"
rc=$(run "$W/k")
[ "$rc" = 1 ] && ok "K shadowed wheel grant -> rc 1" || bad "K shadowed wheel grant -> rc $rc (want 1)"
grep -q 'UNDECLARED-MEMBER agent in wheel' "$W/out" && ok "K names agent in wheel" || bad "K did not name agent in wheel (last-wins read the clean twin)"

# K2 — control: declared entry first, dirty twin shadowed behind it.
mkroot "$W/k2"
echo "wheel:x:1:operator,agent" >> "$W/k2/etc/group"
rc=$(run "$W/k2")
[ "$rc" = 0 ] && ok "K2 shadowed-dirty group twin is clean -> rc 0" || bad "K2 shadowed-dirty group twin -> rc $rc (want 0)"

[ "$fail" = 0 ] && { echo "user-drift battery: PASS"; exit 0; }
echo "user-drift battery: FAIL"; exit 1
