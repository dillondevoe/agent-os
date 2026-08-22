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

let
  # The identity ROOT pin, from the SINGLE constant modules/identity-pkg.nix defines. bin/audit
  # imports modules/identity.py to sign, and identity.py resolves its ROOT at import time from
  # $AGENT_OS_IDENTITY_ROOT with a ~/identity fallback. Without this line the signer would look
  # for keys under whatever $HOME the calling process happened to have, while the boot unit minted
  # them under /var/lib/agent-os/identity — two halves agreeing only by coincidence of
  # environment. Pinned here for exactly the reason the ledger is pinned below.
  identityRoot = (import ./identity-pkg.nix { inherit pkgs; }).root;
in

# Pin the ledger unconditionally: a stray $AGENT_OS_AUDIT_DIR in the broker's env must NOT
# redirect the PRODUCTION binary to a different log (append would exit 0 writing the WRONG
# ledger). The env override survives only as a TEST affordance via a DIRECT `python3
# bin/audit` invocation — exactly how the batteries drive it.
# AGENT_OS_MODULES is pinned for the SAME reason the ledger is, and is what makes record
# signing reachable at all in production: bin/audit lands in the store as a LONE FILE, so its
# repo-relative `../modules` fallback does not exist here. Without this pin a deployment that
# sets $AGENT_OS_AUDIT_SIGNER would fail closed on every append — the feature would be
# unreachable rather than merely off, and a control that cannot fire is a claim, not a control.
# Signing stays OFF unless the operator sets $AGENT_OS_AUDIT_SIGNER; WHICH participant the
# broker signs as is a deploy decision, deliberately not baked in here.
#
# DEPLOY-COUPLING RULE (PR #126 finding G, Geist): any deployment that sets
# $AGENT_OS_AUDIT_SIGNER MUST also set $AGENT_OS_AUDIT_REQUIRE_SIGNED, and should set it to
# the SAME participant name rather than to `1`. Reason, both measured on PR head:
#   - Without the flag, a whole-log rewrite to all-unsigned FROM GENESIS verifies clean
#     (the no-downgrade rule has no earlier signed record left to anchor on).
#   - With `=1`, an actor holding ANY registered participant's key can drop the tail and
#     re-sign a fabricated suffix as themselves; `=<participant>` is what rejects that.
# It is not exported here because it is not this wrapper's decision to make: the signer name
# is a deploy input, and a hardcoded pin that disagreed with $AGENT_OS_AUDIT_SIGNER would
# fail verify on a correct log. The rule belongs with whoever wires the signer.
pkgs.writeShellScriptBin "audit" ''
  export AGENT_OS_AUDIT_DIR=/var/lib/agent-os/audit
  export AGENT_OS_MODULES=${../modules}
  export AGENT_OS_IDENTITY_ROOT=${identityRoot}
  exec ${pkgs.python3}/bin/python3 ${../bin/audit} "$@"
''
