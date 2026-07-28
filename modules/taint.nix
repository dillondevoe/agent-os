# Phase 2 · Step 3 — the per-session provenance taint tracker (SHADOW mode).
#
# bin/taint is the anti-laundering primitive: a monotonic per-session taint bit (set-only,
# human-only reset) plus persistent, model-unwritable mem origin tags. In v1 it only
# COMPUTES and LOGS (through the Step-2 audit primitive) the taint-gated decision it WOULD
# make — it gates nothing. This module (a) installs it system-wide as `taint` and (b)
# provisions its state directory.
#
# /var/lib/agent-os/taint is a Step-1 PROTECTED path: no capability impl sandbox may hold
# it in writable scope (asserted in modules/capability-registry.nix). That is WHERE the
# origin tags live — the model cannot write there, so it cannot forge a mem entry's origin
# or clear its own taint. Only the broker writes here.
#
# v1 scope is deliberately small: create the dir 0700 root:root, mirroring the audit dir.
# Ownership tightens to the dedicated broker user when it lands in Step 5; OS-level
# append-only hardening for the origin ledger layers in with the Step-7 impl sandboxes.
# No systemd unit runs it yet; the broker invokes it in-process, and it shells out to the
# `audit` binary (installed by modules/audit.nix) to record every shadow decision.
{ config, pkgs, lib, ... }:

let
  # The production `taint` wrapper, defined once in modules/taint-pkg.nix (single source of
  # truth, mirroring modules/audit-pkg.nix) so modules/broker.nix pins the EXACT same
  # store-path binary — same state dir, same AUDIT_BIN — with no drift. The wrapper pins
  # AGENT_OS_TAINT_DIR + AUDIT_BIN unconditionally (a stray env override would redirect the
  # PRODUCTION binary to the wrong taint store / a silently-succeeding audit sink, voiding
  # monotonicity and no-log->no-execute); the overrides survive only as TEST affordances via
  # a DIRECT `python3 bin/taint` invocation — exactly how tests/taint-battery.sh drives it.
  taint = import ./taint-pkg.nix { inherit pkgs; };
in
{
  environment.systemPackages = [ taint ];

  systemd.tmpfiles.rules = [
    "d /var/lib/agent-os       0755 root root - -"
    "d /var/lib/agent-os/taint 0700 root root - -"
  ];
}
