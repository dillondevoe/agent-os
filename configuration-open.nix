# Agent OS — OPEN / MESHED variant (`agentos-open`).
#
# WHY THIS EXISTS (Dillon msg 8926, 2026-07-31 founder re-orient — relayed
# rabbot-to-augur-URGENT-open-meshed-installer-dillon-reorient): the sovereign
# path (rootless agent, sealed egress, locked memory-floor REPL) built an island
# neither Dillon nor Rabbot can enter, and turned first-install into days of manual
# labor. The corrected sequence is CAPABILITY-FIRST: get a working, meshed box now →
# seal it → repackage for GitHub at the END. This variant is that "get it working"
# box: deliberately PERMISSIVE so Rabbot can SSH/Tailscale in and build on the Dell
# live. Sovereign hardening is a FINAL packaging step, NOT the starting constraint.
#
# ISOLATION (deliberate): this file is SELF-CONTAINED and shares ZERO modules with
# the sovereign path. It does NOT import configuration.nix (whose hard no-agent-root
# assertions — agent ∉ wheel, wheelNeedsPassword == true — would FAIL the moment this
# variant grants the agent wheel + passwordless sudo), agent-shell.nix (the locked
# REPL), clean-room.nix (the egress wall), brain.nix (the first-boot auto-pull),
# break-glass, system-set, or the capability wall. Nothing here can perturb the
# sealed surface — the open build and the sealed build overlap in no module.
#
# The hardware/boot block below is an INTENTIONAL MIRROR of configuration.nix's
# hardware block (same Dell 5440 facts, incl. the VMD/nvme initrd fix, fb5b20b).
# Mirrored — not shared — to keep the sealed base untouched for this URGENT ship.
# SEAL-TIME FOLLOW-UP: when this variant is folded back for the sovereign repackage,
# extract the shared hardware into a modules/hardware-base.nix imported by both, so
# a future Dell-boot fix can never land in one variant and miss the other.
{ config, pkgs, lib, ... }:

let
  # PUBLIC key — mini's (Rabbot's) key so it can SSH into the Dell over the mesh and
  # build the box live. A public key is safe to commit; the Tailscale PRE-AUTH key is
  # NOT in the repo — it is passed as $TS_AUTHKEY to install.sh and written to
  # authKeyFile on the target at install time (see install.sh, VARIANT=agentos-open).
  meshPubKey = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKJTAziP2h4A1uPJeQ4++F8f+Uw3vLzjV7sGSylxA2RH rabbot-mini-to-agentos-20260731";
in {
  # Phase 2 ambient substrate (Augur, OS build lead). Each app is a self-contained,
  # OPEN-only module so it can never perturb the sealed surface.
  #   #1 calendar-open: nuclear-accurate agent-read/write calendar (Dillon's flagged priority).
  #   #2 desktop-open:  reproducible Hyprland desktop + Waybar ambient bar.
  #   #3 settings-open: agent-drivable agos-sys settings CLI + human GUI tools.
  #   #4 model-open:    the brain (qwen2.5:7b-instruct) BAKED INTO THE IMAGE (Dillon 8988).
  #   #5 genesis-open:  THE GENESIS LOCK — the soul (GENESIS.md) baked into the brain
  #                     derivation (path+hash literals; Geist "bind not bytes"). agent-brain.
  #   #6 calculator-open: Qalculate! GUI + agent-drivable agos-calc CLI (ambient dozen).
  #   #7 files-open:    Thunar file-manager GUI + agent-drivable (read-only) agos-files CLI.
  #   #8 email-open:    Thunderbird human mail GUI (agent hand = MCP connector, RULING-A).
  #      mail-secret-open: agent-side of email (#8) — the (A) MCP-email token scaffold
  #                     (out-of-tree secret plumbing, no creds). NOT a new dozen app.
  imports = [
    ./modules/calendar-open.nix
    ./modules/desktop-open.nix
    ./modules/settings-open.nix
    ./modules/model-open.nix
    ./modules/genesis-open.nix
    ./modules/calculator-open.nix
    ./modules/files-open.nix
    ./modules/email-open.nix
    ./modules/mail-secret-open.nix
  ];

  # --- boot / hardware (MIRROR of configuration.nix — see header) --------------
  boot.loader.systemd-boot.enable = lib.mkDefault true;
  boot.loader.efi.canTouchEfiVariables = lib.mkDefault true;
  # Bare-metal boot, label-based: install.sh labels `nixos` (ext4 root) + `BOOT`
  # (vfat ESP). mkDefault lets a generated hardware-configuration.nix override; the
  # VM builder replaces the whole fileSystems set via mkVMOverride so `.#vm-open`
  # ignores both and supplies its own disk.
  fileSystems."/"     = lib.mkDefault { device = "/dev/disk/by-label/nixos"; fsType = "ext4"; };
  fileSystems."/boot" = lib.mkDefault { device = "/dev/disk/by-label/BOOT";  fsType = "vfat";  };
  boot.initrd.availableKernelModules = [ "vmd" "nvme" "xhci_pci" "thunderbolt" "usb_storage" "sd_mod" ];
  # FORCE-load vmd + nvme at initrd start: the 5440's NVMe sits behind Intel VMD; a
  # vmd-less initrd leaves the disk hidden → emergency mode (lived 2026-07-29, fb5b20b).
  boot.initrd.kernelModules = [ "vmd" "nvme" ];

  hardware.enableRedistributableFirmware = true;   # Intel AX2xx wifi/bt firmware
  hardware.cpu.intel.updateMicrocode = true;       # Raptor Lake microcode
  hardware.graphics.enable = true;                 # Iris Xe (iGPU)
  hardware.graphics.extraPackages = with pkgs; [ intel-media-driver ];
  services.thermald.enable = true;                 # Intel thermal daemon (laptop)
  services.fwupd.enable = true;                     # firmware updates

  # --- identity + network ------------------------------------------------------
  networking.hostName = "agent-os";
  networking.networkmanager.enable = true;         # wifi/ethernet without a GUI
  time.timeZone = "America/Chicago";

  # OPEN network posture: NO clean-room egress wall (not imported). The default
  # NixOS stateful firewall stays on (normal inbound filtering — not the egress
  # seal); OpenSSH opens 22 and Tailscale opens its own ports. "Open" here means
  # no egress restrictions, so the model download and mesh traffic never fight a wall.
  networking.firewall.enable = true;
  networking.firewall.trustedInterfaces = [ "tailscale0" ];

  # --- the human: FULL power (this variant ONLY) -------------------------------
  # Real user, real bash login shell (NOT the agent-shell memory-floor REPL), in
  # wheel with PASSWORDLESS sudo. This is the deliberate opposite of the sovereign
  # no-agent-root posture — so Dillon (console) and Rabbot (SSH) can just DO things.
  users.mutableUsers = true;   # dev box — runtime passwd/user changes persist
  users.users.agent = {
    isNormalUser = true;
    description = "Agent OS (open/meshed dev variant)";
    extraGroups = [ "wheel" "networkmanager" ];
    shell = pkgs.bash;
    openssh.authorizedKeys.keys = [ meshPubKey ];
  };
  # mini's key also lands in root's authorized_keys (Rabbot's ask) — root SSH is
  # KEY-ONLY (prohibit-password), never a password.
  users.users.root.openssh.authorizedKeys.keys = [ meshPubKey ];

  security.sudo.enable = true;
  security.sudo.wheelNeedsPassword = false;   # passwordless sudo — OPEN variant only

  # Console convenience: land a physically-present operator straight in a bash shell
  # (no password) on tty1. SSH is key-only; sudo is passwordless — no account password
  # is needed on either path.
  services.getty.autologinUser = "agent";

  # --- remote access: OpenSSH + Tailscale --------------------------------------
  services.openssh = {
    enable = true;
    settings = {
      PasswordAuthentication = false;          # key auth only
      KbdInteractiveAuthentication = false;
      PermitRootLogin = "prohibit-password";   # root via KEY only (mini's key)
    };
  };

  # Tailscale auto-join. The daemon is declarative here; the PRE-AUTH key is a runtime
  # secret placed by install.sh at authKeyFile (NOT committed). With authKeyFile set,
  # NixOS's tailscaled-autoconnect oneshot runs `tailscale up --auth-key file:... --ssh`
  # on boot. If the file is absent (no TS_AUTHKEY given), autoconnect fails non-fatally
  # and Rabbot runs `tailscale up` by hand. Tailscale SSH (--ssh) is a bonus in-path.
  services.tailscale = {
    enable = true;
    authKeyFile = "/var/lib/tailscale/authkey";
    extraUpFlags = [ "--ssh" ];
  };

  # --- brain: BAKED INTO THE IMAGE, not pulled ---------------------------------
  # Ollama daemon ready. The weights are NOT downloaded on first boot (no brain.nix
  # auto-pull, no mesh rsync): modules/model-open.nix ships qwen2.5:7b-instruct INSIDE
  # the image (a content-hashed FOD in the closure) and a local first-boot oneshot
  # (`agos-seed-model`) imports it into Ollama with ZERO network — Dillon 8988. Boot
  # Agent OS and it is alive.
  services.ollama = {
    enable = true;
    host = "127.0.0.1";
    port = 11434;
  };
  environment.variables = {
    OLLAMA_HOST  = "http://127.0.0.1:11434";
    OLLAMA_MODEL = "qwen2.5:7b-instruct";
  };

  # --- toolbox: a normal, usable box -------------------------------------------
  # rsync is REQUIRED (Rabbot rsyncs the model blobs over the mesh); tmux for building
  # live; the rest is the usual agent toolbox.
  environment.systemPackages = with pkgs; [
    git curl jq ripgrep fd bat neovim python3 rsync tmux htop
  ];

  # The box rebuilds itself from nixpkgs (and here the operator has full sudo to do so).
  nix.settings.experimental-features = [ "nix-command" "flakes" ];

  services.xserver.enable = false;   # talk-to-it / SSH-in box, no desktop
  system.stateVersion = "24.11";
}
