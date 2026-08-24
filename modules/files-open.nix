# modules/files-open.nix — Phase 2: the file manager (OPEN variant).
#
# The roadmap's "files" ambient app, built to its acceptance bar ("launches, the agent
# can read+drive it, tiles cleanly, no red config errors"): the Thunar GUI (xfce.thunar)
# for the human at the Waybar/Hyprland surface, plus a thin agent-facing CLI hand —
# `agos-files` — that returns a directory listing / a path's metadata as JSON, so the
# agent-brain can wrap it 1:1 over the box's real filesystem.
#
# READ-ONLY BY DESIGN: agos-files only ever inspects (list/stat) — it never creates,
# moves, or deletes. The agent already has a full shell for mutation; this hand is the
# STRUCTURED, contract-stable read surface (predictable JSON the brain parses), not a
# second destructive path baked into the image. Mutation stays in the GUI (human) or bash.
#
# Same shape as calendar-open (agos-cal), settings-open (agos-sys), calculator-open
# (agos-calc): a JSON-contract shell hand + a human GUI, OPEN-only, self-contained,
# imported solely from configuration-open.nix. It shares nothing with the sealed path —
# fold into a shared substrate module at seal-time (same follow-up as the others).
#
# The GUI needs no hyprland.conf edit: xfce.thunar ships a .desktop file, so
# `wofi --show drun` ($mod+R, from desktop-open.nix) finds it and Hyprland tiles it like
# any other window — the "tiles cleanly" half of the acceptance bar.
{ pkgs, ... }:
let
  agos-files = import ./pkgs/agos-files.nix { inherit pkgs; };
in {
  environment.systemPackages = with pkgs; [
    agos-files       # the agent's files hand (JSON: list/stat, read-only)
    xfce.thunar      # human-facing Thunar file manager (appears in wofi drun automatically)
  ];
}
