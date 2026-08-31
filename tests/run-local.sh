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
#   audit-signing-battery.py      37 checks — BIP-340 signed audit records + every
#                                 downgrade path (mid-chain drop, prefix strip + re-chain,
#                                 uncheckable signer); control-armed both directions
#   taint-battery.sh              taint tracking (bin/taint + bin/audit + scratch)
#   mem-cap-battery.sh            memory capability round-trip
#   file-cap-battery.sh           file.read/file.write capability round-trip + confinement
#   providers-battery.py          11 checks — modules/providers.py provider-config contract
#   wiring-battery.py             8 checks — agent-brain <-> providers.py wiring (needs pyyaml)
#   brain-dispatch-battery.py     32 checks — agent-brain desktop hands: Lua eval sink unreachable
#   cost-cap-battery.py           28 checks — cost-cap breaker: limits config + turn() trips (needs pyyaml)
#   transport-battery.py          23 checks — provider transport seam + ollama transport
#   anthropic-transport-battery.py 34 checks — anthropic SSE transport + translation
#   bip340-battery.py             47 checks — vendored BIP-340 signer vs the OFFICIAL vectors,
#                                 must-fail half included, control-armed
#   identity-battery.py           59 checks measured on a case-INsensitive fs; 58 DERIVED for
#                                 case-sensitive (the ±1 is the C2/C3 collision arms, which branch
#                                 on what the fs does; every other check is fs-independent, so the
#                                 gap stays 1 as rounds are added — stated as a property rather
#                                 than a per-round tally that rots). Derived, not measured — this box cannot
#                                 run the other branch, and saying so beats typing a number that
#                                 looks measured. The collision arms branch on what the fs does;
#                                 keypairs, NIP-19 npub, 0600/0700 preflight, boot self-test,
#                                 name-namespace confinement; perms/markers/traversal/collision
#                                 control-armed
#   mem-battery.py                11 checks — bin/mem (memory-as-filesystem) contract
#   agent-loop-dispatch-battery.py  8 checks — agent-loop tool-dispatch mechanics vs bin/mcp + broker-stub
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
# providers-battery.py / wiring-battery.py both self-locate modules/ relative to
# their own __file__ (and via cwd=$ROOT, already set above), but PYTHONPATH=modules
# is set explicitly here too to match the documented usage exactly. wiring-battery.py
# additionally FAILS LOUD (not skip) if pyyaml is not importable — see its header
# (K6 post-merge bug, PR #77): a silently-missing pyyaml degrades agent-brain to
# legacy OLLAMA_MODEL with no visible error, so the battery treats that as a hard fail.
need "$T/providers-battery.py" providers && \
  run providers env PYTHONPATH="$ROOT/modules" "$PY" "$T/providers-battery.py"
need "$T/wiring-battery.py" wiring && \
  run wiring env PYTHONPATH="$ROOT/modules" "$PY" "$T/wiring-battery.py"

need "$T/cost-cap-battery.py" cost-cap && \
  run cost-cap env PYTHONPATH="$ROOT/modules" "$PY" "$T/cost-cap-battery.py"
# Transport seam (task 287 slices 5-6). Both were written but neither was registered here —
# an unexecuted regression test is documentation, so they ran only when someone remembered to.
need "$T/transport-battery.py" transport && \
  run transport env PYTHONPATH="$ROOT/modules" "$PY" "$T/transport-battery.py"
need "$T/anthropic-transport-battery.py" anthropic-transport && \
  run anthropic-transport env PYTHONPATH="$ROOT/modules" "$PY" "$T/anthropic-transport-battery.py"
need "$T/bip340-battery.py" bip340 && \
  run bip340 "$PY" "$T/bip340-battery.py"
need "$T/identity-battery.py" identity && \
  run identity "$PY" "$T/identity-battery.py"
# mem-battery.py locates bin/mem via its own __file__ (../bin/mem) — no args, no env.
need "$T/mem-battery.py" mem && \
  run mem "$PY" "$T/mem-battery.py"

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
# audit-signing-battery.py <bin/audit> <scratch>
if need "$BIN/audit" audit-signing; then
  run audit-signing "$PY" "$T/audit-signing-battery.py" "$BIN/audit" "$SCRATCH/audit-sig.d"
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
# file-cap-battery.sh <cap-file-read> <cap-file-write> <scratch>
if need "$BIN/cap-file-read" file-cap; then
  run file-cap bash "$T/file-cap-battery.sh" \
    "$BIN/cap-file-read" "$BIN/cap-file-write" "$SCRATCH/file-cap.d"
fi
# mcp-battery.sh <bin/mcp> <workdir>
if need "$BIN/mcp" mcp; then
  run mcp bash "$T/mcp-battery.sh" "$BIN/mcp" "$SCRATCH/mcp.d"
fi
# agent-loop-dispatch-battery.py — env-wired, not positional: drives the REAL bin/mcp
# piped into tests/broker-stub.py to prove agent-loop's tool-dispatch mechanics.
# AGENT_OS_MCP / AGENT_OS_BROKER / PYTHONPATH per the battery's own header contract.
# (bin/mcp is guarded separately above by the mcp-battery entry; gate here on the
# other two artifacts this battery is the only one to need.)
if need "$BIN/agent-loop" agent-loop-dispatch && need "$T/broker-stub.py" agent-loop-dispatch; then
  run agent-loop-dispatch env \
    AGENT_OS_MCP="$BIN/mcp" \
    AGENT_OS_BROKER="$T/broker-stub.py" \
    PYTHONPATH="$ROOT/modules" \
    "$PY" "$T/agent-loop-dispatch-battery.py"
fi

# brain-dispatch-battery.py — the red arm for agent-brain.py's desktop hands: proves the Lua
# eval sink is UNREACHABLE from open_url (a Lua-escape payload in the url produces zero hyprctl
# invocations) and that arrange_windows only ever dispatches fixed table values. Runs from the
# repo ROOT — it resolves modules/agent-brain.py relative to cwd, same as wiring-battery.py.
if need "$ROOT/modules/agent-brain.py" brain-dispatch; then
  run brain-dispatch env PYTHONPATH="$ROOT/modules" \
    sh -c 'cd "$1" && "$2" tests/brain-dispatch-battery.py' _ "$ROOT" "$PY"
fi

# brain-context-battery.py — the R1 context bound (tier-0 item 3): options.num_ctx on every
# ollama payload, and a history trimmer that only ever cuts at a user boundary so a tool
# result is never orphaned from the assistant tool_call it answers. Arm F runs the naive
# trimmer on the same fixture and asserts it DOES cut mid-group.
if need "$ROOT/modules/agos-user-drift/agos-user-drift.py" user-drift; then
  run user-drift sh -c 'cd "$1" && bash tests/user-drift-battery.sh modules/agos-user-drift/agos-user-drift.py' _ "$ROOT"
fi

if need "$ROOT/bin/fetch-verified.sh" supply-chain; then
  run supply-chain sh -c 'cd "$1" && bash tests/fetch-verified-battery.sh bin/fetch-verified.sh "$1"' _ "$ROOT"
fi

if need "$ROOT/modules/agos-key-drift/agos-key-drift.sh" key-drift; then
  run key-drift sh -c 'cd "$1" && bash tests/key-drift-battery.sh modules/agos-key-drift/agos-key-drift.sh' _ "$ROOT"
fi

if need "$ROOT/modules/agent-brain.py" brain-context; then
  run brain-context env PYTHONPATH="$ROOT/modules" \
    sh -c 'cd "$1" && "$2" tests/brain-context-battery.py' _ "$ROOT" "$PY"
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
