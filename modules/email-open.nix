# modules/email-open.nix — Phase 2: the email client (OPEN variant).
#
# The roadmap's "email + MCP connectors" ambient item. This increment ships the HUMAN
# half only, on purpose — email is materially different from the local, credential-free
# apps (calendar/calc/files), and the honest split is:
#
#   * HUMAN GUI (here, now): Thunderbird — the mail client the operator uses. It ships a
#     .desktop file, so `wofi --show drun` ($mod+R, from desktop-open.nix) finds it and
#     Hyprland tiles it like any other window ("tiles cleanly"). Accounts + passwords are
#     created at RUNTIME in the user's ~/.thunderbird profile (mutable) — NOTHING secret is
#     baked into the image. That is exactly right for a PUBLIC repo (no-credentials-in-tree)
#     and for the capability-first doctrine (get a working, meshed box; configure live).
#
#   * AGENT HAND (deliberately NOT here — escalated): unlike agos-cal/agos-calc/agos-files,
#     an email "hand" cannot be local + credential-free — email is an authed, networked
#     service. Baking creds into a public repo is forbidden, and the natural agent surface
#     for mail is an MCP CONNECTOR wired at the agent-brain layer (OAuth / a runtime secret
#     placed out-of-tree, the same shape as tailscale's authKeyFile). MCP-into-agent-brain
#     is a cross-brain tool-grammar concern, so the hand lands there, not in this module —
#     see augur-to-rabbot-email-gui-shipped-hand-fork-2026-07-31.md.
#
# So this module intentionally has NO agos-* CLI. Its unique guard fingerprint is the
# `thunderbird` GUI package (no other bundled module ships it) — see flake.nix.
#
# ISOLATION: OPEN-only, self-contained, shares ZERO modules with the sovereign path —
# imported solely from configuration-open.nix. Fold into a shared substrate module at
# seal-time (same follow-up as calendar/desktop/settings/calculator/files).
{ pkgs, ... }:
{
  environment.systemPackages = with pkgs; [
    thunderbird      # human-facing email client (appears in wofi drun automatically;
                     # accounts live in the runtime ~/.thunderbird profile — no baked secrets)
  ];
}
