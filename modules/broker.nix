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
  # Step 6: the SAME confirm wrapper modules/confirm.nix installs. Pinning the seam here — in the
  # broker's OWN wrapper, not via inheritable environment.variables — makes AGENT_OS_CONFIRM_SEAM
  # authoritative regardless of launch context, so no model-influenced parent env can redirect the
  # confirm seam to an auto-approver (Fable Step-6 ruling; see modules/confirm-pkg.nix header).
  confirmWrapper = import ./confirm-pkg.nix { inherit pkgs; };
  # Couple the seam pin to confirm-pkg's sandbox invariants: `assert confirmWrapper.checksOk` forces
  # the intrinsic §7 checks (single endpoint, INV-1 tty, §5 timing) at THIS wrapper's build, so the
  # pin stays sandbox-validated even if modules/confirm.nix is ever dropped from the system imports
  # (the store-path identity is by-construction; this makes its SAFETY refactor-proof too).
  confirmSeam = assert confirmWrapper.checksOk; "${confirmWrapper.wrapper}/bin/confirm";

  # Step 7 (go-live): the invoke seam. capInvoke.wrapper is the store-pinned cap-invoke DISPATCHER
  # (AGENT_OS_INVOKE_SEAM); capInvoke.capBinDir is the patchShebangs'd cap-impl dir the dispatcher
  # resolves impls under (AGENT_OS_CAP_BIN_DIR). Pinning BOTH here — in the broker's OWN wrapper,
  # not via inheritable environment.variables — makes them authoritative regardless of launch
  # context (same discipline as TAINT_BIN/AUDIT_BIN/AGENT_OS_CONFIRM_SEAM). cap-invoke reads
  # AGENT_OS_REGISTRY, already exported below, and inherits the broker's env (bin/broker `_run`
  # spawns the seam with NO env= override).
  #
  # ── GATE #5 (task-279): OS confinement is a HARD PREREQUISITE before any CHILD-SPAWNING impl ──
  # cap-invoke's per-impl wall-clock timeout (AGENT_OS_CAP_TIMEOUT_S) kills+reaps only the DIRECT
  # child. The ONLY impl wired at go-live — capabilities.list — is exec-only: it reads the
  # world-readable registry and spawns NO subprocess, so the direct-child reap fully bounds it and
  # no per-cap cgroup is needed YET. Before ANY impl that can fork (file.read/write, net.fetch, and
  # every T1/T2 capability) is wired, this module MUST first grow: (a) systemd per-cap cgroup
  # confinement DERIVED from each cap's Step-1 registry `sandbox` decl (ReadWritePaths /
  # PrivateNetwork / IPAddressDeny), and (b) killpg / start_new_session in the dispatcher so the
  # timeout reaps the whole process GROUP, not an orphan-leaking direct child. Wiring a spawning
  # impl without both is a fail-open regression. Tracked as a known deferral in the go-live PR body.
  capInvoke = import ./cap-invoke-pkg.nix { inherit pkgs; };

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
    export AGENT_OS_CONFIRM_SEAM=${confirmSeam}
    export AGENT_OS_CONFIRM_TIMEOUT_S=${toString confirmWrapper.brokerTimeout}
    export AGENT_OS_INVOKE_SEAM=${capInvoke.wrapper}/bin/cap-invoke
    export AGENT_OS_CAP_BIN_DIR=${capInvoke.capBinDir}/bin
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
