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

# --- CLEAN_NVRAM (opt-in) — snapshot stale Agent OS UEFI entries BEFORE install --------------
# The whole-disk wipe above clears on-DISK loaders but NEVER motherboard UEFI NVRAM, so each
# reinstall's firmware "Linux Boot Manager" entry piles up ("getting insane"). Opt-in
# CLEAN_NVRAM=1 prunes ONLY our own entries, and ONLY those that already exist NOW — snapshot
# them here, before nixos-install registers a FRESH one, so the new entry is never a candidate.
# Default OFF: the whole-drive wipe is the right default; a dual-booter must never lose another
# OS's entry.  Enable with:  CLEAN_NVRAM=1 curl ... | sudo CLEAN_NVRAM=1 bash
CLEAN_NVRAM="${CLEAN_NVRAM:-0}"
NVRAM_LABEL_RE='Linux Boot Manager|NixOS|Agent OS'
# efibootmgr may be absent from the minimal-installer PATH — belt: fall back to nix-shell. Every
# arg used below is a simple token (-v / -b / XXXX / -B), so joining "$*" into --run is safe.
efibm() { if command -v efibootmgr >/dev/null 2>&1; then efibootmgr "$@"; else nix-shell -p efibootmgr --run "efibootmgr $*"; fi; }
STALE_NVRAM_BOOTNUMS=""
if [ "$CLEAN_NVRAM" = 1 ]; then
  echo ">>> CLEAN_NVRAM=1: recording pre-existing Agent OS/NixOS UEFI entries (pruned after install)..."
  # efibm (no -v): one line per entry, "BootXXXX* Label". Keep bootnums whose LABEL matches ours.
  # 2>/dev/null + ||true: a BIOS/legacy boot (no efivars) yields nothing → the feature no-ops.
  STALE_NVRAM_BOOTNUMS="$(efibm 2>/dev/null | grep -E '^Boot[0-9A-Fa-f]{4}[* ]' | grep -E "$NVRAM_LABEL_RE" | sed -E 's/^Boot([0-9A-Fa-f]{4}).*/\1/')" || true
  if [ -n "$STALE_NVRAM_BOOTNUMS" ]; then
    echo "    flagged (pre-existing): $(echo $STALE_NVRAM_BOOTNUMS | tr '\n' ' ')"
  else
    echo "    none found — nothing to prune."
  fi
fi

echo ">>> Installing Agent OS from $FLAKE (fetch + build, a few minutes)..."
nixos-install --no-root-passwd --flake "$FLAKE"

# --- CLEAN_NVRAM (opt-in) — prune the pre-recorded stale entries AFTER install ---------------
# nixos-install just registered a FRESH "Linux Boot Manager" entry via bootctl. Delete exactly the
# entries snapshotted BEFORE it (never the new one), re-verifying each STILL matches our label.
# Print every deletion and gate on YES (same idiom as the disk wipe): nothing is removed silently,
# and only OUR labels are ever eligible. Deletions are non-fatal — the OS is already installed.
if [ "$CLEAN_NVRAM" = 1 ] && [ -n "$STALE_NVRAM_BOOTNUMS" ]; then
  echo ">>> CLEAN_NVRAM: reviewing stale Agent OS/NixOS UEFI entries to prune..."
  CUR_NVRAM="$(efibm 2>/dev/null || true)"
  PRUNE_BOOTNUMS=""
  for bn in $STALE_NVRAM_BOOTNUMS; do
    entry="$(printf '%s\n' "$CUR_NVRAM" | grep -E "^Boot${bn}[* ]" || true)"
    if [ -n "$entry" ] && printf '%s\n' "$entry" | grep -qE "$NVRAM_LABEL_RE"; then
      echo "    will delete: $entry"
      PRUNE_BOOTNUMS="$PRUNE_BOOTNUMS $bn"
    fi
  done
  if [ -n "$PRUNE_BOOTNUMS" ]; then
    printf "Type YES (caps) to delete the above stale UEFI entr(y/ies) from firmware: "
    read -r NVRAM_CONFIRM < /dev/tty
    if [ "$NVRAM_CONFIRM" = YES ]; then
      for bn in $PRUNE_BOOTNUMS; do
        echo "    + efibootmgr -b $bn -B"
        efibm -b "$bn" -B >/dev/null || echo "    (warning: could not delete Boot${bn} — leaving it)"
      done
      echo ">>> NVRAM prune done — stale Agent OS entries removed; the fresh one remains."
    else
      echo ">>> NVRAM prune SKIPPED (not confirmed) — nothing removed."
    fi
  else
    echo "    nothing to prune (snapshotted entries no longer present)."
  fi
fi

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
