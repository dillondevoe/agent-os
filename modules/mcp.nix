# Phase 2 · Step 4 — the MCP stdio front door (hardened JSON-RPC 2.0 parser).
#
# bin/mcp is the WALL'S FRONT DOOR: the ONE parser that turns the untrusted model's
# JSON-RPC-over-stdio bytes into a validated, typed, unambiguous request — or fails
# closed. It is PURE (no filesystem, network, audit, or privileged state), so unlike the
# audit/taint wrappers it provisions NO state dir and cannot fail for I/O reasons.
#
# This module installs it system-wide as `mcp` and PINS its hard caps to production
# values via env, so a stray cap-override in the broker's environment cannot weaken the
# production parser (same lesson as the audit AUDIT_BIN / taint TAINT_DIR pins). The caps
# stay env-overridable ONLY through a direct `python3 bin/mcp` call — exactly how
# tests/mcp-battery.sh drives it. The pinned values intentionally match bin/mcp's own
# fallbacks: defense in depth, not new policy. The protocol rev is a hard pin inside
# bin/mcp with no env escape at all.
{ config, pkgs, lib, ... }:

let
  mcp = pkgs.writeShellScriptBin "mcp" ''
    export AGENT_OS_MCP_MAX_BYTES=262144
    export AGENT_OS_MCP_MAX_STRING=65536
    export AGENT_OS_MCP_MAX_DEPTH=16
    export AGENT_OS_MCP_MAX_ITEMS=4096
    exec ${pkgs.python3}/bin/python3 ${../bin/mcp} "$@"
  '';
in
{
  environment.systemPackages = [ mcp ];
}
