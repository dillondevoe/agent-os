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
  # fileSystems."/" is provided by hardware-configuration.nix on real hardware
  # and by the VM builder in the flake's `vm` output.

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
  users.users.dtd = {
    isNormalUser = true;
    description = "Dillon";
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
    chromium                               # the "GUI guest" it can summon for visual tasks
    # NOTE: the brain (Claude Code CLI, or a local-model runner) is installed by
    # bin/setup-brain.sh at first boot — it isn't in nixpkgs yet. Phase 1.5 swaps in
    # a local model (llama.cpp / ollama) as the offline floor.
  ];

  # No X session by default — this is a talk-to-it machine. Chromium is launched
  # headfully by the agent on demand (cage/weston kiosk comes in a later module).
  services.xserver.enable = false;

  system.stateVersion = "24.11";
}
