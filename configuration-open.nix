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
    ./modules/selfimprove-open.nix
    ./modules/key-drift-open.nix
    ./modules/user-drift-open.nix
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
    # 1s loader timeout) and the SAME wordmark/branding is wanted on both variants. It sets
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
  # seal). "Open" here means no EGRESS restrictions, so the model download and mesh
  # traffic never fight a wall.
  #
  # "OPEN" IS ABOUT WHAT THE OPERATOR MAY DO, NEVER ABOUT WHO MAY REACH THE BOX.
  # (Dillon, 2026-08-30: "agent os should probably account for that so lean towards
  # making it more secure.") Full sudo, open egress, no seal — all still true. Reachable
  # from the open internet — never. A stranger's install has to be safe on a hostile
  # network out of the box; that is the product bar, not a hardening nicety.
  #
  # What this replaces, and why the old line was wrong in a way that read as fine:
  # `services.openssh.enable` sets `allowedTCPPorts = [ 22 ]` for you, and the previous
  # comment here recorded that as "OpenSSH opens 22" — accurate, and silent about the
  # part that matters, which is that it opens 22 on EVERY interface. This box carries a
  # global IPv6 address with a default route via RA and egresses as itself with no NAT,
  # so "every interface" included the public v6 internet. Measured 2026-08-30: sshd
  # listening on 0.0.0.0:22 and [::]:22. Key-only auth was the only thing between a v6
  # scan and the login path.
  #
  # What the logs do NOT show, stated because an earlier draft of this comment implied
  # otherwise: sshd's own connection log has ZERO off-LAN sources. Every auth refusal in
  # it comes from two RFC1918 hosts on this network. Since sshd was bound to ::/0, any
  # off-LAN connection would have been recorded -- so its absence is the discriminating
  # evidence, and it says the exposure was reachable but never observably reached. That
  # is "no positive evidence of exposure," bounded by journal retention, NOT a proof of
  # past safety. The reason to close the port is the reachability, not an attack.
  networking.firewall.enable = true;
  networking.firewall.trustedInterfaces = [ "tailscale0" ];

  # Do NOT let the openssh module open 22 globally; scope it by source below.
  services.openssh.openFirewall = false;

  # Source-scoped SSH. RFC1918 + CGNAT (tailnet) over v4; link-local + ULA over v6.
  #
  # DELIBERATELY ABSENT: this LAN's actual global v6 prefix. Hardcoding it would scope
  # the rule to one household, which is the opposite of a product default — and it is
  # personal infrastructure data, so tools/personal-data-gate.sh would refuse the commit.
  # The gate and the product bar agree here, which is a good sign about both.
  #
  # CONSEQUENCE, stated rather than discovered later: SSH from a LAN peer over its
  # GLOBAL v6 address stops working, because on a RA-configured network that address is
  # indistinguishable from an internet one at the firewall. v4 LAN and Tailscale both
  # still work, and tailscale0 is a trusted interface above, so no reachability this
  # fleet depends on is lost.
  networking.firewall.extraCommands = ''
    for net in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10; do  # CGNAT range definition, not an address  # gate-allow
      iptables -A nixos-fw -p tcp --dport 22 -s "$net" -j nixos-fw-accept
    done
    ip6tables -A nixos-fw -p tcp --dport 22 -s fe80::/10 -j nixos-fw-accept
    ip6tables -A nixos-fw -p tcp --dport 22 -s fd00::/8 -j nixos-fw-accept
  '';

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
  # Real users, real bash login shells (NOT the agent-shell memory-floor REPL). The
  # HUMAN account is in wheel with PASSWORDLESS sudo — the deliberate opposite of the
  # sovereign no-agent-root posture, so Dillon (console) and Rabbot (SSH) can just DO
  # things. As of C1 (below) that human account is `operator`, and the account the
  # local model's tool loop runs under is NOT it. The permissiveness this variant
  # promises was always about the person; it was only ever an accident of packaging
  # that the model inherited it from sharing the uid.
  users.mutableUsers = true;   # dev box — runtime passwd/user changes persist
  # C1 (Fable security review, 2026-08-30) — THE LLM TOOL LOOP DOES NOT GET SUDO.
  #
  # `agent` is the identity the local model's tool loop runs under: agent-brain.py's
  # run_command dispatches `bash -c <string produced by the model>`, and the model's
  # context can contain text fetched from the open web by fetch_web. Before this
  # change `agent` was in `wheel` under wheelNeedsPassword=false, so that path ended
  # at uncontested root with nothing in between — no prompt, no allowlist, no broker.
  # A sentence on a web page was one hop from the whole machine.
  #
  # THE SPLIT: `agent` keeps the desktop and the product experience and loses wheel.
  # `operator` is the human's account and holds wheel + passwordless sudo. This is the
  # same product line the firewall block above states, applied to privilege instead of
  # reachability: OPEN IS ABOUT WHAT THE OPERATOR MAY DO, NEVER ABOUT WHAT THE MODEL
  # MAY DO UNASKED. Dillon at the console and Rabbot over SSH lose nothing.
  #
  # STATED CONSEQUENCE, not discovered later: `run_command` can no longer install
  # packages or change system state. The system prompt still tells the model to try
  # ("Before installing ANYTHING..."), so it will attempt sudo and get a clean refusal
  # rather than silent success. That refusal is the correct behaviour and the prompt
  # should be reworded when the broker lands (Tier 2) — not before, because a model
  # that never tries is a model whose containment is untested.
  #
  # THIS IS THE TIER-0 MINIMUM AND IT IS NOT THE WHOLE FIX. `agent` can still run
  # arbitrary unprivileged commands, read the user's files, and reach the network.
  # Full routing of run_command/fetch_web/summon_claude through the confirm/broker
  # seam is Tier 2 and is where model-driven action actually becomes consented.
  users.users.agent = {
    isNormalUser = true;
    description = "Agent OS (open/meshed dev variant) — LLM tool loop runs here, NOT in wheel";
    extraGroups = [ "networkmanager" ];
    shell = pkgs.bash;
    openssh.authorizedKeys.keys = meshPubKeys;
  };

  # The human operator. Everything `agent` used to be allowed to do, a person still is.
  users.users.operator = {
    isNormalUser = true;
    description = "Human operator (console + mesh SSH) — wheel, passwordless sudo";
    extraGroups = [ "wheel" "networkmanager" ];
    shell = pkgs.bash;
    openssh.authorizedKeys.keys = meshPubKeys;
  };

  # A control that lives only in a comment is not a control. If a later edit puts the
  # tool-loop account back in wheel, the BUILD fails rather than the box quietly
  # regressing to the pre-C1 posture.
  assertions = [
    {
      assertion = !(builtins.elem "wheel" config.users.users.agent.extraGroups);
      message = ''
        agentos-open C1: users.users.agent must NOT be in "wheel". The local model's
        tool loop runs as this account and dispatches `bash -c` on model-authored
        strings, so wheel + wheelNeedsPassword=false hands root to anything that can
        get text into the context (fetch_web reads the open internet). Put the human
        in `operator` instead; that account is unrestricted by design.
      '';
    }
  ];
  # mini's key also lands in root's authorized_keys (Rabbot's ask) — root SSH is
  # KEY-ONLY (prohibit-password), never a password.
  users.users.root.openssh.authorizedKeys.keys = meshPubKeys;

  security.sudo.enable = true;
  # Passwordless sudo for wheel — OPEN variant only. Safe to keep BECAUSE of the C1
  # split above: wheel now contains `operator` (a person) and not `agent` (the tool
  # loop). Re-adding `agent` to wheel while this is false is the exact pre-C1 hole,
  # which is what the assertion above exists to refuse at build time.
  security.sudo.wheelNeedsPassword = false;

  # Console convenience: land a physically-present operator straight in a bash shell
  # (no password) on tty1. SSH is key-only; sudo is passwordless — no account password
  # is needed on either path.
  # Autologin stays `agent`: the console IS the product, and a physically-present
  # person should land in the agent experience, not a shell. A human who needs
  # privilege switches to `operator` (`su - operator`, or another tty). Physical
  # presence was never the thing being defended against here — the model was.
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
    # DECLARED, not assumed. This value was carried for weeks as a *decision* — "hold
    # MAX_LOADED_MODELS at 1 pending the merge/keep call" — and was never once set on the
    # box. `systemctl show ollama -p Environment` on the Dell listed only KEEP_ALIVE, so
    # ollama ran on its own CPU default of 3. A control that is budgeted against but absent
    # is worse than a known-absent one: every argument downstream of it was reasoning about
    # a limit that was not in force (Rabbot, 2026-08-31: "I budgeted against a control that
    # was an intention").
    #
    # 2, not 1 and not the default 3. The box deliberately runs BOTH brains co-resident
    # (the 3B router + the 9B agentos brain, ~9 GB together against 23.9 GB available) —
    # that is the KEEP ruling, so 1 would evict one of them on every alternation and hand
    # back the ~51s cold reload KEEP_ALIVE exists to prevent. 3 leaves room for a third
    # model to be pulled in by accident and quietly change the memory picture. 2 is the
    # number the design actually uses, stated where a reader can see it.
    environmentVariables.OLLAMA_MAX_LOADED_MODELS = "2";
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
    ethtool                                # NIC/WoL introspection — see note below
    # Claude Code CLI (Dillon msg 9280: "talk claude through this box"). Unfree —
    # whitelisted in gaming-open.nix's allowUnfreePredicate (single shared predicate;
    # a second definition elsewhere would conflict). Auth is per-user OAuth (`claude`
    # → browser login with the Max account) — no secrets baked into the image.
    claude-code
  ];

  # ethtool is here for a specific reason, not for completeness. After the 2026-08-11 hard
  # hang the recovery story was "Wake-on-LAN" — and WoL could not be *checked* on the box,
  # because `ethtool -- ` reported nothing and an empty output reads identically to "WoL is
  # off." It was a missing binary (rc=127). A recovery path you cannot query is a recovery
  # path you are assuming, which is the whole class in docs/cancelled-boundaries.md.
  # Note the standing caveat: this Dell's ethernet (enp0s31f6) is NO-CARRIER — it runs on
  # wifi — so WoL is inert here until it is plugged in. ethtool makes that inertness
  # *visible* rather than leaving it indistinguishable from working coverage.

  # The box rebuilds itself from nixpkgs (and here the operator has full sudo to do so).
  nix.settings.experimental-features = [ "nix-command" "flakes" ];

  services.xserver.enable = false;   # talk-to-it / SSH-in box, no desktop
  system.stateVersion = "24.11";
}
