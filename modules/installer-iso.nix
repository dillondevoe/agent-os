# modules/installer-iso.nix — the live installer image (`nixosConfigurations.installer`,
# `packages.x86_64-linux.iso`). M1a: boots to a console, prints a banner, puts the
# existing installer on PATH as `agentos-install`. Nothing more.
#
# DELIBERATELY NOT HERE (later slices): desktop, disko/LUKS, a TUI rewrite, signing.
#
# SELF-CONTAINED: shares no module with the sealed/open systems, so it cannot perturb them
# and nothing from them (agent, models, keys) can leak into the image. It carries NO model
# weights, NO secrets and NO credentials: the installer asks for those at install time and
# writes them to the TARGET only.
{ config, lib, pkgs, modulesPath, ... }:

let
  # install.sh exactly as shipped in this repo, copied into the store unmodified.
  installScript = ./../install.sh;

  agentosInstall = pkgs.writeShellScriptBin "agentos-install" ''
    # Thin wrapper: run the repo's install.sh logic as shipped. Environment overrides
    # (VARIANT, DISK, FLAKE_REV, TS_AUTHKEY, ...) pass straight through; run as root.
    if [ "$(${pkgs.coreutils}/bin/id -u)" -ne 0 ]; then
      echo "agentos-install must run as root:  sudo agentos-install" >&2
      exit 1
    fi
    exec ${pkgs.bash}/bin/bash ${installScript} "$@"
  '';

  banner = ''

    ================================================================
      Agent OS installer (live image)
    ----------------------------------------------------------------
      1. Network:   nmtui              (wifi / ethernet)
      2. Install:   sudo agentos-install
      3. Read:      cat /etc/agent-os/README.txt

      agentos-install ERASES the target disk after you type YES.
      The install fetches the system from the network.
    ================================================================

  '';

  readme = pkgs.writeText "agent-os-installer-README.txt" ''
    Agent OS live installer
    =======================

    This image boots to a text console. It contains no model weights, no
    secrets and no keys. Everything the installed system needs is fetched
    during install, over the network, after you confirm.

    Steps
      1. Get online.  Ethernet usually works on its own.  For wifi run:
             nmtui
         then pick "Activate a connection".
      2. Run the installer as root:
             sudo agentos-install
         It shows the disks, names the one it will ERASE (default
         /dev/nvme0n1) and asks you to type YES.
      3. Reboot and remove the USB stick.

    Overrides (prefix the command; they pass through to the installer):
        sudo DISK=/dev/sda agentos-install
        sudo VARIANT=agentos-open agentos-install

    The installer is the repository's install.sh, unmodified.
    Source and docs: https://github.com/dillondevoe/agent-os
    Build / verify this image: docs/installer-iso.md in that repository.
  '';
in
{
  imports = [ (modulesPath + "/installer/cd-dvd/installation-cd-minimal.nix") ];

  networking.hostName = "agentos-installer";

  # nmtui for wifi. The minimal ISO ships wpa_supplicant standalone; the two conflict.
  networking.networkmanager.enable = true;
  networking.wireless.enable = lib.mkForce false;

  image.baseName = lib.mkForce "agent-os-installer-${pkgs.stdenv.hostPlatform.system}";
  isoImage.volumeID = "AGENTOS_INSTALL";

  environment.systemPackages = with pkgs; [
    agentosInstall
    networkmanager  # nmtui, nmcli
    # disk tools install.sh uses (it can also nix-shell them; having them here keeps
    # the partition/format/EFI steps working before the network is up)
    parted
    util-linux      # lsblk, wipefs, mount
    dosfstools
    e2fsprogs
    efibootmgr
    mkpasswd
    git
    curl
  ];

  # install.sh ends in `nixos-install --flake`, and its nix-shell fallbacks resolve <nixpkgs>
  # through the flake registry: with flakes off the install died right after partitioning.
  # Found by the first real install from this ISO (2026-10-09, the NixOS deploy VM on dellon).
  nix.settings.experimental-features = [ "nix-command" "flakes" ];

  environment.etc."agent-os/README.txt".source = readme;

  # Banner on every console login prompt, plus the post-login motd.
  services.getty.greetingLine = lib.mkForce banner;
  users.motd = banner;
}
