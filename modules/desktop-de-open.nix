# modules/desktop-de-open.nix — the full desktop environments (agentos.desktop = "plasma" | "gnome" |
# "cosmic"; surfaces-and-first-login.md §6/§9 item 4). OPEN variant only, imported by
# configuration-open.nix; inert unless chosen.
#
# Plasma is the design's third adapter. GNOME and COSMIC were to wait for a window-control story; the
# owner asked for all three side by side to vet the UI (2026-10-10), so they ship now with the same
# honest adapter: notify works, window list and arrange answer Unsupported (agos_desktop.DesktopEnv).
# None of these has a wlroots-style IPC the brain may drive without a privileged D-Bus or script API.
#
# NO DISPLAY MANAGER (open question 6 stays open): like Hyprland and sway, the session starts from the
# tty1 autologin shell. Each desktop's own manager (SDDM, GDM, cosmic-greeter) is left off and the
# surface-options check asserts it.
#
# THE BRAIN WINDOW: the same user unit as the other desktops (kitty, class brain-home, Restart=always,
# respawn loop, journal tee). All three reach graphical-session.target (COSMIC's cosmic-session.target
# BindsTo it; GNOME and Plasma are systemd-managed sessions), so the unit hangs off that target; if
# WAYLAND_DISPLAY lands a beat late, Restart retries it.
#
# NO NEW LISTENERS OR LOOKUPS (surfaces-and-first-login.md §3): avahi (mDNS), geoclue (location lookup,
# which runs as its own uid, outside the agent's per-uid wall), and GNOME's media/share/remote-desktop
# servers and first-run wizard are forced off; the check asserts each.
{ config, pkgs, lib, ... }:

let
  d = config.agentos.desktop;
  # What tty1 execs. Plasma and COSMIC have tty launchers; GNOME needs the session type spelled out.
  # start-cosmic re-execs itself in a fresh login shell unless told it is already in one, and that shell
  # would source this loginShellInit again and loop; we ARE in the agent's login shell, so say so.
  start = {
    plasma = "exec ${pkgs.kdePackages.plasma-workspace}/bin/startplasma-wayland";
    gnome = "XDG_SESSION_TYPE=wayland exec ${pkgs.gnome-session}/bin/gnome-session";
    cosmic = "exec ${pkgs.cosmic-session}/bin/start-cosmic --in-login-shell";
  };
  isDe = lib.elem d [ "plasma" "gnome" "cosmic" ];

in
lib.mkIf isDe (lib.mkMerge [
  {
    systemd.user.services.brain-home = {
      description = "agent-brain home window (the desktop's primary surface)";
      wantedBy = [ "graphical-session.target" ];
      partOf = [ "graphical-session.target" ];
      after = [ "graphical-session.target" ];
      serviceConfig = {
        ExecStart = "${pkgs.kitty}/bin/kitty --class brain-home -e ${pkgs.bash}/bin/bash -c 'exec 3>&2; while :; do /run/current-system/sw/bin/agent-brain 2> >(${pkgs.coreutils}/bin/tee -i >(${pkgs.systemd}/bin/systemd-cat -t agent-brain) >&3); sleep 1; done'";
        Restart = "always";
        RestartSec = 2;
      };
    };

    # notify-send for the adapter's notify(); every one of these desktops runs its own daemon.
    environment.systemPackages = [ pkgs.kitty pkgs.libnotify pkgs.firefox ];
    fonts.packages = [ pkgs.dejavu_fonts ];

    services.avahi.enable = lib.mkForce false;
    services.geoclue2.enable = lib.mkForce false;

    # Autologin drops us on tty1 as `agent`; start the desktop there only (SSH ptys are /dev/pts/*).
    # AGENTOS_DE_STARTED: a launcher that opens a nested login shell must never start a second desktop.
    environment.loginShellInit = ''
      if [ -z "''${WAYLAND_DISPLAY:-}" ] && [ -z "''${AGENTOS_DE_STARTED:-}" ] && [ "$(tty)" = "/dev/tty1" ]; then
        export AGENTOS_DE_STARTED=1
        ${start.${d}}
      fi
    '';
  }
  (lib.mkIf (d == "plasma") { services.desktopManager.plasma6.enable = true; })
  (lib.mkIf (d == "gnome") {
    services.desktopManager.gnome.enable = true;
    # gnome.nix's core-apps block sets NAUTILUS_4_EXTENSION_DIR from config.system.path. genesis-open
    # reads environment.variables (which sessionVariables feed) to build agent-brain, which is in
    # system.path: an evaluation loop, and mkForce cannot break it (the override check evaluates the
    # overridden string). So the block is off and its main apps are installed here instead.
    services.gnome.core-apps.enable = false;
    environment.systemPackages = with pkgs; [
      nautilus gnome-console gnome-text-editor gnome-calculator gnome-system-monitor loupe papers
    ];
    programs.bash.vteIntegration = true;   # was in core-apps: Console tabs keep the working directory
    services.gnome.gnome-initial-setup.enable = lib.mkForce false;   # first login is Agent OS's (§4/§5)
    services.gnome.rygel.enable = lib.mkForce false;
    services.gnome.gnome-user-share.enable = lib.mkForce false;
    services.gnome.gnome-remote-desktop.enable = lib.mkForce false;
    services.dleyna.enable = lib.mkForce false;
  })
  (lib.mkIf (d == "cosmic") { services.desktopManager.cosmic.enable = true; })
])
