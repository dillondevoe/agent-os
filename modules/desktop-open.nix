# modules/desktop-open.nix — Agent OS Phase 2: reproducible graphical layer (OPEN variant ONLY)
#
# Phase 0 shipped a polished Hyprland desktop LIVE on the Dell but never committed it — the roadmap
# was ahead of the tree (confirmed by Rabbot, 2026-07-31). This module makes that baseline
# REPRODUCIBLE so every Phase 2 app has a compositor to "tile cleanly" in, and FINISHES the Waybar
# ambient bar (clock / battery / network / volume — the roadmap's "partial, finish it").
#
# Live source of truth: ~/jarvis-sync/dvo-inbox/dell-desktop-baseline/ (hyprland.conf verified
# `hyprctl configerrors`-clean on Hyprland 0.56: multi-line blur/shadow blocks, no `pseudotile`).
#
# OPEN-only + self-contained (isolation doctrine — imported solely from configuration-open.nix,
# shares nothing with the sealed sovereign config). Folds into a shared substrate at seal-time.
#
# Autologin note: configuration-open.nix already sets services.getty.autologinUser = "agent", so we
# do NOT redefine it here (would clash) — we only add the tty1 exec-into-Hyprland login guard below.
#
# Waybar glyphs: this baseline uses ASCII labels (no nerd-font dependency) so the bar renders
# correctly on any host out of the box; nerd-font iconography is a deliberate follow-up (bundle a
# glyph font, then swap the `format` strings). Ship-working-over-pretty.

{ pkgs, ... }:

let
  user = "agent";

  # Reproducible Hyprland config = the Dell live baseline (dwindle tiling, tokyonight gradient
  # borders, blur/rounding/shadows/animations). `$mod`/`$(...)` are literal in Nix '' strings.
  hyprConf = pkgs.writeText "hyprland.conf" ''
    monitor = , preferred, auto, 1
    exec-once = waybar
    exec-once = kitty
    exec-once = hyprctl setcursor Bibata-Modern-Amber 24
    env = XCURSOR_SIZE,24
    env = XCURSOR_THEME,Bibata-Modern-Amber
    general {
        gaps_in = 5
        gaps_out = 12
        border_size = 2
        col.active_border = rgba(7aa2f7ee) rgba(bb9af7ee) 45deg
        col.inactive_border = rgba(1f2335aa)
        layout = dwindle
    }
    dwindle {
        preserve_split = true
    }
    decoration {
        rounding = 10
        active_opacity = 1.0
        inactive_opacity = 0.95
        blur {
            enabled = true
            size = 6
            passes = 3
            new_optimizations = true
        }
        shadow {
            enabled = true
            range = 18
            render_power = 3
            color = rgba(1a1a1aee)
        }
    }
    animations {
        enabled = true
        bezier = ease, 0.25, 0.1, 0.25, 1.0
        animation = windows, 1, 5, ease, popin 80%
        animation = fade, 1, 6, ease
        animation = workspaces, 1, 5, ease, slide
    }
    input {
        kb_layout = us
        follow_mouse = 1
    }
    $mod = SUPER
    bind = $mod, RETURN, exec, kitty
    bind = $mod, B, exec, firefox
    bind = $mod, R, exec, wofi --show drun
    bind = $mod, Q, killactive
    bind = $mod, F, fullscreen
    bind = $mod, V, togglefloating
    bind = $mod SHIFT, M, exit
    bind = $mod, left, movefocus, l
    bind = $mod, right, movefocus, r
    bind = $mod, up, movefocus, u
    bind = $mod, down, movefocus, d

    # Laptop function / media keys (XF86 keysyms). Default Hyprland binds none of these, so the
    # Dell's Fn brightness/volume keys are dead until wired here. bindel = repeat-on-hold (ramp while
    # held) + works on lockscreen; bindl = locked, single-shot (toggles/transport). Backlight write
    # perms come from the video group + brightnessctl's udev rule (see systemPackages block below).
    bindel = ,XF86MonBrightnessUp,exec,brightnessctl set +10%
    bindel = ,XF86MonBrightnessDown,exec,brightnessctl set 10%-
    bindel = ,XF86AudioRaiseVolume,exec,wpctl set-volume -l 1.0 @DEFAULT_AUDIO_SINK@ 5%+
    bindel = ,XF86AudioLowerVolume,exec,wpctl set-volume @DEFAULT_AUDIO_SINK@ 5%-
    bindl  = ,XF86AudioMute,exec,wpctl set-mute @DEFAULT_AUDIO_SINK@ toggle
    bindl  = ,XF86AudioMicMute,exec,wpctl set-mute @DEFAULT_AUDIO_SOURCE@ toggle
    bindl  = ,XF86AudioPlay,exec,playerctl play-pause
    bindl  = ,XF86AudioNext,exec,playerctl next
    bindl  = ,XF86AudioPrev,exec,playerctl previous
  '';

  # The ambient bar: workspaces | clock | volume · network · battery · tray.
  # Battery hides itself on hosts with no battery (VM/desktop) — no error there.
  waybarConf = pkgs.writeText "config.jsonc" ''
    {
      "layer": "top",
      "position": "top",
      "height": 30,
      "spacing": 6,
      "modules-left": ["hyprland/workspaces"],
      "modules-center": ["clock"],
      "modules-right": ["pulseaudio", "network", "battery", "tray"],
      "hyprland/workspaces": {
        "on-click": "activate",
        "format": "{id}"
      },
      "clock": {
        "format": "{:%a %d %b  %H:%M}",
        "tooltip-format": "<tt>{calendar}</tt>"
      },
      "battery": {
        "states": { "warning": 20, "critical": 10 },
        "format": "bat {capacity}%",
        "format-charging": "chg {capacity}%",
        "format-plugged": "plg {capacity}%"
      },
      "network": {
        "format-wifi": "wifi {signalStrength}%",
        "format-ethernet": "eth {ipaddr}",
        "format-disconnected": "offline",
        "tooltip-format": "{ifname}: {ipaddr}"
      },
      "pulseaudio": {
        "format": "vol {volume}%",
        "format-muted": "muted",
        "on-click": "wpctl set-mute @DEFAULT_AUDIO_SINK@ toggle"
      },
      "tray": { "spacing": 8 }
    }
  '';

  waybarStyle = pkgs.writeText "style.css" ''
    * {
      font-family: "DejaVu Sans Mono", monospace;
      font-size: 12px;
      min-height: 0;
    }
    window#waybar {
      background: rgba(26, 27, 38, 0.92);
      color: #c0caf5;
    }
    #workspaces button {
      padding: 0 8px;
      color: #7aa2f7;
      background: transparent;
    }
    #workspaces button.active {
      color: #1a1b26;
      background: #7aa2f7;
      border-radius: 6px;
    }
    #clock, #battery, #network, #pulseaudio, #tray {
      padding: 0 10px;
    }
    #battery.warning { color: #e0af68; }
    #battery.critical { color: #f7768e; }
  '';
in
{
  # Wayland compositor. programs.hyprland pulls in the hyprland portal + graphics defaults and
  # sets up the session; hardware.graphics is already enabled in configuration-open.nix.
  programs.hyprland.enable = true;

  xdg.portal = {
    enable = true;
    extraPortals = [ pkgs.xdg-desktop-portal-gtk ];   # GTK file-chooser (firefox up/downloads)
  };

  # Audio server so the Waybar volume module has something to read (ambient-bar completeness).
  security.rtkit.enable = true;
  services.pipewire = {
    enable = true;
    alsa.enable = true;
    pulse.enable = true;
  };

  fonts.packages = with pkgs; [ dejavu_fonts ];

  environment.systemPackages = with pkgs; [
    firefox                     # GPU-accelerated browser (baseline)
    kitty                       # terminal (baseline)
    waybar                      # ambient status bar
    wofi                        # launcher
    grim slurp wl-clipboard     # screenshots + clipboard
    wireplumber                 # wpctl — the volume module's on-click mute toggle
    xdg-utils                   # xdg-open etc. for portal handoff
    brightnessctl               # backlight control for the XF86MonBrightness* Fn keys
    playerctl                   # MPRIS transport for the XF86AudioPlay/Next/Prev keys
    bibata-cursors              # Bibata-Modern-Amber — the OS's default cursor identity (orange + shadow)
  ];

  # Fn brightness keys need write access to /sys/class/backlight/*/brightness without root.
  # brightnessctl ships a udev rule that chgrps the backlight to the `video` group; installing it
  # via services.udev.packages + putting the agent in `video` is the canonical rootless path.
  # extraGroups is listOf str, so this concatenates with configuration-open.nix's [ "wheel"
  # "networkmanager" ] rather than clashing.
  users.users.${user}.extraGroups = [ "video" ];
  services.udev.packages = [ pkgs.brightnessctl ];

  # Seed the reproducible baseline into the agent's config. Force-symlink to the store so the
  # RUNNING config always == the verified Nix source (reproducibility guarantee). Live per-user
  # customization (copy-on-first-boot / home-manager) is a follow-up. Dirs first, then symlinks.
  systemd.tmpfiles.rules = [
    "d /home/${user}/.config 0755 ${user} users - -"
    "d /home/${user}/.config/hypr 0755 ${user} users - -"
    "d /home/${user}/.config/waybar 0755 ${user} users - -"
    "L+ /home/${user}/.config/hypr/hyprland.conf - - - - ${hyprConf}"
    "L+ /home/${user}/.config/waybar/config.jsonc - - - - ${waybarConf}"
    "L+ /home/${user}/.config/waybar/style.css - - - - ${waybarStyle}"
  ];

  # Autologin (getty) already drops us on tty1 as `agent`. Launch Hyprland from the login shell on
  # tty1 only — SSH ptys are /dev/pts/*, so remote logins are unaffected.
  environment.loginShellInit = ''
    if [ -z "''${WAYLAND_DISPLAY:-}" ] && [ "$(tty)" = "/dev/tty1" ]; then
      exec Hyprland
    fi
  '';
}
