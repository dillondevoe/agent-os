# tests/installer-iso.nix — boots the installer's NixOS configuration (modules/installer-iso.nix)
# under the test driver and asserts what a person at the console would see: the banner, and
# `agentos-install` resolving on PATH to the unmodified install.sh. It does NOT boot the ISO
# file itself (that needs the squashfs/ISO build, ~GBs; see docs/installer-iso.md for the
# manual QEMU check) — it boots the same module set.
{ pkgs, installerModule }:

pkgs.testers.runNixOSTest {
  name = "installer-iso";
  # the installer profile sets nixpkgs.overlays, which the test driver's read-only pkgs forbids
  node.pkgsReadOnly = false;
  nodes.machine = { ... }: {
    imports = [ installerModule ];
    # The test driver supplies its own boot/disk plumbing; the ISO-only bits are inert here.
    virtualisation.memorySize = 2048;
  };
  testScript = ''
    machine.wait_for_unit("multi-user.target")
    machine.succeed("command -v agentos-install")
    machine.succeed("command -v nmtui")
    machine.succeed("grep -q 'Agent OS installer' /etc/issue")
    machine.succeed("test -s /etc/agent-os/README.txt")
    # the wrapper refuses non-root and runs install.sh as shipped for root
    machine.succeed("grep -q 'install.sh' $(command -v agentos-install)")
    out = machine.execute("su nobody -s /bin/sh -c \"$(command -v agentos-install)\" 2>&1")
    print("WRAPPER-AS-NOBODY:", out)
    assert "must run as root" in out[1], out
    # install.sh runs `nixos-install --flake` (and nix-shell, which resolves <nixpkgs> through the
    # flake registry). With flakes off the install dies after partitioning, as the first real
    # install from this ISO did (2026-10-09, NixOS deploy VM on dellon).
    machine.succeed("grep -Eq '^experimental-features *=.*\\bflakes\\b' /etc/nix/nix.conf")
    # the disk tools install.sh formats with are on PATH, so it need not fetch them first
    machine.succeed("command -v mkfs.fat && command -v mkfs.ext4")
  '';
}
