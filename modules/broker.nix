# Phase 2 · Step 5 — the broker ("the wall").
#
# bin/broker is the ONE trusted decision point: it turns a validated MCP verdict (Step 4)
# into {DENY, ALLOW-AUTO, REQUIRE-CONFIRM}, audits that decision BEFORE any effect (Step 2),
# and commits the taint effect covering a result BEFORE the result's bytes are released
# (Step 3). It INVOKES no capability itself — the two effect seams (confirm, invoke) default
# to fail-closed stubs and are replaced by Step 6 (real confirm channel) and Step 7
# (sandboxed impls). This module installs it system-wide as `broker` and materializes its
# read-only registry.
#
# Security surface (the wall itself): branch -> PR -> Fable(code) -> merge. Never direct-push.
#
# Pinning discipline (same lesson as audit/taint/mcp): every path the broker trusts is pinned
# to a store-path here, so no stray env in the broker's own environment can redirect it —
#   * AGENT_OS_REGISTRY -> the materialized read-only registry at a protected path;
#   * TAINT_BIN / AUDIT_BIN -> the EXACT production wrappers (via *-pkg.nix), so the broker's
#     taint consult/effects and audit appends hit the real ledgers, not a silently-succeeding
#     sink or a clean-bit-from-the-wrong-store;
#   * AGENT_OS_BROKER_DIR -> a model-unwritable var dir for the audit-head anchor (Step-2
#     FIX-3: anchoring the chain head OUTSIDE the audit dir makes a suffix-rewrite detectable).
# The env overrides survive only as TEST affordances via a DIRECT `python3 bin/broker` call —
# exactly how tests/broker-battery.sh drives it.
{ config, pkgs, lib, ... }:

let
  auditWrapper = import ./audit-pkg.nix { inherit pkgs; };
  taintWrapper = import ./taint-pkg.nix { inherit pkgs; };

  # Materialize the registry to JSON. Reading `reg.registry` FORCES the Step-1 `assert ok`,
  # so a registry that violates a mechanism-3 invariant fails THIS module's build too — the
  # broker can never be built against an unvalidated registry. The store path is globally
  # read-only (Nix store), and it is surfaced at /etc/agent-os/registry/registry.json (a
  # Step-1 protected path: writable-protected, so no capability impl can rewrite it; the
  # registry is not secret, so it is deliberately NOT a protected-READ path).
  reg = import ./capability-registry.nix { inherit lib; };
  registryJson = pkgs.writeText "agent-os-registry.json" (builtins.toJSON reg.registry);

  broker = pkgs.writeShellScriptBin "broker" ''
    export AGENT_OS_REGISTRY=/etc/agent-os/registry/registry.json
    export TAINT_BIN=${taintWrapper}/bin/taint
    export AUDIT_BIN=${auditWrapper}/bin/audit
    export AGENT_OS_BROKER_DIR=/var/lib/agent-os/broker
    exec ${pkgs.python3}/bin/python3 ${../bin/broker} "$@"
  '';
in
{
  environment.systemPackages = [ broker ];

  # The materialized registry, at a protected path, as an immutable symlink into the store.
  # /etc is not writable at runtime and the target is read-only, so the model cannot alter
  # the registry the broker classifies against.
  environment.etc."agent-os/registry/registry.json".source = registryJson;

  systemd.tmpfiles.rules = [
    "d /var/lib/agent-os        0755 root root - -"
    "d /var/lib/agent-os/broker 0700 root root - -"   # audit-head anchor lives here (not in the audit dir)
  ];
}
