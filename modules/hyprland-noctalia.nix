# modules/hyprland-noctalia.nix — hyprland.lua for agentos.desktopShell = "noctalia" (the default), called
# from desktop-open.nix. Owner ruling 2026-10-10: "set it up noctalia hyprland as cachy ships for now".
#
# BASE: CachyOS's shipped Hyprland + Noctalia config (cachyos-hypr-noctalia /etc/skel, Hyprland 0.56.2),
# its twelve config/*.lua files inlined in its own require order: colors, look, animations, inputs and
# gestures, misc, window and layer rules, and its key bindings (Noctalia's launcher, control center,
# settings, clipboard, wallpaper, window switcher, screenshots, OSD volume/brightness/media keys).
#
# AGENT OS ON TOP (each is a line CachyOS does not have, or a CachyOS line that is dropped):
#   - the brain window: brain-home on workspace 1, maximized; Super+1..5 are workspaces (Super+1 = home),
#     Super+Shift+1..5 move there. CachyOS's Super+digit = focus monitor N is dropped (one-monitor box).
#   - the Agent OS workspace doctrine (Firefox ws2, Steam ws3, games ws5 fullscreen) instead of CachyOS's
#     name:gaming workspace, so Super+5 stays "back to the game".
#   - the shell and brain are systemd user units poked after the session env is imported (no uwsm, no
#     bare `exec noctalia`), so a crash restarts them.
#   - no `xhost +SI:localuser:root` (root X clients on the agent's display) and no lock binding (the agent
#     account has no password: a lock screen could not be unlocked).
#   - Alt+Tab raises as well as cycles, and Alt+Shift+Tab goes back (the Agent OS desktop's binding).
#   - apps are the ones this image ships: Thunar, Apostrophe, Qalculate!; Super+/ cheatsheet; Super+B
#     Firefox and Super+Shift+M exit kept from the Agent OS desktop.
{ terminalCmd, envImport, barUnit, cheatsheetTxt }:
''
  -- CachyOS Hyprland + Noctalia, as CachyOS ships it, with the Agent OS layer (see modules/hyprland-noctalia.nix).

  ---------------------------------------------------------------- variables (CachyOS config/variables.lua)
  TERMINAL     = "${terminalCmd}"
  FILE_MANAGER = "thunar"
  BROWSER      = "firefox"
  EDITOR       = "apostrophe"
  CALCULATOR   = "qalculate-gtk"
  MONITOR1 = ""
  PRIMARY_MONITOR = MONITOR1

  ---------------------------------------------------------------- colors (CachyOS config/colors.lua)
  CACHYLGREEN = "rgba(82dcccff)"
  CACHYMGREEN = "rgba(00aa84ff)"
  CACHYDGREEN = "rgba(007d6fff)"
  CACHYLBLUE  = "rgba(01ccffff)"
  CACHYMBLUE  = "rgba(182545ff)"
  CACHYDBLUE  = "rgba(111826ff)"
  CACHYWHITE  = "rgba(ffffffff)"
  CACHYGREY   = "rgba(ddddddff)"
  CACHYGRAY   = "rgba(798bb2ff)"

  hl.monitor({ output = "", mode = "preferred", position = "auto", scale = 1 })

  ---------------------------------------------------------------- environment (CachyOS session env file)
  hl.env("QT_QPA_PLATFORM", "wayland;xcb")
  hl.env("ELECTRON_OZONE_PLATFORM_HINT", "auto")
  hl.env("HYPRCURSOR_THEME", "Bibata-Modern-Ice")
  hl.env("HYPRCURSOR_SIZE", "24")
  hl.env("XCURSOR_THEME", "Bibata-Modern-Ice")
  hl.env("XCURSOR_SIZE", "24")

  ---------------------------------------------------------------- autostart (Agent OS units, not CachyOS execs)
  -- Once, when the compositor is up: hand the user manager the session env, THEN start the shell and
  -- the brain window (one chained command: separate exec_cmd calls run in parallel, unordered). Both are
  -- Restart=always user units (desktop-open.nix); this only pokes them. Not on config reload, so a
  -- reload no longer restarts the brain window.
  hl.on("hyprland.start", function ()
    hl.exec_cmd("dbus-update-activation-environment --systemd ${envImport} && systemctl --user restart ${barUnit}.service && systemctl --user restart brain-home.service")
  end)

  ---------------------------------------------------------------- decorations (CachyOS config/decorations.lua)
  hl.config({
      general = {
          gaps_in = 3,
          gaps_out = 8,
          border_size = 2,
          extend_border_grab_area = 10,
          resize_on_border = true,
          col = {
              active_border = {
                  colors = { CACHYLGREEN, CACHYDGREEN },
                  angle = 45,
              },
              inactive_border = CACHYGRAY,
          },
      },
      group = {
          col = {
              border_active = CACHYLBLUE,
              border_inactive = CACHYGRAY,
              border_locked_active = CACHYDBLUE,
              border_locked_inactive = CACHYGRAY,
          },
          groupbar = {
              col = {
                  active = CACHYLGREEN,
                  inactive = CACHYGRAY,
                  locked_active = CACHYDBLUE,
                  locked_inactive = CACHYGRAY,
              },
          },
      },
      decoration = {
          dim_special = 0.3,
          rounding = 10,
          active_opacity = 0.95,
          inactive_opacity = 0.85,
          fullscreen_opacity = 1,
          blur = {
              size = 5,
              passes = 4,
              special = true,
          },
      },
  })

  ---------------------------------------------------------------- animations (CachyOS config/animations.lua)
  hl.curve("easeOutQuint",   { type = "bezier", points = { {0.23, 1},    {0.32, 1}    } })
  hl.curve("easeInOutCubic", { type = "bezier", points = { {0.65, 0.05}, {0.36, 1}    } })
  hl.curve("linear",         { type = "bezier", points = { {0, 0},       {1, 1}       } })
  hl.curve("almostLinear",   { type = "bezier", points = { {0.5, 0.5},   {0.75, 1}    } })
  hl.curve("quick",          { type = "bezier", points = { {0.15, 0},    {0.1, 1}     } })
  hl.curve("overshoot",      { type = "bezier", points = { {0.5, 0.9},   {0.1, 1.1}   } })
  hl.curve("easy",           { type = "spring", mass = 1, stiffness = 500, dampening = 35 })
  hl.curve("rubber",         { type = "spring", mass = 1, stiffness = 200, dampening = 15 })
  hl.animation({ leaf = "global",              enabled = true, speed = 3, bezier = "quick"                  })
  hl.animation({ leaf = "windows",             enabled = true, speed = 3, spring = "easy",  style = "slide" })
  hl.animation({ leaf = "workspaces",          enabled = true, speed = 5, bezier = "quick", style = "slide" })
  hl.animation({ leaf = "specialWorkspaceIn",  enabled = true, speed = 2, bezier = "quick", style = "slide top" })
  hl.animation({ leaf = "specialWorkspaceOut", enabled = true, speed = 2, bezier = "quick", style = "slide bottom" })

  ---------------------------------------------------------------- inputs (CachyOS config/inputs.lua)
  hl.config({
      input = {
          kb_layout = "us",
          follow_mouse = 1,
          accel_profile = "flat",
      },
  })
  hl.gesture({ fingers = 4, direction = "horizontal", action = "workspace" })
  hl.gesture({ fingers = 3, direction = "down",       action = "close" })
  hl.gesture({ fingers = 3, direction = "up",         action = "fullscreen" })
  hl.gesture({ fingers = 3, direction = "left",       action = "float" })

  ---------------------------------------------------------------- misc (CachyOS config/misc.lua)
  hl.config({
      dwindle = {
          preserve_split = true,
      },
      ecosystem = {
          no_update_news = true,
          no_donation_nag = true,
      },
      misc = {
          col = {
              splash = CACHYLGREEN,
          },
          middle_click_paste = false,
          enable_swallow = true,
          swallow_regex = "(kitty|ghostty|[Kk]onsole|Alacritty|gnome-terminal|xfce[0-9]?-terminal|foot)",
          vrr = 3,
      },
      render = {
          direct_scanout = 2,
      },
      xwayland = {
          force_zero_scaling = true
      },
  })

  ---------------------------------------------------------------- Agent OS workspace doctrine
  -- ws1 = the brain, always (maximize, not fullscreen, so the bar stays); Firefox ws2, Steam ws3, games
  -- ws5 fullscreen. Super+1 = take me home, Super+5 = back to the game.
  hl.window_rule({ name = "brain-home-ws",  match = { class = "^(brain-home)$" },   workspace = "1" })
  hl.window_rule({ name = "brain-home-max", match = { class = "^(brain-home)$" },   maximize = true })
  hl.window_rule({ name = "firefox-ws",     match = { class = "^(firefox)$" },      workspace = "2" })
  hl.window_rule({ name = "steam-ws",       match = { class = "^(steam)$" },        workspace = "3" })
  hl.window_rule({ name = "game-ws",        match = { class = "^(steam_app_.*)$" }, workspace = "5" })
  hl.window_rule({ name = "game-fs",        match = { class = "^(steam_app_.*)$" }, fullscreen = true })
  hl.window_rule({ name = "cheatsheet",     match = { title = "^(cheatsheet)$" },   float = true })

  ---------------------------------------------------------------- window rules (CachyOS config/windowrules.lua)
  -- Its name:gaming workspace rules are left out (the doctrine above owns games); the rest as shipped.
  hl.window_rule({ match = { float = true }, persistent_size = true })
  hl.window_rule({
      match             = { title = "^([Pp]icture[-\\s]?[Ii]n[-\\s]?[Pp]icture)(.*)$" },
      float             = true,
      keep_aspect_ratio = true,
      size              = { "max(monitor_w, monitor_h)*0.25", "min(monitor_w, monitor_h)*0.25" },
      pin               = true,
  })
  hl.window_rule({ match = { class = "^(steam)$", title = "^(Friends List)$" }, float = true })
  hl.window_rule({ match = { class = "^(.*\\.exe)$", float = true }, monitor = PRIMARY_MONITOR, center = true, fullscreen_state = 0 })
  hl.window_rule({ match = { class = "^(.*[Ll]auncher.*)$" }, float = true, monitor = PRIMARY_MONITOR })
  hl.window_rule({ match = { class = "^(.*[Cc]alc.*)$" }, float = true, size = { "max(monitor_w, monitor_h)*0.17", "min(monitor_w, monitor_h)*0.43" } })
  hl.window_rule({ match = { class = "^(dev\\.)?(noctalia\\.Noctalia(\\.Settings)?)$" }, float = true, size = { "monitor_w*0.70", "monitor_h*0.70" } })
  local terminals = "^(kitty|ghostty|[Kk]onsole|Alacritty|gnome-terminal|xfce[0-9]?-terminal|foot|brain-home)$"
  hl.window_rule({ match = { class = "^(firefox|zen)$" }, opacity = "1.0 override" })
  hl.window_rule({ match = { class = terminals }, opacity = "1.0 override" })
  hl.window_rule({ match = { class = "^(mpv|org.kde.haruna|.*plex.*|org\\.kde\\.gwenview|.*vlc.*)$" }, opacity = "1.0 override" })
  local floatApps = {
      { class = "^(kvantummanager|qt[56]ct|nwg-look)$" },
      { class = "^(org.pulseaudio.pavucontrol|blueman-manager|nm-applet|nm-connection-editor)$" },
      { title = "^(Winetricks.*|Protontricks.*)$" },
  }
  for _, m in ipairs(floatApps) do hl.window_rule({ match = m, float = true }) end
  local modalMatches = {
      { title = "^(Open|Authentication Required|Add Folder to Workspace|Choose Files|Save As|Confirm to replace files|File Operation Progress)$" },
      { initial_title = "^(Open File)$" },
      { class = "^([Xx]dg-desktop-portal-gtk)$" },
      { title = "^(File Upload|Choose wallpaper|Library)(.*)$" },
      { class = "^(.*dialog.*)$" },
      { title = "^(.*dialog.*)$" },
      { class = "^(hyprland-share-picker)$" },
  }
  for _, m in ipairs(modalMatches) do hl.window_rule({ match = m, float = true }) end
  hl.window_rule({
      name  = "fix-xwayland-drags",
      match = {
          class      = "^$",
          title      = "^$",
          xwayland   = true,
          float      = true,
          fullscreen = false,
          pin        = false,
      },
      no_focus = true,
  })
  hl.layer_rule({
    name = "noctalia",
    match = {
      namespace = "^noctalia-(bar-.+|notification|dock|panel|attached-panel|osd|window-switcher)$",
    },
    no_anim = true,
    ignore_alpha = 0.5,
    blur = true,
    blur_popups = true,
  })

  ---------------------------------------------------------------- binds (CachyOS config/binds.lua)
  local mainMod = "SUPER"
  local noctCall = "noctalia msg "

  hl.bind(mainMod .. " + Escape",      hl.dsp.exec_cmd("hyprctl kill"))
  hl.bind(mainMod .. " + Q",           hl.dsp.window.close())
  hl.bind(mainMod .. " + ALT + Space", hl.dsp.window.float({ action = "toggle" }))
  hl.bind(mainMod .. " + D",           hl.dsp.window.fullscreen({ mode = 1 }))
  hl.bind(mainMod .. " + F",           hl.dsp.window.fullscreen())
  hl.bind(mainMod .. " + J",           hl.dsp.layout("togglesplit"))

  hl.bind(mainMod .. " + Left",  hl.dsp.focus({ direction = "left" }))
  hl.bind(mainMod .. " + Right", hl.dsp.focus({ direction = "right" }))
  hl.bind(mainMod .. " + Up",    hl.dsp.focus({ direction = "up" }))
  hl.bind(mainMod .. " + Down",  hl.dsp.focus({ direction = "down" }))
  -- Alt+Tab as the Agent OS desktop had it (Dillon msg 9268: "clicked off Firefox, no way back"):
  -- cycle AND raise, with Shift for the reverse. CachyOS binds only cycle_next.
  hl.bind("ALT+TAB",       hl.dsp.window.cycle_next())
  hl.bind("ALT+TAB",       hl.dsp.window.bring_to_top())
  hl.bind("ALT+SHIFT+TAB", hl.dsp.window.cycle_next({ prev = true }))
  hl.bind("ALT+SHIFT+TAB", hl.dsp.window.bring_to_top())
  hl.bind(mainMod .. " + Tab",   hl.dsp.exec_cmd(noctCall .. "window-switcher"))

  hl.bind(mainMod .. " + SHIFT + Up",    hl.dsp.window.move({ direction = "u" }))
  hl.bind(mainMod .. " + SHIFT + Right", hl.dsp.window.move({ direction = "r" }))
  hl.bind(mainMod .. " + SHIFT + Left",  hl.dsp.window.move({ direction = "l" }))
  hl.bind(mainMod .. " + SHIFT + Down",  hl.dsp.window.move({ direction = "d" }))
  hl.bind(mainMod .. " + CONTROL + SHIFT + Right",      hl.dsp.window.move({ workspace = "m+1" }))
  hl.bind(mainMod .. " + CONTROL + SHIFT + Left",       hl.dsp.window.move({ workspace = "m-1" }))
  hl.bind(mainMod .. " + CONTROL + SHIFT + mouse_up",   hl.dsp.window.move({ workspace = "m-1" }))
  hl.bind(mainMod .. " + CONTROL + SHIFT + mouse_down", hl.dsp.window.move({ workspace = "m+1" }))

  hl.bind(mainMod .. " + mouse:272", hl.dsp.window.drag())
  hl.bind(mainMod .. " + mouse:273", hl.dsp.window.resize())

  local function zoomfunction(value)
      local zoomvalue = hl.get_config("cursor:zoom_factor")
      if (zoomvalue + value) > 3.0 then
          hl.config({ cursor = { zoom_factor = 3.0 } })
      elseif (zoomvalue + value) < 1.0 then
          hl.config({ cursor = { zoom_factor = 1.0 } })
      else
          hl.config({ cursor = { zoom_factor = zoomvalue + value } })
      end
  end
  hl.bind(mainMod .. " + Minus",   function() zoomfunction(-0.3) end, { repeating = true })
  hl.bind(mainMod .. " + Plus",    function() zoomfunction(0.3) end,  { repeating = true })
  hl.bind(mainMod .. " + code:82", function() zoomfunction(-0.3) end, { repeating = true })
  hl.bind(mainMod .. " + code:86", function() zoomfunction(0.3) end,  { repeating = true })

  hl.bind("SUPER+RETURN",  hl.dsp.exec_cmd("${terminalCmd}"))
  hl.bind(mainMod .. " + E",          hl.dsp.exec_cmd(FILE_MANAGER))
  hl.bind(mainMod .. " + T",          hl.dsp.exec_cmd(EDITOR))
  hl.bind(mainMod .. " + C",          hl.dsp.exec_cmd(CALCULATOR))
  hl.bind("XF86Calculator",           hl.dsp.exec_cmd(CALCULATOR))
  hl.bind(mainMod .. " + W",          hl.dsp.exec_cmd(BROWSER))
  hl.bind("SUPER+B",                  hl.dsp.exec_cmd(BROWSER))
  hl.bind("CONTROL + SHIFT + Escape", hl.dsp.exec_cmd(TERMINAL .. " -e btop"))

  hl.bind(mainMod .. " + Z",       hl.dsp.exec_cmd(noctCall .. "settings-toggle"))
  hl.bind(mainMod .. " + X",       hl.dsp.exec_cmd(noctCall .. "panel-toggle control-center"))
  hl.bind(mainMod .. " + Space",   hl.dsp.exec_cmd(noctCall .. "panel-toggle launcher"))
  hl.bind(mainMod .. " + period",  hl.dsp.exec_cmd(noctCall .. "panel-toggle launcher /emo"))
  hl.bind(mainMod .. " + ALT + C", hl.dsp.exec_cmd(noctCall .. "panel-toggle session"))
  -- Quick-ask (Agent OS): one read-only question to the local model; answer as a notification + clipboard.
  hl.bind("SUPER+I",               hl.dsp.exec_cmd("agos-ask"))
  hl.bind("SUPER+SHIFT+M",         hl.dsp.exit())

  hl.bind("XF86AudioRaiseVolume",  hl.dsp.exec_cmd(noctCall .. "volume-up"),       { locked = true, repeating = true })
  hl.bind("XF86AudioLowerVolume",  hl.dsp.exec_cmd(noctCall .. "volume-down"),     { locked = true, repeating = true })
  hl.bind("XF86AudioMute",         hl.dsp.exec_cmd(noctCall .. "volume-mute"),     { locked = true })
  hl.bind("XF86AudioMicMute",      hl.dsp.exec_cmd(noctCall .. "mic-mute"),        { locked = true })
  hl.bind("XF86AudioPlay",         hl.dsp.exec_cmd(noctCall .. "media toggle"),    { locked = true })
  hl.bind("XF86AudioPause",        hl.dsp.exec_cmd(noctCall .. "media toggle"),    { locked = true })
  hl.bind("XF86AudioNext",         hl.dsp.exec_cmd(noctCall .. "media next"),      { locked = true })
  hl.bind("XF86AudioPrev",         hl.dsp.exec_cmd(noctCall .. "media previous"),  { locked = true })
  hl.bind("XF86MonBrightnessUp",   hl.dsp.exec_cmd(noctCall .. "brightness-up"),   { locked = true, repeating = true })
  hl.bind("XF86MonBrightnessDown", hl.dsp.exec_cmd(noctCall .. "brightness-down"), { locked = true, repeating = true })

  hl.bind(mainMod .. " + P",         hl.dsp.exec_cmd("hyprpicker -a -n"))
  hl.bind("Print",                   hl.dsp.exec_cmd(noctCall .. "screenshot-region"))
  hl.bind(mainMod .. " + Print",     hl.dsp.exec_cmd(noctCall .. "screenshot-fullscreen"))
  hl.bind(mainMod .. " + SHIFT + W", hl.dsp.exec_cmd(noctCall .. "panel-toggle wallpaper"))
  hl.bind(mainMod .. " + V",         hl.dsp.exec_cmd(noctCall .. "panel-toggle clipboard"))
  hl.bind(mainMod .. " + A",         hl.dsp.exec_cmd(noctCall .. "panel-toggle control-center notifications"))

  -- Workspaces: the Agent OS doctrine's Super+1..5 (Super+1 = brain home), plus CachyOS's relative moves.
  for i = 1, 5 do
    hl.bind("SUPER+" .. i,       hl.dsp.focus({ workspace = i }))
    hl.bind("SUPER+SHIFT+" .. i, hl.dsp.window.move({ workspace = i }))
  end
  hl.bind(mainMod .. " + CONTROL + Right",      hl.dsp.focus({ workspace = "m+1" }))
  hl.bind(mainMod .. " + CONTROL + Left",       hl.dsp.focus({ workspace = "m-1" }))
  hl.bind(mainMod .. " + CONTROL + Down",       hl.dsp.focus({ workspace = "emptym" }))
  hl.bind(mainMod .. " + mouse_down",           hl.dsp.focus({ workspace = "m-1" }))
  hl.bind(mainMod .. " + mouse_up",             hl.dsp.focus({ workspace = "m+1" }))
  hl.bind(mainMod .. " + CONTROL + mouse_up",   hl.dsp.focus({ workspace = "m-1" }))
  hl.bind(mainMod .. " + CONTROL + mouse_down", hl.dsp.focus({ workspace = "m+1" }))
  hl.bind(mainMod .. " + SHIFT + S", hl.dsp.window.move({ workspace = "special" }))
  hl.bind(mainMod .. " + S",         hl.dsp.workspace.toggle_special())

  -- Cheatsheet: Super+/ pops a kitty window with this desktop's keys.
  hl.bind("SUPER+slash",   hl.dsp.exec_cmd("kitty --title cheatsheet -e less -R ${cheatsheetTxt}"))
''
