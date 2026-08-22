# Phase 1.5B · task 324 step 1 — first-boot identity creation, wired.
#
# Geist's 2026-08-22 ruling on task 324, on Mirror's measurement that `ensure_boot_identities()`
# had zero production callers: the signer is `agent` (NOT `broker` — `broker` is the capability
# broker module and a battery fixture, and an audit trail reading "broker signed" would be
# ambiguous even if it were minted). Step 1 is this file. Step 2 — setting
# $AGENT_OS_AUDIT_SIGNER=agent together with $AGENT_OS_AUDIT_REQUIRE_SIGNED=agent in host config
# — is a DEPLOY decision and is deliberately NOT here: baking a signer name into the build would
# turn "signing is off" into "signing is on and unconfigurable", and modules/audit-pkg.nix's
# deploy-coupling rule (PR #126 finding G) exists precisely because those two values must be set
# together, by whoever deploys.
#
# So: after this module, a box HAS a signer identity. It still does not SIGN. That separation is
# the whole point — the previous state was "cannot ever sign", which no deploy could fix.
{ config, pkgs, lib, ... }:

let
  ident = import ./identity-pkg.nix { inherit pkgs; };
in
{
  # 0700 root:root, same posture as the audit ledger beside it. The keys are an out-of-tree
  # secret: identity.py's preflight() FAILS LOUD if the key dir or any key file is not 0700/0600,
  # so a permissive mode here does not degrade quietly — it stops the boot unit. The dir is
  # created here rather than left to mint() so the mode is declared, not inherited from umask.
  systemd.tmpfiles.rules = [
    "d /var/lib/agent-os          0755 root root - -"
    "d /var/lib/agent-os/identity 0700 root root - -"
  ];

  systemd.services.agent-os-identity-boot = {
    description = "Mint the first-boot participant identities (owner-human `dillon`, os-agent `agent`) and run the identity sign/verify self-test";
    wantedBy = [ "multi-user.target" ];
    after = [ "systemd-tmpfiles-setup.service" ];
    # Ordered BEFORE anything that would want a signer. Today nothing runs `audit` from a unit —
    # the broker invokes it in-process — so this is ordering against the future, cheaply. It is
    # stated rather than assumed because "the signer exists before the thing that signs" is the
    # one property this module is responsible for, and an unstated ordering is not a property.
    before = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      # Runs as root: the ledger it will eventually sign for is 0700 root:root, and the identity
      # tree matches. No User= indirection, so there is no $HOME-dependent path resolution to get
      # wrong — the root is pinned in modules/identity-pkg.nix, not inferred.
      ExecStart = "${ident.boot}/bin/agent-os-identity-boot";
    };
  };

  # Runnable by hand, for the same reason the mail preflight is: a boot check you cannot
  # re-run on demand is a boot check you cannot debug.
  environment.systemPackages = [ ident.boot ];
}
