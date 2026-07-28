# Agent OS — v0.1 scaffold

A computer whose shell is an agent, not a desktop. Boot → autologin → you're
talking to it. Files are markdown memories (path = meaning). The agent can
rewrite the machine's own config and rebuild.

Vision + spec: `~/jarvis-sync/VISION-agent-os-2026-07-27.md`.

## What's here (spec-independent scaffold — started 2026-07-27)
```
flake.nix                 the whole machine + a `vm` output to test before hardware
configuration.nix         boring base: user, network, toolbox, flakes on, no desktop
modules/agent-shell.nix   THE module — autologin tty1 → exec the agent, no bash prompt
bin/agent-shell           login program: seeds memory tree, boot banner, hands off to brain
home/memory/              seed of the markdown-memory home tree
```

## Prove it boots-and-talks (in a VM, no Dell needed yet)
```sh
cd ~/agent-os
nix build .#vm
./result/bin/run-agent-os-vm      # boots into the agent shell in a window
```
> **Flakes only see git-tracked files** — after editing any file, `git add` it or `nix build`
> silently uses the old version. On WSL2: the VM window needs WSLg, and `/dev/kvm` makes it
> fast (without KVM it runs under TCG emulation — works, just slow). `nix flake check` needs
> neither and validates the whole config first.
The VM comes up, autologins tty1, seeds `~/memory`, and drops into the brain
(or the placeholder memory-REPL if no brain is installed yet).

## Brain
- **v0.1 (now):** local + sovereign — a quantized Qwen 2.5 (ollama, loopback-only)
  IS the login program. No cloud brain on the image; `BRAIN=brain-ollama` (default,
  set in `modules/brain.nix`). On first boot, before `setup-brain.sh` pulls the
  model, the login floor is a zero-deps memory-REPL — never a crash loop.
- **Phase 1.5+:** a larger judgment-lane model (14B) is pullable on the 32GB Dell.
  `setup-brain.sh` installs/pulls the weights during the provisioning window.

## Then, onto the Dell Latitude 5440 (32GB, 13th-gen Intel, Iris Xe)
Confirmed target 2026-07-27. Well-supported hardware; 32GB makes the local-model floor real.
The 5440-specific enablement (Intel microcode, Iris Xe, wifi firmware, thermald) **and** the boot
layout (label-based `fileSystems` + initrd nvme/xhci/thunderbolt modules) are already in
`configuration.nix` — so there is **no `nixos-generate-config` and no per-machine edit**. The
installer just creates two labeled partitions (`nixos` ext4 root + `BOOT` vfat ESP) and installs
the flake; `configuration.nix` mounts them by label.

> ⚠️ **The block below ERASES the target disk.** Back up anything off the Windows 11 install
> first. The `lsblk` gate is there so you confirm the exact device before anything destructive
> runs — do not skip it. The partition **labels are load-bearing** (the by-label mounts depend
> on them): type `BOOT` and `nixos` exactly, case-sensitive.

```sh
# ── 0. SEE THE DISK. Confirm the target before anything destructive. ──
lsblk -o NAME,SIZE,TYPE,MODEL,MOUNTPOINT
# Expect ONE internal NVMe (Dell/SK-Hynix/Micron SSD) as nvme0n1, plus the USB you booted from.
# CONFIRM the internal SSD is /dev/nvme0n1 and that erasing it is OK.
# If the internal disk is NOT nvme0n1, STOP.
DISK=/dev/nvme0n1     # ← from lsblk above; change ONLY if lsblk shows otherwise

# ── 1. Partition: GPT · 512MB ESP (label BOOT) · rest ext4 root (label nixos) ──
parted -s "$DISK" -- mklabel gpt \
  mkpart ESP fat32 1MiB 513MiB set 1 esp on \
  mkpart primary 513MiB 100%
P="${DISK}p"                       # nvme → nvme0n1p1 / p2 (a SATA disk would be sda1/2, no "p")
mkfs.fat -F32 -n BOOT "${P}1"      # label MUST be exactly BOOT   (case-sensitive)
mkfs.ext4 -L nixos   "${P}2"       # label MUST be exactly nixos  (case-sensitive)

# ── 2. Mount + install straight from the flake (no clone, no hw-config, no edit) ──
mount "${P}2" /mnt && mkdir -p /mnt/boot && mount "${P}1" /mnt/boot
nixos-install --no-root-passwd --flake github:dillondevoe/agent-os#agentos

# ── 3. Reboot into Agent OS ──
reboot
```

First boot lands in the agent shell → **memory-REPL** (no model yet; the login floor never
crash-loops). Then pull the local brain over the provisioning window and seal the box:
```sh
setup-brain.sh        # pulls the local model (qwen2.5:7b-instruct) during provisioning
sudo nixos-rebuild switch --flake github:dillondevoe/agent-os#agentos-sealed
reboot                # sealed: egress is nixpkgs-only (+ timesyncd); the local model IS the shell
```

## Honest status
v0.1 is a **single-user sovereign demo box**: local brain, default-deny egress
(the clean-room seal), label-based bare-metal boot — no per-machine hardware-config.
The security-kernel hardening (autonomous agent + passwordless root = the real work;
e.g. `sudo` still defeats the egress seal today) is roadmap, tracked as Phase-2 gates,
NOT v0.1.
