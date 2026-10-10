# The human's terminal and shell (docs/design/surfaces-and-first-login.md §4.3/§4.4, order of work
# item 2: "add agentos.terminal and agentos.shell with current defaults (no behaviour change)").
#
# Defaults are what the open image ships today (kitty, bash), so importing this module changes
# nothing. The choices only ever select among packages already in nixpkgs at the pinned revision
# (all seven checked 2026-10-10); first login will later pick among them without building anything.
#
# Scope, per the design's fixed rules (§3, §5): this is the HUMAN's surface. The agent account keeps
# bash and its tty1 loop; the brain's own window stays kitty (files the brain depends on are
# system-owned, §4.4). What the terminal choice changes is the human's "open a terminal" binding
# and which terminal package is installed; the shell choice sets the operator's login shell.
{ config, pkgs, lib, ... }:

let
  cfg = config.agentos;
  terminals = {
    kitty = { pkg = pkgs.kitty; cmd = "kitty"; };
    ghostty = { pkg = pkgs.ghostty; cmd = "ghostty"; };
    foot = { pkg = pkgs.foot; cmd = "foot"; };
    alacritty = { pkg = pkgs.alacritty; cmd = "alacritty"; };
  };
  shells = { bash = pkgs.bash; fish = pkgs.fish; zsh = pkgs.zsh; };   # bash = exactly what the operator had
in
{
  options.agentos = {
    desktop = lib.mkOption {
      type = lib.types.enum [ "none" "hyprland" ];
      default = "hyprland";
      description = ''
        The desktop: "hyprland" (today's open-lane desktop) or "none" (terminal only: the brain runs
        on tty1 in a respawn loop, and its desktop adapter answers Unsupported for window and browser
        verbs instead of probing a display that is not there). sway and Plasma follow
        (surfaces-and-first-login.md §6). The choice is compiled into the installed adapter, so it
        holds for the brain however it is started.
      '';
    };
    terminal = lib.mkOption {
      type = lib.types.enum (lib.attrNames terminals);
      default = "kitty";
      description = "The human's terminal: what Super+Return opens on a desktop, and which terminal package is installed.";
    };
    shell = lib.mkOption {
      type = lib.types.enum (lib.attrNames shells);
      default = "bash";
      description = ''
        The operator account's login shell (su - operator, ssh, other consoles). The open desktop
        session runs as the agent account, so this does not change what Super+Return opens there;
        the agent account always keeps bash.
      '';
    };
    surfaceInternal = lib.mkOption {
      type = lib.types.attrs;
      internal = true;
      readOnly = true;
      description = "Resolved terminal command/package and shell package for the surface modules.";
    };
  };

  config = {
    agentos.surfaceInternal = {
      terminalCmd = terminals.${cfg.terminal}.cmd;
      terminalPkg = terminals.${cfg.terminal}.pkg;
      shellPkg = shells.${cfg.shell};
    };
    # With the Hyprland desktop, kitty is already installed by desktop-open.nix (the brain's window
    # and the cheatsheet use it), so only a non-default terminal adds a package there; the default
    # system stays byte-identical.
    # Read by modules/agos_desktop.py adapter(); set only for a non-default desktop, so the default
    # system's environment is unchanged.
    environment.variables = lib.mkIf (cfg.desktop != "hyprland") { AGENTOS_DESKTOP = cfg.desktop; };
    environment.systemPackages = lib.optional (cfg.terminal != "kitty") terminals.${cfg.terminal}.pkg;
    # NixOS needs the shell enabled system-wide to be a valid login shell (/etc/shells, vendor init).
    programs.fish.enable = lib.mkIf (cfg.shell == "fish") true;
    programs.zsh.enable = lib.mkIf (cfg.shell == "zsh") true;
  };
}
