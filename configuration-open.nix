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

  # PUBLIC key — DVo's (Mirror's) key, same rationale and same safety as the line
  # above: a public key is safe to commit, a private one never appears here.
  #
  # WHY THIS IS DECLARED RATHER THAN INSTALLED. During the 2026-08-11 outage recovery
  # this key existed ONLY as a hand-appended line in /root/.ssh/authorized_keys,
  # typed in at the console by Dillon because the box was otherwise unreachable. That
  # survives `nixos-rebuild` but NOT a reinstall — and a reinstall is exactly the
  # moment you most need to get in. Access that lives only in mutable state is access
  # you lose on the day it matters. Declaring it here means the box comes up
  # reachable by the brain that has to rebuild it, from a fresh install, with no
  # console step.
  mirrorPubKey = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIK7lWcmQXeBX6cgzMIQeLjZwqGgg/z1w7MkkswrV4DKf dvo-wsl";

  # Both mesh brains. Root SSH stays KEY-ONLY (prohibit-password) either way.
  meshPubKeys = [ meshPubKey mirrorPubKey ];
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
  #      mail-secret-open: agent-side of email (#8) — the (A) MCP-email token scaffold for GMAIL
  #                     (out-of-tree secret plumbing, no creds). NOT a new dozen app.
  #      mail-proton-bridge-open: the "both" (Dillon 9035) — Proton-via-Bridge cred scaffold,
  #                     parallel to Gmail (ships protonmail-bridge + localhost IMAP/SMTP contract,
  #                     out-of-tree cred, no secrets). NOT a new dozen app.
  #   #9 notes-open:    Apostrophe markdown editor GUI + agent-drivable agos-notes CLI
  #                     over a real plain-md store (GUI+hand symmetry, mirrors calendar).
  #   #10 docs-open:    Zathura document-viewer GUI + agent-drivable (read-only) agos-doc CLI
  #                     — extracts PDF content (pairs with files-open's metadata-only hand).
  #   #11 media-open:   imv/mpv media GUIs + agent-drivable (read-only) agos-media CLI
  #                     — probes image/video/audio metadata via ffprobe (completes the read trio).
  #   #12 web-open:     Firefox web browser (human) + agent-drivable (read-only) agos-web CLI
  #                     — fetches a public URL & extracts readable text (Rabbot browser-#12 RULING).
  #                     COMPLETES THE AMBIENT DOZEN (12/12). Browser AUTOMATION parked = later increment.
  imports = [
    ./modules/calendar-open.nix
    ./modules/desktop-open.nix
    ./modules/settings-open.nix
    ./modules/model-open.nix
    ./modules/model-3b-open.nix   # additive, NON-DEFAULT 2nd brain (qwen2.5:3b-augur); default unchanged
    ./modules/model-lora-open.nix # seeds qwen3.5:9b-agentos (base + fine-tuned adapter); selected below
    ./modules/genesis-open.nix
    ./modules/calculator-open.nix
    ./modules/files-open.nix
    ./modules/email-open.nix
    ./modules/mail-secret-open.nix
    ./modules/mail-proton-bridge-open.nix
    ./modules/notes-open.nix
    ./modules/docs-open.nix
    ./modules/media-open.nix
    ./modules/web-open.nix
    ./modules/gaming-open.nix
    # Shared with the sovereign path deliberately (the ONE cross-lane import):
    # boot-branding is pure cosmetics (quiet+splash kernel params, Plymouth wordmark,
    # 1s loader timeout) and the SAME identity is wanted on both variants. It sets
    # nothing security-relevant; any future open-specific boot tweak goes in a new
    # boot-branding-open.nix, not edits here that would perturb the sealed surface.
    ./modules/boot-branding.nix
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

  # --- survivability: the hardware watchdog ------------------------------------
  # MEASURED FROM AN OUTAGE, not added speculatively. On 2026-08-11 04:05:36 CDT the
  # Dell went dark for FIVE DAYS. The journal for boot -3 ends mid-sentence in a burst
  # of ACPI Embedded Controller timeouts:
  #
  #     ACPI Error: AE_TIME, Returned by Handler for [EmbeddedControl] (evregion-303)
  #     ACPI Error: Timeout from EC hardware or EC device driver (evregion-313)
  #     ACPI Error: Aborting method \_SB.PC00.LPCB.ECDV.ECW1 due to previous error
  #
  # Triage cleared every software cause: no crash loop (that boot ran a healthy 8h),
  # disk 22% used, thermals normal (CPU 41C / ambient 28C), 24GB RAM available, zero
  # failed units. The kernel hard-hung below the OS, at the firmware/EC layer.
  #
  # The five days are the part worth fixing. The hang is a hardware fault we cannot
  # prevent from here; staying dark until a human walks over to the box IS preventable.
  # This machine has an iTCO_wdt at /dev/watchdog0 and it was sitting UNARMED --
  # RuntimeWatchdogUSec=0. RebootWatchdogUSec was 10min, which only covers the
  # shutdown path: it guards a reboot we asked for, not a hang we didn't.
  #
  # So: PID 1 now pets the hardware watchdog, and a kernel that stops responding gets
  # the box power-cycled by silicon instead of by a person. Five days becomes two
  # minutes.
  #
  # WHY 120s AND NOT SOMETHING TIGHTER. This box does nix builds -- heavy IO that can
  # briefly starve PID 1. The cost asymmetry is lopsided: a spurious reboot on a dev
  # box is an annoyance, but a watchdog that fires during a normal build teaches
  # whoever hits it to disable the watchdog, and then we are back to five days. A
  # recovery mechanism people switch off is worse than none, because it also carries
  # the belief that the box is protected. 120s is chosen to be boring.
  #
  # NOTE FOR WHOEVER READS THIS AFTER THE NEXT HANG: this does NOT fix the EC fault.
  # It bounds the outage. If the reboots start recurring, the fault is the thing to
  # chase (BIOS/EC firmware via the fwupd above), and this stanza is what bought you
  # the uptime to chase it from.
  # Written as systemd.settings.Manager.*, NOT the older systemd.watchdog.*. Both
  # evaluate on nixpkgs 26.11 — the latter through a rename shim that emits a
  # deprecation warning — and that shim is the hazard. A watchdog configured through
  # an alias that a future nixpkgs drops would stop being applied while this file
  # still READS as if the box were protected, and the way we would find out is the
  # next five-day outage. Same failure shape this repo keeps meeting elsewhere: a
  # boundary cancelled by something that still looks like a configuration. Use the
  # name the module actually defines.
  systemd.settings.Manager.RuntimeWatchdogSec = "120s";
  systemd.settings.Manager.RebootWatchdogSec = "10min";

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

  # Wake-on-LAN on the wired NIC (Geist's ask in the 2026-08-16 recovery plan).
  #
  # The MAC deliberately is NOT here. Enabling WoL is a property of the machine; the
  # MAC is only needed by whoever SENDS the magic packet, so it is an operational fact
  # about one person's hardware rather than a fact about the OS. It lives with the
  # senders, in the mesh's own host records — this repo is public and is the product,
  # not the inventory.
  #
  # SCOPE, STATED HONESTLY, because this is easy to over-trust: WoL wakes a machine
  # that is powered OFF or suspended. It does NOT recover the failure we actually had
  # on 2026-08-11 — an EC-level hang leaves the box powered ON and frozen, with no
  # kernel left to process a magic packet. The watchdog above is what covers that
  # case; this covers a genuinely different one (clean shutdown / suspend, wake it
  # remotely). Both are worth having; neither substitutes for the other.
  #
  # SECOND CAVEAT: as of 2026-08-16 the Dell runs WIFI-ONLY — enp0s31f6 has no
  # carrier and no address, and the box lives on DHCP over wlp0s20f3 (which is why
  # its LAN IP churns). Until someone plugs in ethernet this setting is INERT. It is
  # declared now so the capability exists the moment the cable does, rather than
  # being remembered during the next outage.
  networking.interfaces.enp0s31f6.wakeOnLan.enable = true;

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
    openssh.authorizedKeys.keys = meshPubKeys;
  };
  # mini's key also lands in root's authorized_keys (Rabbot's ask) — root SSH is
  # KEY-ONLY (prohibit-password), never a password.
  users.users.root.openssh.authorizedKeys.keys = meshPubKeys;

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
    # Static service user instead of the module default DynamicUser. A DynamicUser's
    # transient uid is NOT guaranteed stable across reboots, so the persistent model
    # store under /var/lib/ollama can end up owned by a uid the daemon no longer runs
    # as → a new `ollama pull` fails "permission denied" writing its -partial blob while
    # the already-seeded 7B still READS fine (world-readable). Compounded by the root-run
    # agos-seed-model (model-open.nix) leaving root-owned blobs. A static ollama user
    # pins ownership so the store stays writable across reboots + future model swaps —
    # the Dell benchmark's broken-pull finding (Rabbot, 2026-08-01). The seed runs as
    # this SAME user (see model-open.nix) so first-boot import never re-poisons ownership.
    user = "ollama";
    group = "ollama";
    # Keep the 7B RESIDENT rather than unloading after the default idle window. The Dell
    # bench measured a ~51s cold reload (model load + a 17 tok/s cold prompt-eval of the
    # 815-tok prefix) on EVERY call after an idle gap; resident → first token 314ms.
    # ~160× on perceived latency, free, zero quality change — THE "it's slow" fix.
    environmentVariables.OLLAMA_KEEP_ALIVE = "-1";
  };
  environment.variables = {
    OLLAMA_HOST  = "http://127.0.0.1:11434";
    # qwen3.5:9b-agentos = base 9B + the fine-tuned LoRA (model-lora-open.nix), selected
    # per Dillon's reveal-boot go-ahead (msg 9410). Rollback is `OLLAMA_MODEL=qwen3.5:9b`
    # in the session — both tags stay seeded, no rebuild needed. (The previous value here,
    # qwen2.5:7b-instruct, was #66 drift: model-open.nix stopped seeding that tag when the
    # 9B became the default brain, so this env pointed at a model the image no longer ships.)
    OLLAMA_MODEL = "qwen3.5:9b-agentos";
  };

  # --- toolbox: a normal, usable box -------------------------------------------
  # rsync is REQUIRED (Rabbot rsyncs the model blobs over the mesh); tmux for building
  # live; the rest is the usual agent toolbox.
  environment.systemPackages = with pkgs; [
    git curl jq ripgrep fd bat neovim python3 rsync tmux htop
    # Claude Code CLI (Dillon msg 9280: "talk claude through this box"). Unfree —
    # whitelisted in gaming-open.nix's allowUnfreePredicate (single shared predicate;
    # a second definition elsewhere would conflict). Auth is per-user OAuth (`claude`
    # → browser login with the Max account) — no secrets baked into the image.
    claude-code
  ];

  # The box rebuilds itself from nixpkgs (and here the operator has full sudo to do so).
  nix.settings.experimental-features = [ "nix-command" "flakes" ];

  services.xserver.enable = false;   # talk-to-it / SSH-in box, no desktop
  system.stateVersion = "24.11";
}
