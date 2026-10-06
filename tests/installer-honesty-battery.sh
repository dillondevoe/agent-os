#!/usr/bin/env bash
# install.sh must not claim the machine it installs is SEALED.
#
# WHY THIS EXISTS. install.sh installs the flake's default `agentos` target, which ships UNSEALED
# (modules/clean-room.nix: egress DNS + 80/443 open so the first-boot model pull can reach the
# network). Nothing seals it automatically: sealing is a separate, manual
# `nixos-rebuild switch --flake ...#agentos-sealed` (agentos.cleanRoom.sealed = true). The
# installer used to call the result "the SEALED sovereign box", which told an operator the
# "nothing leaves the machine" guarantee held when it did not.
#
# WHAT IT CHECKS (static, no nix, no network):
#   A. No user-facing line calls the install "sealed". After removing the tokens that are
#      legitimately about sealing (unsealed, agentos-sealed, cleanRoom.sealed, "seal it"),
#      any remaining word "sealed" (any case) is a claim, and fails.
#   B. The caveat and the exact next step are present: the word UNSEALED, the switch command
#      naming the `#agentos-sealed` target, and `agentos.cleanRoom.sealed = true`.
#   C. Control arm: the same check run on a copy with the old false claim re-inserted MUST fail,
#      and on a copy with the next step deleted MUST fail. A check that cannot go red is not a check.
#
#   usage: bash tests/installer-honesty-battery.sh [path/to/install.sh]
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${1:-$ROOT/install.sh}"
[ -f "$TARGET" ] || { echo "FATAL: $TARGET not found"; exit 2; }

# check FILE -> prints reasons, returns 0 iff honest
check() {
  local f="$1" rc=0 residue
  residue="$(sed -E 's/[Uu][Nn][Ss][Ee][Aa][Ll][Ee][Dd]//g; s/agentos-sealed//g; s/cleanRoom\.sealed//g; s/seal it//g' "$f" \
             | grep -n -i -E '\bsealed\b' || true)"
  if [ -n "$residue" ]; then
    echo "  claims 'sealed' without the caveat:"; echo "$residue" | sed 's/^/    /'; rc=1
  fi
  grep -q 'UNSEALED' "$f" || { echo "  missing the word UNSEALED"; rc=1; }
  grep -q 'nixos-rebuild switch --flake .*#agentos-sealed' "$f" || { echo "  missing the exact next-step command"; rc=1; }
  grep -q 'agentos\.cleanRoom\.sealed = true' "$f" || { echo "  missing 'agentos.cleanRoom.sealed = true'"; rc=1; }
  return $rc
}

fail=0
if out="$(check "$TARGET")"; then
  echo "PASS A+B install.sh is honest about sealing and prints the next step"
else
  echo "FAIL A+B install.sh"; echo "$out"; fail=1
fi

SCR="$(mktemp -d "${TMPDIR:-/tmp}/installer-honesty-XXXXXX")"; trap 'rm -rf "$SCR"' EXIT
# C1: re-insert the old false claim
{ cat "$TARGET"; echo 'echo "  → variant agentos = the SEALED sovereign box"'; } > "$SCR/lie.sh"
if check "$SCR/lie.sh" >/dev/null; then
  echo "FAIL C1 control: a script claiming 'the SEALED sovereign box' passed"; fail=1
else echo "PASS C1 control: false SEALED claim is detected"; fi
# C2: drop the next step
grep -v '#agentos-sealed' "$TARGET" > "$SCR/nonext.sh"
if check "$SCR/nonext.sh" >/dev/null; then
  echo "FAIL C2 control: a script with no next-step command passed"; fail=1
else echo "PASS C2 control: missing next step is detected"; fi
# C3: lowercase claim
{ cat "$TARGET"; echo 'echo "your box is sealed"'; } > "$SCR/lower.sh"
if check "$SCR/lower.sh" >/dev/null; then
  echo "FAIL C3 control: a lowercase 'sealed' claim passed"; fail=1
else echo "PASS C3 control: lowercase claim is detected"; fi

[ "$fail" = 0 ] && echo "installer-honesty: all arms green" || echo "installer-honesty: FAILED"
exit "$fail"
