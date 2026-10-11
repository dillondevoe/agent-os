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
      type = lib.types.enum [ "none" "hyprland" "sway" "plasma" "gnome" "cosmic" ];
      default = "hyprland";
      description = ''
        The desktop: "hyprland" (today's open-lane desktop) or "none" (terminal only: the brain runs
        on tty1 in a respawn loop, and its desktop adapter answers Unsupported for window and browser
        verbs instead of probing a display that is not there), or "sway" (modules/desktop-sway-open.nix,
        the second adapter), or "plasma" / "gnome" / "cosmic" (modules/desktop-de-open.nix: full desktop
        environments, started from tty1 without their display managers; window verbs Unsupported). The choice is compiled into the installed adapter, so it
        holds for the brain however it is started.
      '';
    };
    desktopShell = lib.mkOption {
      type = lib.types.enum [ "waybar" "noctalia" ];
      default = "noctalia";
      description = ''
        The shell layer on the Hyprland desktop: "noctalia" (the default since 2026-10-10, owner ruling
        "as cachy ships": CachyOS's Hyprland + Noctalia config with the Agent OS layer on top, see
        modules/hyprland-noctalia.nix; Noctalia is bar, launcher, notifications, control center,
        wallpaper and OSD in one, and gives the brain's notify() a notification daemon) or "waybar"
        (the earlier Agent OS desktop and its own keys). Noctalia starts on Hyprland's own anime
        wallpaper once; after that the user's pick sticks.
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
    firstLogin = {
      enable = lib.mkOption {
        type = lib.types.bool;
        default = false;
        description = ''
          Ask for the terminal and shell at first login and make both a run-time choice
          (modules/surface-choice.nix; docs/design/first-login-and-setup-window.md slice 1). When on,
          "open a terminal" and the operator's login shell go through wrappers that read the root-owned
          choice store, every offered terminal and shell is installed, and `agentos.terminal` /
          `agentos.shell` become the defaults the questions start from.
        '';
      };
      package = lib.mkOption {
        type = lib.types.package;
        internal = true;
        description = "The agos-choice package (set by modules/surface-choice.nix).";
      };
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
      # With first login on, both go through the choice wrappers by absolute store path (never a name
      # on PATH); without it they are the build-time picks, exactly as before.
      terminalCmd = if cfg.firstLogin.enable then "${cfg.firstLogin.package}/bin/agentos-terminal" else terminals.${cfg.terminal}.cmd;
      terminalLabel = if cfg.firstLogin.enable then "your chosen one; default ${cfg.terminal}" else terminals.${cfg.terminal}.cmd;
      terminalPkg = terminals.${cfg.terminal}.pkg;
      shellPkg = if cfg.firstLogin.enable then "${cfg.firstLogin.package}/bin/agentos-shell" else shells.${cfg.shell};
      inherit terminals shells;
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
