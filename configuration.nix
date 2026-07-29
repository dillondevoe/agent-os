# Base plumbing. The interesting part is modules/agent-shell.nix.
# Everything here is deliberately minimal — Agent OS has no desktop environment.
{ config, pkgs, lib, ... }:

{
  # --- boot / hardware ---------------------------------------------------------
  # Target: Dell Latitude 5440 — 13th-gen Intel (Raptor Lake), Iris Xe, Intel wifi, 32GB.
  # NOTE: the real install generates ./hardware-configuration.nix from the Dell
  # (`nixos-generate-config`). Until we have the device, the VM build supplies its
  # own disk/boot. These are placeholders so `configuration.nix` reads cleanly.
  boot.loader.systemd-boot.enable = lib.mkDefault true;
  boot.loader.efi.canTouchEfiVariables = lib.mkDefault true;
  # Bare-metal boot, label-based (PR2 companion). This makes
  # `nixos-install --flake github:…#agentos` boot on the real Dell with NO generated
  # hardware-configuration.nix and NO editing — the installer just labels two
  # partitions: `nixos` (ext4 root) + `BOOT` (vfat ESP). mkDefault lets a real
  # hardware-configuration.nix override if one is ever generated; the VM builder
  # replaces the whole fileSystems set via mkVMOverride (priority 10 > mkDefault), so
  # `.#vm` ignores both mounts and supplies its own disk.
  fileSystems."/"     = lib.mkDefault { device = "/dev/disk/by-label/nixos"; fsType = "ext4"; };
  fileSystems."/boot" = lib.mkDefault { device = "/dev/disk/by-label/BOOT";  fsType = "vfat";  };
  # initrd must carry the modules to reach root on real hardware: NVMe controller +
  # the USB/Thunderbolt path used to boot the installer. The VM uses virtio and needs
  # none of these (and drops them with the fileSystems above).
  boot.initrd.availableKernelModules = [ "vmd" "nvme" "xhci_pci" "thunderbolt" "usb_storage" "sd_mod" ];
  # FORCE-load vmd + nvme at initrd start. On the real 5440 (Intel 13th-gen), the NVMe
  # sits behind Intel VMD (Volume Management Device) — the STOCK installer's fat initrd
  # has `vmd` so it sees /dev/nvme0n1 fine, but our slim initrd (nvme-only, fb5b20b) did
  # NOT → the disk stays hidden behind the VMD controller → "timed out waiting for
  # /dev/disk/by-label/nixos" + "ahci probe failed -12" → emergency mode (live 2026-07-29).
  # `vmd` binds the VMD controller so the NVMe underneath enumerates; nvme then attaches.
  # (Alternative: disable VMD in BIOS → SATA Operation = AHCI. This module keeps VMD on.)
  # (VM-proof can't catch this: mkVMOverride gives the VM its own virtio disk — the real
  # by-label/VMD/NVMe boot path is only eval'd, never runtime-exercised.)
  boot.initrd.kernelModules = [ "vmd" "nvme" ];

  # 5440-specific enablement (safe to set blind — standard for 13th-gen Intel laptops):
  hardware.enableRedistributableFirmware = true;   # Intel AX2xx wifi/bt firmware
  hardware.cpu.intel.updateMicrocode = true;       # Raptor Lake microcode
  hardware.graphics.enable = true;                 # Iris Xe (iGPU) — also gives local-model
  hardware.graphics.extraPackages = with pkgs; [ intel-media-driver ];
  services.thermald.enable = true;                 # Intel thermal daemon (laptop)
  services.fwupd.enable = true;                     # firmware updates
  # 32GB RAM = the local-model floor is real here (phase 1.5): a quantized 13-14B runs
  # comfortably. setup-brain.sh will install ollama/llama.cpp against the iGPU + CPU.

  # --- identity ----------------------------------------------------------------
  networking.hostName = "agent-os";
  networking.networkmanager.enable = true;   # wifi/ethernet without a GUI
  time.timeZone = "America/Chicago";

  # The human. Autologin into the agent shell is wired in agent-shell.nix.
  # Generic username so every install is `agent@agent-os`, not the author's initials.
  users.users.agent = {
    isNormalUser = true;
    description = "Agent OS";
    extraGroups = [ "wheel" "networkmanager" ];
    # Agent OS thesis: the login shell IS the agent (see agent-shell.nix).
  };

  # The agent needs to rewrite the machine's own config and rebuild. This is the
  # NixOS payoff — "change a setting" = the agent edits a .nix file + nixos-rebuild.
  nix.settings.experimental-features = [ "nix-command" "flakes" ];
  security.sudo.wheelNeedsPassword = false;  # v0.1 single-user demo box; revisit for security-kernel phase

  # --- the toolbox the agent composes from ------------------------------------
  environment.systemPackages = with pkgs; [
    git curl jq ripgrep fd bat            # the agent's hands
    neovim                                 # text-editing capability
    python3                                # mem's interpreter + a general agent tool
    # chromium (the "GUI guest") is deferred to the compositor module — with no X/wayland
    # session it can't launch, so it'd be ~500MB of dead closure. Comes with cage/weston.
    # NOTE: the brain (Claude Code CLI, or a local-model runner) is installed by
    # bin/setup-brain.sh at first boot — it isn't in nixpkgs yet. Phase 1.5 swaps in
    # a local model (llama.cpp / ollama) as the offline floor.
  ];

  # No X session by default — this is a talk-to-it machine. Chromium is launched
  # headfully by the agent on demand (cage/weston kiosk comes in a later module).
  services.xserver.enable = false;

  system.stateVersion = "24.11";
}
