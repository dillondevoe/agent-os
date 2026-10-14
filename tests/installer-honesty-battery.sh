#!/usr/bin/env bash
# install.sh must describe the system it actually installs.
#
# WHY THIS EXISTS. install.sh installs the flake's `agentos` target at a PINNED revision
# (FLAKE_REV). What it tells the operator about sealing must be true of THAT revision.
#   - Until slice 4a the target shipped UNSEALED (DNS + 80/443 open to every account until a
#     manual `nixos-rebuild switch --flake ...#agentos-sealed`), and the installer once called the
#     result "the SEALED sovereign box". This battery was written to stop that claim.
#   - Since slice 4a (37c6bb9, docs/design/first-login-and-setup-window.md 5.1) the target SEALS
#     ITSELF: a setup window lets the model server alone reach DNS + 443 for at most two hours of
#     open time, then the box closes it by itself. The installer now says so, and the old
#     "UNSEALED ... next step: switch to agentos-sealed" text would be the false one.
# So the claim and the pin are checked TOGETHER: the text may say "seals itself" only if the
# pinned revision contains the window, and may not keep the old UNSEALED warning.
#
# WHAT IT CHECKS (no nix, no network; git history for the pin):
#   A. The old claims are gone: no "UNSEALED", no "THIS MACHINE IS", no next-step switch to
#      #agentos-sealed (the window makes it unnecessary, and printing it as a step says otherwise).
#   B. The new claim is present and complete, in what the script PRINTS (echo lines; a comment
#      saying it does not count): "SEALS ITSELF", the two hours, that the model server's account
#      gets this access on top of the sealed box's own (never "alone": root's cache channel and
#      time sync stay), what happens when the time runs out, and how setup is run again.
#   C. THE PIN CONTAINS THE WINDOW: FLAKE_REV is a full sha and 37c6bb9 (slice 4a) is its
#      ancestor. Without git history this FAILS (it does not skip): CI checks out the history.
#   D. Control arms: the old warning re-inserted, the new claim deleted, and a pin moved back to
#      a revision before the window must each FAIL. A check that cannot go red is not a check.
#
#   usage: bash tests/installer-honesty-battery.sh [path/to/install.sh]
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${1:-$ROOT/install.sh}"
WINDOW_COMMIT=37c6bb951e369204e95364f11f7bb6fbb9fbe812       # slice 4a: `agentos` enables the setup window
PRE_WINDOW=05781ca33df4baa14206cda92311c676fadbdb66          # the pin before it (slice 3b part 1)
[ -f "$TARGET" ] || { echo "FATAL: $TARGET not found"; exit 2; }

# check FILE -> prints reasons, returns 0 iff honest
check() {
  local f="$1" rc=0 pin said
  said="$(grep -E '^[[:space:]]*echo ' "$f")"           # what the operator is shown
  grep -n 'UNSEALED' "$f" | sed 's/^/  still says UNSEALED: /' | grep . && rc=1
  grep -n 'THIS MACHINE IS' "$f" | sed 's/^/  the old warning: /' | grep . && rc=1
  grep -n -- '--flake .*#agentos-sealed' "$f" | sed 's/^/  prints a switch to agentos-sealed as a step: /' | grep . && rc=1
  grep -q 'SEALS ITSELF' <<<"$said" || { echo "  missing: the box SEALS ITSELF"; rc=1; }
  grep -q 'two hours' <<<"$said" || { echo "  missing: how long the setup window is (two hours)"; rc=1; }
  grep -q "model server's account may ALSO reach" <<<"$said" || { echo "  missing: who gets the extra access during setup"; rc=1; }
  grep -qi 'alone' <<<"$said" && { echo "  says 'alone': root's cache channel and time sync stay open on a sealed box"; rc=1; }
  grep -q 'sealed with no model' <<<"$said" || { echo "  missing: what happens when the time runs out"; rc=1; }
  grep -q 'rm /var/lib/agent-os/seal/sealed /var/lib/agent-os/seal/window.json && reboot' <<<"$said" || { echo "  missing: how setup is run again"; rc=1; }
  [ "$(grep -c '^[[:space:]]*FLAKE_REV=' "$f")" = 1 ] || { echo "  FLAKE_REV is assigned more than once (or not at all)"; rc=1; }
  pin="$(sed -n 's/^FLAKE_REV="\${FLAKE_REV:-\([0-9a-f]*\)}"$/\1/p' "$f")"
  if ! printf '%s' "$pin" | grep -qE '^[0-9a-f]{40}$'; then
    echo "  the pin does not parse as a full sha: '$pin'"; rc=1
  elif ! git -C "$ROOT" cat-file -e "$pin^{commit}" 2>/dev/null || ! git -C "$ROOT" cat-file -e "$WINDOW_COMMIT^{commit}" 2>/dev/null; then
    echo "  cannot check the pin: no git history for $pin or $WINDOW_COMMIT here (fetch it; this does not skip)"; rc=1
  elif ! git -C "$ROOT" merge-base --is-ancestor "$WINDOW_COMMIT" "$pin"; then
    echo "  the pin $pin predates the setup window ($WINDOW_COMMIT): the text above would be false for it"; rc=1
  fi
  return $rc
}

fail=0
if out="$(check "$TARGET")"; then
  echo "PASS A+B+C install.sh says the box seals itself, and its pin has the window"
else
  echo "FAIL A+B+C install.sh"; echo "$out"; fail=1
fi

SCR="$(mktemp -d "${TMPDIR:-/tmp}/installer-honesty-XXXXXX")"; trap 'rm -rf "$SCR"' EXIT
# D1: the old warning re-inserted
{ cat "$TARGET"; echo 'echo " ⚠️  THIS MACHINE IS UNSEALED."'; } > "$SCR/old.sh"
if check "$SCR/old.sh" >/dev/null; then echo "FAIL D1 control: the old UNSEALED warning passed"; fail=1
else echo "PASS D1 control: the old warning is detected"; fi
# D2: the new claim deleted from what is printed (a comment that still says it does not count)
grep -v '^[[:space:]]*echo .*SEALS ITSELF' "$TARGET" > "$SCR/noclaim.sh"
if check "$SCR/noclaim.sh" >/dev/null; then echo "FAIL D2 control: a script that never says it seals itself passed"; fail=1
else echo "PASS D2 control: a missing claim is detected"; fi
# D3: the pin moved back before the window, text unchanged
sed "s/^FLAKE_REV=\"\${FLAKE_REV:-[0-9a-f]*}\"$/FLAKE_REV=\"\${FLAKE_REV:-$PRE_WINDOW}\"/" "$TARGET" > "$SCR/oldpin.sh"
if ! grep -q "$PRE_WINDOW" "$SCR/oldpin.sh"; then echo "FAIL D3 fixture: the pin was not rewritten"; fail=1
elif check "$SCR/oldpin.sh" >/dev/null; then echo "FAIL D3 control: a pin from before the window passed"; fail=1
else echo "PASS D3 control: a pin that predates the window is detected"; fi
# D4: the old next step re-inserted
{ cat "$TARGET"; echo 'echo "  nixos-rebuild switch --flake github:x/y/z#agentos-sealed"'; } > "$SCR/step.sh"
if check "$SCR/step.sh" >/dev/null; then echo "FAIL D4 control: the old switch step passed"; fail=1
else echo "PASS D4 control: the old switch step is detected"; fi

# D5: "alone" put back into a printed line
{ cat "$TARGET"; echo 'echo " the model server alone may reach the network"'; } > "$SCR/alone.sh"
if check "$SCR/alone.sh" >/dev/null; then echo "FAIL D5 control: 'alone' passed"; fail=1
else echo "PASS D5 control: the overstatement is detected"; fi
# D6: a second pin assignment later in the script
{ cat "$TARGET"; echo "FLAKE_REV=$PRE_WINDOW"; } > "$SCR/twopins.sh"
if check "$SCR/twopins.sh" >/dev/null; then echo "FAIL D6 control: a second FLAKE_REV passed"; fail=1
else echo "PASS D6 control: a second pin is detected"; fi

[ "$fail" = 0 ] && echo "installer-honesty: all arms green" || echo "installer-honesty: FAILED"
exit "$fail"
