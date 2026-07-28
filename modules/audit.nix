# Phase 2 · Step 2 — the append-only audit log, provisioned declaratively.
#
# bin/audit is the privileged primitive (append + hash-chain + fsync + verify). This
# module (a) installs it system-wide as `audit` and (b) provisions its directory.
#
# /var/lib/agent-os/audit is a Step-1 PROTECTED path: no capability impl sandbox may
# hold it in writable OR readable scope (asserted in modules/capability-registry.nix).
# Only the broker writes here.
#
# v1 scope is deliberately small: create the dir 0700 root:root. Ownership tightens to
# the dedicated broker user when that user lands in Step 5 (the broker core); until
# then 0700 root:root keeps it out of every unprivileged reach. OS-level append-only
# (chattr +a) is layered in Step 7 alongside the impl sandboxes — belt to the hash
# chain's suspenders. No systemd unit runs it yet; the broker invokes it in-process.
{ config, pkgs, lib, ... }:

let
  # Wrap the Python primitive so `audit` is on PATH with a pinned interpreter.
  # Pin the ledger unconditionally: a stray $AGENT_OS_AUDIT_DIR in the broker's env
  # must NOT redirect the PRODUCTION binary to a different log (append would exit 0
  # writing the WRONG ledger — a no-attacker misconfig that still voids the guarantee).
  # The env override survives only as a TEST affordance via a DIRECT `python3 bin/audit`
  # invocation — exactly how tests/audit-battery.sh drives it.
  audit = pkgs.writeShellScriptBin "audit" ''
    export AGENT_OS_AUDIT_DIR=/var/lib/agent-os/audit
    exec ${pkgs.python3}/bin/python3 ${../bin/audit} "$@"
  '';
in
{
  environment.systemPackages = [ audit ];

  systemd.tmpfiles.rules = [
    "d /var/lib/agent-os       0755 root root - -"
    "d /var/lib/agent-os/audit 0700 root root - -"
  ];
}
