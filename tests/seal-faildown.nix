# tests/seal-faildown.nix — nixosTest for the seal-failloud fail-down loop on the SEALED
# variant. Boots agentos-sealed under the NixOS test driver, whose control channel is the
# serial backdoor — so dropping eth0 / masking NetworkManager INSIDE the guest does not cut
# the driver (a manual QEMU-over-SSH test would sever its own line; this is why the seal's
# fail-closed network-kill is testable at all). Proves the end-to-end invariant Rabbot asked
# for on PR#23:
#   1. HEALTHY sealed boot writes the positive token /run/agentos-sealed-ok (agent allowed),
#      wall up, NM live, no down-marker; the login guard is polarised on the token; dhcpcd
#      genuinely absent (NM is the only link manager, useDHCP=mkDefault false).
#   2. token re-asserts across a real reboot (the boot path writes it every boot, before getty).
#   3. FAIL-DOWN: wall torn down at runtime + the identical boot check re-run => FAIL-CLOSED:
#      positive token withdrawn (agent blocked), down-marker written, unit failed (loud),
#      egress dropped (NM runtime-masked + every non-lo link down), OnFailure backstop ran.
#   4. RECOVER: restore wall + unmask NM + re-run => token restored, marker cleared, online.
#
# NOT covered here (by design, tracked elsewhere — stated so no coverage is claimed silently):
#   - the tty1 red-banner VISUAL render under the boot splash (display surface — Dell HW item,
#     shared with #22's render caveat). This test asserts the token/marker STATE the guard keys
#     on, not the pixels.
#   - the literal "seal-check unit never starts at all" cold path. That is closed by the
#     inverted-polarity static invariant (token written ONLY on the proven-up path => absent =>
#     agent blocked); re-running the identical ExecStart under systemd against a genuinely-down
#     wall (leg 3) is a faithful proxy for the down-at-boot enforcement.
{ pkgs, baseModules }:
pkgs.testers.runNixOSTest {
  name = "agentos-seal-faildown";
  nodes.machine = {
    imports = baseModules ++ [ { agentos.cleanRoom.sealed = true; } ];
    virtualisation.memorySize = 2048;
    virtualisation.cores = 2;
  };
  testScript = ''
    # start with allow_reboot=True (drops QEMU's -no-reboot) so subtest 2's
    # machine.reboot() actually restarts the guest and the driver reconnects,
    # instead of QEMU exiting on the guest restart => "Shell disconnected".
    machine.start(allow_reboot=True)
    machine.wait_for_unit("multi-user.target")

    with subtest("1. healthy sealed boot: positive token asserted, wall up, NM live"):
        machine.wait_for_unit("agentos-seal-check.service")
        machine.wait_for_unit("NetworkManager.service")
        machine.succeed("test -e /run/agentos-sealed-ok")
        machine.succeed("test ! -e /run/agentos-unsealed")
        machine.succeed("nft list table inet agentos_egress >/dev/null")
        # the agent gate is polarised on the POSITIVE token (fail-closed on absence)
        machine.succeed("grep -q -- '! -e /run/agentos-sealed-ok' /etc/profile")
        # dhcpcd genuinely off — NM is the only link manager
        machine.succeed("! systemctl list-unit-files --no-legend | grep -q '^dhcpcd'")

    with subtest("2. token re-asserts on a real reboot (written every boot, before getty)"):
        machine.reboot()
        machine.wait_for_unit("multi-user.target")
        machine.wait_for_unit("agentos-seal-check.service")
        machine.succeed("test -e /run/agentos-sealed-ok")
        machine.succeed("test ! -e /run/agentos-unsealed")

    with subtest("3. fail-down: wall torn down + boot check re-run => FAIL-CLOSED + egress dropped"):
        machine.succeed("systemctl stop nftables.service || true")
        machine.succeed("nft delete table inet agentos_egress 2>/dev/null || true")
        machine.fail("nft list table inet agentos_egress >/dev/null 2>&1")
        # re-run the identical boot check against the genuinely-down wall (exits 1 => OnFailure)
        machine.succeed("systemctl restart agentos-seal-check.service || true")
        machine.succeed("systemctl is-failed agentos-seal-check.service")
        # FAIL-CLOSED: positive token withdrawn => the tty1 guard would block the agent
        machine.succeed("test ! -e /run/agentos-sealed-ok")
        # loud: down-marker written for the banner
        machine.succeed("test -e /run/agentos-unsealed")
        # the net-kill lands via the async OnFailure backstop (Type=oneshot +
        # RemainAfterExit => 'active' means enforce_offline ran to completion: NM
        # masked + links down). Wait for it FIRST, then assert the effects it produced
        # — asserting before it settles is a race, not a real fail-open.
        machine.wait_until_succeeds("systemctl is-active agentos-seal-failclose.service", timeout=30)
        # diagnostic (read-only): the exact `LoadState` label for a *runtime* mask is
        # version-specific, so we assert the ground truth below, not a label. Print state
        # so any failure here is self-explaining in the log.
        print(machine.execute("systemctl show -p LoadState -p UnitFileState -p ActiveState NetworkManager.service")[1])
        print(machine.execute("ls -la /run/systemd/system/NetworkManager.service 2>&1")[1])
        # egress dropped: NM runtime-masked. The version-agnostic ground truth of a
        # `mask --runtime` is the /run unit symlink pointing at /dev/null (mask requests
        # an async daemon-reload, so poll rather than assert once).
        machine.wait_until_succeeds(
            "test \"$(readlink /run/systemd/system/NetworkManager.service 2>/dev/null)\" = /dev/null",
            timeout=30,
        )
        # and it cannot be brought back up
        machine.succeed("! systemctl is-active --quiet NetworkManager.service")
        # ... and every non-lo link has lost its UP flag
        machine.wait_until_succeeds(
            "for i in $(ls /sys/class/net | grep -vx lo); do "
            "ip -o link show \"$i\" | grep -qw UP && exit 1; done; true",
            timeout=30
        )

    with subtest("4. recover: restore wall + unmask NM + re-run => token restored, online"):
        machine.succeed("systemctl unmask --runtime NetworkManager.service")
        machine.succeed("systemctl start nftables.service")
        machine.succeed("nft list table inet agentos_egress >/dev/null")
        machine.succeed("systemctl reset-failed agentos-seal-check.service")
        machine.succeed("systemctl restart agentos-seal-check.service")
        machine.succeed("test -e /run/agentos-sealed-ok")
        machine.succeed("test ! -e /run/agentos-unsealed")
        machine.succeed("systemctl start NetworkManager.service")
        for i in machine.succeed("ls /sys/class/net | grep -vx lo").split():
            machine.succeed(f"ip link set dev {i} up || true")
        machine.wait_for_unit("NetworkManager.service")
  '';
}
