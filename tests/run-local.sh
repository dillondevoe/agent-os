#!/usr/bin/env bash
# =============================================================================
# tests/run-local.sh — run every battery that does NOT need nix, in one command.
#
# WHY THIS EXISTS
#   The batteries are excellent and CI runs all of them via a sandboxed
#   `nix flake check`. But there was no way to run ANY of them locally without
#   hand-reconstructing each battery's bespoke positional signature — they take
#   between 1 and 7 required arguments, in different orders, with no runner.
#   The practical effect: on a machine without nix (a Mac, a fresh clone, a
#   contributor's laptop) the only feedback loop was "push and wait for CI".
#
#   This closes that gap for the subset that needs no Nix store: it discovers the
#   repo root, builds a scratch dir, wires each battery's arguments, and prints one
#   PASS/FAIL table. It is deliberately NOT a replacement for `nix flake check` —
#   see "NOT COVERED" below, and never treat a green run here as merge evidence.
#
# WHAT IT COVERS (no nix required)
#   frontdoor-kick-battery.py     9 properties — 3B front-door kick signal
#   agent-loop-battery.sh         6 checks — agent loop against stubs
#   agos-events-contract.py       event contract
#   agos-comms-shadow-contract.py shadow-mode comms contract
#   agos-comms-live-contract.py   live comms contract (exactly-once wake)
#   audit-battery.sh              audit log (needs only bin/audit + scratch)
#   taint-battery.sh              taint tracking (bin/taint + bin/audit + scratch)
#   mem-cap-battery.sh            memory capability round-trip
#
# NOT COVERED HERE — these need a materialized Nix registry and/or store paths:
#   broker-battery.sh · cap-battery.sh · confirm-battery.sh · seam-live-battery.sh
#   nft-ruleset-* · agentos-open-imports · seal-faildown · vm / vm-sealed
#   Run those with:  nix flake check --option sandbox true -L
#   (sandbox=true matters: a permissive local nix can pass where the clean-room
#   build fails — that is the cap-battery ENOEXEC/patchShebangs scar.)
#
#   usage: bash tests/run-local.sh [-v]
# =============================================================================
set -uo pipefail

VERBOSE=0
[ "${1:-}" = "-v" ] && VERBOSE=1

# Resolve repo root from this script's location, so it works from any cwd.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT" || exit 1

BIN="$ROOT/bin"
T="$ROOT/tests"
SCRATCH="$(mktemp -d "${TMPDIR:-/tmp}/agentos-local-XXXXXX")"
trap 'rm -rf "$SCRATCH"' EXIT

PY="$(command -v python3 || true)"
if [ -z "$PY" ]; then echo "FATAL: python3 not found"; exit 1; fi

pass=0; fail=0; skip=0
declare -a FAILED=()

run() {
  local name="$1"; shift
  local log="$SCRATCH/$name.log"
  local sub="$SCRATCH/$name.d"; mkdir -p "$sub"
  if timeout 300 "$@" >"$log" 2>&1; then
    printf '  \033[32mPASS\033[0m  %-28s %s\n' "$name" "$(tail -1 "$log" | cut -c1-64)"
    pass=$((pass+1))
  else
    local rc=$?
    printf '  \033[31mFAIL\033[0m  %-28s rc=%s  %s\n' "$name" "$rc" "$(tail -1 "$log" | cut -c1-56)"
    fail=$((fail+1)); FAILED+=("$name")
    [ "$VERBOSE" = 1 ] && sed 's/^/        /' "$log" | tail -25
  fi
}

need() {  # skip cleanly instead of reporting a false failure
  local f="$1" name="$2"
  if [ ! -e "$f" ]; then
    printf '  \033[33mSKIP\033[0m  %-28s missing %s\n' "$name" "${f#$ROOT/}"
    skip=$((skip+1)); return 1
  fi
  return 0
}

echo "agent-os local batteries (no nix) — $(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo '?')"
echo "scratch: $SCRATCH"
echo

# ── python contract batteries: self-contained ────────────────────────────────
need "$T/frontdoor-kick-battery.py" frontdoor-kick && \
  run frontdoor-kick "$PY" "$T/frontdoor-kick-battery.py"
need "$T/agos-events-contract.py" agos-events && \
  run agos-events "$PY" "$T/agos-events-contract.py"
need "$T/agos-comms-shadow-contract.py" agos-comms-shadow && \
  run agos-comms-shadow "$PY" "$T/agos-comms-shadow-contract.py"
need "$T/agos-comms-live-contract.py" agos-comms-live && \
  run agos-comms-live "$PY" "$T/agos-comms-live-contract.py"

# ── shell batteries: wire their positional contracts ─────────────────────────
# agent-loop-battery.sh <agent-loop> <ollama-stub> <mcp> <broker-stub> <workdir>
if need "$BIN/agent-loop" agent-loop; then
  run agent-loop bash "$T/agent-loop-battery.sh" \
    "$BIN/agent-loop" "$T/ollama-stub.py" "$BIN/mcp" "$T/broker-stub.py" "$SCRATCH/agent-loop.d"
fi
# audit-battery.sh <bin/audit> <scratch>
if need "$BIN/audit" audit; then
  run audit bash "$T/audit-battery.sh" "$BIN/audit" "$SCRATCH/audit.d"
fi
# taint-battery.sh <bin/taint> <bin/audit> <scratch>
if need "$BIN/taint" taint; then
  run taint bash "$T/taint-battery.sh" "$BIN/taint" "$BIN/audit" "$SCRATCH/taint.d"
fi
# mem-cap-battery.sh <cap-mem-remember> <cap-mem-recall> <scratch>
if need "$BIN/cap-mem-remember" mem-cap; then
  run mem-cap bash "$T/mem-cap-battery.sh" \
    "$BIN/cap-mem-remember" "$BIN/cap-mem-recall" "$SCRATCH/mem-cap.d"
fi
# mcp-battery.sh <bin/mcp> <workdir>
if need "$BIN/mcp" mcp; then
  run mcp bash "$T/mcp-battery.sh" "$BIN/mcp" "$SCRATCH/mcp.d"
fi

echo
printf 'pass=%d fail=%d skip=%d\n' "$pass" "$fail" "$skip"
if [ "$fail" -gt 0 ]; then
  printf 'failed: %s\n' "${FAILED[*]}"
  echo "re-run with -v for output, or inspect $SCRATCH/<name>.log"
fi
echo
echo "NOTE: this is the nix-free subset. Registry/store-dependent batteries"
echo "(broker, cap, confirm, seam-live, nft-ruleset, open-imports, seal-faildown)"
echo "and the merge gate itself still require:"
echo "    nix flake check --option sandbox true -L"
exit $(( fail > 0 ? 1 : 0 ))
