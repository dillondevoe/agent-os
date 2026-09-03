#!/usr/bin/env bash
# Agent OS v0.1 — Dell installer. One-shot: partition → format → mount → install.
#
# Run as ROOT from the NixOS-minimal installer shell. Become root FIRST, then pipe:
#   sudo -i
#   curl -sL https://raw.githubusercontent.com/dillondevoe/agent-os/main/install.sh | bash
#
# ⚠️ ENV OVERRIDES MUST PREFIX THE PIPED `bash` — not curl, not sudo. The var has to land in
#    the environment of the process that RUNS the script:
#       ✅  curl -sL .../install.sh | VARIANT=agentos-open bash
#       ❌  VARIANT=agentos-open curl -sL .../install.sh | sudo -E bash
#            (the var is in CURL's env; sudo -E has nothing to preserve → script sees it UNSET)
#       ❌  curl -sL .../install.sh | sudo VARIANT=agentos-open bash
#            (sudo's default env_reset strips it → script sees it UNSET)
#    A lost VARIANT silently defaults to the SEALED box, so the YES-gate below prints the
#    RESOLVED variant and makes you type it — a fall-through can't install the wrong OS unseen.
#
# VARIANTS:
#   (default)              — the sovereign box (sealed after model pull). Just `| bash`.
#   VARIANT=agentos-open   — the OPEN / MESHED dev box (Dillon msg 8926): OpenSSH + Tailscale +
#     full-power user + real shell, no egress wall, no auto-pull. Pass a Tailscale PRE-AUTH key so
#     the Dell auto-joins the mesh on first boot (written to the TARGET only, mode 0600 — never in
#     the repo; omit it and run `tailscale up --ssh` by hand later):
#       curl -sL .../install.sh | VARIANT=agentos-open TS_AUTHKEY=tskey-auth-xxxx bash
#
# ⚠️ ERASES THE TARGET DISK. Shows you the disk + resolved variant and requires you to type YES.
set -euo pipefail

VARIANT="${VARIANT:-agentos}"
case "$VARIANT" in
  agentos|agentos-open) : ;;
  *) echo "Unknown VARIANT '$VARIANT' (expected 'agentos' or 'agentos-open')."; exit 1 ;;
esac
# --- THE FLAKE PIN -----------------------------------------------------------------
# Fresh installs build a PINNED rev, not whatever `main` happens to be. Rabbot's ruling
# 2026-08-31 (Dillon delegated: "let's just do what's best"): a fresh install of a broken
# `main` is the worse failure, because STALENESS IS VISIBLE AND BREAKAGE ON FIRST BOOT IS
# NOT. A box that installs an old-but-verified system boots, joins the mesh, and can be
# updated; a box that installs a broken HEAD is a stranger staring at an error on a screen
# with no way in.
#
# WHAT THE PIN DOES NOT COVER, stated so it is not over-read. This script is itself fetched
# from `main` by the curl one-liner in the README, so the SCRIPT is unpinned and always
# newest. Script and pin therefore travel together in the same commit — the pin cannot lag
# behind the script that carries it. What it CAN do is lag behind `main` when someone merges
# without bumping it, and that is the only failure mode left. It is caught by
# tests/pin-freshness.sh in CI, not by anyone remembering.
#
# BUMP IT AS PART OF A VERIFIED RELEASE, never as a habit: the point of the pin is that the
# rev named here has been booted on real hardware. Moving it to an unverified HEAD converts
# this line back into the moving ref it replaced while still looking pinned.
#
# Override for testing an unmerged branch:  ... | FLAKE_REV=my-branch bash
FLAKE_REV="${FLAKE_REV:-6d54109023911b23a8d009b57997b6537c773613}"
FLAKE="github:dillondevoe/agent-os/${FLAKE_REV}#${VARIANT}"
DISK="${DISK:-/dev/nvme0n1}"   # override:  curl ... | DISK=/dev/sdX bash   (prefix on the piped bash)
TS_AUTHKEY="${TS_AUTHKEY:-}"   # OPEN variant only — Tailscale pre-auth key (runtime secret, never committed)

echo "=============================================================="
echo " Agent OS installer — variant: $VARIANT — target disk: $DISK"
# Print the pin at the YES gate. A pin nobody can see at install time is the same
# shape as the moving ref it replaced: the operator has to be able to read WHICH rev
# is about to be built before typing YES, otherwise a stale or overridden pin lands
# silently and the first evidence is a box running a rev nobody chose.
echo " building rev: $FLAKE_REV"
echo "=============================================================="
lsblk -o NAME,SIZE,TYPE,MODEL,MOUNTPOINT
echo "--------------------------------------------------------------"
echo "This will ERASE $DISK completely and install Agent OS (variant: $VARIANT) on it."
echo "The USB you booted from is separate and will NOT be touched."
if [ "$VARIANT" = agentos ]; then
  echo "  → variant 'agentos' = the SEALED sovereign box. If you meant the OPEN/MESHED dev box"
  echo "    and this fell through to the default, ABORT and re-run with the var on the PIPED bash:"
  echo "        curl -sL .../install.sh | VARIANT=agentos-open bash   (NOT before curl, NOT after sudo)"
fi
printf "Type YES (caps) to ERASE %s and install variant '%s': " "$DISK" "$VARIANT"
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

# --- OPEN variant: place the Tailscale pre-auth key on the TARGET (never committed) ----------
# configuration-open.nix sets services.tailscale.authKeyFile = /var/lib/tailscale/authkey, so
# NixOS's tailscaled-autoconnect runs `tailscale up --auth-key file:... --ssh` on first boot and
# the Dell joins the mesh unattended. We write the key ONLY to the mounted target FS, mode 0600,
# root-owned — it lands in the installed system, not the repo and not the installer image. If
# TS_AUTHKEY is empty the box still installs; run `tailscale up` by hand once it's booted.
if [ "$VARIANT" = agentos-open ]; then
  if [ -n "$TS_AUTHKEY" ]; then
    echo ">>> OPEN: staging Tailscale pre-auth key on the target (/var/lib/tailscale/authkey, 0600)..."
    install -d -m 0700 /mnt/var/lib/tailscale
    ( umask 077; printf '%s' "$TS_AUTHKEY" > /mnt/var/lib/tailscale/authkey )
    chmod 0600 /mnt/var/lib/tailscale/authkey
  else
    echo ">>> OPEN: no TS_AUTHKEY given — skipping mesh auto-join. Run 'tailscale up --ssh' after boot."
  fi
fi

# --- Break-glass root password: provision /etc/agent-os/break-glass.hash on the TARGET --------
# configuration.nix runs users.mutableUsers=false and modules/break-glass.nix sets
#   users.users.root.hashedPasswordFile = "/etc/agent-os/break-glass.hash";
# so if that file is ABSENT at activation, root has no valid password and the ONLY admin path — the
# tty3 break-glass console — is fail-safe CLOSED: a fresh base install would then be recoverable
# ONLY from installer media. So for the sealed sovereign box we REQUIRE an operator-set break-glass
# password here: hash it sha-512 (crypt) and write it 0600 root:root to the target BEFORE
# nixos-install (activation reads the file at install time). The plaintext never touches disk or the
# repo — only the one-way crypt hash is written, and only into the installed system.
#   Unattended path: pre-seed BREAK_GLASS_HASH='$6$...' (a sha-512 crypt, NOT a plaintext) on the
#   piped bash for reinstalls — same "secret via env, never committed" rule as TS_AUTHKEY.
#   The OPEN/meshed dev box SKIPS this: it has a full-power passwordless-sudo user, so root's console
#   door is not its only admin path (root.hashedPasswordFile is unused on -open).
BREAK_GLASS_HASH="${BREAK_GLASS_HASH:-}"
if [ "$VARIANT" = agentos ]; then
  BG_HASH=""
  case "$BREAK_GLASS_HASH" in
    '$6$'*) BG_HASH="$BREAK_GLASS_HASH"; echo ">>> Break-glass: using pre-seeded BREAK_GLASS_HASH (sha-512 crypt)." ;;
    '')     : ;;
    *)      echo "BREAK_GLASS_HASH is set but is not a sha-512 crypt ('\$6\$...'). Refusing to use it."; exit 1 ;;
  esac
  if [ -z "$BG_HASH" ]; then
    echo ">>> Break-glass: set the ROOT rescue password for the tty3 console."
    echo "    On the SEALED box this is the ONLY admin path — no agent sudo, no SSH. Don't lose it."
    echo "    Provisioned to the TARGET only (sha-512 crypt, 0600 root:root); never stored in the repo."
    # mkpasswd may be absent from the minimal-installer PATH — pull it via nix-shell (same belt as the
    # mkfs/efibootmgr blocks) and resolve its store path once; the path stays valid after the shell exits.
    MKPASSWD="$(nix-shell -p mkpasswd --run 'command -v mkpasswd')"
    while [ -z "$BG_HASH" ]; do
      printf "    Enter break-glass (root) password: ";    read -rs BG_PW1 < /dev/tty; echo
      printf "    Re-enter break-glass (root) password: "; read -rs BG_PW2 < /dev/tty; echo
      if [ -z "$BG_PW1" ];           then echo "    Empty password not allowed — the door must be gated. Try again."; continue; fi
      if [ "$BG_PW1" != "$BG_PW2" ]; then echo "    Passwords did not match — try again."; continue; fi
      # Password -> mkpasswd via STDIN (-s): never on argv (world-readable in ps) and never
      # interpolated into a command string (no quoting/injection surface for a "'"-bearing password).
      BG_HASH="$(printf '%s' "$BG_PW1" | "$MKPASSWD" -m sha-512 -s)" || { echo "    mkpasswd failed — try again."; BG_HASH=""; }
    done
    unset BG_PW1 BG_PW2
  fi
  case "$BG_HASH" in
    '$6$'*) : ;;
    *) echo "FATAL: break-glass hash is not a valid sha-512 crypt — refusing to install a box with no admin path."; exit 1 ;;
  esac
  echo ">>> Break-glass: writing /etc/agent-os/break-glass.hash on the target (0600 root:root)..."
  install -d -m 0755 /mnt/etc/agent-os
  ( umask 077; printf '%s\n' "$BG_HASH" > /mnt/etc/agent-os/break-glass.hash )
  chmod 0600 /mnt/etc/agent-os/break-glass.hash
  chown root:root /mnt/etc/agent-os/break-glass.hash
  unset BG_HASH
  # Read-back guard: confirm the door credential actually landed before building a mutableUsers=false box.
  grep -q '^\$6\$' /mnt/etc/agent-os/break-glass.hash || { echo "FATAL: break-glass.hash did not write correctly."; exit 1; }
fi

# --- CLEAN_NVRAM (opt-in) — snapshot stale Agent OS UEFI entries BEFORE install --------------
# The whole-disk wipe above clears on-DISK loaders but NEVER motherboard UEFI NVRAM, so each
# reinstall's firmware "Linux Boot Manager" entry piles up ("getting insane"). Opt-in
# CLEAN_NVRAM=1 prunes ONLY our own entries, and ONLY those that already exist NOW — snapshot
# them here, before nixos-install registers a FRESH one, so the new entry is never a candidate.
# Default OFF: the whole-drive wipe is the right default; a dual-booter must never lose another
# OS's entry.  Enable with:  curl ... | CLEAN_NVRAM=1 bash   (var prefixes the piped bash)
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
  # Belt-and-suspenders (Fable): grab the FRESH ESP's PARTUUID and NEVER delete an entry whose
  # verbose device path carries it. bootctl already matches by partition-GUID+path (not label) and
  # the snapshot-before design meant the new entry was never a candidate — this converts that
  # "safe by reasoning" into "safe by DIRECT OBSERVATION" of the just-installed partition. A BIOS/
  # legacy or blkid-less path yields an empty PARTUUID → the guard no-ops (prior safety unchanged).
  # efibootmgr -v prints the GPT partition GUID inside HD(1,GPT,<partuuid>,...), so a fixed-string
  # match of the fresh PARTUUID against the verbose line pins the new entry by observation.
  NEW_ESP_PARTUUID="$(blkid -s PARTUUID -o value "${P}1" 2>/dev/null || true)"
  CUR_NVRAM_V="$(efibm -v 2>/dev/null || true)"
  PRUNE_BOOTNUMS=""
  for bn in $STALE_NVRAM_BOOTNUMS; do
    entry="$(printf '%s\n' "$CUR_NVRAM" | grep -E "^Boot${bn}[* ]" || true)"
    if [ -n "$entry" ] && printf '%s\n' "$entry" | grep -qE "$NVRAM_LABEL_RE"; then
      vline="$(printf '%s\n' "$CUR_NVRAM_V" | grep -E "^Boot${bn}[* ]" || true)"
      if [ -n "$NEW_ESP_PARTUUID" ] && printf '%s\n' "$vline" | grep -iqF "$NEW_ESP_PARTUUID"; then
        echo "    keeping Boot${bn} — device path carries the fresh ESP PARTUUID ($NEW_ESP_PARTUUID)"
        continue
      fi
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
if [ "$VARIANT" = agentos-open ]; then
  echo " OPEN/MESHED box. First boot: ethernet auto-DHCPs (wifi: log in on the console —"
  echo " it autologins to a bash shell — and run 'nmtui'). If you passed TS_AUTHKEY it joins"
  echo " your tailnet automatically (SSH: 'tailscale status' to see it); otherwise run"
  echo " 'sudo tailscale up --ssh'. The ollama daemon is up with NO model — rsync the blobs"
  echo " over the mesh. SSH is key-only for agent@ and root@ (mini's key baked in)."
else
  echo " First boot: it gets online (wifi: it offers a picker; ethernet: auto), installs"
  echo " its local brain, then you're talking to it at a  you ›  prompt."
fi
echo ""
echo " ⚠️  If boot hangs on 'waiting for /dev/disk/by-label/nixos': your"
echo "     machine hides the NVMe behind Intel VMD. Fix: reboot → BIOS (F2)"
echo "     → SATA Operation → set to AHCI → save → boot again. (Or the initrd"
echo "     carries the vmd driver, so most boxes just work.)"
echo "=============================================================="
