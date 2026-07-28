# Phase 2 · Step 2/3 — the shared `audit` wrapper package (single source of truth).
#
# bin/audit is the privileged append-only primitive. TWO callers need the IDENTICAL
# production wrapper:
#   - modules/audit.nix installs it system-wide as `audit` and provisions its dir.
#   - modules/taint.nix must invoke the SAME binary, with the SAME ledger pinned, and
#     pins AUDIT_BIN to THIS package's bin/audit so a stray $AUDIT_BIN — or any PATH-shadow
#     of `audit` — can never redirect the taint tracker's logging to a sink that silently
#     succeeds (Fable Step-3 FIX-1: an unpinned AUDIT_BIN voids no-log->no-execute).
#
# Defining the wrapper once here removes drift: the ledger pin and the pinned interpreter
# change in exactly one place and both callers inherit it. Importing this file twice with
# the same `pkgs` yields the same derivation (Nix deduplicates), so there is no collision.
{ pkgs }:

# Pin the ledger unconditionally: a stray $AGENT_OS_AUDIT_DIR in the broker's env must NOT
# redirect the PRODUCTION binary to a different log (append would exit 0 writing the WRONG
# ledger). The env override survives only as a TEST affordance via a DIRECT `python3
# bin/audit` invocation — exactly how the batteries drive it.
pkgs.writeShellScriptBin "audit" ''
  export AGENT_OS_AUDIT_DIR=/var/lib/agent-os/audit
  exec ${pkgs.python3}/bin/python3 ${../bin/audit} "$@"
''
