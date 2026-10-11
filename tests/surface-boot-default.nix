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
#       into it ONCE, by itself, before the agent ever gets a session (read from that boot's journal,
#       with a positive control on the next boot).
#   D2  after that reboot: the chosen variant is running, the questions are not asked again, no new
#       generation exists, and the boot default is the entry of the running generation.
#   D3  a later change (root edits the store) takes effect at the next boot; choosing the image's own
#       desktop clears the default.
#   D4  a boot default that names an entry which does not exist boots the image's own desktop, and that
#       same boot puts the chosen one back.
#   D5  the agent cannot set the boot default, write the ESP, or touch the system profile.
#   D6  A REBUILD IN THE MIDDLE. A second generation is installed with `switch-to-configuration boot`
#       (the case with no activation). The boot default moves to the NEW generation's entry for the
#       chosen desktop at install time, and the next boot runs the new generation: the choice follows
#       updates instead of pinning the box to the generation it was made on.
#   D7  running an OLD generation (its entry picked by hand) does not pin the box to it either.
#   D8  a later generation with first login turned OFF clears the default when its boot loader is
#       installed (the old generation's entry still exists; left selected it would boot for ever).
{ pkgs }:

pkgs.testers.runNixOSTest {
  name = "agentos-surface-boot-default";

  nodes.box = { config, lib, extendModules, ... }:
    let
      # Two more systems in the store. gen2 (D6): this same node plus a marker file. gen3 (D8): the
      # same with FIRST LOGIN TURNED OFF. The flag stops either from depending on further ones.
      gen2 = (extendModules { modules = [ { test.later = true; environment.etc."gen2".text = "2\n"; } ]; }).config.system.build.toplevel;
      gen3 = (extendModules { modules = [ { test.later = true; environment.etc."gen3".text = "3\n";
                                          agentos.firstLogin.enable = lib.mkForce false; } ]; }).config.system.build.toplevel;
    in
    {
      imports = [ ../modules/surface-options.nix ../modules/surface-choice.nix ];
      options.test.later = lib.mkOption { type = lib.types.bool; default = false; };
      config = {
        virtualisation.useBootLoader = true;
        virtualisation.useEFIBoot = true;
        boot.loader.systemd-boot.enable = true;
        boot.loader.efi.canTouchEfiVariables = true;
        boot.loader.timeout = 1;
        agentos.desktop = "none";
        agentos.firstLogin.enable = lib.mkDefault true;
        agentos.firstLogin.desktops = [ "none" "sway" "hyprland" ];
        users.users.agent = { isNormalUser = true; };
        users.users.operator = { isNormalUser = true; shell = config.agentos.surfaceInternal.shellPkg; };
        services.getty.autologinUser = "agent";
        environment.systemPackages = with pkgs; [ coreutils util-linux procps ];
        virtualisation.memorySize = 2048;
        system.extraDependencies = lib.mkIf (!config.test.later) [ gen2 gen3 ];
        environment.etc."gen2-path" = lib.mkIf (!config.test.later) { text = "${gen2}\n"; };
        environment.etc."gen3-path" = lib.mkIf (!config.test.later) { text = "${gen3}\n"; };
      };
    };

  testScript = ''
    STORE = "/var/lib/agent-os/surface.toml"

    def running():
        # surface-options sets AGENTOS_DESKTOP for every desktop but hyprland.
        out = box.succeed("grep -o 'AGENTOS_DESKTOP=\"[a-z]*\"' /etc/set-environment || echo hyprland").strip()
        return out.replace('AGENTOS_DESKTOP=', "").strip('"')

    def boot_default():
        # What LoaderEntryDefault names ("" when the variable is not set). The variable only: bootctl's
        # "Default Entry" is the EFFECTIVE default, which is loader.conf's when the variable is unset.
        return box.succeed("f=$(ls /sys/firmware/efi/efivars/ | grep '^LoaderEntryDefault-' || true); "
                           "[ -z \"$f\" ] || tail -c +5 /sys/firmware/efi/efivars/$f | tr -d '\\000'").strip()

    def entry_of(name, generation="/nix/var/nix/profiles/system"):
        # Looked up by what the entry boots. The file name is a hash of the entry's content (which
        # includes the generation's build date, so generation 1's differs between the image build and
        # the first install inside the VM; after that an old generation's entry keeps its name).
        spec = box.succeed("readlink -f " + generation + "/specialisation/" + name).strip()
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
    # ...proven from the journal of the boot that asked: the agent's getty never started there and no
    # session was opened for the agent. (The same two greps succeed on THIS boot: see D2.)
    box.succeed("journalctl -b -1 --no-pager -n 1 | grep -q .")           # the previous boot's journal is there
    box.fail("journalctl -b -1 --no-pager -o cat -u getty@tty1.service | grep -q .")
    box.fail("journalctl -b -1 --no-pager | grep -q 'session opened for user agent'")
    box.succeed("journalctl -b -1 --no-pager -t agos-choice | grep -q 'rebooting into desktop=sway'")

    # D2 — the chosen variant is running; nothing was built; the default is this generation's entry.
    assert running() == "sway", "D2: booted " + running()
    assert box.succeed("cat " + STORE) == 'schema = 1\ndesktop = "sway"\nterminal = "kitty"\nshell = "bash"\n', "D2: store"
    assert generations() == gens, "D2: a generation was created"
    assert entry_of("sway") in boot_default(), "D2: the boot default is not the sway entry: " + boot_default()
    box.wait_for_unit("getty@tty1.service")
    box.wait_until_succeeds("pgrep -u agent", timeout=60)
    # Positive control for the D1 proof: on a boot where the agent does get its session, both show.
    box.wait_until_succeeds("journalctl -b --no-pager | grep -q 'session opened for user agent'", timeout=60)
    box.succeed("journalctl -b --no-pager -o cat -u getty@tty1.service | grep -q .")
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
    assert before != "", "D5: precondition: a default is set"
    var = box.succeed("ls /sys/firmware/efi/efivars/ | grep '^LoaderEntryDefault-'").strip()
    box.fail("runuser -u agent -- sh -c 'echo x > /sys/firmware/efi/efivars/" + var + "'")
    box.fail("runuser -u agent -- rm -f /sys/firmware/efi/efivars/" + var)
    box.fail("runuser -u agent -- ln -sfn /tmp /nix/var/nix/profiles/system")
    box.fail("runuser -u agent -- systemctl start agentos-boot-default.service")
    box.fail("runuser -u agent -- sh -c 'command -v agentos-boot-default'")
    assert boot_default() == before, "D5: the boot default changed"

    # D6 — a rebuild in the middle, installed with `boot` (no activation): the default must move to
    # the NEW generation's entry for the chosen desktop when the boot loader is installed.
    # Read from the PARENT system: every variant computes its own "second system", and the one a
    # specialisation computes is a specialisation of a specialisation, with no variants of its own.
    gen2 = box.succeed("cat /nix/var/nix/profiles/system/etc/gen2-path").strip()
    box.fail("test -e /etc/gen2")
    box.succeed(f"test -e {gen2}/specialisation/sway && test -e {gen2}/specialisation/hyprland")
    before_install = boot_default()
    box.succeed(f"ln -sfn {gen2} /nix/var/nix/profiles/system-2-link && ln -sfn system-2-link /nix/var/nix/profiles/system")
    box.succeed(f"{gen2}/bin/switch-to-configuration boot")
    new_entry = entry_of("sway")
    old_entry = entry_of("sway", "/nix/var/nix/profiles/system-1-link")     # the old generation is still in the menu
    assert new_entry != old_entry, "D6: the new generation has no entry of its own for sway"
    assert new_entry not in before_install, "D6: the default already named the new entry before the install"
    assert new_entry in boot_default(), "D6: installing the boot loader did not move the default to the new generation: " + boot_default()
    reboot()
    assert running() == "sway", "D6: " + running()
    box.succeed("test -e /etc/gen2")                                # the NEW generation is what booted
    assert new_entry in boot_default(), "D6: the boot moved the default off the new generation"

    # D7 — running the old generation (its entry picked once, by hand) does not pin the box to it.
    box.succeed("bootctl set-oneshot " + old_entry)
    reboot()
    box.fail("test -e /etc/gen2")                                   # the old generation, this once
    assert running() == "sway", "D7: " + running()
    assert new_entry in boot_default(), "D7: running the old generation re-selected its own entry (pinning)"
    reboot()
    box.succeed("test -e /etc/gen2")

    # D8 — A LATER GENERATION WITH FIRST LOGIN TURNED OFF. Nothing in it will ever re-select a desktop,
    # and the default still names generation 2's sway entry, which still exists. Left there, the box
    # would boot generation 2 for ever and generation 3 would never run. Installing its boot loader
    # must clear the default; the next boot is generation 3, on its own desktop.
    gen3 = box.succeed("cat /nix/var/nix/profiles/system-1-link/etc/gen3-path").strip()
    gen2_sway = entry_of("sway")
    assert gen2_sway in boot_default(), "D8: precondition: the default names generation 2's sway entry"
    box.succeed(f"ln -sfn {gen3} /nix/var/nix/profiles/system-3-link && ln -sfn system-3-link /nix/var/nix/profiles/system")
    box.succeed(f"{gen3}/bin/switch-to-configuration boot")
    box.succeed("test -e /boot/loader/entries/" + gen2_sway)       # the trap: the old entry is still there
    assert boot_default() == "", "D8: a generation with first login off left the default on an older generation: " + boot_default()
    reboot()
    box.succeed("test -e /etc/gen3")                                # generation 3 is what booted
    assert running() == "none", "D8: " + running()
    box.fail("systemctl cat agentos-boot-default.service")          # and it has none of the machinery
  '';
}
