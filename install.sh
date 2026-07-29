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

echo ">>> Partitioning $DISK (GPT: 512M ESP 'BOOT' + rest ext4 'nixos')..."
parted -s "$DISK" -- mklabel gpt \
  mkpart ESP fat32 1MiB 512MiB set 1 esp on \
  mkpart primary 512MiB 100%
partprobe "$DISK" 2>/dev/null || true
sleep 2

echo ">>> Formatting (pulling mkfs tools via nix-shell)..."
nix-shell -p dosfstools e2fsprogs --run "
  set -e
  mkfs.fat -F32 -n BOOT ${P}1
  mkfs.ext4 -F -L nixos  ${P}2
"

echo ">>> Mounting..."
mount "${P}2" /mnt
mkdir -p /mnt/boot
mount "${P}1" /mnt/boot

echo ">>> Installing Agent OS from $FLAKE (fetch + build, a few minutes)..."
nixos-install --no-root-passwd --flake "$FLAKE"

echo "=============================================================="
echo " ✅ DONE. Run:  reboot"
echo " Then boot the INTERNAL disk (F12 → Linux Boot Manager / SK hynix,"
echo " NOT the USB). You should land at Agent OS — a  you ›  prompt."
echo "=============================================================="
