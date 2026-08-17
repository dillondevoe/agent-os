# tests/watchdog-armed.nix — the runtime witness that the hardware watchdog is ARMED.
#
# WHY THIS EXISTS (2026-08-16). On 2026-08-11 the Dell hard-hung and stayed hung, because
# nothing was watching it. PR #102 fixed that by setting, in configuration.nix:
#
#   systemd.settings.Manager.RuntimeWatchdogSec = "120s";
#   systemd.settings.Manager.RebootWatchdogSec  = "10min";
#
# and for the four hours between merging that and writing this file, the word "watchdog"
# appeared in exactly two places in the tree: those two config files. Nothing asserted it.
# A refactor of the systemd settings block, a variant that stops importing configuration.nix,
# a rename of the option (it HAS been renamed once — `systemd.watchdog.*` is the older spelling
# and the shim is deprecated) — any of these removes the protection and every gate in this repo
# stays green, because no gate was ever wired to notice.
#
# That is docs/cancelled-boundaries.md's class exactly, aimed at the mechanism whose entire job
# is to recover the box when nobody is looking. The failure mode is silent by construction: a
# watchdog that is not armed behaves identically to one that is, right up until the moment it
# was supposed to matter, at which point the box is unreachable and cannot tell you.
#
# WHAT THIS ASSERTS, in the order that the evidence actually strengthens:
#   1. the SETTING reaches the running system   — systemd's RuntimeWatchdogUSec is 2min
#   2. the DEVICE exists and the kernel driver bound it
#   3. systemd OPENED it and is petting it     — this is the leg that matters
#
# Legs 1 and 3 are different facts and it is worth being explicit about why both are here.
# `RuntimeWatchdogUSec=2min` is systemd's *intent*: it reports the configured value whether or
# not any device was successfully opened. A box with no watchdog hardware, or a driver that
# failed to bind, reports the identical string. Asserting only leg 1 would produce a test that
# passes on a machine with no watchdog at all — a guard that permits everything, which this
# repo's own ledger names as indistinguishable from a tool that never ran.
#
# The guest is given a real emulated watchdog (`-device i6300esb`, driver `i6300esb`) precisely
# so leg 3 has something to be true about. Without it this test could only ever check leg 1.
#
# NOT covered here, stated so no coverage is claimed silently:
#   - that the watchdog actually REBOOTS a wedged machine. Proving that requires genuinely
#     hanging the kernel and waiting for the hardware to fire. It is testable in principle
#     (`-watchdog-action reset` plus a deliberate hang) and it is not done here: this test is a
#     regression guard against the setting silently disappearing, which is the failure that
#     actually happened. The fire path is an open item, and on the real Dell it is gated on
#     having an out-of-band recovery route first — currently WoL is enabled on the NIC but the
#     ethernet cable is unplugged, so a failed test would leave the box down until someone
#     walks to it. Do not run a fire test on hardware you cannot wake.
#   - the 120s VALUE as a policy choice. 120s is deliberate (a shorter timeout reboots the box
#     during heavy nix builds, and a watchdog people disable is worse than no watchdog). The
#     test pins the configured value so a silent change is visible in a diff; it does not
#     claim 120 is correct, only that it is what was chosen.
{ pkgs, baseModules }:
pkgs.testers.runNixOSTest {
  name = "agentos-watchdog-armed";
  nodes.machine = {
    imports = baseModules;
    virtualisation.memorySize = 1024;
    virtualisation.cores = 1;
    # Give the guest a watchdog to arm. Without this the box has no /dev/watchdog and leg 3
    # cannot be tested at all — which is the difference between this test and a version of it
    # that would pass on any machine.
    virtualisation.qemu.options = [ "-device i6300esb" ];
    boot.kernelModules = [ "i6300esb" ];
  };
  testScript = ''
    machine.start()
    machine.wait_for_unit("multi-user.target")

    with subtest("1. the setting reached the running system"):
        # This is systemd's INTENT, not proof of arming — see the header. Asserted first
        # because if this fails, legs 2 and 3 cannot possibly hold and the diagnosis is
        # "the config did not reach the box", which is a different bug from "the device
        # did not open".
        rt = machine.succeed("systemctl show -p RuntimeWatchdogUSec --value").strip()
        assert rt == "2min", f"RuntimeWatchdogUSec is {rt!r}, expected '2min'"
        rb = machine.succeed("systemctl show -p RebootWatchdogUSec --value").strip()
        assert rb == "10min", f"RebootWatchdogUSec is {rb!r}, expected '10min'"

    with subtest("2. a watchdog device exists and a driver bound it"):
        machine.succeed("test -c /dev/watchdog0")
        ident = machine.succeed("cat /sys/class/watchdog/watchdog0/identity").strip()
        print(f"watchdog driver: {ident}")

    with subtest("3. systemd opened it and is petting it — the leg that matters"):
        # The kernel exposes whether the device is OPEN. 'active' here means a userspace
        # process holds it and the countdown is live; if systemd had failed to open it,
        # leg 1 would still have passed and this is the only assertion that would notice.
        state = machine.succeed("cat /sys/class/watchdog/watchdog0/state").strip()
        assert state == "active", f"watchdog state is {state!r}, expected 'active' (not armed)"

        # The hardware timeout systemd negotiated, in seconds. Belt-and-braces against a
        # device that is open but running on a default timeout unrelated to our setting.
        timeout = machine.succeed("cat /sys/class/watchdog/watchdog0/timeout").strip()
        assert timeout == "120", f"hardware timeout is {timeout!r}s, expected '120'"

        # systemd says so in its own words. Kept as a THIRD source rather than the only one:
        # a journal grep that finds nothing is indistinguishable from a journal that could not
        # be read, so the /sys assertions above are the ground truth and this is corroboration.
        machine.succeed("journalctl -b | grep -q 'Using hardware watchdog'")
        print(machine.succeed("journalctl -b | grep -i 'watchdog' | head -5"))
  '';
}
