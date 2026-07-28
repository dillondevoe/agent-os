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
  # Wrap the Python primitive so `taint` is on PATH with a pinned interpreter.
  # Pin the state dir unconditionally (same lesson as the audit wrapper): a stray
  # $AGENT_OS_TAINT_DIR in the broker's env must NOT redirect the PRODUCTION binary to a
  # different taint store — that would silently read a clean bit from the wrong place and
  # void the monotonicity guarantee. AUDIT_BIN is left unset so taint calls the production
  # `audit` wrapper on PATH, which in turn pins the production audit ledger.
  # The env override survives only as a TEST affordance via a DIRECT `python3 bin/taint`
  # invocation — exactly how tests/taint-battery.sh drives it.
  taint = pkgs.writeShellScriptBin "taint" ''
    export AGENT_OS_TAINT_DIR=/var/lib/agent-os/taint
    exec ${pkgs.python3}/bin/python3 ${../bin/taint} "$@"
  '';
in
{
  environment.systemPackages = [ taint ];

  systemd.tmpfiles.rules = [
    "d /var/lib/agent-os       0755 root root - -"
    "d /var/lib/agent-os/taint 0700 root root - -"
  ];
}
