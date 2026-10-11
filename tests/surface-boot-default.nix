# tests/surface-boot-default.nix — choosing a DESKTOP at first login, on a UEFI machine with a real
# boot loader (docs/design/first-login-and-setup-window.md §3.2, slice 2; the spec's
# `surface-offline-switch`). This is the proof §3.2 asked for before anything depends on the mechanism.
#
# A small node (the full developer image cannot be built in CI): the two surface modules, systemd-boot
# on EFI, the image's own desktop `none`, and `sway` and `hyprland` offered as specialisations. In this
# node a "desktop" is only the AGENTOS_DESKTOP variable surface-options sets, which is enough to tell
# which variant booted. The node has no route off the VM: every switch below happens from the store.
#
#   D1  first login asks for the desktop; the answer becomes the boot default and the machine reboots
#       into it ONCE, by itself, before the agent ever gets a session.
#   D2  after that reboot: the chosen variant is running, the questions are not asked again, no new
#       generation exists, and the boot default is the entry of the running generation.
#   D3  a later change (root edits the store) takes effect at the next boot; choosing the image's own
#       desktop clears the default.
#   D4  a boot default that names an entry which does not exist boots the image's own desktop, and that
#       same boot puts the chosen one back.
#   D5  the agent cannot set the boot default, write the ESP, or touch the system profile.
{ pkgs }:

pkgs.testers.runNixOSTest {
  name = "agentos-surface-boot-default";

  nodes.box = { config, ... }: {
    imports = [ ../modules/surface-options.nix ../modules/surface-choice.nix ];
    virtualisation.useBootLoader = true;
    virtualisation.useEFIBoot = true;
    boot.loader.systemd-boot.enable = true;
    boot.loader.efi.canTouchEfiVariables = true;
    boot.loader.timeout = 1;
    agentos.desktop = "none";
    agentos.firstLogin.enable = true;
    agentos.firstLogin.desktops = [ "none" "sway" "hyprland" ];
    users.users.agent = { isNormalUser = true; };
    users.users.operator = { isNormalUser = true; shell = config.agentos.surfaceInternal.shellPkg; };
    services.getty.autologinUser = "agent";
    environment.systemPackages = with pkgs; [ coreutils util-linux procps ];
    virtualisation.memorySize = 2048;
  };

  testScript = ''
    STORE = "/var/lib/agent-os/surface.toml"

    def running():
        # surface-options sets AGENTOS_DESKTOP for every desktop but hyprland.
        out = box.succeed("grep -o 'AGENTOS_DESKTOP=\"[a-z]*\"' /etc/set-environment || echo hyprland").strip()
        return out.replace('AGENTOS_DESKTOP=', "").strip('"')

    def boot_default():
        return box.succeed("bootctl status --no-pager 2>/dev/null | sed -n 's/^ *Default Entry: *//p' | head -1; "
                           "cat /sys/firmware/efi/efivars/LoaderEntryDefault-* 2>/dev/null | tr -d '\\000' | tail -c +5").strip()

    def entry_of(name):
        spec = box.succeed("readlink -f /nix/var/nix/profiles/system/specialisation/" + name).strip()
        return box.succeed("grep -l -- 'init=" + spec + "/init' /boot/loader/entries/*.conf | xargs basename").strip()

    def generations():
        return box.succeed("ls /nix/var/nix/profiles | grep -c '^system-'").strip()

    def reboot():
        box.shutdown()
        box.start()
        box.wait_for_unit("multi-user.target")

    box.start()
    # D1 — the desktop is asked first (three offered); the image's own is the default.
    box.wait_until_tty_matches("1", "Desktop:")
    screen = box.get_tty_text("1")
    assert "none   (default)" in screen and "sway" in screen and "hyprland" in screen, "D1: " + screen
    assert running() == "none", "D1: the image's own desktop is not what first boots"
    gens = generations()
    box.fail("pgrep -u agent")
    box.send_chars("2\n")                                   # none, sway, hyprland -> sway
    box.wait_until_tty_matches("1", "Terminal:")
    box.send_chars("\n")
    box.wait_until_tty_matches("1", "Shell:")
    box.send_chars("\n")
    # The machine reboots by itself into the chosen desktop; the agent never had a session before it.
    box.wait_for_shutdown()
    box.start()
    box.wait_for_unit("multi-user.target")

    # D2 — the chosen variant is running; nothing was built; the default is this generation's entry.
    assert running() == "sway", "D2: booted " + running()
    assert box.succeed("cat " + STORE) == 'schema = 1\ndesktop = "sway"\nterminal = "kitty"\nshell = "bash"\n', "D2: store"
    assert generations() == gens, "D2: a generation was created"
    assert entry_of("sway") in boot_default(), "D2: the boot default is not the sway entry: " + boot_default()
    box.wait_for_unit("getty@tty1.service")
    box.wait_until_succeeds("pgrep -u agent", timeout=60)
    assert box.succeed("systemctl show -p ConditionResult --value agentos-firstlogin.service").strip() == "no", "D2: asked again"
    box.succeed("journalctl -t agos-choice -b --no-pager | grep -q 'boot-default: desktop=sway entry=nixos-'")
    box.fail("journalctl -t agos-choice -b --no-pager | grep -q rebooting")       # once, not again

    # D3 — a later change takes effect at the next boot; the image's own desktop clears the default.
    box.succeed("sed -i 's/desktop = \"sway\"/desktop = \"hyprland\"/' " + STORE + " && systemctl restart agentos-boot-default.service")
    assert entry_of("hyprland") in boot_default(), "D3: default not moved to hyprland"
    reboot()
    assert running() == "hyprland", "D3: booted " + running()
    box.succeed("sed -i 's/desktop = \"hyprland\"/desktop = \"none\"/' " + STORE + " && systemctl restart agentos-boot-default.service")
    reboot()
    assert running() == "none", "D3: the image's own desktop did not come back: " + running()
    assert generations() == gens, "D3: a generation was created"

    # D4 — a stale default boots the image's own desktop, and that boot puts the chosen one back.
    box.succeed("sed -i 's/desktop = \"none\"/desktop = \"sway\"/' " + STORE)
    box.succeed("bootctl set-default nixos-0000000000000000000000000000000000000000000000000000000000000000.conf")
    reboot()
    assert running() == "none", "D4: a stale default did not fall back to the image's own desktop: " + running()
    assert entry_of("sway") in boot_default(), "D4: the boot did not re-assert the chosen desktop"
    reboot()
    assert running() == "sway", "D4: " + running()

    # D5 — the agent cannot change what boots.
    box.wait_until_succeeds("pgrep -u agent", timeout=60)
    before = boot_default()
    box.fail("runuser -u agent -- bootctl set-default " + entry_of("hyprland"))
    box.fail("runuser -u agent -- sh -c 'echo x > /boot/loader/loader.conf'")
    box.fail("runuser -u agent -- sh -c 'echo x > /boot/loader/entries/x.conf'")
    var = box.succeed("ls /sys/firmware/efi/efivars/ | grep '^LoaderEntryDefault-'").strip()
    box.fail("runuser -u agent -- sh -c 'echo x > /sys/firmware/efi/efivars/" + var + "'")
    box.fail("runuser -u agent -- rm -f /sys/firmware/efi/efivars/" + var)
    box.fail("runuser -u agent -- ln -sfn /tmp /nix/var/nix/profiles/system")
    box.fail("runuser -u agent -- systemctl start agentos-boot-default.service")
    box.fail("runuser -u agent -- sh -c 'command -v agentos-boot-default'")
    assert boot_default() == before, "D5: the boot default changed"
  '';
}
