#!/usr/bin/env bash
# Agent OS v0.1 — Dell installer. One-shot: partition → format → mount → install.
# Run from the NixOS-minimal installer shell:
#   curl -sL https://raw.githubusercontent.com/dillondevoe/agent-os/main/install.sh | sudo bash
#
# ⚠️ ERASES THE TARGET DISK. Shows you the disk and requires you to type YES first.
set -euo pipefail

FLAKE="github:dillondevoe/agent-os#agentos"
DISK="${DISK:-/dev/nvme0n1}"   # override with:  DISK=/dev/sdX curl ... | sudo DISK=/dev/sdX bash

echo "=============================================================="
echo " Agent OS installer — target disk: $DISK"
echo "=============================================================="
lsblk -o NAME,SIZE,TYPE,MODEL,MOUNTPOINT
echo "--------------------------------------------------------------"
echo "This will ERASE $DISK completely and install Agent OS on it."
echo "The USB you booted from is separate and will NOT be touched."
printf "Type YES (caps) to wipe %s and install: " "$DISK"
read -r CONFIRM < /dev/tty
[ "$CONFIRM" = "YES" ] || { echo "Aborted — nothing changed."; exit 1; }

# Partition suffix: nvme0n1 -> nvme0n1p1/p2 ; sda -> sda1/2
case "$DISK" in *nvme*|*mmcblk*) P="${DISK}p" ;; *) P="$DISK" ;; esac

echo ">>> Clearing any leftover mounts/partitions from a prior attempt..."
umount -R /mnt 2>/dev/null || true
umount "${DISK}"* 2>/dev/null || true
wipefs -a "$DISK" 2>/dev/null || true

echo ">>> Partitioning $DISK (GPT: 512M ESP 'BOOT' + rest ext4 'nixos')..."
parted -s "$DISK" -- mklabel gpt \
  mkpart ESP fat32 1MiB 512MiB set 1 esp on \
  mkpart primary 512MiB 100%
partprobe "$DISK" 2>/dev/null || true
sleep 2

echo ">>> Formatting (pulling mkfs tools via nix-shell)..."
wipefs -a "${P}1" "${P}2" 2>/dev/null || true   # kill stale per-partition signatures (FAT leftovers)
nix-shell -p dosfstools e2fsprogs --run "
  set -e
  mkfs.fat -F32 -n BOOT ${P}1
  mkfs.ext4 -F -L nixos  ${P}2
"
udevadm settle 2>/dev/null || true

echo ">>> Mounting (explicit fs types — auto-detect can trip on stale FAT sigs)..."
mount -t ext4 "${P}2" /mnt
mkdir -p /mnt/boot
mount -t vfat "${P}1" /mnt/boot

echo ">>> Installing Agent OS from $FLAKE (fetch + build, a few minutes)..."
nixos-install --no-root-passwd --flake "$FLAKE"

# Network on first boot is NOT carried from the installer. A copied NetworkManager profile
# produces a DEAD connection in the installed system — it comes up "connected" but never
# routes, which masks the real no-network state AND fights the boot-time brain pull. Instead
# the installed system's agent-shell offers an nmtui wifi picker on first boot and pulls the
# brain in-shell once genuinely online (see bin/agent-shell `_net_ok`). Ethernet just DHCPs.
echo ">>> Network: set up on FIRST BOOT (agent-shell offers a wifi picker), not carried"
echo "    from the installer — a carried wifi profile comes up dead. Ethernet auto-DHCPs."

echo "=============================================================="
echo " ✅ DONE. Run:  reboot"
echo " Then boot the INTERNAL disk (F12 → Linux Boot Manager, NOT the USB)."
echo " First boot: it gets online (wifi: it offers a picker; ethernet: auto), installs"
echo " its local brain, then you're talking to it at a  you ›  prompt."
echo ""
echo " ⚠️  If boot hangs on 'waiting for /dev/disk/by-label/nixos': your"
echo "     machine hides the NVMe behind Intel VMD. Fix: reboot → BIOS (F2)"
echo "     → SATA Operation → set to AHCI → save → boot again. (Or the initrd"
echo "     carries the vmd driver, so most boxes just work.)"
echo "=============================================================="
