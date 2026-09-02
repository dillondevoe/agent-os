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
  # The scanner enumerates accounts from passwd, not from a /home/* glob, so the fixture
  # must carry one. Every arm below therefore drives the SAME enumeration code that runs
  # on the box — a fixture that exercised a fallback path would test the wrong scanner.
  printf 'root:x:0:0::/root:/bin/sh\nagent:x:1000:1000::/home/agent:/bin/sh\nsvc:x:900:900::/var/lib/svc:/bin/sh\n' > "$W/etc/passwd"
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

# H — PRE-FIX ARM, and it is the incident key again. `svc` has a home outside /root and
# /home/*, which is the shape of 48 of the Dell's 51 declared accounts (measured
# 2026-09-02; `ollama` -> /var/lib/ollama is a writable one). The glob-based scanner this
# replaces returned 0 here: the planted key was INVISIBLE and the box read "clean".
# Verified firing against the pre-fix script before the fix was written.
setup; mkdir -p "$W/var/lib/svc/.ssh"; printf '%s\n' "$PLANT" > "$W/var/lib/svc/.ssh/authorized_keys"
check "H planted key in a home outside /home -> DRIFT" "$(run)" 1
grep -q 'UNDECLARED svc' "$W/out" || { echo "FAIL H: svc not named"; fail=$((fail+1)); }

# I — CONTROL ARM for H. Without it, a scanner that flagged every out-of-glob home would
# pass H while being useless.
setup; mkdir -p "$W/var/lib/svc/.ssh"; printf '%s\n' "$DECL" > "$W/etc/ssh/authorized_keys.d/svc"
printf '%s\n' "$DECL" > "$W/var/lib/svc/.ssh/authorized_keys"
check "I declared key in a home outside /home -> clean" "$(run)" 0

# J — the scan must not report "clean" when it does not know WHICH accounts to scan.
# Without passwd the enumeration returns nothing, and a silent empty enumeration is
# indistinguishable from a clean box — which is the whole finding.
setup; rm -f "$W/etc/passwd"; printf '%s\n' "$PLANT" > "$W/root/.ssh/authorized_keys"
check "J no passwd -> CANNOT-ASSESS, not clean" "$(run)" 2

# K — THE POPULATION IS IN THE VERDICT. Two clean runs that examined different amounts
# must not be byte-identical. This is the arm that would have caught the finding above
# by reading the report alone.
setup; run >/dev/null; empty_line="$(grep '^RESULT' "$W/out")"
setup; printf 'restrict,pty %s c\n' "$(echo "$MIR" | cut -d' ' -f1-2)" > "$W/root/.ssh/authorized_keys"
run >/dev/null; one_line="$(grep '^RESULT' "$W/out")"
if [ "$empty_line" != "$one_line" ] && case "$empty_line" in *"examined 0 mutable"*) true;; *) false;; esac \
   && case "$one_line" in *"examined 1 mutable"*) true;; *) false;; esac; then
  echo "PASS K clean verdicts carry their population and differ"; pass=$((pass+1))
else
  echo "FAIL K population absent or identical — empty=[$empty_line] one=[$one_line]"; fail=$((fail+1))
fi

# A verdict that does not depend on how many arms ran cannot notice any going missing.
WANT_ARMS=11
ran=$((pass+fail))
if [ "$ran" != "$WANT_ARMS" ]; then
  echo "FAIL arm count: $ran arms ran, expected $WANT_ARMS -- an arm was added or silently lost"
  fail=$((fail+1))
fi

echo "--- $pass passed, $fail failed ($ran arms) ---"
[ "$fail" = 0 ]
