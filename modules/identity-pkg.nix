# Phase 1.5B · task 324 step 1 — the identity ROOT pin and the first-boot minting wrapper,
# defined ONCE so every consumer inherits the same answer to "where do the keys live".
#
# WHY THIS FILE EXISTS AT ALL. Before it, modules/identity.py had ZERO production callers:
# `ensure_boot_identities()` was defined and nothing in any .nix, .py, .sh or unit invoked it
# (measured at 9540b35). So on a fresh box NO participant was ever minted — not `agent`, not
# `dillon` — and setting $AGENT_OS_AUDIT_SIGNER would fail closed on every append. That is the
# correct failure mode (bin/audit is fail-closed by design, and modules/audit-pkg.nix documents
# it), but it means audit record signing was not merely OFF: it was UNREACHABLE, with no wiring
# that could ever deploy a signer. The gap sat one step earlier than "no signer configured".
#
# TWO CONSUMERS MUST AGREE ON `root`, AND THE POINT OF THE PIN IS THAT THEY CANNOT DRIFT:
#   - modules/identity.nix runs the boot unit that MINTS into it.
#   - modules/audit-pkg.nix exports it so bin/audit READS the same tree when it signs.
# identity.py resolves ROOT at IMPORT time from $AGENT_OS_IDENTITY_ROOT, falling back to
# ~/identity. Leaving both sides on that fallback would make correctness depend on the two
# processes happening to run with the same $HOME — an assumption no code states, which is the
# exact defect class that has bitten this repo before. So the pin is explicit on both sides and
# derived from ONE constant here rather than retyped.
#
# /var/lib/agent-os/identity (not /root/identity) so the location is a property of the SYSTEM,
# not of whichever account happens to invoke the signer.
{ pkgs }:

rec {
  # The single source of truth. Anything that mints, signs, or verifies reads THIS.
  root = "/var/lib/agent-os/identity";

  # First-boot keygen, run as a systemd oneshot by modules/identity.nix.
  #
  # Safe to run on EVERY boot, and that is by construction rather than by a guard we maintain:
  # identity.mint() returns an existing key as-is and never replaces it, because re-minting
  # "would silently orphan every signature the old key produced". So the unit is idempotent, no
  # ConditionPathExists is needed, and a re-run cannot rotate anything. Rotation is deliberately
  # NOT a thing boot can do by accident — recovery is an explicit new key plus re-attestation.
  #
  # ensure_boot_identities() ends in boot_self_test(agent): a sign/verify roundtrip that raises
  # on failure. That is the load-bearing half. A signer degraded to producing unverifiable
  # signatures is worse than one plainly absent — the ledger keeps accumulating records nothing
  # can ever check — so this unit must FAIL LOUD, not warn. It prints npubs (public by
  # definition) and never key material.
  boot = pkgs.writeShellScriptBin "agent-os-identity-boot" ''
    export AGENT_OS_IDENTITY_ROOT=${root}
    export AGENT_OS_MODULES=${../modules}
    exec ${pkgs.python3}/bin/python3 -c '
import os, sys
sys.path.insert(0, os.environ["AGENT_OS_MODULES"])
import identity
minted = identity.ensure_boot_identities(owner="dillon", agent="agent")
for name in sorted(minted):
    print("participant %s -> %s" % (name, minted[name]))
print("identity boot self-test PASSED for agent")
'
  '';
}
