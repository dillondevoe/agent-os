# modules/clean-room.nix — the v0.1 "clean room": a default-DENY egress wall + a
# telemetry-off posture that make "sovereign, talks with NO internet" a STRUCTURAL fact,
# not a promise.
#
# Threat model (Phase-2 INV-2, egress): the agent, its local model, and every capability
# it can invoke must have NO path to move bytes off-box. Inference is loopback-only
# (services.ollama @ 127.0.0.1, see modules/brain.nix), so a default-DROP output chain
# costs the agent nothing and denies exfiltration by construction.
#
# The ONE surviving off-box channel once sealed is the nixpkgs binary cache
# (root / nix-daemon), so the box can still rebuild ITSELF from nixpkgs — "nixpkgs-only."
# Everything else is dropped.
#
# Ordering ("seal AFTER the model pull"): `ollama pull` needs the network at provision
# time, so the wall ships UNSEALED (cfg.sealed = false) — a provisioning window that also
# permits DNS + 80/443 so setup-brain.sh can fetch the weights. After the pull, flip
# `agentos.cleanRoom.sealed = true` and `nixos-rebuild switch`; that rebuild removes the
# provisioning allowance — the ollama daemon's own update-ping goes with it — and the box
# is sealed to nixpkgs-only.
#
# SECURITY SURFACE (boot/login + egress wall): routed branch -> PR -> Fable, never
# direct-push, never self-merge.
{ config, pkgs, lib, ... }:
let
  cfg = config.agentos.cleanRoom;
in {
  options.agentos.cleanRoom = {
    enable = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Enable the Agent OS clean-room egress wall + telemetry-off posture.";
    };
    sealed = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = ''
        false = PROVISIONING window: the box may reach DNS + 80/443 so `setup-brain.sh`
        can `ollama pull` the local weights. true = SEALED: the only off-box channel that
        survives is the nixpkgs binary cache (root / nix-daemon), for rebuilds; the agent,
        the local model, and every capability are cut off from the network. Flip to true
        and `nixos-rebuild switch` AFTER the model pull — that rebuild IS the seal.
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    # --- telemetry / phone-home OFF (belt; the egress wall below is the real enforcer) ---
    environment.variables.DO_NOT_TRACK = "1";

    # --- the egress wall: default-DROP output, in its OWN additive table so the inbound
    #     firewall's table is left untouched -----------------------------------------------
    # NOTE: this relies on in-process (glibc) name resolution — configuration.nix uses
    # NetworkManager's default DNS (no systemd-resolved), so a root-initiated lookup
    # egresses as skuid 0 and is accepted. If systemd-resolved is ever enabled, DNS would
    # originate from the systemd-resolve user and need its own accept line — flag at review.
    networking.nftables.enable = true;
    networking.nftables.tables.agentos_egress = {
      family = "inet";
      content = ''
        chain output {
          type filter hook output priority 0; policy drop;

          # return traffic for connections we already allowed
          ct state established,related accept
          ct state invalid drop

          # on-box IPC: the local brain (127.0.0.1:11434), mem, everything loopback
          oifname "lo" accept

          # nixpkgs-only survivor: nix-daemon substituter fetches + fwupd + timesync,
          # all root-initiated. THE only off-box channel once sealed.
          meta skuid 0 accept
        ${lib.optionalString (!cfg.sealed) ''
          # PROVISIONING (unsealed) — removed by `sealed = true` + rebuild. Lets
          # setup-brain.sh resolve + fetch the model weights (and, transiently, lets the
          # ollama daemon's update-ping out; sealing kills it).
          udp dport 53 accept
          tcp dport 53 accept
          tcp dport { 80, 443 } accept
        ''}
          # the agent, the local model, and any capability => dropped (INV-2 egress)
        }
      '';
    };

    # --- structural invariants: eval FAILS if the sovereign posture is broken ------------
    assertions = [
      {
        assertion = config.services.ollama.host == "127.0.0.1";
        message = "clean-room: the local brain (services.ollama) must stay loopback-only "
          + "(127.0.0.1); a routable bind would defeat the INV-2 egress wall.";
      }
    ];

    warnings = lib.optional (!cfg.sealed)
      ("agentos.cleanRoom.sealed = false (PROVISIONING): egress to DNS + 80/443 is open so "
      + "the model can be pulled. Set sealed = true and rebuild once the weights are present.");
  };
}
