# Agent OS boot-branding — turn the raw NixOS text-scroll + bare "NixOS" loader
# entry into a quiet, branded splash. Cosmetic, but it is a BOOT surface, so it
# ships branch -> PR -> Fable like any boot change (see the scope hand-off,
# augur-to-rabbot-agentos-boot-branding-scope-2026-07-30).
#
# Scope (v1, path A = splash-over-hidden-menu on systemd-boot):
#   #1 quiet + branded splash — kernelParams quiet+splash, consoleLogLevel 3,
#      initrd.verbose false, plymouth on with a text-only theme (no image asset).
#   #2 short loader timeout — the menu doesn't linger on "NixOS"; 1s, still
#      reachable for rescue (0 would hide it entirely, hold-a-key to reach).
#
# NOT in scope (path B — a separate follow-up ONLY if Dillon wants the *menu*
# itself branded): renaming the systemd-boot "NixOS" generation title or dropping
# the auto "Reboot Into Firmware Interface" entry. Neither is cleanly doable on
# systemd-boot — that is a switch to a themed GRUB picker, its own PR + HW test.
#
# VM-BLIND: `nix build .#vm` swaps the console/boot surface (mkVMOverride ->
# virtio), so the splash NEVER renders in the VM and `nix flake check` proves
# EVAL ONLY. Real render proof (glyph coverage of the half-block wordmark, color,
# centering) = the physical Dell. This module therefore rides the SAME
# eval-only-or-HW-test merge gate as the PR-A boot surface.
#
# No encrypted root today (no boot.initrd.luks anywhere) -> no passphrase prompt
# function is needed. If disk encryption is ever added, this theme MUST grow a
# Plymouth.SetDisplayPasswordFunction or the LUKS unlock prompt becomes invisible.
{ config, pkgs, lib, ... }:
let
  themeDescriptor = pkgs.writeText "agent-os.plymouth" ''
    [Plymouth Theme]
    Name=Agent OS
    Description=Agent OS text boot splash (matches the agent-shell login wordmark)
    ModuleName=script

    [script]
    ImageDir=/etc/plymouth/themes/agent-os
    ScriptFile=/etc/plymouth/themes/agent-os/agent-os.script
  '';

  # The splash script. Mirrors bin/agent-shell's boot_art: same half-block
  # wordmark, same tagline, same gold (xterm 179) so the splash and the FIRST
  # shell frame read as one identity, not two fonts.
  themeScript = pkgs.writeText "agent-os.script" ''
    # black canvas
    Window.SetBackgroundTopColor(0.0, 0.0, 0.0);
    Window.SetBackgroundBottomColor(0.0, 0.0, 0.0);

    # gold = xterm 179 (215,175,95), matching agent-shell BANNER; dim grey tagline
    gr = 0.843; gg = 0.686; gb = 0.373;
    dr = 0.55;  dg = 0.55;  db = 0.55;

    # wordmark — the SAME half-block lettering as bin/agent-shell boot_art.
    # (These glyphs are the render risk on the Dell: if the plymouth font lacks
    #  U+2580-259F block elements they show as tofu. The ASCII tagline + dots
    #  below always render, so the splash degrades gracefully, never blank.)
    mark[0] = "▄▀█ █▀▀ █▀▀ █▄░█ ▀█▀   █▀█ █▀";
    mark[1] = "█▀█ █▄█ ██▄ █░▀█ ░█░   █▄█ ▄█";
    tagline = "the computer you talk to";

    cx = Window.GetWidth()  / 2;
    cy = Window.GetHeight() / 2;

    for (i = 0; i < 2; i++) {
      img = Image.Text(mark[i], gr, gg, gb);
      s = Sprite(img);
      s.SetX(cx - img.GetWidth() / 2);
      s.SetY(cy - 60 + i * 30);
      s.SetZ(10);
      wordmark[i] = s;   # hold the ref so the sprite isn't collected
    }

    tag_img = Image.Text(tagline, dr, dg, db);
    tag = Sprite(tag_img);
    tag.SetX(cx - tag_img.GetWidth() / 2);
    tag.SetY(cy + 12);
    tag.SetZ(10);

    # progress: a centered row of dots that grows L->R. Re-centered every tick
    # from the rendered image width, so there is no fragile fixed-pixel math.
    bar = Sprite();
    fun on_boot_progress(duration, pct) {
      dots = "";
      count = pct * 20;
      for (j = 0; j < count; j++) dots += ".";
      dimg = Image.Text(dots, gr, gg, gb);
      bar.SetImage(dimg);
      bar.SetX(cx - dimg.GetWidth() / 2);
      bar.SetY(cy + 44);
      bar.SetZ(10);
    }
    Plymouth.SetBootProgressFunction(on_boot_progress);

    # boot messages — fsck progress, "press a key to skip", stage-1 notices.
    # Without a display-message handler these are SILENTLY DROPPED under the splash,
    # so an operator can power-cycle a mid-repair filesystem. Render below the dots.
    msg = Sprite();
    fun on_message(text) {
      mimg = Image.Text(text, dr, dg, db);
      msg.SetImage(mimg);
      msg.SetX(cx - mimg.GetWidth() / 2);
      msg.SetY(cy + 72);
      msg.SetZ(10);
    }
    Plymouth.SetDisplayMessageFunction(on_message);
  '';

  agentOsTheme = pkgs.runCommand "agent-os-plymouth-theme" { } ''
    d="$out/share/plymouth/themes/agent-os"
    mkdir -p "$d"
    cp ${themeDescriptor} "$d/agent-os.plymouth"
    cp ${themeScript}     "$d/agent-os.script"
  '';
in {
  # #1 — quiet + branded splash
  boot.kernelParams    = [ "quiet" "splash" "rd.udev.log_level=3" ];
  # consoleLogLevel 3 (KERN_ERR and worse still print), NOT 0: level 0 would hide
  # the KERN_ERR/KERN_CRIT lines that diagnosed the 2026-07-29 ahci/VMD boot brick.
  # 3 keeps errors visible while staying quiet for warning/info/debug spam.
  # mkDefault (not plain): on the real machine nothing else defines this, so 3 applies;
  # inside a NixOS VM test the driver's test-instrumentation pins a verbose 7 that MUST
  # win — a plain 3 collides with its plain 7 and the seal-faildown test (which now
  # includes this module via baseModules) fails to eval. Same yield-to-an-override
  # discipline as boot.loader.timeout below.
  boot.consoleLogLevel = lib.mkDefault 3;
  boot.initrd.verbose  = false;
  boot.plymouth.enable = true;
  boot.plymouth.themePackages = [ agentOsTheme ];
  boot.plymouth.theme  = "agent-os";

  # #2 — don't linger on the "NixOS" systemd-boot menu. 1s (not 0) keeps the menu
  # reachable for rescue; 0 hides it entirely (hold a key to reach it). mkDefault
  # (not mkForce): sets 1 today but YIELDS to a future explicit rescue override
  # instead of silently stomping it.
  boot.loader.timeout = lib.mkDefault 1;
}
