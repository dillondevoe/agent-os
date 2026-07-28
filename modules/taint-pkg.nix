# Phase 2 · Step 3/5 — the shared `taint` wrapper package (single source of truth).
#
# bin/taint is the anti-laundering primitive (monotonic per-session taint bit + persistent,
# model-unwritable mem origin tags). TWO callers need the IDENTICAL production wrapper:
#   - modules/taint.nix installs it system-wide as `taint` and provisions its state dir.
#   - modules/broker.nix (Step 5) must invoke the SAME binary, with the SAME state dir pinned
#     and the SAME store-path audit wrapper pinned as AUDIT_BIN, so the broker's live taint
#     consult and its set/recall/stamp effects hit the exact production ledger — never a
#     stray $AGENT_OS_TAINT_DIR / $AUDIT_BIN redirect (that would read a clean bit from the
#     wrong place, or log to a sink that silently succeeds, voiding monotonicity + no-log->
#     no-execute).
#
# Defining the wrapper once here removes drift: the state-dir pin, the AUDIT_BIN pin, and the
# pinned interpreter change in exactly one place and both callers inherit it. Importing this
# file twice with the same `pkgs` yields the same derivation (Nix deduplicates), so there is
# no collision — the same lesson, and the same shape, as modules/audit-pkg.nix.
{ pkgs }:

let
  # The SAME production audit wrapper audit.nix installs — imported so AUDIT_BIN below pins
  # the exact same store-path binary (no drift, no PATH lookup).
  auditWrapper = import ./audit-pkg.nix { inherit pkgs; };
in
# Pin the state dir + AUDIT_BIN unconditionally. Both env overrides survive only as TEST
# affordances via a DIRECT `python3 bin/taint` invocation — exactly how the batteries drive it.
pkgs.writeShellScriptBin "taint" ''
  export AGENT_OS_TAINT_DIR=/var/lib/agent-os/taint
  export AUDIT_BIN=${auditWrapper}/bin/audit
  exec ${pkgs.python3}/bin/python3 ${../bin/taint} "$@"
''
