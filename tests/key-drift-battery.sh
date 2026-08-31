#!/usr/bin/env bash
# Battery for agos-key-drift. Every arm below either FAILED before the script existed
# or discriminates against a plausible broken version of it — a battery that only
# exercises a working scanner on a clean box cannot show it caught anything.
set -uo pipefail
SCRIPT="${1:-modules/agos-key-drift/agos-key-drift.sh}"
pass=0; fail=0
check() { if [ "$2" = "$3" ]; then echo "PASS $1"; pass=$((pass+1));
          else echo "FAIL $1 — got rc=$2, want rc=$3"; fail=$((fail+1)); fi; }

DECL="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKJTAziP2h4A1uPJeQ4++F8f+Uw3vLzjV7sGSylxA2RH rabbot-mini"
MIR="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIK7lWcmQXeBX6cgzMIQeLjZwqGgg/z1w7MkkswrV4DKf dvo-wsl"
# The A-arm's "planted" key is not a made-up string: it is the ACTUAL undeclared
# ed25519 key found in the Dell's hand-written /root/.ssh/authorized_keys and removed
# 2026-08-31 (SHA256:lIe2…). The fixture is the incident.
PLANT="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMz5kb9/Xa8wvyZPFhO5Hf/1Y6G63JzClyIx3znn7bSe mini-automation"

setup() {
  W="$(mktemp -d)"
  mkdir -p "$W/etc/ssh/authorized_keys.d" "$W/root/.ssh" "$W/home/agent/.ssh"
  printf '%s\n%s\n' "$DECL" "$MIR" > "$W/etc/ssh/authorized_keys.d/root"
  printf '%s\n%s\n' "$DECL" "$MIR" > "$W/etc/ssh/authorized_keys.d/agent"
}
run() { AGOS_KEY_DRIFT_ROOT="$W" AGOS_KEY_DRIFT_REPORT="$W/report.txt" \
        bash "$SCRIPT" >"$W/out" 2>&1; echo $?; }

# A — PRE-FIX ARM. This is the incident, reconstructed. Without it the whole module
# could ship inert and look identical to this one.
setup; printf '%s\n' "$PLANT" > "$W/root/.ssh/authorized_keys"
check "A planted undeclared root key -> DRIFT" "$(run)" 1
grep -q 'UNDECLARED root' "$W/out" || { echo "FAIL A: drift not named on the right user"; fail=$((fail+1)); }

# B — CONTROL ARM. Without this, a scanner that returned 1 unconditionally would pass A.
# Deliberately textually DIFFERENT from the declared line (options prefix, new comment)
# so it also proves the comparison is by fingerprint and not by string.
setup; printf 'restrict,pty %s a-different-comment\n' "$(echo "$MIR" | cut -d' ' -f1-2)" \
        > "$W/root/.ssh/authorized_keys"
check "B declared key, different text -> clean (fingerprint compare)" "$(run)" 0

# C — no mutable file at all: the shipped posture after the 08-31 removal.
setup
check "C no mutable authorized_keys anywhere -> clean" "$(run)" 0

# D — PER-USER, not union. This key IS declared, just not for THIS account. A union
# comparison calls it clean, and a union comparison is the obvious way to write this.
setup; printf '%s\n' "$DECL" > "$W/root/.ssh/authorized_keys"
sed -i "\#$(echo "$DECL" | cut -d' ' -f2)#d" "$W/etc/ssh/authorized_keys.d/root"
check "D key declared for another user only -> DRIFT" "$(run)" 1

# E — the instrument failing must NOT read as clean. 2 is not 0.
setup; printf 'ssh-ed25519 NOT-VALID-BASE64 junk\n' > "$W/root/.ssh/authorized_keys"
check "E unparseable key line -> CANNOT-ASSESS, not clean" "$(run)" 2

# F — nothing declared at all is not evidence of no drift.
setup; rm -rf "$W/etc/ssh/authorized_keys.d"
printf '%s\n' "$PLANT" > "$W/root/.ssh/authorized_keys"
check "F no declared dir -> CANNOT-ASSESS" "$(run)" 2

# G — a home-directory account is scanned too, not just root.
setup; printf '%s\n' "$PLANT" > "$W/home/agent/.ssh/authorized_keys"
check "G planted key under /home/agent -> DRIFT" "$(run)" 1
grep -q 'UNDECLARED agent' "$W/out" || { echo "FAIL G: agent not named"; fail=$((fail+1)); }

echo "--- $pass passed, $fail failed ---"
[ "$fail" = 0 ]
