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
# One deliberate exception: the battery charging marker ⚡ (U+26A1). Verified present in
# DejaVuSansMono.ttf's own cmap ((3,1) BMP fmt-4 AND (3,10) fmt-12 subtables) — it is a core
# DejaVu glyph, NOT a nerd-font/emoji dependency, so it renders (no tofu box) on the dejavu_fonts
# this module already ships. The plug emoji 🔌 (U+1F50C) is NOT in DejaVu and was rejected for that
# reason. If the bar's font is ever swapped away from DejaVu, re-verify or fall back to ASCII "chg".

{ pkgs, ... }:

let
  user = "agent";

  # Reproducible Hyprland config = the Dell live baseline (dwindle tiling, tokyonight gradient
  # borders, blur/rounding/shadows/animations). `$mod`/`$(...)` are literal in Nix '' strings.
  hyprConf = pkgs.writeText "hyprland.conf" ''
    monitor = , preferred, auto, 1
    # Desktop doctrine (rabbot-to-page-P1-desktop-doctrine-spec 2026-08-02, Dillon msgs
    # 9293/9295): the bar is now a systemd user unit (Restart=always, declared below in Nix)
    # instead of a fire-and-forget exec-once — survives waybar crashes and config reloads.
    # First hand the unit the Wayland session env, then start it.
    exec-once = dbus-update-activation-environment --systemd WAYLAND_DISPLAY XDG_CURRENT_DESKTOP HYPRLAND_INSTANCE_SIGNATURE
    exec-once = systemctl --user restart waybar.service
    # Item 5 (same comm as item 4 above): the session Dillon actually lands in is this
    # Hyprland/kitty desktop, not bare tty1 — agent-shell.nix's respawn loop (PR #52) only
    # covers the tty1-console path. "The brain IS the desktop" means THIS session should open
    # as the brain, and a death/exit should respawn it in place. Mirrors the tty1 respawn
    # shape exactly (while :; do ...; sleep 1; done); warm relaunch ~11s per Rabbot's live
    # numbers. $mod+RETURN (below) stays bound to plain kitty as the manual-shell escape hatch.
    # --class brain-home is the anchor the ws1 window rules (below) key on. Keyed to this
    # dedicated class, NOT generic kitty — otherwise every terminal (incl. the cheatsheet
    # popup and $mod+RETURN shells) would get yanked to ws1.
    exec-once = kitty --class brain-home -e sh -c 'while :; do agent-brain; sleep 1; done'
    # brain-overlay (a second live chat surface pinned to special:brain) was removed
    # 2026-08-06 per Dillon's call (rabbot-to-page-brain-overlay-decision-single-surface):
    # "no idea why we ever made the overlay. it's one chat window always." Single surface
    # only now; workspace 2 is freed for the demo-window "stage" (task #285 next slice).
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
    # Desktop doctrine window rules: ws1 = the brain, always; everything else opens on
    # ws2+ so the brain never gets buried. Super+1 = "take me home."
    # maximize (not fullscreen) on the home brain — deliberate: fullscreen would hide the
    # bar, and "always on" bar is the whole point of the systemd unit above.
    windowrule = workspace 1, class:^(brain-home)$
    windowrule = maximize, class:^(brain-home)$
    windowrule = workspace 2, class:^(firefox)$
    windowrule = workspace 3, class:^(steam)$
    # Games (steam_app_* class) own ws5 fullscreen — console feel: launch from Steam →
    # game owns ws5, Super+1 back to brain, Super+5 back to game. Steam client itself
    # stays ws3 windowed. `immediate` (tearing) deliberately NOT set — needs its own
    # eval on this Mesa/iGPU (post-reset polish, per spec addendum; same for gamescope).
    windowrule = workspace 5, class:^(steam_app_.*)$
    windowrule = fullscreen, class:^(steam_app_.*)$
    # Cheatsheet popup stays a normal floating window — never tiled away or yanked.
    windowrule = float, title:^(cheatsheet)$

    $mod = SUPER
    bind = $mod, RETURN, exec, kitty
    bind = $mod, B, exec, firefox
    bind = $mod, R, exec, wofi --show drun
    bind = $mod, D, exec, wofi --show drun
    bind = $mod, Q, killactive
    bind = $mod, F, fullscreen
    bind = $mod, V, togglefloating
    bind = $mod SHIFT, M, exit
    # Workspace switching — this config had NO workspace binds before the doctrine; the
    # ws1/ws2+/ws5 rules above are only reachable with these. Super+1 = brain home.
    bind = $mod, 1, workspace, 1
    bind = $mod, 2, workspace, 2
    bind = $mod, 3, workspace, 3
    bind = $mod, 4, workspace, 4
    bind = $mod, 5, workspace, 5
    bind = $mod SHIFT, 1, movetoworkspace, 1
    bind = $mod SHIFT, 2, movetoworkspace, 2
    bind = $mod SHIFT, 3, movetoworkspace, 3
    bind = $mod SHIFT, 4, movetoworkspace, 4
    bind = $mod SHIFT, 5, movetoworkspace, 5
    bind = $mod, left, movefocus, l
    bind = $mod, right, movefocus, r
    bind = $mod, up, movefocus, u
    bind = $mod, down, movefocus, d

    # Window discoverability (rabbot-to-page-P2-window-discoverability-alt-tab-taskbar-
    # cheatsheet-2026-08-01, Dillon msg 9268: clicked off Firefox, no way back). Alt-Tab is
    # the reflex everyone reaches for first — this config had none.
    bind = ALT, TAB, cyclenext
    bind = ALT, TAB, bringactivetotop
    bind = ALT SHIFT, TAB, cyclenext, prev
    bind = ALT SHIFT, TAB, bringactivetotop
    # Cheatsheet: Super+/ pops a kitty window rendering the static keybind list below.
    bind = $mod, slash, exec, kitty --title cheatsheet -e less -R ${cheatsheetTxt}

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
  # Item 4 fix (rabbot-to-page-RUNTIME-ANSWERS-hyprland-kitty-firefox-items345-2026-08-01):
  # wl-clipboard + kitty were already present and working — the gap was Dillon pressing plain
  # Ctrl+V (kitty's default paste is Ctrl+Shift+V) and getting a literal ^V into the brain's
  # input. Bind Ctrl+V to paste explicitly. Plain Ctrl+C is deliberately left UNBOUND here —
  # it must keep reaching the brain as SIGINT (agent-brain.py's own ^C handling, PR #52).
  kittyConf = pkgs.writeText "kitty.conf" ''
    map ctrl+v paste_from_clipboard
    map ctrl+shift+v paste_from_clipboard
    map ctrl+shift+c copy_to_clipboard
  '';

  # Cheatsheet (item 3 of the same comm) — a static list, popped via Super+/. Deliberately
  # plain text through `less`, not a fancier overlay: this is the "how do I get back" panic
  # button, it has to work even if something else is broken.
  cheatsheetTxt = pkgs.writeText "cheatsheet.txt" ''
    Agent OS — keybind cheatsheet (Super+/ to reopen this)

      Super + 1         brain home (workspace 1 — the brain always lives here)
      Super + `         brain overlay from anywhere (tap again to hide)
      Super + Return    open terminal (kitty)
      Super + B         open Firefox (opens on workspace 2)
      Super + D         app launcher (wofi, fuzzy search)
      Super + R         app launcher (wofi — same as Super+D)
      Super + 2..5      switch workspace (Shift = move window there)
      Alt + Tab         cycle to next window   (Alt+Shift+Tab = prev)
      Super + Q         close focused window
      Super + F         fullscreen toggle
      Super + V         floating toggle
      Super + arrows    move focus between windows
      Ctrl + V          paste into terminal
      Ctrl + Shift + C  copy from terminal
      Super + Shift + M exit Hyprland session

    Lost a window? Alt+Tab cycles through everything open, including a
    minimized/click-away Firefox. The taskbar in the top bar also shows it —
    click to focus. Super+1 always returns to the brain.

    Bar quick-slots (left side): steam / web / apps — "apps" opens the
    full-screen app drawer (nwg-drawer).

    Gaming: launch a game from Steam → it takes workspace 5 fullscreen.
    Super+1 back to the brain, Super+5 back to the game.

    q to close this.
  '';

  waybarConf = pkgs.writeText "config.jsonc" ''
    {
      "layer": "top",
      "position": "top",
      "height": 30,
      "spacing": 6,
      "modules-left": ["hyprland/workspaces", "wlr/taskbar", "custom/steam", "custom/firefox", "custom/drawer"],
      "modules-center": ["clock"],
      "modules-right": ["pulseaudio", "network", "battery", "tray"],
      "hyprland/workspaces": {
        "on-click": "activate",
        "format": "{id}"
      },
      "wlr/taskbar": {
        "format": "{icon} {title:.20}",
        "icon-size": 16,
        "tooltip-format": "{title}",
        "on-click": "activate",
        "on-click-middle": "close"
      },
      "clock": {
        "format": "{:%a %d %b  %H:%M}",
        "tooltip-format": "<tt>{calendar}</tt>"
      },
      "battery": {
        "states": { "warning": 20, "critical": 10 },
        "format": "bat {capacity}%",
        "format-charging": "bat {capacity}% ⚡",
        "format-plugged": "bat {capacity}% ⚡",
        "format-full": "bat {capacity}% ⚡",
        "tooltip-format": "{timeTo}"
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
      "custom/steam": {
        "format": "steam",
        "on-click": "steam",
        "tooltip": false
      },
      "custom/firefox": {
        "format": "web",
        "on-click": "firefox",
        "tooltip": false
      },
      "custom/drawer": {
        "format": "apps",
        "on-click": "nwg-drawer",
        "tooltip": false
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
    #taskbar {
      padding: 0 4px;
    }
    #taskbar button {
      padding: 0 8px;
      margin: 3px 2px;
      color: #c0caf5;
      background: rgba(122, 162, 247, 0.15);
      border-radius: 6px;
    }
    #taskbar button.active {
      color: #1a1b26;
      background: #bb9af7;
    }
    #custom-steam, #custom-firefox, #custom-drawer {
      padding: 0 8px;
      margin: 3px 2px;
      color: #7aa2f7;
      background: rgba(122, 162, 247, 0.10);
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

  # The "always on" bar (doctrine item 2): waybar as a systemd user unit with
  # Restart=always — survives waybar crashes AND hyprland config reloads. Not
  # WantedBy graphical-session.target: this session is exec'd from the tty1 login
  # shell (loginShellInit below), so graphical-session.target never activates —
  # hyprland's exec-once starts the unit explicitly after importing the Wayland
  # env into the user manager (dbus-update-activation-environment line above).
  systemd.user.services.waybar = {
    description = "Waybar ambient status bar";
    serviceConfig = {
      ExecStart = "${pkgs.waybar}/bin/waybar";
      Restart = "always";
      RestartSec = 2;
    };
  };

  environment.systemPackages = with pkgs; [
    firefox                     # GPU-accelerated browser (baseline)
    kitty                       # terminal (baseline)
    waybar                      # ambient status bar
    wofi                        # launcher — keyboard-first fuzzy search (Super+D / Super+R)
    nwg-drawer                  # full-screen icon-grid app drawer ("phone home screen" browse mode)
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
    "d /home/${user}/.config/kitty 0755 ${user} users - -"
    "L+ /home/${user}/.config/hypr/hyprland.conf - - - - ${hyprConf}"
    "L+ /home/${user}/.config/waybar/config.jsonc - - - - ${waybarConf}"
    "L+ /home/${user}/.config/waybar/style.css - - - - ${waybarStyle}"
    "L+ /home/${user}/.config/kitty/kitty.conf - - - - ${kittyConf}"
  ];

  # Autologin (getty) already drops us on tty1 as `agent`. Launch Hyprland from the login shell on
  # tty1 only — SSH ptys are /dev/pts/*, so remote logins are unaffected.
  environment.loginShellInit = ''
    if [ -z "''${WAYLAND_DISPLAY:-}" ] && [ "$(tty)" = "/dev/tty1" ]; then
      exec Hyprland
    fi
  '';
}
