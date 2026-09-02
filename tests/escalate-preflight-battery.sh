#!/usr/bin/env bash
# Battery for agos-escalate-key-preflight.
#
# Why this exists: the preflight has SIX branches (absent, bad mode, wrong owner, empty,
# trailing newline, OK) and until now exactly ONE of them had ever executed — the absent
# branch, which is the box's steady state while Dillon has not placed a key. A gate that has
# only ever run its no-op branch is not known to gate anything. Same class as #246 (the
# shipped providers.yaml was parsed by nothing) and #248 (true by construction, asserted
# nowhere).
#
# Inputs (set by the flake check):
#   PREFLIGHT          path to the built agos-escalate-key-preflight binary
#   SHIPPED_OVERRIDES  "1" if the shipped systemd unit sets either override env var, else "0"
#   DETECTOR_ARMED     "1" if a grafted unit that DOES set an override is seen as such, else "0"
set -uo pipefail

pass=0; fail=0
check() { # name, expected-exit, expected-substring, actual-exit, actual-output
  local name="$1" wexit="$2" want="$3" gexit="$4" got="$5"
  if [ "$gexit" = "$wexit" ] && printf '%s' "$got" | grep -qF -- "$want"; then
    echo "  ok   $name"; pass=$((pass+1))
  else
    echo "  FAIL $name"
    echo "       want exit=$wexit containing: $want"
    echo "       got  exit=$gexit output: $got"
    fail=$((fail+1))
  fi
}

run() { # env-assignments... -- returns output, sets RC
  local out rc
  out=$("$@" 2>&1); rc=$?
  RC=$rc; OUT=$out
}

tmp=$(mktemp -d)
me=$(id -un)

echo "escalate-key-preflight battery (running as $me)"

# --- P0: the permitting arm. Without it, a script that failed EVERYTHING would score 5/5.
good="$tmp/good"; printf %s 'sk-ant-fixture-not-a-real-key' > "$good"; chmod 400 "$good"
run env AGOS_ESCALATE_KEY="$good" AGOS_ESCALATE_KEY_OWNER="$me" "$PREFLIGHT"
check "P0: a well-formed key is ACCEPTED (permitting arm)" 0 "OK — escalate key present" "$RC" "$OUT"

# --- A1: absent is not a failure. This is the ONLY branch the box has ever exercised.
run env AGOS_ESCALATE_KEY="$tmp/nope" AGOS_ESCALATE_KEY_OWNER="$me" "$PREFLIGHT"
check "A1: an absent key exits 0 and says escalate is inert" 0 "no key at" "$RC" "$OUT"

# --- A2: mode. Checked before owner, so no override is load-bearing here.
loose="$tmp/loose"; printf %s 'x' > "$loose"; chmod 644 "$loose"
run env AGOS_ESCALATE_KEY="$loose" AGOS_ESCALATE_KEY_OWNER="$me" "$PREFLIGHT"
check "A2: a group/world-readable key is REJECTED" 1 "is mode 644" "$RC" "$OUT"

# --- A3: empty.
empty="$tmp/empty"; : > "$empty"; chmod 400 "$empty"
run env AGOS_ESCALATE_KEY="$empty" AGOS_ESCALATE_KEY_OWNER="$me" "$PREFLIGHT"
check "A3: an empty key file is REJECTED" 1 "exists but is empty" "$RC" "$OUT"

# --- A4: trailing newline. The `echo` corruption: surfaces at the API as a 401 that looks
# like a bad key rather than a bad write. Detected by SIZE, never by reading the secret.
nl="$tmp/nl"; printf 'sk-ant-fixture\n' > "$nl"; chmod 400 "$nl"
run env AGOS_ESCALATE_KEY="$nl" AGOS_ESCALATE_KEY_OWNER="$me" "$PREFLIGHT"
check "A4: a key written with echo (trailing newline) is REJECTED" 1 "ends in a newline" "$RC" "$OUT"

# --- C1: CONTROL ARM for the owner override. Every arm above passes AGOS_ESCALATE_KEY_OWNER,
# so without this arm the override could simply be disabling the owner check and all five
# would still be green. Here the override is absent: the file is owned by this sandbox user,
# not by `agent`, and the check MUST fire.
if [ "$me" = "agent" ]; then
  echo "  SKIP C1: this build runs as 'agent', so the owner check cannot be made to fail here"
else
  run env AGOS_ESCALATE_KEY="$good" "$PREFLIGHT"
  check "C1 CONTROL: with no override, a key not owned by 'agent' is REJECTED" 1 "must be agent" "$RC" "$OUT"
fi

# --- S1/S2: the overrides exist for THIS battery. Production must not set them, and the
# assertion that production does not must itself be armed (S2), or S1 passes on a detector
# that can only ever answer "no".
echo
if [ "$SHIPPED_OVERRIDES" = "0" ]; then
  echo "  ok   S1: the shipped preflight unit sets neither override env var"; pass=$((pass+1))
else
  echo "  FAIL S1: the shipped preflight unit sets an override env var"; fail=$((fail+1))
fi
if [ "$DETECTOR_ARMED" = "1" ]; then
  echo "  ok   S2 CONTROL: a unit that DOES set an override is detected as such"; pass=$((pass+1))
else
  echo "  FAIL S2 CONTROL: the override detector answered 'no' for a unit that sets one — S1 is vacuous"
  fail=$((fail+1))
fi

echo
echo "escalate-key-preflight battery: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
