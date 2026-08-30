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
  # Hyprland 0.56 REMOVED the `class:` / `title:` matchers from hyprlang `windowrule`
  # (verified against the pinned compositor's own parser — every spelling of the old form
  # is rejected, and `windowrulev2` no longer exists either). Lua is the vendor's only
  # remaining format for these rules: the package ships `share/hypr/hyprland.lua` and no
  # `hyprland.conf` at all. There is NO hybrid — `source =` inside a .conf reads the
  # sourced file as hyprlang regardless of its extension, so the manager is chosen once,
  # by the top-level config file's suffix. Hence the whole block moves, not just the rules.
  #
  # This is a TRANSLATION, not a redesign: every value below is the one that was here
  # before. The one forced drift is `hl.monitor` requiring a named `output` field where
  # hyprlang took a bare leading comma. Regex anchors are carried across verbatim —
  # unanchored "steam" would also match steam_app_*, which would fight the ws5 rule.
  #
  # 0.56 resolves ~/.config/hypr/hyprland.lua ahead of hyprland.conf when both exist
  # (verified), so the old .conf the L+ symlink used to point at is a live rollback.
  hyprConf = pkgs.writeText "hyprland.lua" ''
    hl.monitor({ output = "", mode = "preferred", position = "auto", scale = 1 })

    -- Desktop doctrine (rabbot-to-page-P1-desktop-doctrine-spec 2026-08-02, Dillon msgs
    -- 9293/9295): the bar is a systemd user unit (Restart=always, declared below in Nix)
    -- instead of a fire-and-forget exec — it survives waybar crashes and config reloads.
    -- First hand the unit the Wayland session env, then start it.
    hl.exec_cmd("dbus-update-activation-environment --systemd WAYLAND_DISPLAY XDG_CURRENT_DESKTOP HYPRLAND_INSTANCE_SIGNATURE")
    hl.exec_cmd("systemctl --user restart waybar.service")
    -- The session Dillon actually lands in is this Hyprland/kitty desktop, not bare tty1;
    -- agent-shell.nix's respawn loop (PR #52) only covers the tty1-console path. "The brain
    -- IS the desktop" means THIS session opens as the brain and respawns in place on exit.
    -- $mod+RETURN stays plain kitty as the escape hatch. --class brain-home is the anchor the
    -- ws1 rules below key on — keyed to this dedicated class, NOT generic kitty, or every
    -- terminal would get yanked to ws1.
    --
    -- NOT a bare exec. Under a .conf, `exec-once` was DEFERRED until the compositor was up;
    -- under a .lua the top level of this file runs AT CONFIG LOAD, so a bare
    -- hl.exec_cmd("kitty ...") launched before there was a compositor to map a window into
    -- and no brain-home window existed after boot (observed on the Dell, 2026-08-30).
    -- The bar had already been through this and survived, for exactly one reason: it is a
    -- systemd user unit that this line only POKES. Same shape here, and Restart=always is a
    -- strict gain over exec-once — it covers a kitty crash, which exec-once never did. The
    -- inner `while` loop is kept as well and is not redundant with it: the loop respawns
    -- agent-brain IN PLACE (the window survives), the unit respawns the window.
    hl.exec_cmd("systemctl --user restart brain-home.service")
    -- brain-overlay was removed 2026-08-06 per Dillon
    -- (rabbot-to-page-brain-overlay-decision-single-surface): "no idea why we ever made the
    -- overlay. it's one chat window always." Single surface; ws2 is free for the demo stage.
    hl.exec_cmd("hyprctl setcursor Bibata-Modern-Amber 24")

    hl.env("XCURSOR_SIZE", "24")
    hl.env("XCURSOR_THEME", "Bibata-Modern-Amber")

    hl.config({
      ["general.gaps_in"]             = 5,
      ["general.gaps_out"]            = 12,
      ["general.border_size"]         = 2,
      ["general.col.active_border"]   = { colors = { "rgba(7aa2f7ee)", "rgba(bb9af7ee)" }, angle = 45 },
      ["general.col.inactive_border"] = "rgba(1f2335aa)",
      ["general.layout"]              = "dwindle",
      ["dwindle.preserve_split"]      = true,
      ["decoration.rounding"]               = 10,
      ["decoration.active_opacity"]         = 1.0,
      ["decoration.inactive_opacity"]       = 0.95,
      ["decoration.blur.enabled"]           = true,
      ["decoration.blur.size"]              = 6,
      ["decoration.blur.passes"]            = 3,
      ["decoration.blur.new_optimizations"] = true,
      ["decoration.shadow.enabled"]         = true,
      ["decoration.shadow.range"]           = 18,
      ["decoration.shadow.render_power"]    = 3,
      ["decoration.shadow.color"]           = "rgba(1a1a1aee)",
      ["animations.enabled"]  = true,
      ["input.kb_layout"]     = "us",
      ["input.follow_mouse"]  = 1,
    })

    hl.curve("ease", { type = "bezier", points = { {0.25, 0.1}, {0.25, 1.0} } })
    hl.animation({ leaf = "windows",    enabled = true, speed = 5, bezier = "ease", style = "popin 80%" })
    hl.animation({ leaf = "fade",       enabled = true, speed = 6, bezier = "ease" })
    hl.animation({ leaf = "workspaces", enabled = true, speed = 5, bezier = "ease", style = "slide" })

    -- Desktop doctrine window rules: ws1 = the brain, always; everything else opens on
    -- ws2+ so the brain never gets buried. Super+1 = "take me home."
    -- maximize (not fullscreen) on the home brain — deliberate: fullscreen would hide the
    -- bar, and an always-on bar is the whole point of the systemd unit above.
    hl.window_rule({ name = "brain-home-ws",  match = { class = "^(brain-home)$" },   workspace = "1" })
    hl.window_rule({ name = "brain-home-max", match = { class = "^(brain-home)$" },   maximize = true })
    hl.window_rule({ name = "firefox-ws",     match = { class = "^(firefox)$" },      workspace = "2" })
    hl.window_rule({ name = "steam-ws",       match = { class = "^(steam)$" },        workspace = "3" })
    -- Games (steam_app_* class) own ws5 fullscreen — console feel: launch from Steam ->
    -- game owns ws5, Super+1 back to brain, Super+5 back to game. The Steam client itself
    -- stays ws3 windowed. `immediate` (tearing) deliberately NOT set — needs its own eval
    -- on this Mesa/iGPU (post-reset polish, per spec addendum; same for gamescope).
    hl.window_rule({ name = "game-ws",        match = { class = "^(steam_app_.*)$" }, workspace = "5" })
    hl.window_rule({ name = "game-fs",        match = { class = "^(steam_app_.*)$" }, fullscreen = true })
    -- Cheatsheet popup stays a normal floating window — never tiled away or yanked.
    hl.window_rule({ name = "cheatsheet",     match = { title = "^(cheatsheet)$" },   float = true })

    hl.bind("SUPER+RETURN",  hl.dsp.exec_cmd("kitty"))
    hl.bind("SUPER+B",       hl.dsp.exec_cmd("firefox"))
    hl.bind("SUPER+R",       hl.dsp.exec_cmd("wofi --show drun"))
    hl.bind("SUPER+D",       hl.dsp.exec_cmd("wofi --show drun"))
    hl.bind("SUPER+Q",       hl.dsp.window.close())
    hl.bind("SUPER+F",       hl.dsp.window.fullscreen())
    hl.bind("SUPER+V",       hl.dsp.window.float())
    hl.bind("SUPER+SHIFT+M", hl.dsp.exit())

    -- Workspace switching — the ws1/ws2+/ws5 rules above are only reachable with these.
    -- Super+1 = brain home.
    for i = 1, 5 do
      hl.bind("SUPER+" .. i,       hl.dsp.focus({ workspace = i }))
      hl.bind("SUPER+SHIFT+" .. i, hl.dsp.window.move({ workspace = i }))
    end

    hl.bind("SUPER+left",  hl.dsp.focus({ direction = "left" }))
    hl.bind("SUPER+right", hl.dsp.focus({ direction = "right" }))
    hl.bind("SUPER+up",    hl.dsp.focus({ direction = "up" }))
    hl.bind("SUPER+down",  hl.dsp.focus({ direction = "down" }))

    -- Window discoverability (rabbot-to-page-P2-window-discoverability-alt-tab-taskbar-
    -- cheatsheet-2026-08-01, Dillon msg 9268: clicked off Firefox, no way back). Alt-Tab is
    -- the reflex everyone reaches for first — this config had none.
    hl.bind("ALT+TAB",       hl.dsp.window.cycle_next())
    hl.bind("ALT+TAB",       hl.dsp.window.bring_to_top())
    hl.bind("ALT+SHIFT+TAB", hl.dsp.window.cycle_next({ prev = true }))
    hl.bind("ALT+SHIFT+TAB", hl.dsp.window.bring_to_top())
    -- Cheatsheet: Super+/ pops a kitty window rendering the static keybind list.
    hl.bind("SUPER+slash",   hl.dsp.exec_cmd("kitty --title cheatsheet -e less -R ${cheatsheetTxt}"))

    -- Laptop function / media keys (XF86 keysyms). Default Hyprland binds none of these, so
    -- the Dell's Fn brightness/volume keys are dead until wired here. `repeating` = ramp
    -- while held (was bindel), `locked` = still works on the lockscreen (was bindel/bindl).
    -- Backlight write perms come from the video group + brightnessctl's udev rule (below).
    local el = { repeating = true, locked = true }
    local l  = { locked = true }
    hl.bind("XF86MonBrightnessUp",   hl.dsp.exec_cmd("brightnessctl set +10%"), el)
    hl.bind("XF86MonBrightnessDown", hl.dsp.exec_cmd("brightnessctl set 10%-"), el)
    hl.bind("XF86AudioRaiseVolume",  hl.dsp.exec_cmd("wpctl set-volume -l 1.0 @DEFAULT_AUDIO_SINK@ 5%+"), el)
    hl.bind("XF86AudioLowerVolume",  hl.dsp.exec_cmd("wpctl set-volume @DEFAULT_AUDIO_SINK@ 5%-"), el)
    hl.bind("XF86AudioMute",         hl.dsp.exec_cmd("wpctl set-mute @DEFAULT_AUDIO_SINK@ toggle"), l)
    hl.bind("XF86AudioMicMute",      hl.dsp.exec_cmd("wpctl set-mute @DEFAULT_AUDIO_SOURCE@ toggle"), l)
    hl.bind("XF86AudioPlay",         hl.dsp.exec_cmd("playerctl play-pause"), l)
    hl.bind("XF86AudioNext",         hl.dsp.exec_cmd("playerctl next"), l)
    hl.bind("XF86AudioPrev",         hl.dsp.exec_cmd("playerctl previous"), l)
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
  # Exposed so the `hyprland-config-parses` flake check can feed the EXACT derivation
  # this module ships to the pinned compositor's own parser. It is deliberately not a
  # re-derivation: the check must verify the bytes that get symlinked to
  # ~agent/.config/hypr/hyprland.lua below, or reader and writer are two spellings of
  # one rule with nothing asserting they agree.
  system.build.hyprlandConf = hyprConf;

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

  # "The brain IS the desktop" — the brain-home window, same unit shape as the bar above and
  # for the same reason. Under the Lua config the compositor's top level runs at CONFIG LOAD,
  # so the old `exec` form fired before there was anything to map a window into; the bar was
  # the only startup item that survived the switch, and it survived BECAUSE it was a unit the
  # config merely pokes. Not WantedBy graphical-session.target (see waybar's note): the Wayland
  # env is imported into the user manager by the dbus-update-activation-environment line in
  # hyprland.lua, and the `systemctl --user restart` immediately after it is what starts this.
  #
  # Two nested restarts, deliberately, covering two different deaths:
  #   the inner `while` loop  -> agent-brain exits, respawns IN the same window (window lives)
  #   Restart=always          -> kitty itself dies, the window comes back
  systemd.user.services.brain-home = {
    description = "agent-brain home window (the desktop's primary surface)";
    serviceConfig = {
      # Absolute paths, not bare names: the old form ran from hyprland's exec and inherited the
      # login shell's PATH; a user unit does not. `agent-brain` is built in genesis-open.nix, so
      # ${"$"}{agent-brain} is not in scope here — /run/current-system/sw/bin is the seam, and it is
      # a guaranteed one: flake.nix's agentos-open-imports guard asserts genesis-open puts
      # agent-brain in systemPackages, so this path exists on any system that has this module.
      ExecStart = "${pkgs.kitty}/bin/kitty --class brain-home -e ${pkgs.bash}/bin/sh -c 'while :; do /run/current-system/sw/bin/agent-brain; sleep 1; done'";
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
    "L+ /home/${user}/.config/hypr/hyprland.lua - - - - ${hyprConf}"
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
