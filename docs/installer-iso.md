# Live installer ISO (M1a)

A bootable image that gets a person from "spare machine" to "run the installer" without
first installing NixOS by hand. It is the smallest useful slice: a text console, a banner,
and the repo's `install.sh` on PATH as `agentos-install`. No desktop, no disk TUI, no
signing yet.

## What is in it, what is not

- In: NetworkManager (`nmtui` for wifi), the disk tools `install.sh` uses, `git`, `curl`,
  a README at `/etc/agent-os/README.txt`.
- Not in: model weights, secrets, keys, any module shared with the sealed or open systems.
  `modules/installer-iso.nix` imports only the upstream minimal-ISO profile, so nothing from
  the agent configurations can leak into the image, and the image cannot perturb them.
- The installer inside is `install.sh` copied into the store unmodified. Overrides
  (`DISK`, `VARIANT`, `FLAKE_REV`, `TS_AUTHKEY`, ...) pass through unchanged.

## Build

    nix build .#iso
    ls -l result/iso/

The output is `agent-os-installer-x86_64-linux.iso` (volume ID `AGENTOS_INSTALL`).
Expect a few GB of downloads on first build.

## Verify before writing to a USB stick

The CI test (`packages.x86_64-linux.test-installer-iso`, run by `vm-tests.yml`) boots the
same module set under the NixOS test driver and asserts: the banner is in `/etc/issue`,
`agentos-install` and `nmtui` are on PATH, the README exists, and the wrapper refuses to
run as non-root. It does not boot the ISO file itself.

To boot the actual ISO in QEMU:

    qemu-system-x86_64 -m 2048 -enable-kvm -cdrom result/iso/*.iso

You should see the banner at the login prompt. Log in as `nixos` (no password) and run
`agentos-install --help` or `sudo agentos-install` against a scratch virtual disk.

## Write to USB

    sudo dd if=result/iso/*.iso of=/dev/sdX bs=4M status=progress oflag=sync

Replace `/dev/sdX` with the stick, not a system disk. Check with `lsblk` first.

## Later slices

Desktop on the live image, disko/LUKS partitioning, a TUI installer, image signing
(cosign keyless in GitHub Actions plus checksums). Tracked in the v1 plan.
